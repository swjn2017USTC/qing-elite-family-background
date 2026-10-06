"""Literature-pipeline schemas (U04R).

Framework reused from the verified ``ACADEMIC_LITERATURE_PIPELINE_HANDOFF`` (SANTONG /
24histories projects): registry → digest → evidence ledger → claim delta. The topic data
and conclusions of those projects are *not* reused; only the field conventions are.

Four record kinds are pinned here:

* ``literature_registry`` — one row per paper, project-wide, ``LIT-NNNN``;
* ``literature_claims`` — the LC ledger: one claim per row, always with a locator;
* ``claim_source_map`` — claim → variable → source → testability;
* ``source_feasibility`` — one row per candidate historical source, with the probe
  evidence that justifies its ``access_type``.
"""

from __future__ import annotations

from collections.abc import Mapping

import pandas as pd
import pandera.pandas as pa

LITERATURE_ID_PATTERN = r"^LIT-\d{4}$"
CLAIM_ID_PATTERN = r"^LC-\d{3,4}$"

#: Access classification, reused verbatim from the handoff's design.
ACCESS_STATUSES = (
    "METADATA_ONLY",
    "ABSTRACT_ONLY",
    "OPEN_FULLTEXT_HTML",
    "OPEN_FULLTEXT_PDF",
    "PUBLIC_REPOSITORY_FULLTEXT",
    "LOCAL_USER_FILE",
    "RESTRICTED",
)
RIGHTS_STATUSES = ("OPEN_ACCESS", "INSTITUTION_LICENSED", "PERSONAL_COPY", "UNKNOWN")
TIERS = ("core", "candidate")
EVIDENCE_LEVELS = ("METADATA", "ABSTRACT", "FULLTEXT")
LANGUAGES = ("en", "zh", "ja", "other")

#: Source feasibility classification (V0.3 plan, machine-enforced).
SOURCE_ACCESS_TYPES = (
    "PUBLIC_STRUCTURED",
    "PUBLIC_SCAN",
    "PUBLIC_UI_ONLY",
    "ACCESS_REQUEST_REQUIRED",
    "PAPER_TABLE_ONLY",
    "UNAVAILABLE",
)
PLAN_ROLES = ("exposure_side", "outcome_side", "backfill_only")
SOURCE_TIERS = ("S", "A", "B", "C")
#: Which relations a source records *when it records kin*. ``NARROW`` = spouse-only or
#: lineage-role labels without parent-child edges; ``BROAD`` = father/grandfather plus
#: collateral kin; ``PARTIAL`` = direct line only, collateral unsystematic.
KIN_SCOPE_GRADES = ("BROAD", "PARTIAL", "NARROW", "NONE")
#: What share of the *target population* actually carries kin records. This is the field
#: that killed the v0.2 design: CBDB records kin well but for almost none of the Qing local
#: officials (D-tier link coverage 0.78%).
KIN_POPULATION_COVERAGE = ("HIGH", "MEDIUM", "LOW", "NONE", "UNKNOWN")
TESTABILITY_LEVELS = (
    "TESTABLE_NOW",
    "TESTABLE_WITH_OCR",
    "TESTABLE_WITH_ACCESS_REQUEST",
    "NOT_TESTABLE_IN_V03",
)
VARIABLE_ROLES = ("exposure", "outcome", "stratum", "legacy_benchmark")
CONFIDENCE_LEVELS = ("HIGH", "MEDIUM", "LOW")
EVIDENCE_TYPES = (
    "AUTHOR_ARGUMENT",
    "AUTHOR_EVIDENCE",
    "AUTHOR_INFERENCE",
    "EDITORIAL_NOTE",
)

LITERATURE_REGISTRY_COLUMNS = (
    "literature_id",
    "title",
    "authors",
    "year",
    "venue",
    "doi",
    "url",
    "language",
    "type",
    "query_cluster",
    "discovery_source",
    "retrieved_at",
    "abstract",
    "relevance_score",
    "tier",
    "topics",
    "access_status",
    "rights_status",
    "evidence_level",
    "local_file",
    "sha256",
    "normalized_text",
    "fulltext_verified",
    "notes",
)

