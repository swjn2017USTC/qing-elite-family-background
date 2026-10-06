"""U09R analysis sample: linked persons with both family capital and career outcomes.

The sample is defined by the *link*, and that has to be said out loud: a person enters only if
a CGED-Q roster record and a CBDB record were accepted as the same individual. Linkage success
is therefore a selection mechanism, not a nuisance — every table carries the link-confidence
flag, and a sensitivity run repeats the analysis on auto-accepted links only.

Sources of the join:

* U06R ``entity_links`` (auto-accepted by the ML region) and
* the recovered v0.2 ``entity_links`` (deterministic multi-field rules),

union-merged, with ``link_confidence`` recording which rule admitted the pair.

Exposure comes from CBDB kin/credential/postings through the U07R indicator code; outcome comes
from the U08R career panel. Nothing here recomputes either side differently — reusing the stage
modules is what keeps U09R honest about what the upstream stages actually measured.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from qing_elite.utils.config import PROJECT_ROOT
from qing_elite.v03.kin.indicators import compute_indicators
from qing_elite.v03.lit.registry import PROCESSED_V03_DIR
from qing_elite.v03.pilot.structured import extract_cbdb

LEGACY_LINKS = PROJECT_ROOT / "data" / "interim_v03" / "legacy_v02" / "entity_links.parquet"
INTERIM = PROJECT_ROOT / "data" / "interim_v03" / "analysis"


def load_links() -> pd.DataFrame:
    """Union of U06R and v0.2 accepted links, with the admitting rule per pair."""
    frames: list[pd.DataFrame] = []
    if LEGACY_LINKS.exists():
        legacy = pd.read_parquet(LEGACY_LINKS)
        frames.append(
            pd.DataFrame(
                {
                    "cgedq_person_id": legacy["source_entity_id"].astype(str).str.replace("cgedq:", "", regex=False),
                    "cbdb_person_id": legacy["target_entity_id"].astype(str).str.replace("cbdb:", "", regex=False),
                    "link_confidence": "v02_deterministic",
                    "decision_reason": legacy["decision_reason"].astype(str),
                }
            )
        )
    u06 = PROCESSED_V03_DIR / "entity_links.parquet"
    if u06.exists():
        modern = pd.read_parquet(u06)
        accepted = modern.loc[modern["decision"] == "auto_accept"]
        frames.append(
            pd.DataFrame(
                {
                    "cgedq_person_id": accepted["left_id"].astype(str),
                    "cbdb_person_id": accepted["right_id"].astype(str),
                    "link_confidence": "u06r_auto_accept",
                    "decision_reason": accepted["decision_rule"].astype(str),
                }
            )
        )
    links = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if links.empty:
        return links
    # a pair admitted by both is recorded at its strongest evidence level
    links["confidence_rank"] = links["link_confidence"].map(
        {"u06r_auto_accept": 0, "v02_deterministic": 1}
    )
    links = (
        links.sort_values("confidence_rank")
        .drop_duplicates(subset=["cgedq_person_id", "cbdb_person_id"], keep="first")
        .drop(columns=["confidence_rank"])
    )
    return links


def family_side(cbdb_ids: list[str]) -> pd.DataFrame:
    """Kin indicators for the linked CBDB persons, using the U07R indicator code."""
    if not cbdb_ids:
        return pd.DataFrame()
    cache = INTERIM / "linked_family_indicators.parquet"
    INTERIM.mkdir(parents=True, exist_ok=True)
    if cache.exists():
        cached = pd.read_parquet(cache)
        if set(cbdb_ids) <= set(cached["person_id"].str.replace("cbdb:", "", regex=False)):
            return cached
    clean_ids = [str(int(float(value))) for value in cbdb_ids if str(value).strip()]
    extraction = extract_cbdb([int(value) for value in clean_ids])
    edges = extraction.kin_edges.rename(columns={"relation_code": "relation_type"})
    edges["page_number"] = None
    edges["quote"] = edges["alter_name_raw"].fillna("")
    edges["offsets"] = None
    edges["focal_person"] = edges["ego_person_id"]
    edges["kin_name"] = edges["alter_name_raw"]
    edges["source_id"] = "cbdb"
    edges["generation"] = edges["generation_delta"]
    focal = [f"cbdb:{value}" for value in clean_ids]
    indicators = compute_indicators(focal, edges, extraction.credentials, extraction.career_events)
    indicators.to_parquet(cache, index=False)
    return indicators


def build_sample() -> dict[str, Any]:
    """Return the analysis sample plus the counts needed for the flow diagram."""
    links = load_links()
    events = pd.read_parquet(PROCESSED_V03_DIR / "career_events.parquet")
    outcomes = pd.read_parquet(PROCESSED_V03_DIR / "career_outcomes.parquet")
    jsl_persons = set(events.loc[events["source_id"] == "cgedq_jsl", "person_id"])
    linked_jsl = set(links["cgedq_person_id"]) & jsl_persons
    linked = links.loc[links["cgedq_person_id"].isin(linked_jsl)].copy()

    family = family_side(sorted(set(linked["cbdb_person_id"])))
    family["person_id"] = family["person_id"].astype(str)
    # the indicator frame uses prefixed ids ("cbdb:123"); the link table stores the raw id
    family["cbdb_id"] = family["person_id"].str.replace("cbdb:", "", regex=False)

    frame = linked.merge(
        family, left_on="cbdb_person_id", right_on="cbdb_id", how="left", suffixes=("", "_family")
    ).merge(
        outcomes, left_on="cgedq_person_id", right_on="person_id", how="inner", suffixes=("", "_career")
    )

    # strata: cohort from the first observed year, region and credential from the JSL side
    persons_frame = INTERIM / "jsl_person_attributes.parquet"
    if persons_frame.exists():
        attributes = pd.read_parquet(persons_frame)
        frame = frame.merge(attributes, left_on="cgedq_person_id", right_on="cgedq_person_id", how="left")

    frame["cohort_id"] = pd.cut(
        frame["first_event_year"],
        bins=[1759, 1775, 1785, 1798],
        labels=["1760-1775", "1776-1785", "1786-1798"],
    ).astype(str)

    flow = {
        "links_total": int(len(links)),
        "links_with_jsl_career": int(len(linked)),
        "links_with_family_indicators": int(frame["direct_3g_degree_count"].notna().sum()),
        "analysis_rows": int(len(frame)),
    }
    return {"frame": frame, "flow": flow, "links": links, "family": family}
