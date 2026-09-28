"""P03 driver: structured family extraction + entity linkage.

    uv run python -m qing_elite.build_family

No LLM is called. Everything that CBDB's structured relations can answer is
answered here; the leftovers become the measured demand that P04/P05 will fill.

Outputs:

    data/processed/family_structured.parquet      (one row per person x ancestor slot)
    output/tables/p03_*.csv                        (coverage, evidence, candidates, cost)
"""

from __future__ import annotations

import sqlite3
from typing import Any, Iterable, Mapping

import pandas as pd

from qing_elite.cbdb.offices import included_office_ids, resolve_cbdb_offices
from qing_elite.family.kinship import (
    SLOTS,
    load_ancestor_attributes,
    load_merged_person_map,
    load_slot_relations,
)
from qing_elite.linkage.candidates import (
    RecallConfig,
    merge_with_explicit_links,
    recall_candidates,
    status_summary,
)
from qing_elite.linkage.names import build_cbdb_candidates, normalize_name
from qing_elite.llm.estimation import (
    PRICES_CNY_PER_MTOK,
    TOKENS_PER_CJK_CHAR,
    estimate_tokens,
)
from qing_elite.utils.config import CBDB_SQLITE, OUTPUT_DIR, PROCESSED_DIR, load_offices

TIER_ORDER = ["A1", "A2", "A3", "B", "C", "D"]


def main() -> int:
    cfg = load_offices()
    conn = sqlite3.connect(CBDB_SQLITE)
    try:
        universe, audit, summary = build(cfg, conn)
    finally:
        conn.close()
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "tables").mkdir(parents=True, exist_ok=True)
    path = PROCESSED_DIR / "family_structured.parquet"
    universe.to_parquet(path, index=False)
    for name, table in audit.items():
        table.to_csv(OUTPUT_DIR / "tables" / f"p03_{name}.csv", index=False)
    print(f"family_structured.parquet : {len(universe):,} rows -> {path}")
    for line in summary:
        print(line)
    return 0


def build(
    cfg: Mapping[str, Any], conn: sqlite3.Connection
) -> tuple[pd.DataFrame, dict[str, pd.DataFrame], list[str]]:
    master = pd.read_parquet(PROCESSED_DIR / "officials_master.parquet")
    master["person_uid"] = master["person_uid"].astype(str)
    cbdb_ids = sorted(
        {int(value) for value in master["cbdb_personid"].dropna().tolist()}
    )

    positions = conn.execute(
        "SELECT c_office_id, c_office_chn, c_dy FROM OFFICE_CODES"
    ).fetchall()
    tier_map = {
        row.office_id: (row.tier, row.canonical)
        for row in resolve_cbdb_offices(positions, cfg)
        if row.source == "include"
    }
    merged_map = load_merged_person_map(conn)
    relations = load_slot_relations(conn, cbdb_ids, merged_map)
    ancestor_ids = sorted(
        {
            int(value)
            for value in relations.get("ancestor_personid", pd.Series(dtype="float")).dropna()
        }
    )
    attributes = load_ancestor_attributes(conn, ancestor_ids, tier_map)
    family = assemble_family(master, relations, attributes)

    candidates = recall_by_section(conn, cfg, master)
    demand = llm_demand(family)
    passage_sample, cost = cost_estimate(demand, family, master)
    passage_volume_summary = passage_simplified(passage_sample)

    audit = {
        "family_coverage": family_coverage(family),
        "ancestor_evidence": ancestor_evidence(family),
        "ancestor_attribute_coverage": ancestor_attribute_coverage(family),
        "llm_demand": demand,
        "cost_estimate": cost,
        "passage_sample": passage_sample,
        "passage_volume_summary": passage_volume_summary,
        "linkage_candidates": candidates["rows"],
        "linkage_candidate_summary": candidates["summary"],
    }
    by_slot = family.groupby("ancestor_slot")["ancestor_known"].sum().to_dict()
    summary = [
        "structured ancestors from CBDB: "
        + ", ".join(f"{slot}={int(by_slot.get(slot, 0)):,}" for slot in SLOTS),
        f"persons needing LLM enrichment: "
        f"{int(demand.loc[demand['tier'] == 'ALL', 'persons_needing_llm'].iloc[0]):,} "
        f"of {len(master):,}",
        "estimated cost (off-peak / peak RMB): "
        + "; ".join(
            f"{row.scenario}={row.rmb_off_peak:.2f}/{row.rmb_peak:.2f}"
            for row in cost.itertuples(index=False)
        ),
    ]
    return family, audit, summary


