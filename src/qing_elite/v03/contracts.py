"""V0.3 data contracts (U03R skeleton).

Ten tables describe the cohort-and-kin-network study (see ``V03_DATA_MODEL.md``):

``persons`` / ``source_documents`` / ``career_events`` / ``kin_edges`` / ``credentials`` /
``offices`` / ``evidence_assertions`` / ``entity_links`` / ``review_queue`` / ``risk_scores``.

Two rules inherited from v0.2 are hard-coded here, because they are the ones v0.1 broke:

* ``unknown != negative``: the four-state ledger lives in ``evidence_assertions``. Detail
  tables (``career_events``, ``kin_edges``, ``credentials``) only carry ``positive`` or
  ``conflict`` rows — a relation or an office that is merely unrecorded is *no row at
  all*, never a ``0``/``False`` value. ``unknown`` and ``conflict`` assertions carry no
  value, and an affirmative assertion needs a source, a locator and a verbatim quote.
* every detail row must cite one ledger assertion (``validate_detail_rows_cite_ledger``),
  so a derived indicator can always be traced back to a source span.

The module is a schema/test skeleton only: it validates frames, it does not build them.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pandas as pd
import pandera.pandas as pa

from qing_elite.v03.design import (
    DesignContractError,
    load_relation_ontology,
    load_review_policy,
    relation_index,
)

V03_TABLES = (
    "persons",
    "source_documents",
    "career_events",
    "kin_edges",
    "credentials",
    "offices",
    "evidence_assertions",
    "entity_links",
    "review_queue",
    "risk_scores",
)

ASSERTION_STATES = ("positive", "explicit_negative", "unknown", "conflict")
AFFIRMATIVE_STATES = ("positive", "explicit_negative")
#: Detail tables record attested facts and unresolved disagreements, nothing else.
DETAIL_STATES = ("positive", "conflict")

REVIEW_STATUSES = ("not_required", "pending", "reviewed_accepted", "reviewed_rejected")
PRIMARY_REVIEW_STATUSES = ("not_required", "reviewed_accepted")
LINK_STATUSES = (
    "explicit_id",
    "accepted_cross_link",
    "standalone",
    "review_pending",
    "rejected",
    "merged_into_cbdb",
)
RESOLUTION_STATUSES = ("resolved", "unresolved")
PRIMARY_SOURCES = ("cbdb", "cgedq", "standardized_family_source", "literature")

DOCUMENT_TYPES = (
    "cbdb_structured_record",
    "cgedq_roster_row",
    "wikisource_page",
    "standardized_family_source",
    "literature_document",
    "ocr_scan",
)
ACCESS_TYPES = (
    "PUBLIC_STRUCTURED",
    "PUBLIC_SCAN",
    "PUBLIC_UI_ONLY",
    "ACCESS_REQUEST_REQUIRED",
    "PAPER_TABLE_ONLY",
    "UNAVAILABLE",
)
RIGHTS_STATUSES = ("public_domain", "restricted", "unknown")

ADMIN_LEVELS = ("central", "provincial", "prefectural", "county", "other", "unknown")
CENTRAL_LOCAL = ("central", "local", "unknown")
APPOINTMENT_TYPES = (
    "substantive",
    "acting",
    "expectant",
    "honorary",
    "concurrent",
    "unknown",
)
DATE_PRECISIONS = ("exact_year", "reign_era", "decade", "unknown")
MAPPING_STATUSES = ("matched", "unmatched", "ambiguous")

CREDENTIAL_TYPES = (
    "jinshi",
    "juren",
    "gongsheng",
    "jiansheng",
    "shengyuan",
    "yinsheng",
    "juanna",
    "wuju",
    "other_exam",
    "other_privilege",
)

EXTRACTORS = (
    "cbdb_data",
    "cgedq_data",
    "rule_parser",
    "ocr_parser",
    "llm_extraction",
    "manual_entry",
    "migration",
)

SUBJECT_TYPES = ("person", "kin_edge", "career_event", "credential", "entity_link")
RISK_SUBJECT_TYPES = SUBJECT_TYPES
LINK_KINDS = ("cbdb_person", "cgedq_person", "standardized_person", "biography", "sinica_person")
LINK_TYPES = ("dedupe", "cross_source", "biography_attribution", "kin_person_match")
LINK_METHODS = ("deterministic", "ml_chinese_record_linkage", "splink", "agreement", "manual")
LINK_DECISIONS = ("auto_accept", "grey", "reject")
EDGE_ORIGINS = ("source_explicit", "machine_inferred")
LINEAGE_SIDES = ("paternal", "maternal", "affinal", "unknown")

ROUTES = ("auto_accept", "machine_adjudicate", "human_review")
QUEUE_STATUSES = ("open", "in_progress", "resolved")
RISK_TIERS = ("LOW", "MEDIUM", "HIGH")
RISK_COMPONENTS = (
    "linkage_uncertainty",
    "ocr_uncertainty",
    "parser_disagreement",
    "source_conflict",
    "rare_relation",
    "office_unknown",
    "chronology_violation",
    "evidence_span_failure",
)

#: Detail tables and the ledger key they must cite.
DETAIL_TABLES: Mapping[str, tuple[str, str]] = {
    "career_events": ("event_id", "career_event"),
    "kin_edges": ("edge_id", "kin_edge"),
    "credentials": ("credential_id", "credential"),
}

_ONTOLOGY = load_relation_ontology()
_RELATION_INDEX = relation_index(_ONTOLOGY)
RELATION_CODES = tuple(_RELATION_INDEX)
RELATION_CLASS_BY_CODE = {code: entry["class"] for code, entry in _RELATION_INDEX.items()}
GENERATION_DELTA_BY_CODE = {code: entry["generation_delta"] for code, entry in _RELATION_INDEX.items()}
RELATION_CLASSES = tuple(_ONTOLOGY["classes"])


def _one_of(values: tuple[str, ...]) -> pa.Check:
    return pa.Check(
        lambda series, values=values: series.isna() | series.isin(values),
        name=f"one_of_{len(values)}",
    )


def _unit_interval() -> pa.Check:
    return pa.Check(
        lambda series: series.isna() | ((series >= 0.0) & (series <= 1.0)),
        name="unit_interval_or_na",
    )


def _affirmative(frame: pd.DataFrame) -> pd.Series:
    return frame["assertion_state"].isin(DETAIL_STATES)


PERSONS_SCHEMA = pa.DataFrameSchema(
    name="persons",
    columns={
        "person_id": pa.Column(str, nullable=False, unique=True),
        "canonical_name": pa.Column(str, nullable=False),
        "surname": pa.Column(str, nullable=True),
        "given_name": pa.Column(str, nullable=True),
        "name_variants": pa.Column(str, nullable=True),
        "birth_year": pa.Column("Int64", nullable=True),
        "death_year": pa.Column("Int64", nullable=True),
        "native_province": pa.Column(str, nullable=True),
        "native_county": pa.Column(str, nullable=True),
        "banner_status": pa.Column(str, nullable=True),
        "cohort_id": pa.Column(str, nullable=True),
        "primary_source": pa.Column(str, checks=[_one_of(PRIMARY_SOURCES)]),
        "source_person_ids": pa.Column(str, nullable=True),
        "link_status": pa.Column(str, checks=[_one_of(LINK_STATUSES)]),
        "link_evidence": pa.Column(str, nullable=True),
        "resolution_status": pa.Column(str, checks=[_one_of(RESOLUTION_STATUSES)]),
        "primary_eligible": pa.Column(bool, nullable=False),
    },
    checks=[
        pa.Check(
            lambda frame: frame["primary_eligible"]
            == (frame["resolution_status"] == "resolved"),
            name="primary_eligibility_follows_resolution",
        ),
        pa.Check(
            lambda frame: frame["death_year"].isna()
            | frame["birth_year"].isna()
            | (frame["death_year"] >= frame["birth_year"]),
            name="life_years_ordered",
        ),
    ],
    strict=True,
    coerce=False,
)

SOURCE_DOCUMENTS_SCHEMA = pa.DataFrameSchema(
    name="source_documents",
    columns={
        "document_id": pa.Column(str, nullable=False, unique=True),
        "source_id": pa.Column(str, nullable=False),
        "source_release": pa.Column(str, nullable=True),
        "document_type": pa.Column(str, checks=[_one_of(DOCUMENT_TYPES)]),
        "title": pa.Column(str, nullable=False),
        "volume": pa.Column(str, nullable=True),
        "locator": pa.Column(str, nullable=True),
        "text": pa.Column(str, nullable=True),
        "text_sha256": pa.Column(str, nullable=True),
        "chars": pa.Column("Int64", nullable=True),
        "rights_status": pa.Column(str, checks=[_one_of(RIGHTS_STATUSES)]),
        "license": pa.Column(str, nullable=True),
        "access_type": pa.Column(str, checks=[_one_of(ACCESS_TYPES)]),
        "machine_readable": pa.Column(bool, nullable=False),
        "ocr_used": pa.Column(bool, nullable=False),
        "url": pa.Column(str, nullable=True),
        "retrieved_at": pa.Column("datetime64[ns]", nullable=True),
    },
    checks=[
        pa.Check(
            lambda frame: frame["text"].isna()
            | (frame["text_sha256"].notna() & (frame["chars"] == frame["text"].str.len())),
            name="text_implies_hash_and_length",
        ),
    ],
    strict=True,
    coerce=False,
)

CAREER_EVENTS_SCHEMA = pa.DataFrameSchema(
    name="career_events",
    columns={
        "event_id": pa.Column(str, nullable=False, unique=True),
        "person_id": pa.Column(str, nullable=False),
        "office_id": pa.Column(str, nullable=True),
        "office_raw": pa.Column(str, nullable=True),
        "office_normalized": pa.Column(str, nullable=True),
        "start_year": pa.Column("Int64", nullable=True),
        "end_year": pa.Column("Int64", nullable=True),
        "date_precision": pa.Column(str, checks=[_one_of(DATE_PRECISIONS)]),
        "reign": pa.Column(str, nullable=True),
        "province": pa.Column(str, nullable=True),
        "jurisdiction": pa.Column(str, nullable=True),
        "rank_label": pa.Column(str, nullable=True),
        "administrative_level": pa.Column(str, checks=[_one_of(ADMIN_LEVELS)]),
        "central_local": pa.Column(str, checks=[_one_of(CENTRAL_LOCAL)]),
        "appointment_type": pa.Column(str, checks=[_one_of(APPOINTMENT_TYPES)]),
        "selection_method": pa.Column(str, nullable=True),
        "assertion_state": pa.Column(str, checks=[_one_of(DETAIL_STATES)]),
        "evidence_assertion_id": pa.Column(str, nullable=False),
        "source_document_id": pa.Column(str, nullable=False),
        "confidence": pa.Column(str, nullable=True),
        "review_status": pa.Column(str, checks=[_one_of(REVIEW_STATUSES)]),
    },
    checks=[
        pa.Check(
            lambda frame: frame.loc[_affirmative(frame), "office_raw"].notna(),
            name="positive_event_needs_an_office_string",
        ),
        pa.Check(
            lambda frame: frame.loc[frame["assertion_state"] == "conflict"]
            .loc[:, ["office_normalized", "rank_label", "administrative_level"]]
            .isna()
            .all(axis=1),
            name="conflicting_event_carries_no_resolved_value",
        ),
        pa.Check(
            lambda frame: frame["end_year"].isna()
            | frame["start_year"].isna()
            | (frame["end_year"] >= frame["start_year"]),
            name="event_years_ordered",
        ),
    ],
    strict=True,
    coerce=False,
)

KIN_EDGES_SCHEMA = pa.DataFrameSchema(
    name="kin_edges",
    columns={
        "edge_id": pa.Column(str, nullable=False, unique=True),
        "ego_person_id": pa.Column(str, nullable=False),
        "alter_person_id": pa.Column(str, nullable=True),
        "alter_name_raw": pa.Column(str, nullable=True),
        "relation_code": pa.Column(str, checks=[_one_of(RELATION_CODES)]),
        "relation_class": pa.Column(str, checks=[_one_of(RELATION_CLASSES)]),
        "lineage_side": pa.Column(str, checks=[_one_of(LINEAGE_SIDES)]),
        "generation_delta": pa.Column("Int64", nullable=False),
        "edge_origin": pa.Column(str, checks=[_one_of(EDGE_ORIGINS)]),
        "assertion_state": pa.Column(str, checks=[_one_of(DETAIL_STATES)]),
        "evidence_assertion_id": pa.Column(str, nullable=False),
        "source_document_id": pa.Column(str, nullable=False),
        "confidence": pa.Column(str, nullable=True),
        "review_status": pa.Column(str, checks=[_one_of(REVIEW_STATUSES)]),
    },
    checks=[
        # relation_class / generation_delta are projections of the versioned ontology,
        # never free text typed by a parser or a model
        pa.Check(
            lambda frame: frame["relation_class"]
            == frame["relation_code"].map(RELATION_CLASS_BY_CODE),
            name="relation_class_follows_relation_code",
        ),
        pa.Check(
            lambda frame: frame["generation_delta"]
            == frame["relation_code"].map(GENERATION_DELTA_BY_CODE),
            name="generation_delta_follows_relation_code",
        ),
        pa.Check(
            lambda frame: frame["alter_person_id"].isna()
            | (frame["alter_person_id"] != frame["ego_person_id"]),
            name="no_self_kin_edge",
        ),
        pa.Check(
            lambda frame: frame["alter_person_id"].notna() | frame["alter_name_raw"].notna(),
            name="unresolved_alter_keeps_a_raw_name",
        ),
        pa.Check(
            lambda frame: ~frame.loc[frame["alter_person_id"].notna()]
            .duplicated(subset=["ego_person_id", "alter_person_id", "relation_code"])
            .any(),
            name="resolved_kin_edge_unique",
        ),
    ],
    strict=True,
    coerce=False,
)

CREDENTIALS_SCHEMA = pa.DataFrameSchema(
    name="credentials",
    columns={
        "credential_id": pa.Column(str, nullable=False, unique=True),
        "person_id": pa.Column(str, nullable=False),
        "credential_type": pa.Column(str, nullable=True, checks=[_one_of(CREDENTIAL_TYPES)]),
        "exam_route": pa.Column(str, nullable=True),
        "credential_year": pa.Column("Int64", nullable=True),
        "rank_in_exam": pa.Column(str, nullable=True),
        "assertion_state": pa.Column(str, checks=[_one_of(DETAIL_STATES)]),
        "evidence_assertion_id": pa.Column(str, nullable=False),
        "source_document_id": pa.Column(str, nullable=False),
        "confidence": pa.Column(str, nullable=True),
        "review_status": pa.Column(str, checks=[_one_of(REVIEW_STATUSES)]),
    },
    checks=[
        pa.Check(
            lambda frame: frame.loc[_affirmative(frame), "credential_type"].notna(),
            name="positive_credential_needs_a_type",
        )
    ],
    strict=True,
    coerce=False,
)

OFFICES_SCHEMA = pa.DataFrameSchema(
    name="offices",
    columns={
        "office_id": pa.Column(str, nullable=False, unique=True),
        "office_title": pa.Column(str, nullable=False),
        "office_title_variants": pa.Column(str, nullable=True),
        "rank_label": pa.Column(str, nullable=True),
        "rank_class": pa.Column("Int64", nullable=True),
        "rank_side": pa.Column(str, nullable=True),
        "administrative_level": pa.Column(str, checks=[_one_of(ADMIN_LEVELS)]),
        "central_local": pa.Column(str, checks=[_one_of(CENTRAL_LOCAL)]),
        "authority_type": pa.Column(str, nullable=True),
        "institutional_body": pa.Column(str, nullable=True),
        "substantive_default": pa.Column("boolean", nullable=True),
        "valid_from_year": pa.Column("Int64", nullable=True),
        "valid_to_year": pa.Column("Int64", nullable=True),
        "mapping_status": pa.Column(str, checks=[_one_of(MAPPING_STATUSES)]),
        "unmapped_reason": pa.Column(str, nullable=True),
        "ontology_version": pa.Column(str, nullable=False),
    },
    checks=[
        pa.Check(
            lambda frame: (frame["mapping_status"] == "matched")
            | frame["unmapped_reason"].notna(),
            name="unmatched_office_states_a_reason",
        ),
        pa.Check(
            lambda frame: frame["rank_class"].isna()
            | ((frame["rank_class"] >= 1) & (frame["rank_class"] <= 9)),
            name="rank_class_bounded",
        ),
        pa.Check(
            lambda frame: frame["valid_to_year"].isna()
            | frame["valid_from_year"].isna()
            | (frame["valid_to_year"] >= frame["valid_from_year"]),
            name="office_validity_ordered",
        ),
    ],
    strict=True,
    coerce=False,
)

EVIDENCE_ASSERTIONS_SCHEMA = pa.DataFrameSchema(
    name="evidence_assertions",
    columns={
        "assertion_id": pa.Column(str, nullable=False, unique=True),
        "subject_type": pa.Column(str, checks=[_one_of(SUBJECT_TYPES)]),
        "subject_id": pa.Column(str, nullable=False),
        "field": pa.Column(str, nullable=False),
        "assertion_state": pa.Column(str, checks=[_one_of(ASSERTION_STATES)]),
        "value_raw": pa.Column(str, nullable=True),
        "value_normalized": pa.Column(str, nullable=True),
        "source_document_id": pa.Column(str, nullable=True),
        "source_locator": pa.Column(str, nullable=True),
        "quote": pa.Column(str, nullable=True),
        "quote_start": pa.Column("Int64", nullable=True),
        "quote_end": pa.Column("Int64", nullable=True),
        "extractor": pa.Column(str, checks=[_one_of(EXTRACTORS)]),
        "extractor_version": pa.Column(str, nullable=True),
        "extraction_run_id": pa.Column(str, nullable=True),
        "review_status": pa.Column(str, checks=[_one_of(REVIEW_STATUSES)]),
        "confidence": pa.Column(str, nullable=True),
        "conflict_group_id": pa.Column(str, nullable=True),
    },
    checks=[
        pa.Check(
            lambda frame: frame.loc[
                frame["assertion_state"].isin(AFFIRMATIVE_STATES),
                ["source_document_id", "source_locator", "quote"],
            ]
            .notna()
            .all(axis=1),
            name="affirmative_assertion_needs_source_locator_quote",
        ),
        pa.Check(
            lambda frame: frame.loc[
                frame["assertion_state"].isin(AFFIRMATIVE_STATES), "quote"
            ]
            .astype(str)
            .str.strip()
            .ne("")
            .all(),
            name="affirmative_quote_not_blank",
        ),
        pa.Check(
            lambda frame: frame.loc[
                frame["assertion_state"].isin(("unknown", "conflict")),
                ["value_raw", "value_normalized", "quote", "quote_start", "quote_end"],
            ]
            .isna()
            .all(axis=1),
            name="unknown_and_conflict_carry_no_value",
        ),
        pa.Check(
            lambda frame: frame["quote_start"].isna() == frame["quote_end"].isna(),
            name="quote_offsets_paired",
        ),
        pa.Check(
            lambda frame: frame["quote_start"].isna()
            | ((frame["quote_start"] >= 0) & (frame["quote_end"] > frame["quote_start"])),
            name="quote_offsets_ordered",
        ),
        pa.Check(
            lambda frame: frame.loc[frame["assertion_state"] == "conflict", "conflict_group_id"]
            .notna()
            .all(),
            name="conflict_has_a_group",
        ),
    ],
    strict=True,
    coerce=False,
)

ENTITY_LINKS_SCHEMA = pa.DataFrameSchema(
    name="entity_links",
    columns={
        "link_id": pa.Column(str, nullable=False, unique=True),
        "left_kind": pa.Column(str, checks=[_one_of(LINK_KINDS)]),
        "left_id": pa.Column(str, nullable=False),
        "right_kind": pa.Column(str, checks=[_one_of(LINK_KINDS)]),
        "right_id": pa.Column(str, nullable=False),
        "link_type": pa.Column(str, checks=[_one_of(LINK_TYPES)]),
        "method": pa.Column(str, checks=[_one_of(LINK_METHODS)]),
        "score": pa.Column(float, nullable=True, checks=[_unit_interval()]),
        "decision": pa.Column(str, checks=[_one_of(LINK_DECISIONS)]),
        "decision_rule": pa.Column(str, nullable=False),
        "evidence": pa.Column(str, nullable=True),
        "risk_id": pa.Column(str, nullable=True),
        "review_status": pa.Column(str, checks=[_one_of(REVIEW_STATUSES)]),
        "upstream_commit": pa.Column(str, nullable=True),
        "decided_at": pa.Column("datetime64[ns]", nullable=True),
    },
    checks=[
        pa.Check(
            lambda frame: (frame["left_kind"] != frame["right_kind"])
            | (frame["left_id"] != frame["right_id"]),
            name="link_is_not_a_self_loop",
        ),
        pa.Check(
            lambda frame: ~frame.duplicated(
                subset=["left_kind", "left_id", "right_kind", "right_id", "link_type"]
            ).any(),
            name="link_key_unique",
        ),
        pa.Check(
            lambda frame: frame.loc[frame["decision"] == "auto_accept", "score"].notna(),
            name="auto_accept_needs_a_score",
        ),
        pa.Check(
            lambda frame: frame.loc[frame["decision"] == "auto_accept", "review_status"].isin(
                PRIMARY_REVIEW_STATUSES
            ),
            name="auto_accept_is_not_pending_review",
        ),
        pa.Check(
            lambda frame: frame.loc[frame["decision"] == "grey", "review_status"].eq("pending"),
            name="grey_decision_waits_for_review",
        ),
    ],
    strict=True,
    coerce=False,
)

REVIEW_QUEUE_SCHEMA = pa.DataFrameSchema(
    name="review_queue",
    columns={
        "item_id": pa.Column(str, nullable=False, unique=True),
        "subject_type": pa.Column(str, checks=[_one_of(SUBJECT_TYPES)]),
        "subject_id": pa.Column(str, nullable=False),
        "risk_id": pa.Column(str, nullable=True),
        "priority": pa.Column("Int64", nullable=True),
        "reason_codes": pa.Column(str, nullable=False),
        "route": pa.Column(str, checks=[_one_of(ROUTES)]),
        "status": pa.Column(str, checks=[_one_of(QUEUE_STATUSES)]),
        "decision": pa.Column(str, nullable=True),
        "rationale": pa.Column(str, nullable=True),
        "reviewer": pa.Column(str, nullable=True),
        "created_at": pa.Column("datetime64[ns]", nullable=True),
        "resolved_at": pa.Column("datetime64[ns]", nullable=True),
    },
    checks=[
        pa.Check(
            lambda frame: ~(frame["status"] == "resolved")
            | frame[["decision", "rationale", "reviewer", "resolved_at"]].notna().all(axis=1),
            name="resolved_item_records_decision_and_reviewer",
        ),
        pa.Check(
            lambda frame: frame.loc[frame["status"] != "resolved", ["resolved_at"]]
            .isna()
            .all(axis=1),
            name="unresolved_item_has_no_resolution_time",
        ),
        pa.Check(
            lambda frame: ~frame[frame["status"] != "resolved"].duplicated(
                subset=["subject_type", "subject_id"]
            ).any(),
            name="one_open_item_per_subject",
        ),
    ],
    strict=True,
    coerce=False,
)

RISK_SCORES_SCHEMA = pa.DataFrameSchema(
    name="risk_scores",
    columns={
        "risk_id": pa.Column(str, nullable=False, unique=True),
        "subject_type": pa.Column(str, checks=[_one_of(RISK_SUBJECT_TYPES)]),
        "subject_id": pa.Column(str, nullable=False),
        **{
            name: pa.Column(float, nullable=False, checks=[_unit_interval()])
            for name in RISK_COMPONENTS
        },
        "total_score": pa.Column(float, nullable=False, checks=[_unit_interval()]),
        "risk_tier": pa.Column(str, checks=[_one_of(RISK_TIERS)]),
        "policy_version": pa.Column(str, nullable=False),
        "computed_at": pa.Column("datetime64[ns]", nullable=True),
    },
    checks=[
        pa.Check(
            lambda frame: ~frame.duplicated(subset=["subject_type", "subject_id"]).any(),
            name="one_risk_score_per_subject",
        )
    ],
    strict=True,
    coerce=False,
)

SCHEMAS: Mapping[str, pa.DataFrameSchema] = {
    "persons": PERSONS_SCHEMA,
    "source_documents": SOURCE_DOCUMENTS_SCHEMA,
    "career_events": CAREER_EVENTS_SCHEMA,
    "kin_edges": KIN_EDGES_SCHEMA,
    "credentials": CREDENTIALS_SCHEMA,
    "offices": OFFICES_SCHEMA,
    "evidence_assertions": EVIDENCE_ASSERTIONS_SCHEMA,
    "entity_links": ENTITY_LINKS_SCHEMA,
    "review_queue": REVIEW_QUEUE_SCHEMA,
    "risk_scores": RISK_SCORES_SCHEMA,
}


def validate_table(name: str, frame: pd.DataFrame) -> pd.DataFrame:
    """Validate ``frame`` against the named V0.3 schema; raise on any violation."""
    if name not in SCHEMAS:
        raise KeyError(f"unknown V0.3 table: {name}")
    return SCHEMAS[name].validate(frame, lazy=True)


def validate_referential_integrity(tables: Mapping[str, pd.DataFrame]) -> None:
    """Foreign keys a single-table schema cannot express."""
    person_ids = set(tables["persons"]["person_id"])
    document_ids = set(tables["source_documents"]["document_id"])

    for table, column in (
        ("career_events", "person_id"),
        ("credentials", "person_id"),
        ("kin_edges", "ego_person_id"),
    ):
        orphans = sorted(set(tables[table][column].dropna()) - person_ids)
        if orphans:
            raise ValueError(f"{table}.{column} references unknown persons: {orphans[:5]}")

    alters = sorted(set(tables["kin_edges"]["alter_person_id"].dropna()) - person_ids)
    if alters:
        raise ValueError(f"kin_edges.alter_person_id references unknown persons: {alters[:5]}")

    for table, column in (
        ("career_events", "source_document_id"),
        ("credentials", "source_document_id"),
        ("kin_edges", "source_document_id"),
    ):
        orphans = sorted(set(tables[table][column].dropna()) - document_ids)
        if orphans:
            raise ValueError(f"{table}.{column} references unknown documents: {orphans[:5]}")

    orphans = sorted(
        set(tables["evidence_assertions"]["source_document_id"].dropna()) - document_ids
    )
    if orphans:
        raise ValueError(f"evidence_assertions reference unknown documents: {orphans[:5]}")

    office_ids = set(tables["offices"]["office_id"])
    orphans = sorted(set(tables["career_events"]["office_id"].dropna()) - office_ids)
    if orphans:
        raise ValueError(f"career_events.office_id references unknown offices: {orphans[:5]}")

    risk_ids = set(tables["risk_scores"]["risk_id"])
    for table in ("entity_links", "review_queue"):
        orphans = sorted(set(tables[table]["risk_id"].dropna()) - risk_ids)
        if orphans:
            raise ValueError(f"{table}.risk_id references unknown risk scores: {orphans[:5]}")


def validate_detail_rows_cite_ledger(tables: Mapping[str, pd.DataFrame]) -> None:
    """Every detail row must cite the ledger assertion that attests it.

    Also checks that the assertion is about that very row and that both point at the
    same source document, so an indicator can be traced back to one span.
    """
    assertions = tables["evidence_assertions"].set_index("assertion_id")
    problems: list[str] = []
    for table, (key_column, subject_type) in DETAIL_TABLES.items():
        frame = tables[table]
        for row in frame.itertuples(index=False):
            row_id = getattr(row, key_column)
            assertion_id = getattr(row, "evidence_assertion_id")
            if pd.isna(assertion_id) or assertion_id not in assertions.index:
                problems.append(f"{table}.{row_id}: cites unknown assertion {assertion_id!r}")
                continue
            assertion = assertions.loc[assertion_id]
            if assertion["subject_type"] != subject_type or assertion["subject_id"] != row_id:
                problems.append(
                    f"{table}.{row_id}: assertion {assertion_id} is about "
                    f"{assertion['subject_type']}/{assertion['subject_id']}"
                )
            document = getattr(row, "source_document_id", None)
            if pd.notna(document) and pd.notna(assertion["source_document_id"]):
                if document != assertion["source_document_id"]:
                    problems.append(
                        f"{table}.{row_id}: document {document!r} disagrees with assertion "
                        f"{assertion['source_document_id']!r}"
                    )
    if problems:
        raise ValueError("detail rows without matching ledger evidence:\n  " + "\n  ".join(problems[:10]))


def validate_evidence_spans(
    assertions: pd.DataFrame, source_documents: pd.DataFrame
) -> None:
    """``quote_start:quote_end`` must reproduce ``quote`` verbatim in the document text.

    Documents without stored text (structured records) cannot be span-checked; their
    affirmative assertions must therefore carry no offsets at all.
    """
    texts = dict(zip(source_documents["document_id"], source_documents["text"]))
    problems: list[str] = []
    affirmative = assertions[assertions["assertion_state"].isin(AFFIRMATIVE_STATES)]
    for row in affirmative.itertuples(index=False):
        text = texts.get(row.source_document_id)
        has_span = pd.notna(row.quote_start) and pd.notna(row.quote_end)
        if not isinstance(text, str):
            if has_span:
                problems.append(
                    f"{row.assertion_id}: span given for a document without text "
                    f"({row.source_document_id})"
                )
            continue
        if not has_span:
            problems.append(
                f"{row.assertion_id}: no span for text document {row.source_document_id}"
            )
            continue
        snippet = text[int(row.quote_start) : int(row.quote_end)]
        if snippet != row.quote:
            problems.append(
                f"{row.assertion_id}: span does not reproduce quote "
                f"({snippet[:20]!r} != {row.quote[:20]!r})"
            )
    if problems:
        raise ValueError("evidence span violations:\n  " + "\n  ".join(problems[:10]))


def expected_risk_tier(total_score: float, policy: Mapping[str, Any] | None = None) -> str:
    """Return the policy tier a normalised risk score falls into."""
    policy = policy or load_review_policy()
    for name in RISK_TIERS:
        if float(total_score) <= float(policy["tiers"][name]["max_score"]):
            return name
    raise DesignContractError(f"risk score {total_score} exceeds every tier bound")


def validate_risk_tiers(
    risk_scores: pd.DataFrame, policy: Mapping[str, Any] | None = None
) -> None:
    """The stored tier must be the one the policy assigns to the stored score."""
    policy = policy or load_review_policy()
    mismatched = [
        (row.risk_id, row.total_score, row.risk_tier)
        for row in risk_scores.itertuples(index=False)
        if row.risk_tier != expected_risk_tier(row.total_score, policy)
    ]
    if mismatched:
        raise ValueError(f"risk tier does not follow the policy: {mismatched[:5]}")


def validate_review_routing(
    review_queue: pd.DataFrame,
    risk_scores: pd.DataFrame,
    policy: Mapping[str, Any] | None = None,
) -> None:
    """Human review is reserved for HIGH risk; routes must follow the policy."""
    policy = policy or load_review_policy()
    tiers = dict(zip(risk_scores["risk_id"], risk_scores["risk_tier"]))
    problems: list[str] = []
    for row in review_queue.itertuples(index=False):
        if pd.isna(row.risk_id):
            if row.route == "human_review":
                problems.append(f"{row.item_id}: human review without a risk score")
            continue
        tier = tiers.get(row.risk_id)
        if tier is None:
            problems.append(f"{row.item_id}: unknown risk score {row.risk_id}")
            continue
        if row.route != policy["tiers"][tier]["route"]:
            problems.append(
                f"{row.item_id}: route {row.route} disagrees with tier {tier}"
            )
        if row.route == "human_review" and tier != "HIGH":
            problems.append(f"{row.item_id}: human review reserved for HIGH risk, got {tier}")
    if problems:
        raise ValueError("review routing violations:\n  " + "\n  ".join(problems[:10]))


def validate_all(
    tables: Mapping[str, pd.DataFrame], *, policy: Mapping[str, Any] | None = None
) -> None:
    """Validate every V0.3 table plus the cross-table rules."""
    missing = [name for name in V03_TABLES if name not in tables]
    if missing:
        raise ValueError(f"missing V0.3 tables: {missing}")
    for name in V03_TABLES:
        validate_table(name, tables[name])
    validate_referential_integrity(tables)
    validate_detail_rows_cite_ledger(tables)
    validate_evidence_spans(tables["evidence_assertions"], tables["source_documents"])
    validate_risk_tiers(tables["risk_scores"], policy)
    validate_review_routing(tables["review_queue"], tables["risk_scores"], policy)
