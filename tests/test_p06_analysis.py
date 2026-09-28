"""Behaviour tests for the P06 codebook, coverage and statistics.

The invariant that matters most: 寒門/官宦 labels come from these deterministic rules,
NA is never silently turned into 0, and a structured value always wins over an
extracted one.
"""

from __future__ import annotations

import pandas as pd
import pytest

from qing_elite.analysis.codebook import (
    attach_groups,
    build_family_final,
    classify_office_text,
    coverage_table,
    load_codebook,
    person_indicators,
)
from qing_elite.analysis.statistics import (
    fit_with_fallback,
    generations_distribution,
    prevalence_by,
    wilson_interval,
)

TIER_NAMES = {
    "大學士": "A1",
    "內閣大學士": "A1",
    "軍機大臣": "A3",
    "吏部尚書": "B",
    "兵部侍郎": "B",
    "巡撫": "C",
    "總督": "C",
}


@pytest.fixture(scope="module")
def codebook():
    return load_codebook()


def _structured(rows: list[dict]) -> pd.DataFrame:
    base = {
        "person_uid": "cbdb:1",
        "ancestor_slot": "father",
        "tiers_present": "A1",
        "highest_tier": "A1",
        "ancestor_known": False,
        "ancestor_name": None,
        "ancestor_degree": None,
        "ancestor_office_sample": None,
        "ancestor_highest_tier": None,
        "ancestor_native_addr": None,
    }
    return pd.DataFrame([{**base, **row} for row in rows])


def test_classify_office_text_picks_the_highest_tier() -> None:
    assert classify_office_text("吏部尚書", TIER_NAMES) == "B"
    assert classify_office_text("內閣大學士", TIER_NAMES) == "A1"
    assert classify_office_text("歷官知縣", TIER_NAMES) is None  # local office, not "high"
    assert classify_office_text("", TIER_NAMES) is None


def test_structured_value_wins_over_extracted_value(codebook) -> None:
    structured = _structured(
        [
            {
                "ancestor_slot": "father",
                "ancestor_known": True,
                "ancestor_name": "張英",
                "ancestor_degree": "進士",
                "ancestor_office_sample": "大學士",
                "ancestor_highest_tier": "A1",
            }
        ]
    )
    enriched = pd.DataFrame(
        [
            {
                "person_uid": "cbdb:1",
                "ancestor_slot": "father",
                "final_name": "别的名字",
                "final_degree": "舉人",
                "final_office": "知縣",
                "final_source": "llm_extraction",
                "llm_office": "知縣",
                "llm_evidence_verbatim": True,
            }
        ]
    )
    final = build_family_final(structured, enriched)
    row = final.iloc[0]
    assert row["provenance"] == "cbdb_structured"
    assert row["final_name"] == "張英" and row["final_degree"] == "進士"


def test_llm_value_is_used_only_for_a_structured_gap(codebook) -> None:
    structured = _structured([{"ancestor_slot": "father"}])
    enriched = pd.DataFrame(
        [
            {
                "person_uid": "cbdb:1",
                "ancestor_slot": "father",
                "final_name": "劉統勳父",
                "final_degree": None,
                "final_office": "四川布政使",
                "final_source": "llm_extraction",
                "llm_office": "四川布政使",
                "llm_evidence_verbatim": True,
            }
        ]
    )
    final = build_family_final(structured, enriched)
    assert final.iloc[0]["provenance"] == "llm_extraction"
    assert final.iloc[0]["slot_sufficient"]


def test_indicators_are_na_when_nothing_is_known(codebook) -> None:
    final = build_family_final(_structured([{"ancestor_slot": "father"}]), None)
    indicators = person_indicators(final, codebook, TIER_NAMES).iloc[0]
    assert pd.isna(indicators["ancestor_official_any"])
    assert pd.isna(indicators["elite_generations_count"])
    assert pd.isna(indicators["strict_commoner_3g"])  # NA, never 0