LITERATURE_CLAIM_COLUMNS = (
    "lit_claim_id",
    "literature_id",
    "claim",
    "locator_type",
    "locator_value",
    "evidence_type",
    "source_basis",
    "topic",
    "v03_variables",
    "confidence",
    "notes",
)

CLAIM_SOURCE_COLUMNS = (
    "claim_id",
    "lit_claim_id",
    "literature_id",
    "claim",
    "v03_variable",
    "variable_role",
    "candidate_source",
    "testability",
    "required_source_capability",
    "reason",
)

SOURCE_FEASIBILITY_COLUMNS = (
    "source_name",
    "zh_name",
    "tier",
    "plan_role",
    "coverage_years",
    "population",
    "kin_scope",
    "kin_scope_grade",
    "kin_population_coverage",
    "career_scope",
    "access_type",
    "bulk_available",
    "api_available",
    "scan_available",
    "machine_readable",
    "terms",
    "redistribution",
    "local_cache_allowed",
    "estimated_n",
    "estimated_ocr_pages",
    "automation_score",
    "research_value",
    "probe_url",
    "probe_http_status",
    "probe_observed",
    "probed_at",
    "evidence_confidence",
    "notes",
)


def _one_of(values: tuple[str, ...]) -> pa.Check:
    return pa.Check(
        lambda series, values=values: series.isna() | series.isin(values),
        name=f"one_of_{len(values)}",
    )


def _unit_interval() -> pa.Check:
    return pa.Check(
        lambda series: series.isna() | ((series >= 0) & (series <= 1)),
        name="unit_interval_or_na",
    )


def _truthy(series: pd.Series) -> pd.Series:
    """Arrow-backed / nullable booleans must be normalised before logical operators.

    pandera coerces ``str`` columns to ``string[pyarrow]``; comparing them yields a
    pyarrow-backed boolean Series, and mixing that with a numpy-backed one raises a
    ``TypeError`` inside pandas. Every check below normalises first.
    """
    return series.fillna(False).astype(bool)


def _equals(series: pd.Series, value: str) -> pd.Series:
    return _truthy(series.astype("object").fillna("").astype(str).eq(value))


LITERATURE_REGISTRY_SCHEMA = pa.DataFrameSchema(
    name="literature_registry",
    columns={
        "literature_id": pa.Column(str, nullable=False, unique=True, checks=pa.Check.str_matches(LITERATURE_ID_PATTERN)),
        "title": pa.Column(str, nullable=False),
        "authors": pa.Column(str, nullable=True),
        "year": pa.Column("Int64", nullable=True),
        "venue": pa.Column(str, nullable=True),
        "doi": pa.Column(str, nullable=True),
        "url": pa.Column(str, nullable=True),
        "language": pa.Column(str, checks=[_one_of(LANGUAGES)]),
        "type": pa.Column(str, nullable=True),
        "query_cluster": pa.Column(str, nullable=False),
        "discovery_source": pa.Column(str, nullable=False),
        "retrieved_at": pa.Column(str, nullable=False),
        "abstract": pa.Column(str, nullable=True),
        "relevance_score": pa.Column("Int64", nullable=False, checks=pa.Check.ge(0)),
        "tier": pa.Column(str, checks=[_one_of(TIERS)]),
        "topics": pa.Column(str, nullable=True),
        "access_status": pa.Column(str, checks=[_one_of(ACCESS_STATUSES)]),
        "rights_status": pa.Column(str, checks=[_one_of(RIGHTS_STATUSES)]),
        "evidence_level": pa.Column(str, checks=[_one_of(EVIDENCE_LEVELS)]),
        "local_file": pa.Column(str, nullable=True),
        "sha256": pa.Column(str, nullable=True),
        "normalized_text": pa.Column(str, nullable=True),
        "fulltext_verified": pa.Column(bool, nullable=False),
        "notes": pa.Column(str, nullable=True),
    },
    checks=[
        # a record may only claim full text if it stores where that text is
        pa.Check(
            lambda frame: ~_truthy(frame["fulltext_verified"])
            | frame["local_file"].notna(),
            name="verified_fulltext_has_a_local_file",
        ),
        # METADATA_ONLY / ABSTRACT_ONLY can never be "verified fulltext"
        pa.Check(
            lambda frame: ~(
                _equals(frame["access_status"], "METADATA_ONLY")
                | _equals(frame["access_status"], "ABSTRACT_ONLY")
            )
            | ~_truthy(frame["fulltext_verified"]),
            name="no_fulltext_claim_without_fulltext_access",
        ),
        pa.Check(
            lambda frame: ~_equals(frame["evidence_level"], "FULLTEXT")
            | _truthy(frame["fulltext_verified"]),
            name="fulltext_evidence_requires_verification",
        ),
        pa.Check(
            lambda frame: ~_equals(frame["evidence_level"], "ABSTRACT")
            | frame["abstract"].notna(),
            name="abstract_evidence_requires_an_abstract",
        ),
    ],
    strict=True,
    coerce=False,
)

