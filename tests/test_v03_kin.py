"""U07R tests: canonical tables, graph view, validators, indicators and coverage.

The indicator tests carry the two rules that v0.1 broke: a count always comes with its
denominator, and ``*_new_entrant`` is ``unknown`` (not ``False``) when the kin are not
observable.
"""

from __future__ import annotations

import pandas as pd
import pytest

from qing_elite.v03.kin import graph as graph_mod
from qing_elite.v03.kin import indicators as indicators_mod
from qing_elite.v03.kin.coverage import coverage_by_cohort, coverage_by_source


def _edges(rows: list[dict]) -> pd.DataFrame:
    base = {
        "edge_id": None,
        "ego_person_id": "cbdb:1",
        "alter_person_id": "cbdb:2",
        "alter_name_raw": "甲",
        "relation_code": "father",
        "relation_class": "direct_line",
        "lineage_side": "paternal",
        "generation_delta": 1,
        "edge_origin": "source_explicit",
        "assertion_state": "positive",
        "evidence_assertion_id": "a1",
        "source_document_id": "d1",
        "confidence": "high",
        "review_status": "not_required",
        "alter_index_year": None,
        "ego_index_year": None,
    }
    frame = pd.DataFrame([{**base, **row} for row in rows])
    frame["edge_id"] = [row["edge_id"] or f"e{index}" for index, row in enumerate(frame.to_dict("records"))]
    return frame


# --------------------------------------------------------------------- graph + validators


def test_graph_is_rebuilt_from_edges_not_persisted() -> None:
    graph = graph_mod.build_graph(_edges([{}, {"edge_id": "e2", "alter_person_id": "cbdb:3"}]))
    assert graph.number_of_edges() == 2
    assert set(graph.nodes) == {"cbdb:1", "cbdb:2", "cbdb:3"}


def test_unresolved_alter_adds_no_edge() -> None:
    graph = graph_mod.build_graph(_edges([{"alter_person_id": None}]))
    assert graph.number_of_edges() == 0


def test_duplicated_kin_is_flagged() -> None:
    flags = graph_mod.validate_edges(_edges([{}, {}]))
    assert (flags["check"] == "duplicated_kin").any()


def test_generation_paradox_is_flagged() -> None:
    flags = graph_mod.validate_edges(_edges([{"generation_delta": -1}]))
    assert (flags["check"] == "generation_paradox").any()


def test_conflicting_single_valued_relation_is_flagged() -> None:
    flags = graph_mod.validate_edges(
        _edges([{"edge_id": "e1", "alter_person_id": "cbdb:2"}, {"edge_id": "e2", "alter_person_id": "cbdb:9"}])
    )
    assert (flags["check"] == "conflicting_relationship").any()


def test_multi_valued_relation_is_not_a_conflict() -> None:
    flags = graph_mod.validate_edges(
        _edges(
            [
                {"edge_id": "e1", "relation_code": "son", "relation_class": "junior_collateral", "generation_delta": -1, "alter_person_id": "cbdb:2"},
                {"edge_id": "e2", "relation_code": "son", "relation_class": "junior_collateral", "generation_delta": -1, "alter_person_id": "cbdb:9"},
            ]
        )
    )
    assert not (flags["check"] == "conflicting_relationship").any()


def test_relation_cycle_is_flagged() -> None:
    flags = graph_mod.validate_edges(
        _edges(
            [
                {"edge_id": "e1", "ego_person_id": "cbdb:1", "alter_person_id": "cbdb:2"},
                {"edge_id": "e2", "ego_person_id": "cbdb:2", "alter_person_id": "cbdb:1"},
            ]
        )
    )
    assert (flags["check"] == "relation_cycle").any()


def test_chronology_contradiction_needs_both_years() -> None:
    impossible = graph_mod.chronology_check(
        _edges([{"alter_index_year": 1800, "ego_index_year": 1700, "generation_delta": 1}])
    )
    assert len(impossible) == 1
    silent = graph_mod.chronology_check(_edges([{"alter_index_year": None, "ego_index_year": 1700}]))
    assert silent.empty


# --------------------------------------------------------------------- offices


def test_office_levels_are_keyword_mapped_with_reasons() -> None:
    career = pd.DataFrame({"office_raw": ["知縣", "巡撫", "某不可考官"]})
    offices = graph_mod.build_offices(career, ontology_version="relations-v2")
    lookup = offices.set_index("office_title")
    assert lookup.loc["知縣", "administrative_level"] == "county"
    assert lookup.loc["巡撫", "administrative_level"] == "provincial"
    assert lookup.loc["某不可考官", "mapping_status"] == "unmatched"
    assert lookup.loc["某不可考官", "unmapped_reason"]


# --------------------------------------------------------------------- indicators


def _credentials(rows: list[tuple[str, str]]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "credential_id": [f"c{i}" for i in range(len(rows))],
            "person_id": [row[0] for row in rows],
            "credential_type": [row[1] for row in rows],
            "exam_route": None,
            "credential_year": pd.array([None] * len(rows), dtype="Int64"),
            "rank_in_exam": None,
            "assertion_state": "positive",
            "evidence_assertion_id": [f"a{i}" for i in range(len(rows))],
            "source_document_id": ["d1"] * len(rows),
            "confidence": "high",
            "review_status": "not_required",
        }
    )


