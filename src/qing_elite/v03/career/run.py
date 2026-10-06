"""U08R orchestration: JSL + CBDB → career_events → derived outcomes → tier crosswalk.

``python -m qing_elite.v03.career.run`` writes:

* ``data/processed_v03/career_events.parquet`` — the longitudinal panel (JSL spells + CBDB and
  later standardized-source postings, each with its document id)
* ``data/processed_v03/career_offices.parquet`` — office ontology v2 (rank / level / authority
  / appointment status), with the rank table's verification status attached
* ``data/processed_v03/career_outcomes.parquet`` — derived outcomes with lineage
* ``data/processed_v03/career_validation.parquet`` — chronology and transition flags
* ``data/processed_v03/career_tier_crosswalk.parquet`` — legacy A/B/C/D as an extension outcome
* ``reports/upgrade_v03/U08R.md`` is written by hand; the numbers it quotes come from
  ``data/interim_v03/career/u08r_metrics.json``

Standardized-source resumes (requirement 3): the only standardized family source this project
can read automatically is the public-domain 同官录 scan, and U05R demoted its attribute layer
to identity-only. No resume events are therefore folded in, and the run records that fact
rather than leaving a silent hole.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd

from qing_elite.utils.config import PROJECT_ROOT, load_offices
from qing_elite.v03.career import derive as derive_mod
from qing_elite.v03.career import jsl as jsl_mod
from qing_elite.v03.career import offices as offices_mod
from qing_elite.v03.lit.registry import PROCESSED_V03_DIR

ONTOLOGY_VERSION = "offices-v2"
METRICS_JSON = PROJECT_ROOT / "data" / "interim_v03" / "career" / "u08r_metrics.json"
LEGACY_TIER_YAML = PROJECT_ROOT / "config" / "offices.yaml"


def legacy_tier_map() -> dict[str, str]:
    """v0.1's tier lists, read from the frozen config (office title → tier).

    The lists live under ``cbdb.<TIER>.office_names`` in ``config/offices.yaml``; the frozen
    file is read as-is, so the crosswalk uses exactly the pre-registered v0.1 vocabulary.
    """
    config = load_offices()
    mapping: dict[str, str] = {}
    for tier in ("A1", "A2", "A3", "B", "C"):
        spec = (config.get("cbdb") or {}).get(tier) or {}
        for title in spec.get("office_names") or []:
            mapping[title] = tier
    return mapping


def legacy_tier_for(office_raw: str, mapping: dict[str, str]) -> str | None:
    for title, tier in sorted(mapping.items(), key=lambda item: -len(item[0])):
        if title and title in str(office_raw):
            return tier
    return None


def run(*, start_year: int = 1760, end_year: int = 1798) -> dict[str, Any]:
    PROCESSED_V03_DIR.mkdir(parents=True, exist_ok=True)
    METRICS_JSON.parent.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------------- events
    observations = jsl_mod.load_jsl_events(start_year=start_year, end_year=end_year)
    jsl_events = jsl_mod.collapse_spells(observations)
    cbdb_source = PROCESSED_V03_DIR / "pilot_career_events.parquet"
    cbdb_events = (
        jsl_mod.cbdb_events(pd.read_parquet(cbdb_source)) if cbdb_source.exists() else pd.DataFrame()
    )
    common = [
        "event_id", "person_id", "office_raw", "start_year", "end_year", "n_observations",
        "selection_method", "province", "post_importance", "source_id",
    ]
    if len(cbdb_events):
        events = pd.concat(
            [jsl_events.loc[:, common], cbdb_events.reindex(columns=common)],
            ignore_index=True,
        )
    else:
        events = jsl_events.loc[:, common].copy()
    events = jsl_mod.flag_source_conflicts(events)

    # ---------------------------------------------------------------- ontology
    offices = offices_mod.build_ontology(
        events["office_raw"].dropna().tolist(), ontology_version=ONTOLOGY_VERSION
    )
    offices.to_parquet(PROCESSED_V03_DIR / "career_offices.parquet", index=False)

    lookup = offices.set_index("office_title")
    for column, source in (("rank_class", "rank_class"), ("administrative_level", "administrative_level")):
        events[column] = [
            lookup.loc[title, source] if title in lookup.index else pd.NA
            for title in events["office_raw"]
        ]
    flags = pd.DataFrame(
        [
            offices_mod.appointment_flags(
                office_raw=str(row.office_raw), selection_method=str(row.selection_method or "")
            )
            for row in events.itertuples(index=False)
        ],
        index=events.index,
    )
    events = pd.concat([events, flags], axis=1)
    events["source_document_id"] = events.apply(
        lambda row: f"{row.source_id}|{row.office_raw}|{row.start_year}", axis=1
    )
    events["assertion_state"] = "positive"
    events["review_status"] = events["conflict_with_other_source"].map(
        {True: "pending", False: "not_required"}
    )
    events.to_parquet(PROCESSED_V03_DIR / "career_events.parquet", index=False)

    # ---------------------------------------------------------------- derived + validators
    outcomes = derive_mod.derive_outcomes(events, offices)
    outcomes.to_parquet(PROCESSED_V03_DIR / "career_outcomes.parquet", index=False)
    issues = derive_mod.validate_events(events)
    issues.to_parquet(PROCESSED_V03_DIR / "career_validation.parquet", index=False)

    # ---------------------------------------------------------------- legacy tier crosswalk
    tier_map = legacy_tier_map()
    tier_rows = []
    for person, group in events.groupby("person_id"):
        tiers = [tier for tier in (legacy_tier_for(title, tier_map) for title in group["office_raw"]) if tier]
        order = ["D", "C", "B", "A3", "A2", "A1"]
        highest = next((tier for tier in order if tier in tiers), None)
        tier_rows.append(
            {
                "person_id": person,
                "legacy_tier": highest,
                "legacy_tiers_present": ";".join(sorted(set(tiers))),
                "note": "tier 仅作 legacy/extension outcome，不作 V0.3 primary outcome 或分组变量",
            }
        )
    crosswalk = pd.DataFrame(tier_rows)
    crosswalk.to_parquet(PROCESSED_V03_DIR / "career_tier_crosswalk.parquet", index=False)

    metrics = {
        "stage": "U08R",
        "window": {"start": start_year, "end": end_year},
        "ontology": {"version": ONTOLOGY_VERSION, **offices_mod.describe(offices)},
        "events": jsl_mod.summary(events),
        "jsl_observations": int(len(observations)),
        "jsl_spells": int(len(jsl_events)),
        "cbdb_events": int(len(cbdb_events)),
        "outcomes": {
            "persons": int(len(outcomes)),
            "with_rank": int(outcomes["highest_rank_class"].notna().sum()),
            "with_route": int((outcomes["central_local_route"] != "unknown").sum()),
            "mean_career_length": round(float(outcomes["career_length_years"].dropna().mean()), 2)
            if outcomes["career_length_years"].notna().any()
            else None,
            "appointment_composition": {
                "acting": round(float(outcomes["acting_share"].mean()), 4),
                "expectant": round(float(outcomes["expectant_share"].mean()), 4),
                "substantive": round(float(outcomes["substantive_share"].mean()), 4),
                "honorific": round(float(outcomes["honorific_share"].mean()), 4),
                "concurrent": round(float(outcomes["concurrent_share"].mean()), 4),
            },
        },
        "validation": derive_mod.summary(issues, len(events)),
        "tier_crosswalk": {
            "persons": int(len(crosswalk)),
            "with_legacy_tier": int(crosswalk["legacy_tier"].notna().sum()),
            "by_tier": crosswalk["legacy_tier"].value_counts(dropna=False).to_dict(),
        },
        "standardized_source_resumes": {
            "folded_in": 0,
            "reason": "U05R demoted the only automatically readable standardized source (公版同官录扫描件) to identity-only; no attributed resume events exist to fold in",
        },
        "biographies": {
            "used": 0,
            "reason": "requirement 4 reserves biographies for backfill; the current panel needs no backfill and no biography may override a structured source silently",
        },
        "lineage": {
            "events_with_document": int(events["source_document_id"].notna().sum()),
            "outcomes_with_lineage": int((outcomes["lineage_event_ids"].str.len() > 0).sum()),
        },
    }
    METRICS_JSON.write_text(json.dumps(metrics, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return metrics


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - thin CLI
    parser = argparse.ArgumentParser(description="build the U08R career panel")
    parser.add_argument("--start", type=int, default=1760)
    parser.add_argument("--end", type=int, default=1798)
    args = parser.parse_args(argv)
    metrics = run(start_year=args.start, end_year=args.end)
    print(json.dumps(metrics, ensure_ascii=False, indent=2, default=str)[:3000])
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
