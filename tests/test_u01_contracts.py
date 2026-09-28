"""U01 negative tests: the v0.2 evidence contracts must reject bad data.

Every test here injects exactly one violation of the upgrade plan's hard constraints
(§5) and asserts that validation raises. A validator that passes any of these is
worthless, so these are the tests that keep ``unknown != 0`` mechanical.
"""

from __future__ import annotations

import pandas as pd
import pytest
from pandera.errors import SchemaError, SchemaErrors

from qing_elite.v02.contracts import (
    EVIDENCE_COLUMNS,
    validate_evidence_spans,
    validate_referential_integrity,
    validate_table,
)
from qing_elite.v02.indicators import build_primary_indicators

SCHEMA_ERRORS = (SchemaError, SchemaErrors)


def _assertion(**overrides) -> dict:
    row = {
        "entity_id": "cbdb:1",
        "ancestor_slot": "father",
        "relation_type": "父",
        "attribute": "office",
        "assertion_state": "positive",
        "value_raw": "巡撫",
        "value_normalized": "巡撫",
        "source_id": "qsg:清史稿/卷1#甲",
        "source_locator": "清史稿/卷1#甲",
        "quote": "巡撫",
        "quote_start": 0,
        "quote_end": 2,
        "extractor": "llm_extraction",
        "extractor_version": "p04-family-v3",
        "extraction_run_id": "call-1",
        "review_status": "not_required",
        "confidence": "high",
        "conflict_group_id": None,
    }
    row.update(overrides)
    return row


def _assertions(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame.from_records(rows, columns=list(EVIDENCE_COLUMNS))


def _documents(text: str | None = "巡撫") -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "document_id": "qsg:清史稿/卷1#甲",
                "document_type": "qingshigao_biography",
                "title": "甲",
                "volume": "卷1",
                "locator": "清史稿/卷1#甲",
                "release_id": "test",
                "retrieved_at": None,
                "rights_status": "public_domain",
                "license": None,
                "text": text,
                "text_sha256": None,
                "chars": None,
            }
        ]
    )


def test_wellformed_ledger_validates() -> None:
    """Control: the same row shape without a violation must pass."""
    validate_table("evidence_assertions", _assertions([_assertion()]))


def test_positive_without_source_is_rejected() -> None:
    with pytest.raises(SCHEMA_ERRORS):
        validate_table(
            "evidence_assertions",
            _assertions([_assertion(source_id=None, source_locator=None, quote=None)]),
        )


def test_positive_with_blank_quote_is_rejected() -> None:
    with pytest.raises(SCHEMA_ERRORS):
        validate_table("evidence_assertions", _assertions([_assertion(quote="   ")]))


def test_unknown_carrying_a_value_is_rejected() -> None:
    with pytest.raises(SCHEMA_ERRORS):
        validate_table(
            "evidence_assertions",
            _assertions(
                [
                    _assertion(
                        assertion_state="unknown",
                        value_raw="巡撫",
                        value_normalized="巡撫",
                        source_id=None,
                        source_locator=None,
                        quote=None,
                        quote_start=None,
                        quote_end=None,
                    )
                ]
            ),
        )


def test_conflict_carrying_a_value_is_rejected() -> None:
    with pytest.raises(SCHEMA_ERRORS):
        validate_table(
            "evidence_assertions",
            _assertions(
                [
                    _assertion(
                        assertion_state="conflict",
                        value_raw="巡撫",
                        value_normalized="巡撫",
                        source_id=None,
                        source_locator=None,
                        quote=None,
                        quote_start=None,
                        quote_end=None,
                    )
                ]
            ),
        )


def test_unknown_assertion_state_enum_is_rejected() -> None:
    with pytest.raises(SCHEMA_ERRORS):
        validate_table("evidence_assertions", _assertions([_assertion(assertion_state="unclear")]))


def test_duplicate_evidence_key_is_rejected() -> None:
    with pytest.raises(SCHEMA_ERRORS):
        validate_table("evidence_assertions", _assertions([_assertion(), _assertion()]))


def test_wrong_span_is_rejected() -> None:
    """quote_start:quote_end must reproduce the quote verbatim."""
    assertions = _assertions([_assertion(quote_start=1, quote_end=3)])
    with pytest.raises(ValueError, match="span does not reproduce"):
        validate_evidence_spans(assertions, _documents("巡撫"))


