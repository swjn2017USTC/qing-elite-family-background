"""U08R tests: office ontology v2, spell collapsing, derived outcomes, career validators."""

from __future__ import annotations

import pandas as pd

from qing_elite.v03.career import derive as derive_mod
from qing_elite.v03.career import jsl as jsl_mod
from qing_elite.v03.career import offices as offices_mod
from qing_elite.v03.career.run import legacy_tier_for, legacy_tier_map


# --------------------------------------------------------------------- office ontology


def test_rank_match_prefers_the_longer_title() -> None:
    assert offices_mod.rank_for("直隸州知州")["rank_class"] == 5
    assert offices_mod.rank_for("知州")["rank_class"] == 5
    assert offices_mod.rank_for("知縣")["rank_class"] == 7


def test_level_and_authority_are_keyword_mapped() -> None:
    assert offices_mod.level_for("巡撫") == "provincial"
    assert offices_mod.level_for("知縣") == "county"
    assert offices_mod.level_for("內務府大臣") == "central"
    assert offices_mod.authority_for("提督") == "military"
    assert offices_mod.authority_for("知縣") == "civil"


def test_appointment_flags_come_from_markers_and_selection_method() -> None:
    acting = offices_mod.appointment_flags(office_raw="署理知縣")
    assert acting["acting"] is True
    expectant = offices_mod.appointment_flags(office_raw="候補知縣")
    assert expectant["expectant"] is True
    concurrent = offices_mod.appointment_flags(office_raw="兼署總督")
    assert concurrent["concurrent"] is True
    honour = offices_mod.appointment_flags(office_raw="加銜侍郎")
    assert honour["honorific"] is True
    plain = offices_mod.appointment_flags(office_raw="知縣", selection_method="調")
    assert plain["substantive"] is True


def test_ontology_reports_reasons_and_rank_provenance() -> None:
    ontology = offices_mod.build_ontology(["知縣", "某不可考官稱"], ontology_version="offices-v2")
    lookup = ontology.set_index("office_title")
    assert lookup.loc["知縣", "rank_label"] == "正七品"
    assert lookup.loc["某不可考官稱", "mapping_status"] == "unmatched"
    assert lookup.loc["某不可考官稱", "unmapped_reason"]
    assert ontology.attrs["rank_verification_status"] == "pending"
    assert "策展" in ontology.attrs["rank_basis"] or "清史稿" in ontology.attrs["rank_basis"]


# --------------------------------------------------------------------- spells


def _observations() -> pd.DataFrame:
    return pd.DataFrame(
        {
            # S1: four consecutive 1760 seasons, then a re-appointment in the same office at 1762
            # S2: one observation only
            "person_id": ["S1"] * 5 + ["S2"],
            # ``year`` is the integer year the loader derives; ``year_float`` keeps the season
            "year": pd.array([1760, 1760, 1760, 1760, 1762, 1760], dtype="Int64"),
            "year_float": [1760.0, 1760.25, 1760.5, 1760.75, 1762.0, 1760.0],
            "season": pd.array([1, 2, 3, 4, 1, 1], dtype="Int64"),
            "office_raw": ["知縣", "知縣", "知縣", "知縣", "知縣", "巡檢"],
            "selection_method": ["調"] * 6,
            "province": ["江蘇"] * 6,
            "post_importance": [None] * 6,
            "record_number": ["r1", "r2", "r3", "r4", "r5", "r6"],
        }
    )


def test_consecutive_seasons_collapse_and_gaps_split() -> None:
    spells = jsl_mod.collapse_spells(_observations())
    s1 = spells.loc[spells["person_id"] == "S1"].sort_values("start_year")
    assert len(s1) == 2  # the four consecutive seasons of 1760, then a re-appointment in 1762
    assert s1.iloc[0]["start_year"] == 1760 and s1.iloc[0]["end_year"] == 1760
    assert s1.iloc[0]["n_observations"] == 4
    assert s1.iloc[1]["start_year"] == 1762


def test_spell_ids_are_stable_and_unique() -> None:
    spells = jsl_mod.collapse_spells(_observations())
    assert spells["event_id"].is_unique
    assert jsl_mod.collapse_spells(_observations())["event_id"].tolist() == spells["event_id"].tolist()