# ------------------------------------------------------------------- assembly


def assemble_family(
    master: pd.DataFrame,
    relations: pd.DataFrame,
    attributes: pd.DataFrame,
) -> pd.DataFrame:
    """One row per (person, ancestor slot); unknown slots stay as explicit rows."""
    base_columns = [
        "person_uid",
        "cbdb_personid",
        "cgedq_person_id",
        "source",
        "tiers_present",
        "highest_tier",
        "c_name_chn",
        "native_province_effective",
        "banner_effective",
        "degree_effective",
        "linkage_confidence",
    ]
    base = master[base_columns].copy()
    base["cbdb_personid"] = base["cbdb_personid"].astype("Int64")
    frame = base.merge(pd.DataFrame({"ancestor_slot": list(SLOTS)}), how="cross")
    if relations.empty or "slot" not in relations.columns:
        relations = pd.DataFrame(columns=["cbdb_personid", "slot", "ancestor_personid"])
    frame = frame.merge(
        relations,
        left_on=["cbdb_personid", "ancestor_slot"],
        right_on=["cbdb_personid", "slot"],
        how="left",
    ).drop(columns=["slot"])

    if attributes.empty or "ancestor_personid" not in attributes.columns:
        attributes = pd.DataFrame({"ancestor_personid": pd.Series(dtype="Int64")})
    frame = frame.merge(attributes, on="ancestor_personid", how="left")
    for column in (
        "kin_code",
        "kin_relation_chn",
        "evidence_origin",
        "ancestor_personid_raw",
        "source_pages",
        "source_title",
        "remapped_from_merged_id",
    ):
        if column not in frame.columns:
            frame[column] = pd.NA

    frame["ancestor_personid"] = frame["ancestor_personid"].astype("Int64")
    frame["ancestor_known"] = frame["ancestor_personid"].notna()
    frame["family_source"] = frame["ancestor_known"].map({True: "CBDB_KIN_DATA", False: None})
    frame["extraction_method"] = frame["ancestor_known"].map({True: "cbdb_structured", False: None})
    frame["match_status"] = [
        "confirmed_by_explicit_kin_id" if known else "not_in_cbdb_structured_data"
        for known in frame["ancestor_known"]
    ]
    frame["match_method"] = [
        None
        if not known
        else ("merged_id_remap" if remapped else "explicit_kin_id")
        for known, remapped in zip(
            frame["ancestor_known"], frame["remapped_from_merged_id"].fillna(False)
        )
    ]
    frame["match_score"] = frame["ancestor_known"].map({True: 1.0, False: None})
    frame["match_features"] = [
        _match_features(known, kin_code, relation, origin, ancestor_id, raw_id, pages, title)
        for known, kin_code, relation, origin, ancestor_id, raw_id, pages, title in zip(
            frame["ancestor_known"],
            frame["kin_code"],
            frame["kin_relation_chn"],
            frame["evidence_origin"],
            frame["ancestor_personid"],
            frame["ancestor_personid_raw"],
            frame["source_pages"],
            frame["source_title"],
        )
    ]
    frame["confidence"] = frame["ancestor_known"].map({True: "high", False: None})
    in_universe = set(master["cbdb_personid"].dropna().astype(int))
    frame["ancestor_in_universe"] = [
        bool(pd.notna(value) and int(value) in in_universe) for value in frame["ancestor_personid"]
    ]
    frame["llm_needed"] = ~frame["ancestor_known"]
    frame["evidence_origin"] = frame["evidence_origin"].fillna("none")
    return frame.reset_index(drop=True)