LITERATURE_CLAIMS_SCHEMA = pa.DataFrameSchema(
    name="literature_claims",
    columns={
        "lit_claim_id": pa.Column(str, nullable=False, unique=True, checks=pa.Check.str_matches(CLAIM_ID_PATTERN)),
        "literature_id": pa.Column(str, nullable=False),
        "claim": pa.Column(str, nullable=False),
        "locator_type": pa.Column(str, nullable=False),
        "locator_value": pa.Column(str, nullable=False),
        "evidence_type": pa.Column(str, checks=[_one_of(EVIDENCE_TYPES)]),
        "source_basis": pa.Column(str, nullable=True),
        "topic": pa.Column(str, nullable=False),
        "v03_variables": pa.Column(str, nullable=False),
        "confidence": pa.Column(str, checks=[_one_of(CONFIDENCE_LEVELS)]),
        "notes": pa.Column(str, nullable=True),
    },
    checks=[
        pa.Check(
            lambda frame: frame["claim"].str.len() >= 10,
            name="claim_is_a_sentence_not_a_label",
        ),
        # a claim without a locator cannot be traced back to the paper
        pa.Check(
            lambda frame: frame["locator_value"].astype("object").fillna("").astype(str).str.len() > 0,
            name="claim_needs_a_locator",
        ),
    ],
    strict=True,
    coerce=False,
)

CLAIM_SOURCE_SCHEMA = pa.DataFrameSchema(
    name="claim_source_map",
    columns={
        "claim_id": pa.Column(str, nullable=False, unique=True),
        "lit_claim_id": pa.Column(str, nullable=False),
        "literature_id": pa.Column(str, nullable=False),
        "claim": pa.Column(str, nullable=False),
        "v03_variable": pa.Column(str, nullable=False),
        "variable_role": pa.Column(str, checks=[_one_of(VARIABLE_ROLES)]),
        "candidate_source": pa.Column(str, nullable=False),
        "testability": pa.Column(str, checks=[_one_of(TESTABILITY_LEVELS)]),
        "required_source_capability": pa.Column(str, nullable=False),
        "reason": pa.Column(str, nullable=False, checks=pa.Check(lambda s: s.str.len() >= 10, name="reason_len")),
    },
    strict=True,
    coerce=False,
)

