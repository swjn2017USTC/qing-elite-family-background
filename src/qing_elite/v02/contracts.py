"""v0.2 evidence-state contracts (U01).

Explicit, machine-checked schemas for the evidence ledger and its satellites. Every
downstream phase writes through these validators, so a violation stops the build instead
of silently producing a "fact":

* ``assertion_state`` is one of ``positive / explicit_negative / unknown / conflict``;
* a ``positive`` or ``explicit_negative`` assertion must carry a source, a locator and a
  verbatim quote;
* ``unknown`` and ``conflict`` must not carry a value (absence of record is not a value);
* ``quote_start:quote_end`` must reproduce ``quote`` character-for-character in the
  source document text;
* the primary indicator frame may not contain ``legacy_`` columns, and a ``0`` on a
  documented indicator requires explicit negative evidence.

Pandera checks the shape of one table; :func:`validate_referential_integrity` and
:func:`validate_evidence_spans` check the parts that need two tables.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pandas as pd
import pandera.pandas as pa

ANCILLARY_ATTRIBUTES = ("name", "degree", "office")
ANCESTOR_SLOTS = ("father", "grandfather", "great_grandfather")
RELATION_TYPES = ("父", "祖父", "曾祖", "unknown")

ASSERTION_STATES = ("positive", "explicit_negative", "unknown", "conflict")
AFFIRMATIVE_STATES = ("positive", "explicit_negative")
REVIEW_STATUSES = ("not_required", "pending", "reviewed_accepted", "reviewed_rejected")
PRIMARY_REVIEW_STATUSES = ("not_required", "reviewed_accepted")
DECISIONS = ("pending", "accepted", "rejected", "corrected", "abstain")

DOCUMENT_TYPES = ("qingshigao_biography", "cbdb_structured_record")
RIGHTS_STATUSES = ("public_domain", "restricted", "unknown")
SEARCH_OUTCOMES = ("found", "not_found", "unavailable", "error")

EXTRACTORS = ("cbdb_kin_data", "llm_extraction", "migration")
RESOLUTION_STATUSES = ("resolved", "unresolved")
LINK_STATUSES = (
    "explicit_id",
    "accepted_cross_link",
    "merged_into_cbdb",
    "review_pending",
    "rejected",
    "standalone",
)
ENTITY_SOURCES = ("CBDB", "CGEDQ", "CBDB+CGEDQ")
V01_LINKAGE_CONFIDENCES = ("high", "medium", "ambiguous", "unmatched", "unlinkable_no_surname")

EVIDENCE_COLUMNS = (
    "entity_id",
    "ancestor_slot",
    "relation_type",
    "attribute",
    "assertion_state",
    "value_raw",
    "value_normalized",
    "source_id",
    "source_locator",
    "quote",
    "quote_start",
    "quote_end",
    "extractor",
    "extractor_version",
    "extraction_run_id",
    "review_status",
    "confidence",
    "conflict_group_id",
)
EVIDENCE_KEY = ("entity_id", "ancestor_slot", "attribute")

# Counts surfaced on the primary frame so the "0 needs explicit evidence" rule is
# checkable inside the table itself.
PRIMARY_COUNT_COLUMNS = (
    "n_known_slots",
    "n_positive_name",
    "n_positive_office",
    "n_explicit_negative_office",
    "n_unknown_office",
    "n_positive_degree",
    "n_explicit_negative_degree",
    "n_unknown_degree",
)
PRIMARY_METRIC_COLUMNS = (
    "ancestor_identity_coverage_1g",
    "ancestor_identity_coverage_2g",
    "ancestor_identity_coverage_3g",
    "attribute_ascertainment_office",
    "attribute_ascertainment_degree",
    "documented_ancestor_official_any",
    "documented_ancestor_degree_any",
    "documented_family_capital_any",
    "documented_commoner_explicit",
    "source_protocol_complete",
)
PRIMARY_COLUMNS = ("person_uid",) + PRIMARY_COUNT_COLUMNS + PRIMARY_METRIC_COLUMNS
LEGACY_PREFIX = "legacy_"


def _all_in(values: tuple[str, ...]) -> pa.Check:
    return pa.Check(
        lambda series, values=values: series.isna() | series.isin(values),
        name=f"in_{'_'.join(values)}",
    )


def _one_of(values: tuple[str, ...]) -> pa.Check:
    return pa.Check(lambda series, values=values: series.isin(values), name=f"one_of_{len(values)}")


def _flag(values: tuple[int, ...] = (0, 1)) -> pa.Check:
    return pa.Check(lambda series, values=values: series.isna() | series.isin(values), name="flag_or_na")


ENTITIES_SCHEMA = pa.DataFrameSchema(
    name="entities",
    columns={
        "entity_id": pa.Column(str, nullable=False, unique=True),
        "cbdb_personid": pa.Column("Int64", nullable=True),
        "cgedq_person_id": pa.Column(str, nullable=True),
        "name_chn": pa.Column(str, nullable=False),
        "source": pa.Column(str, checks=[_one_of(ENTITY_SOURCES)]),
        "highest_tier": pa.Column(str, nullable=True),
        "tiers_present": pa.Column(str, nullable=True),
        "career_first_year": pa.Column("Int64", nullable=True),
        "career_last_year": pa.Column("Int64", nullable=True),
        "native_province_effective": pa.Column(str, nullable=True),
        "banner_effective": pa.Column(str, nullable=True),
        "degree_effective": pa.Column(str, nullable=True),
        "v01_linkage_confidence": pa.Column(str, nullable=True, checks=[_all_in(V01_LINKAGE_CONFIDENCES)]),
        "link_status": pa.Column(str, checks=[_one_of(LINK_STATUSES)]),
        "link_evidence": pa.Column(str, nullable=True),
        "resolution_status": pa.Column(str, checks=[_one_of(RESOLUTION_STATUSES)]),
        "primary_eligible": pa.Column(bool, nullable=False),
    },
    checks=[
        pa.Check(
            lambda frame: (frame["resolution_status"] == "resolved") == frame["primary_eligible"],
            name="resolution_matches_primary_eligibility",
        )
    ],
    strict=True,
    coerce=False,
)

SOURCE_DOCUMENTS_SCHEMA = pa.DataFrameSchema(
    name="source_documents",
    columns={
        "document_id": pa.Column(str, nullable=False, unique=True),
        "document_type": pa.Column(str, checks=[_one_of(DOCUMENT_TYPES)]),
        "title": pa.Column(str, nullable=False),
        "volume": pa.Column(str, nullable=True),
        "locator": pa.Column(str, nullable=True),
        "release_id": pa.Column(str, nullable=True),
        "retrieved_at": pa.Column(str, nullable=True),
        "rights_status": pa.Column(str, checks=[_one_of(RIGHTS_STATUSES)]),
        "license": pa.Column(str, nullable=True),
        "text": pa.Column(str, nullable=True),
        "text_sha256": pa.Column(str, nullable=True),
        "chars": pa.Column("Int64", nullable=True),
    },
    checks=[
        pa.Check(
            lambda frame: frame["text"].isna()
            | (frame["text_sha256"].notna() & (frame["chars"] == frame["text"].str.len())),
            name="text_implies_hash_and_length",
        )
    ],
    strict=True,
    coerce=False,
)

SOURCE_SEARCH_LOG_SCHEMA = pa.DataFrameSchema(
    name="source_search_log",
    columns={
        "search_id": pa.Column(str, nullable=False, unique=True),
        "entity_id": pa.Column(str, nullable=False),
        "source_id": pa.Column(str, nullable=False),
        "query": pa.Column(str, nullable=False),
        "searched": pa.Column(bool, nullable=False),
        "outcome": pa.Column(str, checks=[_one_of(SEARCH_OUTCOMES)]),
        "searched_at": pa.Column("datetime64[ns]", nullable=True),
        "notes": pa.Column(str, nullable=True),
    },
    checks=[
        pa.Check(
            lambda frame: ~((frame["searched"]) & frame["outcome"].isna()),
            name="searched_requires_outcome",
        )
    ],
    strict=True,
    coerce=False,
)

EVIDENCE_ASSERTIONS_SCHEMA = pa.DataFrameSchema(
    name="evidence_assertions",
    columns={
        "entity_id": pa.Column(str, nullable=False),
        "ancestor_slot": pa.Column(str, checks=[_one_of(ANCESTOR_SLOTS)]),
        "relation_type": pa.Column(str, checks=[_one_of(RELATION_TYPES)]),
        "attribute": pa.Column(str, checks=[_one_of(ANCILLARY_ATTRIBUTES)]),
        "assertion_state": pa.Column(str, checks=[_one_of(ASSERTION_STATES)]),
        "value_raw": pa.Column(str, nullable=True),
        "value_normalized": pa.Column(str, nullable=True),
        "source_id": pa.Column(str, nullable=True),
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
                ["source_id", "source_locator", "quote"],
            ].notna().all(axis=1),
            name="assertion_requires_source_locator_quote",
        ),
        pa.Check(
            lambda frame: frame.loc[
                frame["assertion_state"].isin(AFFIRMATIVE_STATES), "quote"
            ].astype(str).str.strip().ne(""),
            name="assertion_quote_not_blank",
        ),
        pa.Check(
            lambda frame: frame.loc[
                frame["assertion_state"].isin(("unknown", "conflict")),
                ["value_raw", "value_normalized"],
            ].isna().all(axis=1),
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
            lambda frame: ~frame.duplicated(subset=list(EVIDENCE_KEY)).any(),
            name="evidence_key_unique",
        ),
    ],
    strict=True,
    coerce=False,
)

REVIEW_DECISIONS_SCHEMA = pa.DataFrameSchema(
    name="review_decisions",
    columns={
        "decision_id": pa.Column(str, nullable=False, unique=True),
        "entity_id": pa.Column(str, nullable=False),
        "ancestor_slot": pa.Column(str, checks=[_one_of(ANCESTOR_SLOTS)]),
        "attribute": pa.Column(str, checks=[_one_of(ANCILLARY_ATTRIBUTES)]),
        "proposed_value": pa.Column(str, nullable=True),
        "decision": pa.Column(str, checks=[_one_of(DECISIONS)]),
        "reviewer": pa.Column(str, nullable=True),
        "decided_at": pa.Column("datetime64[ns]", nullable=True),
        "reason": pa.Column(str, nullable=True),
        "auto_verdict": pa.Column(str, nullable=True),
    },
    checks=[
        pa.Check(
            lambda frame: frame.loc[frame["decision"] != "pending", ["reviewer", "decided_at"]]
            .notna()
            .all(axis=1),
            name="decided_rows_need_reviewer_and_time",
        )
    ],
    strict=True,
    coerce=False,
)

PRIMARY_INDICATORS_SCHEMA = pa.DataFrameSchema(
    name="person_indicators_v02",
    columns={
        "person_uid": pa.Column(str, nullable=False, unique=True),
        **{name: pa.Column("Int64", nullable=False) for name in PRIMARY_COUNT_COLUMNS},
        "ancestor_identity_coverage_1g": pa.Column("Int64", checks=[_flag((0, 1))]),
        "ancestor_identity_coverage_2g": pa.Column("Int64", checks=[_flag((0, 1))]),
        "ancestor_identity_coverage_3g": pa.Column("Int64", checks=[_flag((0, 1))]),
        "attribute_ascertainment_office": pa.Column(float, nullable=True),
        "attribute_ascertainment_degree": pa.Column(float, nullable=True),
        "documented_ancestor_official_any": pa.Column("Int64", nullable=True, checks=[_flag((0, 1))]),
        "documented_ancestor_degree_any": pa.Column("Int64", nullable=True, checks=[_flag((0, 1))]),
        "documented_family_capital_any": pa.Column("Int64", nullable=True, checks=[_flag((0, 1))]),
        "documented_commoner_explicit": pa.Column("Int64", nullable=True, checks=[_flag((0, 1))]),
        "source_protocol_complete": pa.Column("boolean", nullable=True),
    },
    checks=[
        pa.Check(
            lambda frame: not any(column.startswith(LEGACY_PREFIX) for column in frame.columns),
            name="no_legacy_columns_in_primary",
        ),
        pa.Check(
            lambda frame: frame["n_known_slots"] >= 0, name="known_slots_non_negative"
        ),
        pa.Check(
            lambda frame: ~(
                (frame["documented_ancestor_official_any"] == 0)
                & (frame["n_explicit_negative_office"] == 0)
            ),
            name="zero_official_requires_explicit_negative",
        ),
        pa.Check(
            lambda frame: ~(
                (frame["documented_ancestor_degree_any"] == 0)
                & (frame["n_explicit_negative_degree"] == 0)
            ),
            name="zero_degree_requires_explicit_negative",
        ),
        pa.Check(
            lambda frame: ~(
                (frame["documented_ancestor_official_any"] == 1) & (frame["n_positive_office"] == 0)
            ),
            name="one_official_requires_a_positive_assertion",
        ),
        pa.Check(
            lambda frame: ~(
                (frame["documented_family_capital_any"] == 1)
                & (frame["n_positive_office"] == 0)
                & (frame["n_positive_degree"] == 0)
            ),
            name="capital_requires_a_positive_assertion",
        ),
        pa.Check(
            lambda frame: (
                frame["ancestor_identity_coverage_1g"] == (frame["n_known_slots"] >= 1).astype("int64")
            ).all()
            and (frame["ancestor_identity_coverage_2g"] == (frame["n_known_slots"] >= 2).astype("int64")).all()
            and (frame["ancestor_identity_coverage_3g"] == (frame["n_known_slots"] >= 3).astype("int64")).all(),
            name="identity_coverage_matches_known_slots",
        ),
    ],
    strict=True,
    coerce=False,
)

LEGACY_INDICATORS_SCHEMA = pa.DataFrameSchema(
    name="legacy_person_indicators",
    columns={"person_uid": pa.Column(str, nullable=False, unique=True)},
    checks=[
        pa.Check(
            lambda frame: all(
                column.startswith(LEGACY_PREFIX) for column in frame.columns if column != "person_uid"
            ),
            name="legacy_columns_are_prefixed",
        ),
        pa.Check(
            lambda frame: not any(
                column in PRIMARY_METRIC_COLUMNS for column in frame.columns if column != "person_uid"
            ),
            name="legacy_frame_holds_no_primary_metric",
        ),
    ],
    strict=False,  # the legacy frame grows with whatever v0.1 computed
    coerce=False,
)

SCHEMAS: Mapping[str, pa.DataFrameSchema] = {
    "entities": ENTITIES_SCHEMA,
    "source_documents": SOURCE_DOCUMENTS_SCHEMA,
    "source_search_log": SOURCE_SEARCH_LOG_SCHEMA,
    "evidence_assertions": EVIDENCE_ASSERTIONS_SCHEMA,
    "review_decisions": REVIEW_DECISIONS_SCHEMA,
    "person_indicators_v02": PRIMARY_INDICATORS_SCHEMA,
    "legacy_person_indicators": LEGACY_INDICATORS_SCHEMA,
}


def validate_table(name: str, frame: pd.DataFrame) -> pd.DataFrame:
    """Validate ``frame`` against the named v0.2 schema; raise on any violation."""
    if name not in SCHEMAS:
        raise KeyError(f"unknown v0.2 table: {name}")
    return SCHEMAS[name].validate(frame, lazy=True)


def validate_referential_integrity(
    *,
    entities: pd.DataFrame,
    source_documents: pd.DataFrame,
    assertions: pd.DataFrame,
    decisions: pd.DataFrame | None = None,
    search_log: pd.DataFrame | None = None,
) -> None:
    """Foreign keys that a single-table schema cannot express."""
    entity_ids = set(entities["entity_id"])
    document_ids = set(source_documents["document_id"])

    unknown_entities = sorted(set(assertions["entity_id"]) - entity_ids)
    if unknown_entities:
        raise ValueError(f"evidence_assertions reference unknown entities: {unknown_entities[:5]}")
    unknown_documents = sorted(set(assertions["source_id"].dropna()) - document_ids)
    if unknown_documents:
        raise ValueError(f"evidence_assertions reference unknown documents: {unknown_documents[:5]}")
    if decisions is not None:
        orphan = sorted(set(decisions["entity_id"]) - entity_ids)
        if orphan:
            raise ValueError(f"review_decisions reference unknown entities: {orphan[:5]}")
    if search_log is not None and len(search_log):
        orphan = sorted(set(search_log["entity_id"]) - entity_ids)
        if orphan:
            raise ValueError(f"source_search_log references unknown entities: {orphan[:5]}")


def validate_evidence_spans(
    assertions: pd.DataFrame, source_documents: pd.DataFrame
) -> None:
    """`quote_start:quote_end` must reproduce `quote` in the source text (verbatim).

    Documents without stored text (structured records) cannot be span-checked; their
    affirmative assertions must therefore carry no offsets at all.
    """
    texts = dict(zip(source_documents["document_id"], source_documents["text"]))
    problems: list[str] = []
    affirmative = assertions[assertions["assertion_state"].isin(AFFIRMATIVE_STATES)]
    for row in affirmative.itertuples(index=False):
        text = texts.get(row.source_id)
        has_span = pd.notna(row.quote_start) and pd.notna(row.quote_end)
        if not isinstance(text, str):
            if has_span:
                problems.append(
                    f"{row.entity_id}/{row.ancestor_slot}/{row.attribute}: span given for "
                    f"a document without text ({row.source_id})"
                )
            continue
        if not has_span:
            problems.append(
                f"{row.entity_id}/{row.ancestor_slot}/{row.attribute}: no span for text document "
                f"{row.source_id}"
            )
            continue
        snippet = text[int(row.quote_start) : int(row.quote_end)]
        if snippet != row.quote:
            problems.append(
                f"{row.entity_id}/{row.ancestor_slot}/{row.attribute}: span does not reproduce "
                f"quote ({snippet[:20]!r} != {row.quote[:20]!r})"
            )
    if problems:
        raise ValueError(
            "evidence span violations:\n  " + "\n  ".join(problems[:10])
        )


def validate_all(
    tables: Mapping[str, pd.DataFrame], *, span_check: bool = True
) -> None:
    """Validate every v0.2 table plus the cross-table rules."""
    for name, frame in tables.items():
        validate_table(name, frame)
    validate_referential_integrity(
        entities=tables["entities"],
        source_documents=tables["source_documents"],
        assertions=tables["evidence_assertions"],
        decisions=tables.get("review_decisions"),
        search_log=tables.get("source_search_log"),
    )
    if span_check:
        validate_evidence_spans(tables["evidence_assertions"], tables["source_documents"])


def describe(frame: pd.DataFrame) -> dict[str, Any]:
    """Small helper used by the migration audit."""
    return {"rows": int(len(frame)), "columns": list(frame.columns)}
