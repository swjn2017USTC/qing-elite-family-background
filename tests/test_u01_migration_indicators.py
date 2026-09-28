"""U01 migration + indicator semantics: absence-of-record must never become 0.

These tests exercise the real migration and indicator code on small synthetic v0.1
shapes, so they run without the local enrichment cache and pin the two acceptance
criteria of U01: a person known only by an ancestor's name keeps office/degree
``unknown``, and the primary frame contains no zero that was manufactured from a
missing field.
"""

from __future__ import annotations

import pandas as pd
import pytest

from qing_elite.v02.contracts import validate_evidence_spans
from qing_elite.v02.indicators import (
    build_legacy_indicators,
    build_primary_indicators,
    select_primary_assertions,
)
from qing_elite.v02.migrate import build_evidence_assertions

SLOTS = ("father", "grandfather", "great_grandfather")


def _structured(*rows: dict) -> pd.DataFrame:
    columns = [
        "person_uid",
        "ancestor_slot",
        "source_title",
        "source_textid",
        "source_pages",
        "kin_relation_chn",
        "ancestor_degree_chn",
        "ancestor_office_sample",
    ]
    return pd.DataFrame.from_records(list(rows), columns=columns)


def _family_final(*rows: dict) -> pd.DataFrame:
    columns = [
        "entity_id",
        "ancestor_slot",
        "provenance",
        "slot_sufficient",
        "slot_has_degree",
        "slot_has_office",
        "final_name",
        "final_degree",
        "final_office",
    ]
    return pd.DataFrame.from_records(list(rows), columns=columns)


def _empty_enriched() -> pd.DataFrame:
    columns = [
        "person_uid",
        "ancestor_slot",
        "llm_evidence",
        "llm_confidence",
        "call_id",
        "prompt_version",
        "source_ids",
        "final_source",
    ]
    return pd.DataFrame(columns=columns)


def _slot_frame(assertions: pd.DataFrame, entity: str, slot: str) -> pd.DataFrame:
    return assertions[(assertions["entity_id"] == entity) & (assertions["ancestor_slot"] == slot)]


def _state(assertions: pd.DataFrame, entity: str, slot: str, attribute: str) -> str:
    rows = _slot_frame(assertions, entity, slot)
    return str(rows.loc[rows["attribute"] == attribute, "assertion_state"].iloc[0])


def test_name_only_slot_migrates_to_unknown_office_and_degree() -> None:
    """Acceptance: only an ancestor's name known -> office and degree stay unknown."""
    final = _family_final(
        {
            "entity_id": "cbdb:1",
            "ancestor_slot": "father",
            "provenance": "cbdb_structured",
            "slot_sufficient": True,
            "slot_has_degree": False,
            "slot_has_office": False,
            "final_name": "甲",
            "final_degree": None,
            "final_office": None,
        }
    )
    structured = _structured(
        {
            "person_uid": "cbdb:1",
            "ancestor_slot": "father",
            "source_title": "人名權威資料",
            "source_textid": "T1",
            "source_pages": None,
            "kin_relation_chn": "父",
            "ancestor_degree_chn": None,
            "ancestor_office_sample": None,
        }
    )
    assertions = build_evidence_assertions(
        final, structured, _empty_enriched(), {}, set()
    )
    assert _state(assertions, "cbdb:1", "father", "name") == "positive"
    for attribute in ("office", "degree"):
        row = _slot_frame(assertions, "cbdb:1", "father").query("attribute == @attribute").iloc[0]
        assert row["assertion_state"] == "unknown", attribute
        assert row["value_raw"] is None or pd.isna(row["value_raw"])
        assert row["source_id"] is None or pd.isna(row["source_id"])
    # positive name keeps a source, a locator and a verbatim quote
    name = _slot_frame(assertions, "cbdb:1", "father").query("attribute == 'name'").iloc[0]
    assert name["quote"] == "甲"
    assert "人名權威資料" in name["source_locator"]


def test_migration_emits_no_explicit_negative() -> None:
    """v0.1 has no explicit 未仕/寒素 evidence, so the migration must not invent any."""
    final = _family_final(
        *[
            {
                "entity_id": "cbdb:1",
                "ancestor_slot": slot,
                "provenance": "cbdb_structured",
                "slot_sufficient": True,
                "slot_has_degree": False,
                "slot_has_office": False,
                "final_name": "甲",
                "final_degree": None,
                "final_office": None,
            }
            for slot in SLOTS
        ]
    )
    structured = _structured(
        *(
            {
                "person_uid": "cbdb:1",
                "ancestor_slot": slot,
                "source_title": "人名權威資料",
                "source_textid": "T1",
                "source_pages": None,
                "kin_relation_chn": "父",
                "ancestor_degree_chn": None,
                "ancestor_office_sample": None,
            }
            for slot in SLOTS
        )
    )
    assertions = build_evidence_assertions(final, structured, _empty_enriched(), {}, set())
    assert not assertions["assertion_state"].isin(["explicit_negative", "conflict"]).any()