SOURCE_FEASIBILITY_SCHEMA = pa.DataFrameSchema(
    name="source_feasibility",
    columns={
        "source_name": pa.Column(str, nullable=False, unique=True),
        "zh_name": pa.Column(str, nullable=False),
        "tier": pa.Column(str, checks=[_one_of(SOURCE_TIERS)]),
        "plan_role": pa.Column(str, checks=[_one_of(PLAN_ROLES)]),
        "coverage_years": pa.Column(str, nullable=False),
        "population": pa.Column(str, nullable=False),
        "kin_scope": pa.Column(str, nullable=False),
        "kin_scope_grade": pa.Column(str, checks=[_one_of(KIN_SCOPE_GRADES)]),
        "kin_population_coverage": pa.Column(str, checks=[_one_of(KIN_POPULATION_COVERAGE)]),
        "career_scope": pa.Column(str, nullable=False),
        "access_type": pa.Column(str, checks=[_one_of(SOURCE_ACCESS_TYPES)]),
        "bulk_available": pa.Column("boolean", nullable=True),
        "api_available": pa.Column("boolean", nullable=True),
        "scan_available": pa.Column("boolean", nullable=True),
        "machine_readable": pa.Column("boolean", nullable=True),
        "terms": pa.Column(str, nullable=False),
        "redistribution": pa.Column(str, nullable=False),
        "local_cache_allowed": pa.Column("boolean", nullable=True),
        "estimated_n": pa.Column(str, nullable=False),
        "estimated_ocr_pages": pa.Column(str, nullable=False),
        "automation_score": pa.Column(float, checks=[_unit_interval()]),
        "research_value": pa.Column(float, checks=[_unit_interval()]),
        "probe_url": pa.Column(str, nullable=True),
        "probe_http_status": pa.Column("Int64", nullable=True),
        "probe_observed": pa.Column(str, nullable=False),
        "probed_at": pa.Column(str, nullable=False),
        "evidence_confidence": pa.Column(str, checks=[_one_of(CONFIDENCE_LEVELS)]),
        "notes": pa.Column(str, nullable=False),
    },
    checks=[
        # every source must cite an observed endpoint …
        pa.Check(
            lambda frame: frame["probe_url"].notna(),
            name="every_source_cites_a_probe",
        ),
        # … and a machine-access claim additionally needs an observed HTTP status
        pa.Check(
            lambda frame: ~_truthy(frame["access_type"].isin(("PUBLIC_STRUCTURED", "PUBLIC_SCAN")))
            | frame["probe_http_status"].notna(),
            name="machine_access_requires_probe_evidence",
        ),
        pa.Check(
            lambda frame: frame["probe_observed"].str.len() >= 10,
            name="probe_observation_is_recorded",
        ),
        pa.Check(
            lambda frame: ~frame["bulk_available"].fillna(False) | frame["local_cache_allowed"].fillna(False),
            name="bulk_download_requires_local_cache_permission",
        ),
    ],
    strict=True,
    coerce=False,
)

LIT_SCHEMAS: Mapping[str, pa.DataFrameSchema] = {
    "literature_registry": LITERATURE_REGISTRY_SCHEMA,
    "literature_claims": LITERATURE_CLAIMS_SCHEMA,
    "claim_source_map": CLAIM_SOURCE_SCHEMA,
    "source_feasibility": SOURCE_FEASIBILITY_SCHEMA,
}


def validate_lit_table(name: str, frame: pd.DataFrame) -> pd.DataFrame:
    """Validate ``frame`` against the named literature-pipeline schema."""
    if name not in LIT_SCHEMAS:
        raise KeyError(f"unknown literature table: {name}")
    return LIT_SCHEMAS[name].validate(frame, lazy=True)


def validate_claim_links(
    claims: pd.DataFrame, registry: pd.DataFrame, mapping: pd.DataFrame
) -> None:
    """Every LC / claim-map row must point at a registered paper."""
    known = set(registry["literature_id"])
    problems: list[str] = []
    for row in claims.itertuples(index=False):
        if row.literature_id not in known:
            problems.append(f"{row.lit_claim_id}: unknown literature_id {row.literature_id}")
    claim_ids = set(claims["lit_claim_id"])
    for row in mapping.itertuples(index=False):
        if row.literature_id not in known:
            problems.append(f"{row.claim_id}: unknown literature_id {row.literature_id}")
        if row.lit_claim_id not in claim_ids:
            problems.append(f"{row.claim_id}: unknown lit_claim_id {row.lit_claim_id}")
    if problems:
        raise ValueError("claim linkage violations:\n  " + "\n  ".join(problems[:10]))


def validate_core_have_digests(registry: pd.DataFrame, digest_ids: set[str]) -> None:
    """Every core paper must have a digest file; candidates need none."""
    missing = sorted(
        set(registry.loc[registry["tier"] == "core", "literature_id"]) - digest_ids
    )
    if missing:
        raise ValueError(f"core papers without a digest: {missing}")


def validate_digest_sections(text: str, required: tuple[str, ...]) -> None:
    """A digest must carry every required section heading, verbatim."""
    missing = [heading for heading in required if heading not in text]
    if missing:
        raise ValueError(f"digest is missing sections: {missing}")
