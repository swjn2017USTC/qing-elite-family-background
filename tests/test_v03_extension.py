"""U10R tests: legacy-variable mapping, recomputed capital, comparison classification."""

from __future__ import annotations

import pandas as pd
import pytest

from qing_elite.v03.extension import compare, legacy


def _legacy_frames() -> legacy.LegacyFrame:
    persons = pd.DataFrame(
        {
            "person_uid": ["cbdb:1", "cbdb:2", "cbdb:3", "cgedq:S9"],
            "highest_tier": ["B", "C", "D", "B"],
            "ancestor_high_official": [1, 0, 0, 1],
            "strict_commoner_3g": [0, 1, 1, 0],
        }
    )
    slots = pd.DataFrame(
        {
            "person_uid": ["cbdb:1", "cbdb:1", "cbdb:1", "cbdb:2", "cbdb:2", "cbdb:2"],
            "ancestor_slot": ["father", "grandfather", "great_grandfather"] * 2,
            "structured_known": [True, True, True, False, False, False],
            "final_name": ["甲", "乙", "丙", None, None, None],
            "final_degree": ["進士", None, None, None, None, None],
            "final_office": ["知縣", None, None, None, None, None],
        }
    )
    master = pd.DataFrame(
        {
            "person_uid": ["cbdb:1", "cbdb:2", "cbdb:3", "cgedq:S9"],
            "native_province": ["江蘇", "浙江", None, "江蘇"],
            "banner_effective": ["unknown", "漢軍旗人", None, "unknown"],
            "career_first_year": [1760, 1770, 1780, 1765],
            "career_last_year": [1790, 1780, 1785, 1775],
        }
    )
    return legacy.LegacyFrame(persons=persons, slots=slots, master=master, recovered=True)


def test_mapping_lists_rejected_variables_with_reasons() -> None:
    table = legacy.mapping_table()
    rejected = table.loc[table["status"] == "not_comparable"]
    assert "strict_commoner_3g" in set(rejected["legacy_variable"])
    assert rejected["note"].str.len().gt(10).all()


def test_d_layer_is_excluded_from_the_extension_frame() -> None:
    frame = legacy.early_qing_frame(_legacy_frames())
    assert "D" not in set(frame["highest_tier"])
    assert len(frame) == 3  # the three A/B/C persons survive; D is dropped


def test_capital_is_recomputed_only_where_slots_are_documented() -> None:
    frame = legacy.early_qing_frame(_legacy_frames())
    row = frame.loc[frame["person_uid"] == "cbdb:1"].iloc[0]
    assert bool(row["family_observable"]) is True
    assert row["direct_3g_degree_count"] == 1
    assert row["direct_3g_office_count"] == 1
    assert row["documented_direct_office_any"] in (True, 1)


def test_observable_persons_without_records_are_not_capitalised_falsely() -> None:
    frames = _legacy_frames()
    frames.slots.loc[:, "structured_known"] = False
    frames.slots.loc[:, "final_name"] = None
    frames.slots.loc[:, "final_degree"] = None
    frames.slots.loc[:, "final_office"] = None
    frame = legacy.early_qing_frame(frames)
    row = frame.loc[frame["person_uid"] == "cbdb:1"].iloc[0]
    assert bool(row["family_observable"]) is False
    assert pd.isna(row["direct_elite_generations"])


def _cohorts() -> tuple[pd.DataFrame, pd.DataFrame]:
    early = pd.DataFrame(
        {
            "family_observable": [True, True, False],
            "direct_3g_degree_count": [1, 0, 0],
            "direct_3g_office_count": [1, 0, 0],
            "native_province": ["江蘇", "浙江", None],
            "native_province_effective": ["江蘇", "浙江", None],
            "banner_group": ["unknown", "漢軍旗人", None],
            "ancestor_high_official": [1, 0, 0],
            "highest_tier": ["B", "C", "B"],
        }
    )
    late = pd.DataFrame(
        {
            "family_observable": [True, False],
            "direct_3g_degree_count": [2, 0],
            "direct_3g_office_count": [1, 0],
            "native_province": ["江蘇", None],
            "banner_status": ["unknown", None],
            "all_senior_elite_kin_count": [1, 0],
            "central_local_route": ["mixed", "unknown"],
        }
    )
    return early, late


def test_documentation_share_is_the_only_cross_period_consistent_row() -> None:
    early, late = _cohorts()
    result = compare.evaluate(early, late)
    table = result["tables"]["documentation_share"]
    assert set(table["classification"]) == {"CROSS_PERIOD_CONSISTENT"}
    assert table["share"].tolist() == [round(2 / 3, 4), 0.5]


def test_capital_levels_stay_period_specific() -> None:
    early, late = _cohorts()
    table = compare.evaluate(early, late)["tables"]["direct_capital"]
    assert set(table["classification"]) == {"PERIOD_SPECIFIC"}
    assert table["denominator_observable"].tolist() == [2, 1]


def test_banner_and_persistence_are_not_comparable() -> None:
    early, late = _cohorts()
    tables = compare.evaluate(early, late)["tables"]
    assert set(tables["banner_status"]["classification"]) == {"NOT_COMPARABLE"}
    assert set(tables["elite_persistence"]["classification"]) == {"NOT_COMPARABLE"}


def test_no_pooled_regression_is_run() -> None:
    early, late = _cohorts()
    result = compare.evaluate(early, late)
    assert result["pooled_regression"]["run"] is False
    assert result["pooled_regression"]["reason"]


def test_career_outcomes_are_reported_side_by_side() -> None:
    early, late = _cohorts()
    table = compare.evaluate(early, late)["tables"]["career_outcomes"]
    assert set(table["cohort"]) == {"early_mid_qing_elite_ABC", "late_qing_standardized"}
    assert "NOT_COMPARABLE" in set(table["classification"])
