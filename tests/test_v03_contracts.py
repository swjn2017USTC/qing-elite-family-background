"""V0.3 data-contract tests (U03R skeleton).

Two things are being pinned here, and both are things v0.1 got wrong:

* ``unknown != negative`` — an unrecorded kin relation, office or degree is *no row*, and
  an ``unknown`` assertion may not carry a value;
* every detail row cites the ledger assertion that attests it, so a derived indicator can
  be traced back to one verbatim span.

The fixtures below are the smallest frames that satisfy the whole contract; each negative
test mutates exactly one thing.
"""

from __future__ import annotations

import pandas as pd
import pandera.errors
import pytest

from qing_elite.v03.contracts import (
    SCHEMAS,
    V03_TABLES,
    validate_all,
    validate_evidence_spans,
    validate_review_routing,
    validate_risk_tiers,
    validate_table,
)

DOC_TEXT = "張某，江蘇人，官知縣。"
DOC_QUOTE = "官知縣"
DOC_SPAN = (DOC_TEXT.index(DOC_QUOTE), DOC_TEXT.index(DOC_QUOTE) + len(DOC_QUOTE))


def _assertion(assertion_id: str, subject_type: str, subject_id: str, **overrides):
    row = {
        "assertion_id": assertion_id,
        "subject_type": subject_type,
        "subject_id": subject_id,
        "field": "relation" if subject_type == "kin_edge" else "value",
        "assertion_state": "positive",
        "value_raw": "父",
        "value_normalized": None,
        "source_document_id": "doc1",
        "source_locator": "清史稿/卷1#張某",
        "quote": DOC_QUOTE,
        "quote_start": DOC_SPAN[0],
        "quote_end": DOC_SPAN[1],
        "extractor": "rule_parser",
        "extractor_version": "v1",
        "extraction_run_id": "run1",
        "review_status": "not_required",
        "confidence": "high",
        "conflict_group_id": None,
    }
    row.update(overrides)
    return row