def _career(rows: list[tuple[str, str]], offices: pd.DataFrame) -> pd.DataFrame:
    frame = pd.DataFrame(
        {
            "event_id": [f"ev{i}" for i in range(len(rows))],
            "person_id": [row[0] for row in rows],
            "office_id": None,
            "office_raw": [row[1] for row in rows],
            "office_normalized": None,
            "start_year": pd.array([None] * len(rows), dtype="Int64"),
            "end_year": pd.array([None] * len(rows), dtype="Int64"),
            "date_precision": "unknown",
            "reign": None,
            "province": None,
            "jurisdiction": None,
            "rank_label": None,
            "administrative_level": "unknown",
            "central_local": "unknown",
            "appointment_type": "unknown",
            "selection_method": None,
            "assertion_state": "positive",
            "evidence_assertion_id": [f"a{i}" for i in range(len(rows))],
            "source_document_id": ["d1"] * len(rows),
            "confidence": "high",
            "review_status": "not_required",
        }
    )
    return graph_mod.attach_office_levels(frame, offices)


def test_indicators_count_degrees_and_offices_with_denominators() -> None:
    edges = _edges(
        [
            {"edge_id": "e1", "relation_code": "father", "alter_person_id": "cbdb:10", "generation_delta": 1},
            {"edge_id": "e2", "relation_code": "grandfather", "alter_person_id": "cbdb:11", "generation_delta": 2},
            {"edge_id": "e3", "relation_code": "great_grandfather", "alter_person_id": "cbdb:12", "generation_delta": 3},
            {"edge_id": "e4", "relation_code": "uncle_paternal", "relation_class": "senior_collateral", "alter_person_id": "cbdb:13", "generation_delta": 1},
        ]
    )
    credentials = _credentials([("cbdb:10", "jinshi"), ("cbdb:11", "juren"), ("cbdb:13", "jinshi")])
    offices = graph_mod.build_offices(pd.DataFrame({"office_raw": ["知縣"]}), ontology_version="relations-v2")
    career = _career([("cbdb:12", "知縣"), ("cbdb:13", "知縣")], offices)
    frame = indicators_mod.compute_indicators(["cbdb:1"], edges, credentials, career)
    row = frame.iloc[0]
    assert row["direct_3g_degree_count"] == 2
    assert row["direct_3g_office_count"] == 1
    assert row["direct_elite_generations"] == 3
    assert row["senior_collateral_degree_count"] == 1
    assert row["senior_collateral_office_count"] == 1
    assert row["all_senior_elite_kin_count"] == 1
    assert row["denominator_direct_kin"] == 3
    assert row["observable_direct_kin"] == 3
    assert row["lineage_edge_ids"] != ""


def test_new_entrant_is_unknown_when_kin_are_not_observable() -> None:
    edges = _edges([{"edge_id": "e1", "relation_code": "father", "alter_person_id": "cbdb:10"}])
    frame = indicators_mod.compute_indicators(
        ["cbdb:1"], edges, _credentials([]), _career([], graph_mod.build_offices(pd.DataFrame({"office_raw": []}), ontology_version="relations-v2"))
    )
    row = frame.iloc[0]
    assert row["direct_line_new_entrant"] is None or pd.isna(row["direct_line_new_entrant"])
    assert row["extended_family_new_entrant"] is None or pd.isna(row["extended_family_new_entrant"])


def test_new_entrant_is_true_only_with_complete_direct_line() -> None:
    edges = _edges(
        [
            {"edge_id": "e1", "relation_code": "father", "alter_person_id": "cbdb:10", "generation_delta": 1},
            {"edge_id": "e2", "relation_code": "grandfather", "alter_person_id": "cbdb:11", "generation_delta": 2},
            {"edge_id": "e3", "relation_code": "great_grandfather", "alter_person_id": "cbdb:12", "generation_delta": 3},
        ]
    )
    offices = graph_mod.build_offices(pd.DataFrame({"office_raw": []}), ontology_version="relations-v2")
    frame = indicators_mod.compute_indicators(["cbdb:1"], edges, _credentials([]), _career([], offices))
    assert frame.iloc[0]["direct_line_new_entrant"] == True  # noqa: E712


# --------------------------------------------------------------------- coverage


def test_coverage_by_source_reports_share_and_counts() -> None:
    persons = pd.DataFrame({"person_id": ["cbdb:1", "cbdb:2"], "primary_eligible": [True, True]})
    edges = _edges([{}]).assign(ego_person_id="cbdb:1", edge_source="cbdb")
    credentials = _credentials([])
    career = _career([], graph_mod.build_offices(pd.DataFrame({"office_raw": []}), ontology_version="v"))
    assertions = pd.DataFrame({"assertion_id": ["a1"], "source_document_id": ["cbdb|KIN_DATA|1|2|75"]})
    frame = coverage_by_source(persons, edges, credentials, career, assertions)
    assert frame.iloc[0]["source"] == "cbdb"
    assert frame.iloc[0]["coverage_share"] == 0.5
    assert frame.iloc[0]["assertions"] == 1


def test_coverage_by_cohort_keeps_observability_visible() -> None:
    persons = pd.DataFrame(
        {
            "person_id": ["cbdb:1", "cbdb:2"],
            "primary_eligible": [True, True],
            "cohort_id": ["1796-1860", "1796-1860"],
        }
    )
    indicators = pd.DataFrame(
        {
            "person_id": ["cbdb:1", "cbdb:2"],
            "n_all_edges": [3, 0],
            "observable_direct_kin": [3, 0],
            "observable_senior_kin": [1, 0],
            "direct_line_complete": [True, False],
            "direct_line_new_entrant": [False, None],
        }
    )
    frame = coverage_by_cohort(persons, indicators)
    assert frame.iloc[0]["with_any_kin"] == 1
    assert frame.iloc[0]["with_any_kin_share"] == 0.5
    assert frame.iloc[0]["new_entrant_defined"] == 1