def _match_features(
    known: object,
    kin_code: object,
    relation: object,
    origin: object,
    ancestor_id: object,
    raw_id: object,
    pages: object,
    title: object,
) -> str | None:
    if not known:
        return "no kin record in CBDB for this slot"
    parts = [
        f"CBDB KIN_DATA c_kin_code={int(kin_code)}" if pd.notna(kin_code) else "CBDB KIN_DATA",
        f"relation={relation}" if isinstance(relation, str) else "relation=unknown",
        f"ancestor_cbdb_id={int(ancestor_id)}",
        f"evidence={origin}",
    ]
    if pd.notna(raw_id) and raw_id != ancestor_id:
        parts.append(f"remapped_from_merged_id={int(raw_id)}")
    if isinstance(title, str) and title:
        parts.append(f"source={title}")
    if isinstance(pages, str) and pages:
        parts.append(f"pages={pages}")
    return "; ".join(parts)


# -------------------------------------------------------------------- audits


def family_coverage(family: pd.DataFrame) -> pd.DataFrame:
    """Per tier x slot: how much family the CBDB side can answer."""
    rows: list[dict[str, object]] = []
    for tier in TIER_ORDER:
        members = family[family["tiers_present"].fillna("").str.contains(tier, regex=False)]
        if members.empty:
            continue
        for slot in SLOTS:
            slot_rows = members[members["ancestor_slot"] == slot]
            known = slot_rows[slot_rows["ancestor_known"]]
            rows.append(
                {
                    "tier": tier,
                    "ancestor_slot": slot,
                    "n_persons": len(slot_rows),
                    "n_known": len(known),
                    "known_pct": round(100 * len(known) / max(len(slot_rows), 1), 2),
                    "known_with_degree_pct": _pct(known, "ancestor_degree"),
                    "known_with_office_pct": _pct(known, "ancestor_n_postings"),
                    "known_with_native_place_pct": _pct(known, "ancestor_native_addr"),
                    "known_with_birth_year_pct": _pct(known, "ancestor_birth_year"),
                }
            )
    return pd.DataFrame.from_records(rows)


def _pct(frame: pd.DataFrame, column: str) -> float:
    if frame.empty or column not in frame.columns:
        return 0.0
    return round(100 * frame[column].notna().mean(), 2)


def ancestor_evidence(family: pd.DataFrame) -> pd.DataFrame:
    known = family[family["ancestor_known"]]
    if known.empty:
        return pd.DataFrame(columns=["ancestor_slot", "evidence_origin", "n"])
    grouped = (
        known.groupby(["ancestor_slot", "evidence_origin"]).size().rename("n").reset_index()
    )
    totals = grouped.groupby("ancestor_slot")["n"].transform("sum")
    grouped["share_pct"] = (100 * grouped["n"] / totals).round(2)
    return grouped


def ancestor_attribute_coverage(family: pd.DataFrame) -> pd.DataFrame:
    known = family[family["ancestor_known"]]
    rows = []
    for slot in SLOTS:
        slot_rows = known[known["ancestor_slot"] == slot]
        rows.append(
            {
                "ancestor_slot": slot,
                "n_known": len(slot_rows),
                "has_degree": int(slot_rows.get("ancestor_degree", pd.Series(dtype=object)).notna().sum()),
                "has_office": int(slot_rows.get("ancestor_n_postings", pd.Series(dtype=object)).notna().sum()),
                "has_native_addr": int(slot_rows.get("ancestor_native_addr", pd.Series(dtype=object)).notna().sum()),
                "has_index_year": int(slot_rows["ancestor_index_year"].notna().sum()),
                "has_birth_year": int(slot_rows["ancestor_birth_year"].notna().sum()),
                "in_study_universe": int(slot_rows["ancestor_in_universe"].sum()),
            }
        )
    return pd.DataFrame.from_records(rows)