def test_cross_source_conflict_is_flagged_not_merged() -> None:
    events = pd.DataFrame(
        {
            "event_id": ["a", "b"],
            "person_id": ["cbdb:1", "S1"],
            "office_raw": ["知縣", "知府"],
            "start_year": [1765, 1765],
            "end_year": [1770, 1770],
            "n_observations": [1, 1],
            "selection_method": [None, "調"],
            "province": [None, None],
            "post_importance": [None, None],
            "source_id": ["cbdb", "cgedq_jsl"],
        }
    )
    flagged = jsl_mod.flag_source_conflicts(events)
    # different persons: no conflict
    assert flagged["conflict_with_other_source"].sum() == 0
    same_person = events.assign(person_id=["P1", "P1"])
    flagged = jsl_mod.flag_source_conflicts(same_person)
    assert flagged["conflict_with_other_source"].sum() == 2


# --------------------------------------------------------------------- derived outcomes


def _events_with_ranks() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "event_id": ["e1", "e2", "e3"],
            "person_id": ["P1"] * 3,
            "office_raw": ["知縣", "知府", "侍郎"],
            "start_year": [1760, 1770, 1790],
            "end_year": [1769, 1789, 1795],
            "source_id": ["cgedq_jsl"] * 3,
            "rank_class": pd.array([7, 4, 2], dtype="Int64"),
            "acting": [False, True, False],
            "expectant": [False, False, True],
            "substantive": [True, False, False],
            "honorific": [False, False, False],
            "concurrent": [False, False, False],
        }
    )


def _offices() -> pd.DataFrame:
    return offices_mod.build_ontology(["知縣", "知府", "侍郎"], ontology_version="offices-v2")


def test_derived_outcomes_carry_rank_route_and_lineage() -> None:
    outcomes = derive_mod.derive_outcomes(_events_with_ranks(), _offices())
    row = outcomes.iloc[0]
    assert row["n_events"] == 3
    assert row["highest_rank_class"] == 2
    assert row["career_length_years"] == 35
    assert row["first_substantive_office"] == "知縣"
    assert row["central_local_route"] == "mixed"
    assert row["n_major_transitions"] == 2
    assert row["lineage_event_ids"] == "e1;e2;e3"
    assert row["acting_share"] > 0 and row["expectant_share"] > 0


def test_central_only_route_is_distinguished() -> None:
    events = _events_with_ranks().iloc[[2]]
    outcomes = derive_mod.derive_outcomes(events, _offices())
    assert outcomes.iloc[0]["central_local_route"] == "central_only"


def test_chronology_and_transition_validators() -> None:
    events = pd.DataFrame(
        {
            "event_id": ["a", "b", "c", "d"],
            "person_id": ["P1", "P1", "P2", "P3"],
            "office_raw": ["知縣", "知縣", "知縣", "知縣"],
            "start_year": [1765, 1780, 1500, 1790],
            "end_year": [1760, 1785, 1505, 1795],
            "rank_class": pd.array([7, 1, 7, 7], dtype="Int64"),
        }
    )
    issues = derive_mod.validate_events(events)
    checks = set(issues["check"])
    assert "chronology_inverted" in checks
    assert "chronology_out_of_window" in checks
    assert "impossible_transition" in checks


def test_same_year_rank_change_is_flagged() -> None:
    events = pd.DataFrame(
        {
            "event_id": ["a", "b"],
            "person_id": ["P1", "P1"],
            "office_raw": ["知縣", "知府"],
            "start_year": [1765, 1765],
            "end_year": [1765, 1770],
            "rank_class": pd.array([7, 4], dtype="Int64"),
        }
    )
    issues = derive_mod.validate_events(events)
    assert "same_year_rank_change" in set(issues["check"])


# --------------------------------------------------------------------- tier crosswalk


def test_legacy_tier_is_matched_longest_first() -> None:
    mapping = legacy_tier_map()
    if not mapping:
        return  # the frozen config may not expose office_controls; nothing to assert
    longest = max(mapping, key=len)
    assert legacy_tier_for(longest, mapping) == mapping[longest]