@pytest.fixture()
def tables() -> dict[str, pd.DataFrame]:
    """A minimal but complete set of V0.3 tables that must validate."""
    return _coerce_integer_columns({
        "persons": pd.DataFrame(
            [
                {
                    "person_id": "p1",
                    "canonical_name": "張某",
                    "surname": "張",
                    "given_name": "某",
                    "name_variants": None,
                    "birth_year": 1730,
                    "death_year": 1790,
                    "native_province": "江蘇",
                    "native_county": "吳縣",
                    "banner_status": "unknown",
                    "cohort_id": "1761-1820",
                    "primary_source": "cgedq",
                    "source_person_ids": "cgedq:S1",
                    "link_status": "standalone",
                    "link_evidence": None,
                    "resolution_status": "resolved",
                    "primary_eligible": True,
                },
                {
                    "person_id": "p2",
                    "canonical_name": "張父",
                    "surname": "張",
                    "given_name": "父",
                    "name_variants": None,
                    "birth_year": None,
                    "death_year": None,
                    "native_province": "江蘇",
                    "native_county": None,
                    "banner_status": "unknown",
                    "cohort_id": None,
                    "primary_source": "cbdb",
                    "source_person_ids": "cbdb:2",
                    "link_status": "explicit_id",
                    "link_evidence": "cbdb KIN_DATA 75",
                    "resolution_status": "resolved",
                    "primary_eligible": True,
                },
                {
                    "person_id": "p3",
                    "canonical_name": "李某",
                    "surname": "李",
                    "given_name": "某",
                    "name_variants": None,
                    "birth_year": None,
                    "death_year": None,
                    "native_province": None,
                    "native_county": None,
                    "banner_status": None,
                    "cohort_id": None,
                    "primary_source": "literature",
                    "source_person_ids": None,
                    "link_status": "review_pending",
                    "link_evidence": None,
                    "resolution_status": "unresolved",
                    "primary_eligible": False,
                },
            ]
        ),
        "source_documents": pd.DataFrame(
            [
                {
                    "document_id": "doc1",
                    "source_id": "wikisource_dump",
                    "source_release": "2026-09-01",
                    "document_type": "wikisource_page",
                    "title": "清史稿/卷1",
                    "volume": "1",
                    "locator": "張某",
                    "text": DOC_TEXT,
                    "text_sha256": "0" * 64,
                    "chars": len(DOC_TEXT),
                    "rights_status": "public_domain",
                    "license": "PD-old",
                    "access_type": "PUBLIC_SCAN",
                    "machine_readable": True,
                    "ocr_used": False,
                    "url": None,
                    "retrieved_at": pd.Timestamp("2026-09-27"),
                },
                {
                    "document_id": "doc2",
                    "source_id": "cbdb",
                    "source_release": "cbdb_20260912",
                    "document_type": "cbdb_structured_record",
                    "title": "CBDB KIN_DATA",
                    "volume": None,
                    "locator": "personid=2",
                    "text": None,
                    "text_sha256": None,
                    "chars": None,
                    "rights_status": "unknown",
                    "license": None,
                    "access_type": "PUBLIC_STRUCTURED",
                    "machine_readable": True,
                    "ocr_used": False,
                    "url": None,
                    "retrieved_at": None,
                },
            ]
        ),
        "offices": pd.DataFrame(
            [
                {
                    "office_id": "o1",
                    "office_title": "知縣",
                    "office_title_variants": "知縣;縣令",
                    "rank_label": "正七品",
                    "rank_class": 7,
                    "rank_side": "正",
                    "administrative_level": "county",
                    "central_local": "local",
                    "authority_type": "civil",
                    "institutional_body": None,
                    "substantive_default": True,
                    "valid_from_year": 1644,
                    "valid_to_year": 1911,
                    "mapping_status": "matched",
                    "unmapped_reason": None,
                    "ontology_version": "offices-v03-skeleton",
                }
            ]
        ),
        "evidence_assertions": pd.DataFrame(
            [
                _assertion("a1", "kin_edge", "e1"),
                _assertion(
                    "a2",
                    "career_event",
                    "ev1",
                    source_document_id="doc2",
                    source_locator="cbdb POSTED_TO_OFFICE_DATA|textid=9",
                    quote="知縣",
                    quote_start=None,
                    quote_end=None,
                    extractor="cbdb_data",
                ),
                _assertion(
                    "a3",
                    "credential",
                    "c1",
                    source_document_id="doc2",
                    source_locator="cbdb ENTRY_DATA|textid=7",
                    quote="舉人",
                    quote_start=None,
                    quote_end=None,
                    extractor="cbdb_data",
                    value_raw="舉人",
                ),
            ]
        ),
        "kin_edges": pd.DataFrame(
            [
                {
                    "edge_id": "e1",
                    "ego_person_id": "p1",
                    "alter_person_id": "p2",
                    "alter_name_raw": "張父",
                    "relation_code": "father",
                    "relation_class": "direct_line",
                    "lineage_side": "paternal",
                    "generation_delta": 1,
                    "edge_origin": "source_explicit",
                    "assertion_state": "positive",
                    "evidence_assertion_id": "a1",
                    "source_document_id": "doc1",
                    "confidence": "high",
                    "review_status": "not_required",
                }
            ]
        ),
        "career_events": pd.DataFrame(
            [
                {
                    "event_id": "ev1",
                    "person_id": "p1",
                    "office_id": "o1",
                    "office_raw": "知縣",
                    "office_normalized": "知縣",
                    "start_year": 1760,
                    "end_year": 1765,
                    "date_precision": "exact_year",
                    "reign": "乾隆",
                    "province": "江蘇",
                    "jurisdiction": "吳縣",
                    "rank_label": "正七品",
                    "administrative_level": "county",
                    "central_local": "local",
                    "appointment_type": "substantive",
                    "selection_method": "掣籤",
                    "assertion_state": "positive",
                    "evidence_assertion_id": "a2",
                    "source_document_id": "doc2",
                    "confidence": "high",
                    "review_status": "not_required",
                }
            ]
        ),
        "credentials": pd.DataFrame(
            [
                {
                    "credential_id": "c1",
                    "person_id": "p1",
                    "credential_type": "juren",
                    "exam_route": "鄉試",
                    "credential_year": 1756,
                    "rank_in_exam": None,
                    "assertion_state": "positive",
                    "evidence_assertion_id": "a3",
                    "source_document_id": "doc2",
                    "confidence": "high",
                    "review_status": "not_required",
                }
            ]
        ),
        "entity_links": pd.DataFrame(
            [
                {
                    "link_id": "l1",
                    "left_kind": "cgedq_person",
                    "left_id": "S1",
                    "right_kind": "cbdb_person",
                    "right_id": "2",
                    "link_type": "cross_source",
                    "method": "deterministic",
                    "score": 1.0,
                    "decision": "auto_accept",
                    "decision_rule": "name+province+degree",
                    "evidence": "江蘇|舉人",
                    "risk_id": "r1",
                    "review_status": "not_required",
                    "upstream_commit": None,
                    "decided_at": pd.Timestamp("2026-09-27"),
                }
            ]
        ),
        "risk_scores": pd.DataFrame(
            [
                {
                    "risk_id": "r1",
                    "subject_type": "entity_link",
                    "subject_id": "l1",
                    **{name: 0.0 for name in (
                        "linkage_uncertainty",
                        "ocr_uncertainty",
                        "parser_disagreement",
                        "source_conflict",
                        "rare_relation",
                        "office_unknown",
                        "chronology_violation",
                        "evidence_span_failure",
                    )},
                    "total_score": 0.0,
                    "risk_tier": "LOW",
                    "policy_version": "review-policy-v1",
                    "computed_at": pd.Timestamp("2026-09-27"),
                },
                {
                    "risk_id": "r2",
                    "subject_type": "kin_edge",
                    "subject_id": "e1",
                    **{
                        name: 0.15
                        for name in (
                            "linkage_uncertainty",
                            "ocr_uncertainty",
                            "parser_disagreement",
                            "source_conflict",
                            "rare_relation",
                            "office_unknown",
                            "chronology_violation",
                            "evidence_span_failure",
                        )
                    },
                    "total_score": 1.0,
                    "risk_tier": "HIGH",
                    "policy_version": "review-policy-v1",
                    "computed_at": pd.Timestamp("2026-09-27"),
                },
            ]
        ),
        "review_queue": pd.DataFrame(
            [
                {
                    "item_id": "q1",
                    "subject_type": "kin_edge",
                    "subject_id": "e1",
                    "risk_id": "r2",
                    "priority": 1,
                    "reason_codes": "source_conflict;chronology_violation",
                    "route": "human_review",
                    "status": "open",
                    "decision": None,
                    "rationale": None,
                    "reviewer": None,
                    "created_at": pd.Timestamp("2026-09-27"),
                    "resolved_at": None,
                },
                {
                    "item_id": "q2",
                    "subject_type": "entity_link",
                    "subject_id": "l1",
                    "risk_id": "r1",
                    "priority": 3,
                    "reason_codes": "deterministic_rule",
                    "route": "auto_accept",
                    "status": "resolved",
                    "decision": "accepted",
                    "rationale": "all deterministic invariants hold",
                    "reviewer": "auto",
                    "created_at": pd.Timestamp("2026-09-27"),
                    "resolved_at": pd.Timestamp("2026-09-27"),
                },
            ]
        ),
    })