def llm_demand(family: pd.DataFrame) -> pd.DataFrame:
    """Persons who still need text extraction, by tier and by missing-slot pattern."""
    per_person = (
        family.assign(missing=family["llm_needed"].astype(int))
        .groupby(["person_uid", "tiers_present", "source"], dropna=False)["missing"]
        .sum()
        .rename("n_missing_slots")
        .reset_index()
    )
    rows: list[dict[str, object]] = []
    for tier in TIER_ORDER:
        members = per_person[per_person["tiers_present"].fillna("").str.contains(tier, regex=False)]
        if members.empty:
            continue
        needing = members[members["n_missing_slots"] > 0]
        rows.append(
            {
                "tier": tier,
                "n_persons": len(members),
                "persons_needing_llm": len(needing),
                "needing_llm_pct": round(100 * len(needing) / max(len(members), 1), 2),
                "persons_missing_all_three": int((members["n_missing_slots"] == 3).sum()),
                "slot_gaps": int(members["n_missing_slots"].sum()),
            }
        )
    total = {
        "tier": "ALL",
        "n_persons": len(per_person),
        "persons_needing_llm": int((per_person["n_missing_slots"] > 0).sum()),
        "needing_llm_pct": round(100 * (per_person["n_missing_slots"] > 0).mean(), 2),
        "persons_missing_all_three": int((per_person["n_missing_slots"] == 3).sum()),
        "slot_gaps": int(per_person["n_missing_slots"].sum()),
    }
    return pd.DataFrame.from_records(rows + [total])


