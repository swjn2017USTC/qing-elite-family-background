"""v0.2 staging migration: v0.1 artifacts -> evidence-state tables (U01).

    uv run python -m qing_elite.v02.migrate            # build data/processed_v02/*
    uv run python -m qing_elite.v02.migrate --check     # validate what is on disk

Rules (upgrade plan §3.3, §6/U01):

* a fact that v0.1 recorded as a positive field migrates to ``positive``;
* an empty field migrates to ``unknown`` — never to ``explicit_negative``;
* no ``explicit_negative`` is produced in this phase (v0.1 has no explicit
  "未仕/寒素" evidence), and nothing is inferred from an ancestor's name;
* assertions belonging to the persons whose v0.1 human review is still ``pending``
  are marked ``pending`` and are excluded from the primary indicators downstream.

No linking, no new sources, no LLM calls, no statistics.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from typing import Any, Mapping, Sequence

import pandas as pd

from qing_elite.utils.config import (
    INTERIM_DIR,
    PROCESSED_DIR,
    PROCESSED_V02_DIR,
    PROJECT_ROOT,
)
from qing_elite.v02.contracts import (
    EVIDENCE_COLUMNS,
    validate_all,
    validate_table,
)
from qing_elite.v02.indicators import (
    build_legacy_indicators,
    build_primary_indicators,
    select_primary_assertions,
)

REVIEW_FILE = PROJECT_ROOT / "audit" / "manual_validation.csv"
DONE_FILE = INTERIM_DIR / "enrich" / "done.jsonl"
AUDIT_SUMMARY = PROJECT_ROOT / "audit" / "v02" / "u01_migration_summary.csv"
RELEASE_ID = "v01-to-v02-staging"

SLOT_RELATION = {"father": "父", "grandfather": "祖父", "great_grandfather": "曾祖"}
SLOTS = ("father", "grandfather", "great_grandfather")
ATTRIBUTES = ("name", "degree", "office")


def _str(series: pd.Series) -> pd.Series:
    """Nullable object-domain string column (NaN -> ``pd.NA``)."""
    return pd.Series(series.astype("object").where(pd.notna(series), pd.NA), dtype="object")


def _int(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").astype("Int64")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_passages() -> dict[str, str]:
    """Prompt windows of the v0.1 enrichment run, keyed by person_uid."""
    if not DONE_FILE.exists():
        return {}
    passages: dict[str, str] = {}
    with DONE_FILE.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            if record.get("prompt_window"):
                passages[str(record["person_uid"])] = str(record["prompt_window"])
    return passages


def pending_entities() -> set[str]:
    """Entities whose v0.1 pilot review is still pending."""
    if not REVIEW_FILE.exists():
        return set()
    review = pd.read_csv(REVIEW_FILE)
    pending = review[review["human_review_status"] == "pending"]
    return set(pending["person_uid"].astype(str))


# --------------------------------------------------------------------------- tables


def build_entities(master: pd.DataFrame) -> pd.DataFrame:
    resolved = (master["source"] == "CBDB") | (master["linkage_confidence"] == "high")
    frame = pd.DataFrame(
        {
            "entity_id": _str(master["person_uid"]),
            "cbdb_personid": _int(master["cbdb_personid"]),
            "cgedq_person_id": _str(master["cgedq_person_id"]),
            "name_chn": _str(master["c_name_chn"]),
            "source": _str(master["source"]),
            "highest_tier": _str(master["highest_tier"]),
            "tiers_present": _str(master["tiers_present"]),
            "career_first_year": _int(master["career_first_year"]),
            "career_last_year": _int(master["career_last_year"]),
            "native_province_effective": _str(master["native_province_effective"]),
            "banner_effective": _str(master["banner_effective"]),
            "degree_effective": _str(master["degree_effective"]),
            "v01_linkage_confidence": _str(master["linkage_confidence"]),
            "resolution_status": pd.Series(
                ["resolved" if flag else "unresolved" for flag in resolved], dtype="object"
            ),
            "primary_eligible": pd.Series(resolved.to_numpy(), dtype=bool),
        }
    )
    frame = frame.reset_index(drop=True)
    return _apply_linkage(frame, master)


def _apply_linkage(frame: pd.DataFrame, master: pd.DataFrame) -> pd.DataFrame:
    """Use the U02 link table when it exists; otherwise fall back to the U01 labels."""
    from qing_elite.utils.config import PROCESSED_V02_DIR

    cross_path = PROCESSED_V02_DIR / "link_cross_source.parquet"
    canonical_path = PROCESSED_V02_DIR / "cgedq_canonical.parquet"
    if cross_path.exists() and canonical_path.exists():
        from qing_elite.v02.linkage import resolve_entity_links

        return resolve_entity_links(
            frame, master, pd.read_parquet(cross_path), pd.read_parquet(canonical_path)
        )
    fallback_source = frame["source"]
    frame["link_status"] = [
        "explicit_id" if value == "CBDB" else "review_pending" for value in fallback_source
    ]
    frame["link_evidence"] = "U01 fallback: v0.1 linkage labels (run qing_elite.v02.linkage first)"
    resolved = frame["link_status"].isin(["explicit_id", "accepted_cross_link", "standalone"])
    frame["resolution_status"] = ["resolved" if value else "unresolved" for value in resolved]
    frame["primary_eligible"] = resolved.to_numpy()
    return frame


def _document_id_for_title(title: str) -> str:
    return "cbdb_src:" + hashlib.sha1(title.encode("utf-8")).hexdigest()[:12]


def build_source_documents(
    structured: pd.DataFrame, enriched: pd.DataFrame, passages: Mapping[str, str]
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    known_titles = sorted(structured.loc[structured["ancestor_known"], "source_title"].dropna().unique())
    for title in known_titles:
        rows.append(
            {
                "document_id": _document_id_for_title(str(title)),
                "document_type": "cbdb_structured_record",
                "title": str(title),
                "volume": None,
                "locator": "CBDB KIN_DATA (kin records)",
                "release_id": "cbdb_20260912",
                "retrieved_at": None,
                "rights_status": "unknown",
                "license": None,
                "text": None,
                "text_sha256": None,
                "chars": None,
            }
        )
    accepted = enriched[enriched["final_source"] == "llm_extraction"]
    for source_ids in sorted(accepted["source_ids"].dropna().unique()):
        person = str(accepted.loc[accepted["source_ids"] == source_ids, "person_uid"].iloc[0])
        text = passages.get(person)
        volume, _, biography = str(source_ids).partition("#")
        rows.append(
            {
                "document_id": f"qsg:{source_ids}",
                "document_type": "qingshigao_biography",
                "title": biography or str(source_ids),
                "volume": volume.replace("清史稿/", ""),
                "locator": str(source_ids),
                "release_id": "zhwikisource-online-20260914",
                "retrieved_at": None,
                "rights_status": "public_domain",
                "license": "Wikimedia zhwikisource",
                "text": text,
                "text_sha256": _sha(text) if isinstance(text, str) else None,
                "chars": len(text) if isinstance(text, str) else None,
            }
        )
    frame = pd.DataFrame.from_records(rows)
    frame["chars"] = _int(frame["chars"])
    return frame


def build_source_search_log() -> pd.DataFrame:
    """No source search was executed before U03; the table exists, empty by design."""
    return pd.DataFrame(
        {
            "search_id": pd.Series(dtype="string"),
            "entity_id": pd.Series(dtype="string"),
            "source_id": pd.Series(dtype="string"),
            "query": pd.Series(dtype="string"),
            "searched": pd.Series(dtype="bool"),
            "outcome": pd.Series(dtype="string"),
            "searched_at": pd.Series(dtype="datetime64[ns]"),
            "notes": pd.Series(dtype="string"),
        }
    )


def build_evidence_assertions(
    family_final: pd.DataFrame,
    structured: pd.DataFrame,
    enriched: pd.DataFrame,
    passages: Mapping[str, str],
    pending: set[str],
) -> pd.DataFrame:
    locators = structured[
        [
            "person_uid",
            "ancestor_slot",
            "source_title",
            "source_textid",
            "source_pages",
            "kin_relation_chn",
            "ancestor_degree_chn",
            "ancestor_office_sample",
        ]
    ].rename(columns={"person_uid": "entity_id"})
    frame = family_final.merge(locators, on=["entity_id", "ancestor_slot"], how="left")
    llm = enriched.set_index(["person_uid", "ancestor_slot"])[
        ["llm_evidence", "llm_confidence", "call_id", "prompt_version", "source_ids"]
    ]
    frame = frame.join(llm, on=["entity_id", "ancestor_slot"])

    rows: list[dict[str, Any]] = []
    for row in frame.to_dict("records"):
        entity = row["entity_id"]
        slot = row["ancestor_slot"]
        provenance = row["provenance"]
        review_status = "pending" if entity in pending else "not_required"
        relation = row.get("kin_relation_chn")
        relation = str(relation) if isinstance(relation, str) and relation in SLOT_RELATION.values() else SLOT_RELATION[slot]

        if provenance == "cbdb_structured":
            source_id = _document_id_for_title(str(row["source_title"]))
            locator = (
                f"{row['source_title']}"
                f" | textid={row['source_textid'] if pd.notna(row['source_textid']) else 'NA'}"
                f" | pages={row['source_pages'] if pd.notna(row['source_pages']) else 'NA'}"
            )
            extractor, version, run_id = "cbdb_kin_data", "cbdb_20260912", None
            slot_quote, start, end = None, None, None
            confidence = None
        elif provenance == "llm_extraction":
            source_id = f"qsg:{row['source_ids']}"
            locator = str(row["source_ids"])
            extractor, version, run_id = "llm_extraction", row["prompt_version"], row["call_id"]
            slot_quote = row["llm_evidence"] if isinstance(row["llm_evidence"], str) else None
            confidence = row["llm_confidence"]
            text = passages.get(entity)
            if slot_quote and isinstance(text, str):
                if slot_quote not in text:
                    raise ValueError(
                        f"LLM evidence for {entity}/{slot} is not verbatim in the passage; "
                        "cannot migrate a span"
                    )
                start, end = text.find(slot_quote), text.find(slot_quote) + len(slot_quote)
            else:
                # no local passage cache: the assertion still migrates, without offsets
                start, end = None, None
        else:
            source_id, locator, extractor, version, run_id = None, None, "migration", RELEASE_ID, None
            slot_quote, start, end, confidence = None, None, None, None

        values = {
            "name": row.get("final_name"),
            "degree": row.get("final_degree"),
            "office": row.get("final_office"),
        }
        # the verbatim source string backing each positive: the CBDB field itself for a
        # structured record, the model's quote for a 清史稿 window.
        raw_quotes = {
            "name": row.get("final_name"),
            "degree": row.get("ancestor_degree_chn"),
            "office": row.get("ancestor_office_sample"),
        }
        states = {
            "name": "positive" if row.get("slot_sufficient") else "unknown",
            "degree": "positive" if row.get("slot_has_degree") else "unknown",
            "office": "positive" if row.get("slot_has_office") else "unknown",
        }
        for attribute in ATTRIBUTES:
            state = states[attribute]
            value = values[attribute] if state == "positive" else None
            value = value if isinstance(value, str) and value.strip() else None
            if state == "positive" and value is None:
                state = "unknown"
            affirmative = state == "positive"
            if not affirmative:
                quote = None
            elif provenance == "llm_extraction":
                quote = slot_quote
            else:
                candidate = raw_quotes[attribute]
                quote = candidate if isinstance(candidate, str) and candidate.strip() else value
            if provenance == "llm_extraction":
                quote_start, quote_end = (start, end) if affirmative else (None, None)
            else:
                quote_start, quote_end = None, None
            rows.append(
                {
                    "entity_id": entity,
                    "ancestor_slot": slot,
                    "relation_type": relation,
                    "attribute": attribute,
                    "assertion_state": state,
                    "value_raw": value,
                    "value_normalized": value,
                    "source_id": source_id if affirmative else None,
                    "source_locator": locator if affirmative else None,
                    "quote": quote if affirmative else None,
                    "quote_start": quote_start,
                    "quote_end": quote_end,
                    "extractor": extractor if affirmative else "migration",
                    "extractor_version": version if affirmative else RELEASE_ID,
                    "extraction_run_id": run_id if affirmative else None,
                    "review_status": review_status,
                    "confidence": confidence if affirmative else None,
                    "conflict_group_id": None,
                }
            )
    out = pd.DataFrame.from_records(rows, columns=list(EVIDENCE_COLUMNS))
    for column in ("quote_start", "quote_end"):
        out[column] = _int(out[column])
    return out


def build_review_decisions(review: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for record in review.to_dict("records"):
        entity = str(record["person_uid"])
        for slot in SLOTS:
            rows.append(
                {
                    "decision_id": f"u01:{entity}:{slot}:name",
                    "entity_id": entity,
                    "ancestor_slot": slot,
                    "attribute": "name",
                    "proposed_value": record.get(f"{slot}_llm_name"),
                    "decision": str(record.get("human_review_status", "pending")),
                    "reviewer": None,
                    "decided_at": None,
                    "reason": None,
                    "auto_verdict": record.get(f"{slot}_verdict"),
                }
            )
    frame = pd.DataFrame.from_records(rows)
    frame["decided_at"] = pd.to_datetime(frame["decided_at"])
    return frame


# ----------------------------------------------------------------------------- run


def build_tables() -> tuple[dict[str, pd.DataFrame], dict[str, Any]]:
    master = pd.read_parquet(PROCESSED_DIR / "officials_master.parquet")
    structured = pd.read_parquet(PROCESSED_DIR / "family_structured.parquet")
    family_final = pd.read_parquet(PROCESSED_DIR / "family_final.parquet").rename(
        columns={"person_uid": "entity_id"}
    )
    enriched = pd.read_parquet(PROCESSED_DIR / "family_enriched.parquet")
    passages = load_passages()
    pending = pending_entities()

    entities = build_entities(master)
    documents = build_source_documents(structured, enriched, passages)
    search_log = build_source_search_log()
    assertions = build_evidence_assertions(
        family_final, structured, enriched, passages, pending
    )
    review = pd.read_csv(REVIEW_FILE)
    decisions = build_review_decisions(review)

    tables = {
        "entities": entities,
        "source_documents": documents,
        "source_search_log": search_log,
        "evidence_assertions": assertions,
        "review_decisions": decisions,
    }
    validate_all(tables)

    primary, exclusions = select_primary_assertions(assertions, entities)
    indicators = build_primary_indicators(primary, entities)
    legacy = build_legacy_indicators(pd.read_parquet(PROCESSED_DIR / "person_indicators.parquet"))
    validate_table("legacy_person_indicators", legacy)

    tables["person_indicators_v02"] = indicators
    tables["legacy_person_indicators"] = legacy
    stats = {
        "passages_loaded": len(passages),
        "spans_verified": int(assertions["quote_start"].notna().sum()),
        "pending_entities": len(pending),
        "primary_assertions": int(len(primary)),
        "primary_entities": int(len(indicators)),
        **exclusions,
    }
    return tables, stats


def write_tables(tables: Mapping[str, pd.DataFrame]) -> dict[str, str]:
    PROCESSED_V02_DIR.mkdir(parents=True, exist_ok=True)
    written: dict[str, str] = {}
    for name, frame in tables.items():
        path = PROCESSED_V02_DIR / f"{name}.parquet"
        frame.to_parquet(path, index=False)
        written[name] = str(path.relative_to(PROJECT_ROOT))
    return written


def summary_rows(tables: Mapping[str, pd.DataFrame]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for name, frame in tables.items():
        rows.append({"table": name, "rows": int(len(frame)), "columns": int(frame.shape[1])})
    assertions = tables["evidence_assertions"]
    for state, count in assertions["assertion_state"].value_counts().items():
        rows.append({"table": f"evidence_assertions[{state}]", "rows": int(count), "columns": 0})
    primary = tables["person_indicators_v02"]
    for metric in (
        "documented_ancestor_official_any",
        "documented_family_capital_any",
        "documented_commoner_explicit",
    ):
        counts = primary[metric].value_counts(dropna=False).to_dict()
        rows.append(
            {
                "table": f"person_indicators_v02[{metric}]",
                "rows": int(len(primary)),
                "columns": 0,
                "note": json.dumps({str(key): int(value) for key, value in counts.items()}, ensure_ascii=False),
            }
        )
    legacy = tables["legacy_person_indicators"]
    checks = {
        "acceptance:explicit_negative_rows": int((assertions["assertion_state"] == "explicit_negative").sum()),
        "acceptance:primary_documented_any_zero": int((primary["documented_ancestor_official_any"] == 0).sum()),
        "acceptance:legacy_ancestor_official_any_zero": int((legacy["legacy_ancestor_official_any"] == 0).sum()),
        "acceptance:unknown_rows_with_a_value": int(
            assertions.loc[assertions["assertion_state"] == "unknown", "value_raw"].notna().sum()
        ),
        "acceptance:positive_rows_without_quote": int(
            assertions.loc[assertions["assertion_state"] == "positive", "quote"].isna().sum()
        ),
        "acceptance:llm_spans_verified": int(assertions["quote_start"].notna().sum()),
        # every assertion of a pending-review entity is marked pending, so such an
        # entity must contribute nothing to the primary indicator frame.
        "acceptance:pending_entities_with_primary_counts": int(
            (
                primary.loc[
                    primary["person_uid"].isin(
                        set(assertions.loc[assertions["review_status"] == "pending", "entity_id"])
                    ),
                    ["n_positive_name", "n_positive_office", "n_positive_degree"],
                ]
                .sum(axis=1)
                > 0
            ).sum()
        ),
    }
    for check, value in checks.items():
        rows.append({"table": check, "rows": value, "columns": 0})
    return pd.DataFrame.from_records(rows)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="U01 v0.2 staging migration")
    parser.add_argument("--check", action="store_true", help="validate the tables already on disk")
    args = parser.parse_args(argv)

    if args.check:
        tables = {
            name: pd.read_parquet(PROCESSED_V02_DIR / f"{name}.parquet")
            for name in (
                "entities",
                "source_documents",
                "source_search_log",
                "evidence_assertions",
                "review_decisions",
                "person_indicators_v02",
                "legacy_person_indicators",
            )
        }
        validate_all(tables)
        print(f"validated {len(tables)} tables in {PROCESSED_V02_DIR}")
        return 0

    tables, stats = build_tables()
    written = write_tables(tables)
    summary = summary_rows(tables)
    AUDIT_SUMMARY.parent.mkdir(parents=True, exist_ok=True)
    summary.to_csv(AUDIT_SUMMARY, index=False)
    for name, path in written.items():
        print(f"{len(tables[name]):>8} rows  {path}")
    print("stats:", json.dumps(stats, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