def test_text_document_positive_without_span_is_rejected() -> None:
    assertions = _assertions([_assertion(quote_start=None, quote_end=None)])
    with pytest.raises(ValueError, match="no span for text document"):
        validate_evidence_spans(assertions, _documents("巡撫"))


def test_span_on_a_document_without_text_is_rejected() -> None:
    assertions = _assertions([_assertion()])
    with pytest.raises(ValueError, match="span given for a document without text"):
        validate_evidence_spans(assertions, _documents(None))


def test_unpaired_span_offsets_are_rejected() -> None:
    with pytest.raises(SCHEMA_ERRORS):
        validate_table("evidence_assertions", _assertions([_assertion(quote_end=None)]))


def test_assertion_referring_to_unknown_entity_is_rejected() -> None:
    entities = pd.DataFrame(
        {
            "entity_id": ["cbdb:2"],
            "cbdb_personid": pd.array([2], dtype="Int64"),
            "cgedq_person_id": pd.array([pd.NA], dtype="object"),
            "name_chn": ["乙"],
            "source": ["CBDB"],
            "highest_tier": ["A1"],
            "tiers_present": ["A1"],
            "career_first_year": pd.array([1700], dtype="Int64"),
            "career_last_year": pd.array([1750], dtype="Int64"),
            "native_province_effective": pd.array([pd.NA], dtype="object"),
            "banner_effective": ["unknown"],
            "degree_effective": pd.array([pd.NA], dtype="object"),
            "v01_linkage_confidence": pd.array([pd.NA], dtype="object"),
            "resolution_status": ["resolved"],
            "primary_eligible": [True],
        }
    )
    with pytest.raises(ValueError, match="unknown entities"):
        validate_referential_integrity(
            entities=entities,
            source_documents=_documents("巡撫"),
            assertions=_assertions([_assertion()]),
        )


def test_assertion_referring_to_unknown_document_is_rejected() -> None:
    entities = pd.DataFrame({"entity_id": ["cbdb:1"]})
    with pytest.raises(ValueError, match="unknown documents"):
        validate_referential_integrity(
            entities=entities,
            source_documents=pd.DataFrame({"document_id": ["other"]}),
            assertions=_assertions([_assertion()]),
        )


# --------------------------------------------------------------------- primary frame


def _entities(*ids: str, resolved: bool = True) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "entity_id": list(ids),
            "resolution_status": ["resolved" if resolved else "unresolved"] * len(ids),
            "primary_eligible": [resolved] * len(ids),
        }
    )


def _ledger_row(entity: str, state: str, attribute: str = "name", review: str = "not_required") -> dict:
    return {
        "entity_id": entity,
        "ancestor_slot": "father",
        "attribute": attribute,
        "assertion_state": state,
        "review_status": review,
    }


def test_pending_review_cannot_enter_the_primary_frame() -> None:
    ledger = pd.DataFrame(
        [_ledger_row("e1", "positive"), _ledger_row("e1", "positive", "office", review="pending")]
    )
    with pytest.raises(ValueError, match="may not enter the primary frame"):
        build_primary_indicators(ledger, _entities("e1"))


def test_conflict_cannot_enter_the_primary_frame() -> None:
    ledger = pd.DataFrame([_ledger_row("e1", "positive"), _ledger_row("e1", "conflict", "office")])
    with pytest.raises(ValueError, match="may not enter the primary frame"):
        build_primary_indicators(ledger, _entities("e1"))


def test_unresolved_entity_cannot_enter_the_primary_frame() -> None:
    ledger = pd.DataFrame([_ledger_row("e1", "positive")])
    with pytest.raises(ValueError, match="may not enter the primary frame"):
        build_primary_indicators(ledger, _entities("e1", resolved=False))


def test_legacy_columns_are_rejected_in_the_primary_frame() -> None:
    from qing_elite.v02.indicators import assert_no_legacy_columns

    frame = pd.DataFrame({"person_uid": ["e1"], "legacy_ancestor_official_any": [0]})
    with pytest.raises(ValueError, match="legacy columns"):
        assert_no_legacy_columns(frame)
    with pytest.raises(SCHEMA_ERRORS):
        validate_table("person_indicators_v02", frame)


def test_unknown_attribute_is_rejected() -> None:
    ledger = pd.DataFrame([_ledger_row("e1", "positive", "salary")])
    with pytest.raises(ValueError, match="unknown attributes"):
        build_primary_indicators(ledger, _entities("e1"))