def cost_estimate(
    demand: pd.DataFrame, family: pd.DataFrame, master: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Token/RMB estimate from *measured* 《清史稿》 passages, per scoping scenario."""
    sample_path = PROCESSED_DIR.parent / "interim" / "passages" / "p03_passage_sample.csv"
    if sample_path.exists():
        passage_sample = pd.read_csv(sample_path)
    else:
        passage_sample = pd.DataFrame(columns=["volume", "name", "biography_chars", "window_chars"])
    windows: Iterable[int] = (
        passage_sample["window_chars"].tolist() if not passage_sample.empty else [3000]
    )
    scenarios = scenario_person_counts(family, master)
    rows = []
    for scenario, persons in scenarios.items():
        estimate = estimate_tokens(persons, windows)
        rows.append(
            {
                "scenario": scenario,
                "persons_needing_llm": estimate.persons,
                "mean_biography_chars": round(float(passage_sample["biography_chars"].mean()), 1)
                if not passage_sample.empty
                else None,
                "mean_window_chars": estimate.mean_window_chars,
                "median_window_chars": float(passage_sample["window_chars"].median())
                if not passage_sample.empty
                else None,
                "p90_window_chars": float(passage_sample["window_chars"].quantile(0.9))
                if not passage_sample.empty
                else None,
                "mean_input_tokens": estimate.mean_input_tokens,
                "total_input_tokens": estimate.total_input_tokens,
                "mean_output_tokens": estimate.mean_output_tokens,
                "total_output_tokens": estimate.total_output_tokens,
                "rmb_off_peak": estimate.rmb_off_peak,
                "rmb_peak": estimate.rmb_peak,
                "passages_measured": int(len(passage_sample)),
                "volumes_measured": int(passage_sample["volume"].nunique())
                if not passage_sample.empty
                else 0,
                "tokens_per_cjk_char": TOKENS_PER_CJK_CHAR,
                "price_input_per_mtok_off_peak": PRICES_CNY_PER_MTOK["off_peak"]["input_cache_miss"],
                "price_output_per_mtok_off_peak": PRICES_CNY_PER_MTOK["off_peak"]["output"],
                "price_source": "api-docs.deepseek.com/zh-cn/quick_start/pricing (retrieved 2026-09-14)",
            }
        )
    # Audit keeps the raw per-passage measurements (the evidence behind the cost)
    # plus a per-volume roll-up.
    return passage_sample, pd.DataFrame(rows)


def scenario_person_counts(family: pd.DataFrame, master: pd.DataFrame) -> dict[str, int]:
    """How many people each plausible enrichment scope would send to the LLM."""
    per_person = (
        family.assign(missing=family["llm_needed"].astype(int))
        .groupby("person_uid")
        .agg(n_missing=("missing", "sum"), tiers=("tiers_present", "first"), confidence=("linkage_confidence", "first"))
        .reset_index()
    )
    gap = per_person[per_person["n_missing"] > 0]
    abc = gap[gap["tiers"].fillna("").str.contains("A1|A2|A3|B|C", regex=True)]
    d_high = gap[
        gap["tiers"].fillna("").str.contains("D", regex=False)
        & gap["confidence"].isin(["high"])
    ]
    abc_plus_d_high = pd.concat([abc, d_high]).drop_duplicates("person_uid")
    abcn_only = gap[
        gap["tiers"].fillna("").str.contains("A1|A2|A3|B|C", regex=True)
        & ~gap["tiers"].fillna("").str.contains("D", regex=False)
    ]
    return {
        "S1_full_universe": len(gap),
        "S2_ABC_all_tiers": len(abc),
        "S3_ABC_plus_D_high_linkage": len(abc_plus_d_high),
        "S4_ABC_excluding_D_persons": len(abcn_only),
    }


def passage_simplified(sample: pd.DataFrame) -> pd.DataFrame:
    if sample.empty:
        return sample
    return (
        sample.groupby("volume")
        .agg(
            n_biographies=("name", "size"),
            mean_biography_chars=("biography_chars", "mean"),
            mean_window_chars=("window_chars", "mean"),
            median_window_chars=("window_chars", "median"),
        )
        .round(1)
        .reset_index()
    )


def recall_by_section(
    conn: sqlite3.Connection, cfg: Mapping[str, Any], master: pd.DataFrame
) -> dict[str, pd.DataFrame]:
    """RapidFuzz candidate recall for JSL persons without an explicit-id link."""
    from qing_elite.cbdb.appointments import build_province_lookup

    province_lookup = build_province_lookup(conn)
    candidates = build_cbdb_candidates(conn, province_lookup).rename(
        columns={"c_personid": "cbdb_personid"}
    )
    simplify = cfg["cgedq"]["simplify_map"]
    variants = cfg["cgedq"]["char_variants"]
    candidates["name_norm"] = [
        normalize_name(value, simplify, variants) for value in candidates["c_name_chn"]
    ]
    candidates = candidates.dropna(subset=["name_norm"])

    degrees = pd.read_sql_query(
        """
        SELECT e.c_personid AS cbdb_personid, c.c_entry_desc_chn
        FROM ENTRY_DATA e JOIN ENTRY_CODES c ON c.c_entry_code = e.c_entry_code
        JOIN BIOG_MAIN b ON b.c_personid = e.c_personid WHERE b.c_dy = 20
        """,
        conn,
    )
    from qing_elite.cbdb.degrees import classify_entry_desc

    if not degrees.empty:
        classified = degrees["c_entry_desc_chn"].map(classify_entry_desc)
        degrees["rank"] = [value[1] for value in classified]
        degrees["degree"] = [value[0] for value in classified]
        best_degree = (
            degrees.sort_values("rank", ascending=False)
            .drop_duplicates("cbdb_personid")[["cbdb_personid", "degree"]]
            .rename(columns={"degree": "degree_cbdb"})
        )
        candidates = candidates.merge(best_degree, on="cbdb_personid", how="left")

    unresolved = master[
        master["cgedq_person_id"].notna()
        & master["linkage_confidence"].isin(["unmatched", "ambiguous", "unlinkable_no_surname"])
    ].copy()
    # Build the subject frame column by column: master already has a CBDB-side
    # ``native_province``, so a rename here would create duplicate columns and make
    # the province comparison silently read the wrong field.
    subjects = pd.DataFrame(
        {
            "cgedq_person_id": unresolved["cgedq_person_id"],
            "name_norm": unresolved["c_name_chn"],
            "native_province": unresolved["native_province_effective"],
            "degree_effective": unresolved["degree_effective"],
            "banner_effective": unresolved["banner_effective"],
            "p02_linkage_confidence": unresolved["linkage_confidence"],
        }
    )
    # JSL writes bannermen by given name only; those subjects are kept in the recall
    # but flagged, because a name-only proposal cannot be corroborated by surname.
    subjects["subject_has_surname"] = subjects["p02_linkage_confidence"].ne(
        "unlinkable_no_surname"
    )
    subjects["name_norm"] = [
        normalize_name(value, simplify, variants) for value in subjects["name_norm"]
    ]
    subjects = subjects.dropna(subset=["name_norm"])
    recall = recall_candidates(subjects, candidates, config=RecallConfig(limit=3, score_cutoff=82.0))
    recall = merge_with_explicit_links(recall, master)
    summary = status_summary(recall, len(subjects))
    summary["subjects_submitted"] = int(len(subjects))
    return {"rows": recall, "summary": summary}


if __name__ == "__main__":
    raise SystemExit(main())