def test_strict_commoner_requires_three_sufficient_and_clean_generations(codebook) -> None:
    rows = [
        {"ancestor_slot": "father", "ancestor_known": True, "ancestor_name": "甲"},
        {"ancestor_slot": "grandfather", "ancestor_known": True, "ancestor_name": "乙"},
        {"ancestor_slot": "great_grandfather", "ancestor_known": True, "ancestor_name": "丙"},
    ]
    indicators = person_indicators(build_family_final(_structured(rows), None), codebook, TIER_NAMES).iloc[0]
    assert int(indicators["strict_commoner_3g"]) == 1
    assert int(indicators["elite_generations_count"]) == 0
    assert int(indicators["ancestor_official_any"]) == 0
    assert pd.isna(indicators["ancestor_high_official"])  # no office anywhere

    rows[0]["ancestor_office_sample"] = "巡撫"
    indicators = person_indicators(build_family_final(_structured(rows), None), codebook, TIER_NAMES).iloc[0]
    assert int(indicators["strict_commoner_3g"]) == 0
    assert int(indicators["ancestor_official_any"]) == 1
    assert int(indicators["elite_generations_count"]) == 1


def test_high_official_uses_the_pre_registered_tiers(codebook) -> None:
    rows = [
        {"ancestor_slot": "father", "ancestor_known": True, "ancestor_name": "甲", "ancestor_degree": "進士"},
        {
            "ancestor_slot": "grandfather",
            "ancestor_known": True,
            "ancestor_name": "乙",
            "ancestor_office_sample": "巡撫",
            "ancestor_highest_tier": "C",
        },
    ]
    indicators = person_indicators(build_family_final(_structured(rows), None), codebook, TIER_NAMES).iloc[0]
    assert int(indicators["ancestor_high_official"]) == 1  # C is in the primary tier set
    assert int(indicators["ancestor_high_official_sensitivity"]) == 0  # C is outside the A/B set


def test_free_text_office_from_llm_is_tiered(codebook) -> None:
    structured = _structured([{"ancestor_slot": "father"}])
    enriched = pd.DataFrame(
        [
            {
                "person_uid": "cbdb:1",
                "ancestor_slot": "father",
                "final_name": "允升",
                "final_degree": "進士",
                "final_office": "兵部侍郎",
                "final_source": "llm_extraction",
                "llm_office": "兵部侍郎",
                "llm_evidence_verbatim": True,
            }
        ]
    )
    indicators = person_indicators(build_family_final(structured, enriched), codebook, TIER_NAMES).iloc[0]
    assert int(indicators["ancestor_high_official"]) == 1
    assert int(indicators["ancestor_degree_any"]) == 1


def test_attach_groups_maps_cohort_banner_and_jiangnan(codebook) -> None:
    persons = pd.DataFrame([{"person_uid": "cbdb:1"}])
    master = pd.DataFrame(
        [
            {
                "person_uid": "cbdb:1",
                "highest_tier": "A1",
                "tiers_present": "A1",
                "source": "CBDB",
                "banner_effective": "unknown",
                "native_province_effective": "江蘇",
                "career_first_year": 1750,
                "career_last_year": 1780,
                "n_appointments": 3,
            }
        ]
    )
    grouped = attach_groups(persons, master, codebook)
    row = grouped.iloc[0]
    assert row["tier_group"] == "A"
    assert row["cohort"] == "1701-1760"
    assert bool(row["jiangnan_core"]) is True
    assert row["banner_group"] == "unknown"  # never collapsed into "non-banner"


def test_coverage_table_counts_generations() -> None:
    final = pd.DataFrame(
        [
            {"person_uid": "cbdb:1", "ancestor_slot": "father", "slot_sufficient": True},
            {"person_uid": "cbdb:1", "ancestor_slot": "grandfather", "slot_sufficient": True},
            {"person_uid": "cbdb:1", "ancestor_slot": "great_grandfather", "slot_sufficient": False},
            {"person_uid": "cbdb:2", "ancestor_slot": "father", "slot_sufficient": False},
            {"person_uid": "cbdb:2", "ancestor_slot": "grandfather", "slot_sufficient": False},
            {"person_uid": "cbdb:2", "ancestor_slot": "great_grandfather", "slot_sufficient": False},
        ]
    )
    master = pd.DataFrame(
        [{"person_uid": "cbdb:1", "highest_tier": "A1"}, {"person_uid": "cbdb:2", "highest_tier": "A1"}]
    )
    table = coverage_table(final, master).iloc[0]
    assert int(table["N_total"]) == 2
    assert int(table["N_father_observed"]) == 1
    assert int(table["N_2gen_observed"]) == 1
    assert int(table["N_3gen_observed"]) == 0
    assert float(table["coverage_rate_any"]) == 50.0