def test_llm_slot_carries_a_verbatim_span() -> None:
    passage = "……巡撫乙，有子二人……"
    final = _family_final(
        {
            "entity_id": "cbdb:2",
            "ancestor_slot": "father",
            "provenance": "llm_extraction",
            "slot_sufficient": True,
            "slot_has_degree": False,
            "slot_has_office": True,
            "final_name": "乙",
            "final_degree": None,
            "final_office": "巡撫",
        }
    )
    structured = _structured(
        {
            "person_uid": "cbdb:2",
            "ancestor_slot": "father",
            "source_title": None,
            "source_textid": None,
            "source_pages": None,
            "kin_relation_chn": "父",
            "ancestor_degree_chn": None,
            "ancestor_office_sample": None,
        }
    )
    enriched = pd.DataFrame.from_records(
        [
            {
                "person_uid": "cbdb:2",
                "ancestor_slot": "father",
                "llm_evidence": "巡撫乙",
                "llm_confidence": "high",
                "call_id": "call-1",
                "prompt_version": "p04-family-v3",
                "source_ids": "清史稿/卷1#乙",
                "final_source": "llm_extraction",
            }
        ]
    )
    assertions = build_evidence_assertions(
        final, structured, enriched, {"cbdb:2": passage}, set()
    )
    office = _slot_frame(assertions, "cbdb:2", "father").query("attribute == 'office'").iloc[0]
    assert office["quote"] == "巡撫乙"
    assert passage[office["quote_start"] : office["quote_end"]] == "巡撫乙"
    documents = pd.DataFrame(
        [
            {
                "document_id": "qsg:清史稿/卷1#乙",
                "document_type": "qingshigao_biography",
                "title": "乙",
                "volume": "卷1",
                "locator": "清史稿/卷1#乙",
                "release_id": "test",
                "retrieved_at": None,
                "rights_status": "public_domain",
                "license": None,
                "text": passage,
                "text_sha256": None,
                "chars": None,
            }
        ]
    )
    validate_evidence_spans(assertions, documents)


# ------------------------------------------------------------------ indicators


def _entities(*ids: str) -> pd.DataFrame:
    return pd.DataFrame(
        {"entity_id": list(ids), "resolution_status": ["resolved"] * len(ids), "primary_eligible": [True] * len(ids)}
    )


def _ledger(entity: str, states: dict[str, dict[str, str]], review: str = "not_required") -> list[dict]:
    rows = []
    for slot, attributes in states.items():
        for attribute, state in attributes.items():
            rows.append(
                {
                    "entity_id": entity,
                    "ancestor_slot": slot,
                    "attribute": attribute,
                    "assertion_state": state,
                    "review_status": review,
                }
            )
    return rows


def test_absent_office_is_na_not_zero_in_primary() -> None:
    rows = _ledger("e1", {"father": {"name": "positive", "office": "unknown", "degree": "unknown"}})
    primary = build_primary_indicators(pd.DataFrame(rows), _entities("e1"))
    row = primary.iloc[0]
    assert row["n_known_slots"] == 1
    assert pd.isna(row["documented_ancestor_official_any"])
    assert pd.isna(row["documented_ancestor_degree_any"])
    assert pd.isna(row["documented_family_capital_any"])
    assert pd.isna(row["documented_commoner_explicit"])
    assert row["ancestor_identity_coverage_1g"] == 1
    assert row["attribute_ascertainment_office"] == 0.0


def test_explicit_negative_is_what_licenses_a_zero() -> None:
    states = {
        slot: {"name": "positive", "office": "explicit_negative", "degree": "explicit_negative"}
        for slot in SLOTS
    }
    primary = build_primary_indicators(pd.DataFrame(_ledger("e1", states)), _entities("e1"))
    row = primary.iloc[0]
    assert row["documented_ancestor_official_any"] == 0
    assert row["documented_ancestor_degree_any"] == 0
    assert row["documented_family_capital_any"] == 0
    assert row["documented_commoner_explicit"] == 1


def test_partial_explicit_negative_stays_na() -> None:
    rows = _ledger("e1", {"father": {"name": "positive", "office": "explicit_negative", "degree": "unknown"}})
    primary = build_primary_indicators(pd.DataFrame(rows), _entities("e1"))
    row = primary.iloc[0]
    assert row["documented_ancestor_official_any"] == 0
    assert pd.isna(row["documented_family_capital_any"])


def test_pending_assertions_are_excluded_before_the_primary_frame() -> None:
    rows = _ledger("e1", {"father": {"name": "positive"}}) + _ledger(
        "e2", {"father": {"name": "positive", "office": "positive"}}, review="pending"
    )
    ledger = pd.DataFrame(rows)
    entities = _entities("e1", "e2")
    primary, exclusions = select_primary_assertions(ledger, entities)
    assert exclusions["pending_or_rejected_review"] == 2
    indicators = build_primary_indicators(primary, entities)
    assert indicators.set_index("person_uid").loc["e2", "n_known_slots"] == 0


def test_legacy_metrics_are_prefixed_and_never_primary() -> None:
    v01 = pd.DataFrame(
        {"person_uid": ["cbdb:1"], "ancestor_official_any": [0], "strict_commoner_3g": [1]}
    )
    legacy = build_legacy_indicators(v01)
    assert list(legacy.columns) == ["person_uid", "legacy_ancestor_official_any", "legacy_strict_commoner_3g"]
    assert legacy.loc[0, "legacy_ancestor_official_any"] == 0

    # the very same absence-of-record case is NA in the primary frame
    rows = _ledger("cbdb:1", {"father": {"name": "positive", "office": "unknown", "degree": "unknown"}})
    primary = build_primary_indicators(pd.DataFrame(rows), _entities("cbdb:1"))
    assert pd.isna(primary.loc[0, "documented_ancestor_official_any"])
    assert not any(column.startswith("legacy_") for column in primary.columns)