def _mutate(
    tables: dict[str, pd.DataFrame], table: str, index: int = 0, **changes
) -> dict[str, pd.DataFrame]:
    updated = {name: frame.copy() for name, frame in tables.items()}
    for column, value in changes.items():
        updated[table].loc[index, column] = value
    return updated


def _coerce_integer_columns(tables: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """pandas widens nullable integers to float64 once a NaN appears; restore Int64.

    Nullable booleans get the same treatment: an all-``True`` column would otherwise come
    out as plain ``bool`` instead of pandas' nullable ``boolean``.
    """
    for name, frame in tables.items():
        for column, spec in SCHEMAS[name].columns.items():
            if column not in frame.columns:
                continue
            dtype = str(spec.dtype)
            if dtype in ("Int64", "boolean"):
                frame[column] = frame[column].astype(dtype)
    return tables


# --------------------------------------------------------------- positive control


def test_every_declared_table_has_a_schema() -> None:
    assert tuple(SCHEMAS) == V03_TABLES


def test_wellformed_tables_validate(tables) -> None:
    validate_all(tables)


def test_input_tables_carry_no_tier_column(tables) -> None:
    # A/B/C/D is an outcome of the v0.2 lineage, never an input attribute of a person
    for name in ("persons", "career_events"):
        assert not [column for column in tables[name].columns if "tier" in column]


# --------------------------------------------------------------- unknown != negative


def test_unknown_assertion_may_not_carry_a_value(tables) -> None:
    broken = _mutate(
        tables,
        "evidence_assertions",
        assertion_state="unknown",
        value_raw="知縣",
        quote=None,
        quote_start=None,
        quote_end=None,
    )
    with pytest.raises(pandera.errors.SchemaErrors):
        validate_table("evidence_assertions", broken["evidence_assertions"])


def test_unknown_assertion_without_value_is_accepted(tables) -> None:
    clean = _mutate(
        tables,
        "evidence_assertions",
        assertion_state="unknown",
        value_raw=None,
        quote=None,
        quote_start=None,
        quote_end=None,
    )
    validate_table("evidence_assertions", clean["evidence_assertions"])


def test_affirmative_assertion_needs_a_source_locator_and_quote(tables) -> None:
    broken = _mutate(tables, "evidence_assertions", source_document_id=None)
    with pytest.raises(pandera.errors.SchemaErrors):
        validate_table("evidence_assertions", broken["evidence_assertions"])


def test_conflict_assertion_needs_a_conflict_group(tables) -> None:
    broken = _mutate(
        tables,
        "evidence_assertions",
        assertion_state="conflict",
        value_raw=None,
        quote=None,
        quote_start=None,
        quote_end=None,
        conflict_group_id=None,
    )
    with pytest.raises(pandera.errors.SchemaErrors):
        validate_table("evidence_assertions", broken["evidence_assertions"])


def test_evidence_span_must_reproduce_the_quote(tables) -> None:
    broken = _mutate(tables, "evidence_assertions", quote_start=0, quote_end=2)
    with pytest.raises(ValueError, match="span does not reproduce quote"):
        validate_evidence_spans(
            broken["evidence_assertions"], broken["source_documents"]
        )


def test_structured_document_may_not_carry_offsets(tables) -> None:
    broken = _mutate(
        tables,
        "evidence_assertions",
        source_document_id="doc2",
        quote_start=0,
        quote_end=2,
    )
    with pytest.raises(ValueError, match="span given for a document without text"):
        validate_evidence_spans(
            broken["evidence_assertions"], broken["source_documents"]
        )


# --------------------------------------------------------------- primary keys / domains


def test_duplicate_person_key_is_rejected(tables) -> None:
    broken = {name: frame.copy() for name, frame in tables.items()}
    broken["persons"] = pd.concat([broken["persons"], broken["persons"].iloc[[0]]])
    with pytest.raises(pandera.errors.SchemaErrors):
        validate_table("persons", broken["persons"])


def test_unmatched_office_must_state_a_reason(tables) -> None:
    broken = _mutate(
        tables, "offices", mapping_status="unmatched", unmapped_reason=None
    )
    with pytest.raises(pandera.errors.SchemaErrors):
        validate_table("offices", broken["offices"])


# --------------------------------------------------------------- kin edges


def test_relation_class_must_follow_the_ontology(tables) -> None:
    broken = _mutate(tables, "kin_edges", relation_class="senior_collateral")
    with pytest.raises(pandera.errors.SchemaErrors):
        validate_table("kin_edges", broken["kin_edges"])


def test_generation_delta_must_follow_the_ontology(tables) -> None:
    broken = _mutate(tables, "kin_edges", generation_delta=-1)
    with pytest.raises(pandera.errors.SchemaErrors):
        validate_table("kin_edges", broken["kin_edges"])


def test_unknown_relation_code_is_rejected(tables) -> None:
    broken = _mutate(tables, "kin_edges", relation_code="sister_husband")
    with pytest.raises(pandera.errors.SchemaErrors):
        validate_table("kin_edges", broken["kin_edges"])


def test_unresolved_kin_keeps_the_raw_name(tables) -> None:
    broken = _mutate(tables, "kin_edges", alter_person_id=None, alter_name_raw=None)
    with pytest.raises(pandera.errors.SchemaErrors):
        validate_table("kin_edges", broken["kin_edges"])


def test_same_kin_edge_cannot_be_recorded_twice(tables) -> None:
    broken = {name: frame.copy() for name, frame in tables.items()}
    broken["kin_edges"] = pd.concat([broken["kin_edges"], broken["kin_edges"].iloc[[0]]])
    with pytest.raises(pandera.errors.SchemaErrors):
        validate_table("kin_edges", broken["kin_edges"])


def test_extended_kin_relation_is_representable(tables) -> None:
    """The schema must express senior collateral kin, which v0.2 could not."""
    extended = _mutate(
        tables,
        "kin_edges",
        edge_id="e2",
        alter_person_id=None,
        alter_name_raw="張伯",
        relation_code="uncle_paternal",
        relation_class="senior_collateral",
        generation_delta=1,
    )
    validate_table("kin_edges", extended["kin_edges"])
    row = extended["kin_edges"].loc[0]
    assert (row["relation_class"], row["generation_delta"]) == ("senior_collateral", 1)
    assert row["alter_person_id"] is None or pd.isna(row["alter_person_id"])


# --------------------------------------------------------------- ledger lineage


def test_detail_row_must_cite_an_existing_assertion(tables) -> None:
    broken = _mutate(tables, "kin_edges", evidence_assertion_id="nope")
    with pytest.raises(ValueError, match="cites unknown assertion"):
        validate_all(broken)


def test_detail_row_must_cite_its_own_assertion(tables) -> None:
    broken = _mutate(tables, "kin_edges", evidence_assertion_id="a2")
    with pytest.raises(ValueError, match="is about career_event/ev1"):
        validate_all(broken)


def test_detail_row_document_must_match_the_assertion(tables) -> None:
    broken = _mutate(tables, "kin_edges", source_document_id="doc2")
    with pytest.raises(ValueError, match="disagrees with assertion"):
        validate_all(broken)


def test_orphan_person_reference_is_rejected(tables) -> None:
    broken = _mutate(tables, "career_events", person_id="ghost")
    with pytest.raises(ValueError, match="references unknown persons"):
        validate_all(broken)


def test_positive_assertion_in_text_document_needs_a_span(tables) -> None:
    """A positive assertion without any span for a text document must fail."""
    broken = _mutate(tables, "evidence_assertions", quote_start=None, quote_end=None)
    with pytest.raises(ValueError, match="no span for text document"):
        validate_evidence_spans(
            broken["evidence_assertions"], broken["source_documents"]
        )


# --------------------------------------------------------------- links and review


def test_auto_accept_needs_a_score_and_no_pending_review(tables) -> None:
    broken = _mutate(tables, "entity_links", score=None)
    with pytest.raises(pandera.errors.SchemaErrors):
        validate_table("entity_links", broken["entity_links"])

    pending = _mutate(tables, "entity_links", review_status="pending")
    with pytest.raises(pandera.errors.SchemaErrors):
        validate_table("entity_links", pending["entity_links"])


def test_grey_link_must_wait_for_review(tables) -> None:
    broken = _mutate(
        tables, "entity_links", decision="grey", review_status="not_required"
    )
    with pytest.raises(pandera.errors.SchemaErrors):
        validate_table("entity_links", broken["entity_links"])


def test_resolved_review_item_records_decision_and_reviewer(tables) -> None:
    broken = _mutate(tables, "review_queue", index=1, reviewer=None)
    with pytest.raises(pandera.errors.SchemaErrors):
        validate_table("review_queue", broken["review_queue"])


def test_one_open_review_item_per_subject(tables) -> None:
    broken = {name: frame.copy() for name, frame in tables.items()}
    duplicate = broken["review_queue"].iloc[[0]].copy()
    duplicate["item_id"] = "q3"
    broken["review_queue"] = pd.concat([broken["review_queue"], duplicate])
    with pytest.raises(pandera.errors.SchemaErrors):
        validate_table("review_queue", broken["review_queue"])


def test_human_review_is_reserved_for_high_risk(tables) -> None:
    broken = _mutate(tables, "review_queue", risk_id="r1")
    with pytest.raises(ValueError, match="reserved for HIGH risk"):
        validate_review_routing(broken["review_queue"], broken["risk_scores"])


def test_risk_tier_must_follow_the_policy_thresholds(tables) -> None:
    broken = _mutate(tables, "risk_scores", index=1, risk_tier="LOW")
    with pytest.raises(ValueError, match="risk tier does not follow the policy"):
        validate_risk_tiers(broken["risk_scores"])


def test_risk_score_outside_the_unit_interval_is_rejected(tables) -> None:
    broken = _mutate(tables, "risk_scores", total_score=1.5)
    with pytest.raises(pandera.errors.SchemaErrors):
        validate_table("risk_scores", broken["risk_scores"])