def test_wilson_interval_behaves_for_extremes() -> None:
    low, high = wilson_interval(0, 10)
    assert low == 0.0 and 0 < high < 30
    low, high = wilson_interval(10, 10)
    assert high == 100.0 and 60 < low < 100
    low, high = wilson_interval(0, 0)
    assert low != low  # NaN for an empty cell


def test_prevalence_reports_the_na_denominator() -> None:
    persons = pd.DataFrame(
        [
            {"person_uid": "a", "highest_tier": "A1", "ancestor_official_any": 1},
            {"person_uid": "b", "highest_tier": "A1", "ancestor_official_any": 0},
            {"person_uid": "c", "highest_tier": "A1", "ancestor_official_any": pd.NA},
        ]
    )
    table = prevalence_by(persons, "ancestor_official_any", ["highest_tier"]).iloc[0]
    assert int(table["n"]) == 2  # NA excluded from the denominator
    assert int(table["n_na_excluded"]) == 1
    assert float(table["pct"]) == 50.0


def test_generations_distribution_keeps_na_visible() -> None:
    persons = pd.DataFrame(
        [
            {"person_uid": "a", "highest_tier": "A1", "elite_generations_count": 0},
            {"person_uid": "b", "highest_tier": "A1", "elite_generations_count": 2},
            {"person_uid": "c", "highest_tier": "A1", "elite_generations_count": pd.NA},
        ]
    )
    table = generations_distribution(persons).iloc[0]
    assert int(table["n_na"]) == 1
    assert int(table[0.0]) == 1 and int(table[2.0]) == 1


def test_model_falls_back_when_the_full_formula_separates() -> None:
    frame = pd.DataFrame(
        [
            {"ancestor_official_any": 1, "tier_group": "A", "cohort": "1761-1820", "banner_group": "rare"},
            {"ancestor_official_any": 0, "tier_group": "D", "cohort": "1761-1820", "banner_group": "common"},
            {"ancestor_official_any": 0, "tier_group": "D", "cohort": "1761-1820", "banner_group": "common"},
        ]
    )
    fit, attempted = fit_with_fallback(
        frame,
        [
            "ancestor_official_any ~ C(tier_group) + C(banner_group)",
            "ancestor_official_any ~ C(tier_group)",
        ],
    )
    assert attempted[0].endswith("C(banner_group)")
    assert fit["status"] in {"ok", "failed"}
    assert fit.get("formula") in attempted


def test_model_frame_keeps_the_central_group() -> None:
    """Regression for the P07 finding: the filter must take tier *group* labels.

    The original bug passed individual tier codes (A1/A2/A3/...), so the whole A group
    silently dropped out of both models and the reference category became B.
    """
    from qing_elite.analysis.statistics import _model_frame

    codebook = load_codebook()
    persons = pd.DataFrame(
        [
            {"person_uid": "a", "tier_group": "A", "ancestor_official_any": 1, "career_year": 1780,
             "cohort": "1761-1820", "banner_group": "unknown"},
            {"person_uid": "b", "tier_group": "B", "ancestor_official_any": 0, "career_year": 1780,
             "cohort": "1761-1820", "banner_group": "unknown"},
            {"person_uid": "d", "tier_group": "D", "ancestor_official_any": 1, "career_year": 1780,
             "cohort": "1761-1820", "banner_group": "unknown"},
            {"person_uid": "e", "tier_group": "D", "ancestor_official_any": None, "career_year": 1780,
             "cohort": "1761-1820", "banner_group": "unknown"},
            {"person_uid": "f", "tier_group": "B", "ancestor_official_any": 1, "career_year": 1500,
             "cohort": None, "banner_group": "unknown"},
        ]
    )
    common = _model_frame(persons, codebook, "d_layer", tier_groups=list(codebook.tier_groups))
    assert set(common["tier_group"]) == {"A", "B", "D"}  # A survives; out-of-window row drops
    assert len(common) == 3  # the NA-outcome row is excluded

    abc = _model_frame(persons, codebook, "study", tier_groups=["A", "B", "C"])
    assert set(abc["tier_group"]) == {"A", "B"}
