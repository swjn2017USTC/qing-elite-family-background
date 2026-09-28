"""Literature-pipeline and source-feasibility tests (U04R).

The pipeline spans three things that each failed somewhere before: a registry that must not
claim more evidence than it has, digest/ledger structures that must stay traceable to a
paper, and a feasibility table whose ``access_type`` must be backed by an observed probe.
The plan-selection rules are tested against synthetic feasibility frames so the outcome
never depends on today's network.
"""

from __future__ import annotations

import copy

import pandas as pd
import pandera.errors
import pytest

from qing_elite.v03.feasibility import (
    _matches,
    load_catalog,
    load_plan_rules,
    load_probe_config,
    probe_index,
    select_default_plan,
)
from qing_elite.v03.lit.harvest import load_plan, normalize_title, relevance_score, validate_plan
from qing_elite.v03.lit.schema import (
    LITERATURE_CLAIMS_SCHEMA,
    LITERATURE_REGISTRY_SCHEMA,
    SOURCE_FEASIBILITY_SCHEMA,
    validate_claim_links,
    validate_core_have_digests,
    validate_digest_sections,
    validate_lit_table,
)

DIGEST_SECTIONS = tuple(load_plan()["digest_requirements"]["required_sections"])


def _registry_row(**overrides) -> dict:
    row = {
        "literature_id": "LIT-0001",
        "title": "Social mobility in Qing China",
        "authors": "A. Author",
        "year": 2001,
        "venue": "Journal of Test Studies",
        "doi": "10.1000/test",
        "url": "https://example.org/1",
        "language": "en",
        "type": "journal-article",
        "query_cluster": "Q1_qing_social_mobility",
        "discovery_source": "semanticscholar:test",
        "retrieved_at": "2026-09-27",
        "abstract": "We study mobility.",
        "relevance_score": 12,
        "tier": "core",
        "topics": "Q1_qing_social_mobility",
        "access_status": "ABSTRACT_ONLY",
        "rights_status": "UNKNOWN",
        "evidence_level": "ABSTRACT",
        "local_file": None,
        "sha256": None,
        "normalized_text": None,
        "fulltext_verified": False,
        "notes": "oa_url=None",
    }
    row.update(overrides)
    return row


def _claim_row(**overrides) -> dict:
    row = {
        "lit_claim_id": "LC-001",
        "literature_id": "LIT-0001",
        "claim": "The paper argues that examination success depended on lineage resources.",
        "locator_type": "abstract",
        "locator_value": "abstract",
        "evidence_type": "AUTHOR_ARGUMENT",
        "source_basis": None,
        "topic": "family_capital",
        "v03_variables": "direct_3g_degree_count",
        "confidence": "MEDIUM",
        "notes": None,
    }
    row.update(overrides)
    return row


def _feasibility_row(**overrides) -> dict:
    row = {
        "source_name": "test_source",
        "zh_name": "测试来源",
        "tier": "A",
        "plan_role": "exposure_side",
        "coverage_years": "1800-1900",
        "population": "candidates",
        "kin_scope": "father, grandfather",
        "kin_scope_grade": "BROAD",
        "kin_population_coverage": "HIGH",
        "career_scope": "office",
        "access_type": "PUBLIC_STRUCTURED",
        "bulk_available": True,
        "api_available": False,
        "scan_available": False,
        "machine_readable": True,
        "terms": "CC0",
        "redistribution": "not allowed",
        "local_cache_allowed": True,
        "estimated_n": "1000",
        "estimated_ocr_pages": "0",
        "automation_score": 0.9,
        "research_value": 0.8,
        "probe_url": "https://example.org/api",
        "probe_http_status": 200,
        "probe_observed": "GET https://example.org/api -> 200 [application/json] {\"ok\":true}",
        "probed_at": "2026-09-27",
        "evidence_confidence": "HIGH",
        "notes": "synthetic row",
    }
    row.update(overrides)
    return row


def _feasibility_frame(rows: list[dict]) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    frame["probe_http_status"] = frame["probe_http_status"].astype("Int64")
    for column, spec in SOURCE_FEASIBILITY_SCHEMA.columns.items():
        if str(spec.dtype) == "boolean":
            frame[column] = frame[column].astype("boolean")
    return frame


def _frames(**overrides) -> pd.DataFrame:
    frame = pd.DataFrame([_feasibility_row(**overrides)])
    frame["probe_http_status"] = frame["probe_http_status"].astype("Int64")
    for column, spec in SOURCE_FEASIBILITY_SCHEMA.columns.items():
        if str(spec.dtype) == "boolean":
            frame[column] = frame[column].astype("boolean")
    return frame


# ------------------------------------------------------------------ search plan


def test_plan_covers_all_twelve_required_clusters() -> None:
    clusters = load_plan()["query_clusters"]
    assert len(clusters) >= 12
    for prefix in ("Q1_", "Q4_tongnianchilu", "Q6_tongguanlu", "Q9_jinshenlu", "Q12_"):
        assert any(key.startswith(prefix) for key in clusters), prefix


def test_plan_without_a_cluster_is_rejected() -> None:
    plan = copy.deepcopy(load_plan())
    plan["query_clusters"].pop("Q6_tongguanlu")
    with pytest.raises(ValueError, match="must cover clusters"):
        validate_plan(plan)


def test_plan_without_core_rules_is_rejected() -> None:
    plan = copy.deepcopy(load_plan())
    plan["core_selection"].pop("core_limit")
    with pytest.raises(ValueError, match="core_selection.core_limit"):
        validate_plan(plan)


def test_relevance_scoring_is_deterministic_and_source_sensitive() -> None:
    weights = load_plan()["core_selection"]["weights"]
    paper = {"title": "Jinshenlu and Qing official careers", "abstract": "lineage and kin", "venue": ""}
    other = {"title": "Cell biology", "abstract": "nothing relevant", "venue": ""}
    assert relevance_score(paper, weights) > relevance_score(other, weights)
    assert relevance_score(paper, weights) == relevance_score(paper, weights)


def test_title_normalisation_collapses_punctuation_and_case() -> None:
    assert normalize_title("Qing China: Mobility!") == normalize_title("qing chinamobility")


# ------------------------------------------------------------------ registry schema


def test_registry_row_validates() -> None:
    frame = pd.DataFrame([_registry_row()])
    frame["year"] = frame["year"].astype("Int64")
    frame["relevance_score"] = frame["relevance_score"].astype("Int64")
    validate_lit_table("literature_registry", frame)


def test_literature_id_format_is_enforced() -> None:
    with pytest.raises(pandera.errors.SchemaErrors):
        validate_lit_table("literature_registry", pd.DataFrame([_registry_row(literature_id="LIT-1")]))


def test_verified_fulltext_must_point_at_a_local_file() -> None:
    with pytest.raises(pandera.errors.SchemaErrors):
        validate_lit_table(
            "literature_registry",
            pd.DataFrame([_registry_row(fulltext_verified=True)]),
        )


def test_metadata_only_cannot_be_verified_fulltext() -> None:
    with pytest.raises(pandera.errors.SchemaErrors):
        validate_lit_table(
            "literature_registry",
            pd.DataFrame(
                [
                    _registry_row(
                        access_status="METADATA_ONLY",
                        evidence_level="FULLTEXT",
                        fulltext_verified=True,
                        local_file="sources/literature/public/LIT-0001.pdf",
                    )
                ]
            ),
        )


def test_fulltext_evidence_requires_verification() -> None:
    with pytest.raises(pandera.errors.SchemaErrors):
        validate_lit_table(
            "literature_registry",
            pd.DataFrame([_registry_row(evidence_level="FULLTEXT", fulltext_verified=False)]),
        )


def test_abstract_evidence_requires_an_abstract() -> None:
    with pytest.raises(pandera.errors.SchemaErrors):
        validate_lit_table(
            "literature_registry",
            pd.DataFrame([_registry_row(abstract=None)]),
        )


# ------------------------------------------------------------------ ledger + digests


def test_claim_needs_a_locator_and_a_sentence() -> None:
    validate_lit_table("literature_claims", pd.DataFrame([_claim_row()]))
    with pytest.raises(pandera.errors.SchemaErrors):
        validate_lit_table(
            "literature_claims", pd.DataFrame([_claim_row(locator_value="")])
        )


def test_claim_must_not_be_a_bare_label() -> None:
    with pytest.raises(pandera.errors.SchemaErrors):
        validate_lit_table("literature_claims", pd.DataFrame([_claim_row(claim="mobility")]))


def test_claim_must_reference_a_registered_paper() -> None:
    registry = pd.DataFrame([_registry_row()])
    claims = pd.DataFrame([_claim_row()])
    mapping = pd.DataFrame(
        [
            {
                "claim_id": "C-01",
                "lit_claim_id": "LC-001",
                "literature_id": "LIT-0001",
                "claim": "lineage resources mattered for examination success",
                "v03_variable": "direct_3g_degree_count",
                "variable_role": "exposure",
                "candidate_source": "tongnianchilu",
                "testability": "TESTABLE_WITH_OCR",
                "required_source_capability": "kin + degree extraction",
                "reason": "the variable needs kin degree counts from examination records",
            }
        ]
    )
    validate_claim_links(claims, registry, mapping)

    broken = mapping.copy()
    broken.loc[0, "literature_id"] = "LIT-9999"
    with pytest.raises(ValueError, match="unknown literature_id"):
        validate_claim_links(claims, registry, broken)


def test_claim_map_rejects_unknown_testability() -> None:
    row = {
        "claim_id": "C-01",
        "lit_claim_id": "LC-001",
        "literature_id": "LIT-0001",
        "claim": "lineage resources mattered",
        "v03_variable": "direct_3g_degree_count",
        "variable_role": "exposure",
        "candidate_source": "tongnianchilu",
        "testability": "PROBABLY_FINE",
        "required_source_capability": "kin extraction",
        "reason": "because the source should have it",
    }
    with pytest.raises(pandera.errors.SchemaErrors):
        validate_lit_table("claim_source_map", pd.DataFrame([row]))


def test_core_paper_without_a_digest_is_rejected() -> None:
    registry = pd.DataFrame([_registry_row()])
    with pytest.raises(ValueError, match="core papers without a digest"):
        validate_core_have_digests(registry, set())
    validate_core_have_digests(registry, {"LIT-0001"})


def test_digest_must_carry_every_required_section() -> None:
    complete = "\n".join(DIGEST_SECTIONS)
    validate_digest_sections(complete, DIGEST_SECTIONS)
    with pytest.raises(ValueError, match="missing sections"):
        validate_digest_sections(complete.replace("## LIMITATIONS", "## NOTES"), DIGEST_SECTIONS)


# ------------------------------------------------------------------ feasibility schema


def test_feasibility_row_validates() -> None:
    validate_lit_table("source_feasibility", _frames())


def test_access_type_must_be_in_the_declared_enum() -> None:
    with pytest.raises(pandera.errors.SchemaErrors):
        validate_lit_table("source_feasibility", _frames(access_type="MAYBE_AVAILABLE"))


def test_machine_access_claim_needs_probe_evidence() -> None:
    with pytest.raises(pandera.errors.SchemaErrors):
        validate_lit_table(
            "source_feasibility",
            _frames(access_type="PUBLIC_STRUCTURED", probe_url=None, probe_http_status=None),
        )


def test_bulk_download_requires_local_cache_permission() -> None:
    with pytest.raises(pandera.errors.SchemaErrors):
        validate_lit_table(
            "source_feasibility", _frames(bulk_available=True, local_cache_allowed=False)
        )


def test_probe_observation_must_be_recorded() -> None:
    with pytest.raises(pandera.errors.SchemaErrors):
        validate_lit_table("source_feasibility", _frames(probe_observed="n/a"))


def test_probe_index_prefers_a_reachable_response() -> None:
    records = [
        {
            "source": "x",
            "url": "https://example.org/root",
            "status_code": 503,
            "content_type": "text/html",
            "bytes": 100,
            "snippet": "maintenance",
            "outcome": "ok",
            "method": "GET",
            "checked_at": "2026-09-27",
        },
        {
            "source": "x",
            "url": "https://example.org/api",
            "status_code": 200,
            "content_type": "application/json",
            "bytes": 20,
            "snippet": "{}",
            "outcome": "ok",
            "method": "GET",
            "checked_at": "2026-09-27",
        },
    ]
    assert probe_index(records)["x"]["url"] == "https://example.org/api"


def test_probe_config_has_no_bypass_targets() -> None:
    config = load_probe_config()
    assert {"bypass_login_or_captcha", "bulk_download", "paywall_access"} <= set(
        config["forbidden_actions"]
    )
    for entry in config["probes"]:
        assert not any(
            token in entry["url"].lower() for token in ("login", "signin", "captcha", "paywall")
        )


# ------------------------------------------------------------------ plan rules


def test_plan_requires_predicate_matches_on_declared_fields() -> None:
    row = pd.Series(_feasibility_row())
    assert _matches(row, {"plan_role": "exposure_side", "any_of": ["bulk_available"]})
    assert not _matches(row, {"plan_role": "outcome_side"})
    assert not _matches(row, {"access_type": "PUBLIC_SCAN"})
    assert _matches(row, {"kin_population_coverage": "HIGH"})
    assert not _matches(row, {"kin_population_coverage": "LOW"})
    assert not _matches(row, {"all_of": ["api_available"]})


def test_broad_kin_structured_exposure_selects_plan_a() -> None:
    selection = select_default_plan(_frames())
    assert selection["selected_plan"] == "PLAN_A"
    assert selection["rule_id"] == "exposure_side_broad_kin_high_coverage_structured"
    assert selection["supporting_sources"] == ["test_source"]


def test_thin_kin_coverage_does_not_earn_plan_a() -> None:
    """A structured source with sparse kin over the target population is not PLAN_A."""
    selection = select_default_plan(
        _frames(kin_scope_grade="PARTIAL", kin_population_coverage="LOW")
    )
    assert selection["selected_plan"] == "PLAN_C"
    assert selection["rule_id"] == "fallback"


def test_small_kin_scope_never_reaches_plan_a_or_b() -> None:
    selection = select_default_plan(
        _frames(kin_scope_grade="NARROW", kin_population_coverage="LOW")
    )
    assert selection["rule_id"] == "fallback"


def test_bulk_scan_exposure_selects_plan_b() -> None:
    selection = select_default_plan(
        _frames(
            access_type="PUBLIC_SCAN",
            bulk_available=True,
            machine_readable=False,
            scan_available=True,
        )
    )
    assert selection["selected_plan"] == "PLAN_B"
    assert selection["rule_id"] == "exposure_side_broad_or_partial_covered_bulk"


def test_ui_only_exposure_selects_plan_c() -> None:
    selection = select_default_plan(
        _frames(
            access_type="PUBLIC_UI_ONLY",
            bulk_available=False,
            api_available=False,
            machine_readable=False,
        )
    )
    assert selection["selected_plan"] == "PLAN_C"
    assert selection["rule_id"] == "exposure_side_ui_or_request_only"


def test_outcome_side_availability_never_drives_the_plan() -> None:
    selection = select_default_plan(
        _frames(plan_role="outcome_side", access_type="PUBLIC_STRUCTURED", bulk_available=True)
    )
    assert selection["selected_plan"] == "PLAN_C"
    assert selection["rule_id"] == "fallback"
    assert selection["supporting_sources"] == []


def test_plan_rules_are_ordered_and_point_at_declared_plans() -> None:
    plan = load_plan_rules()
    rules = plan["plan_selection"]["rules"]
    assert [rule["id"] for rule in rules][:4] == [
        "exposure_side_broad_kin_high_coverage_structured",
        "exposure_side_broad_or_partial_covered_bulk",
        "exposure_side_scan_bulk",
        "exposure_side_ui_or_request_only",
    ]
    assert set(plan["plans"]) == {"PLAN_A", "PLAN_B", "PLAN_C"}


def test_catalog_sources_are_declared_and_unique() -> None:
    catalog = load_catalog()
    names = [entry["source_name"] for entry in catalog["sources"]]
    assert names == sorted(set(names)) or len(names) == len(set(names))
    for entry in catalog["sources"]:
        assert entry["access_type"]
        assert entry["zh_name"]


# ------------------------------------------------------------------ claim → source map


def test_testability_follows_probed_access_not_opinion() -> None:
    from qing_elite.v03.lit.claim_map import testability_for

    structured_broad = _frames(kin_population_coverage="HIGH").iloc[0]
    structured_thin = _frames(kin_population_coverage="LOW").iloc[0]
    scan_bulk = _frames(access_type="PUBLIC_SCAN", machine_readable=False).iloc[0]
    gated = _frames(access_type="ACCESS_REQUEST_REQUIRED").iloc[0]
    unreachable = _frames(access_type="UNAVAILABLE").iloc[0]

    assert testability_for("exposure", structured_broad) == "TESTABLE_NOW"
    assert testability_for("exposure", structured_thin) == "NOT_TESTABLE_IN_V03"
    assert testability_for("exposure", scan_bulk) == "TESTABLE_WITH_OCR"
    assert testability_for("exposure", gated) == "TESTABLE_WITH_ACCESS_REQUEST"
    assert testability_for("exposure", unreachable) == "TESTABLE_WITH_ACCESS_REQUEST"
    assert testability_for("exposure", None) == "NOT_TESTABLE_IN_V03"


def test_outcome_testability_ignores_kin_coverage() -> None:
    from qing_elite.v03.lit.claim_map import testability_for

    structured_no_kin = _frames(kin_scope_grade="NONE", kin_population_coverage="NONE").iloc[0]
    assert testability_for("outcome", structured_no_kin) == "TESTABLE_NOW"


def test_variable_map_covers_every_research_variable() -> None:
    from qing_elite.v03.design import load_research_contract
    from qing_elite.v03.lit.claim_map import load_variable_map

    contract = load_research_contract()
    declared = set(contract["exposure"]["primary"]) | set(contract["outcome"]["primary"])
    mapped = set(load_variable_map()["variables"])
    assert declared <= mapped, f"V0.3 primary variables missing from the map: {declared - mapped}"


def test_claim_map_joins_claim_to_sources() -> None:
    from qing_elite.v03.lit.claim_map import build_claim_map, load_variable_map

    claims = pd.DataFrame(
        [
            {
                "lit_claim_id": "LC-001",
                "literature_id": "LIT-0001",
                "claim": "Kin degrees cluster within families.",
                "v03_variables": "direct_3g_degree_count",
            }
        ]
    )
    variable = "direct_3g_degree_count"
    sources = load_variable_map()["variables"][variable]["candidate_sources"]
    feasibility = _feasibility_frame(
        [
            _feasibility_row(source_name=name, zh_name=name)
            for name in sources
        ]
    )
    mapping = build_claim_map(claims, feasibility, load_variable_map())
    assert list(mapping["candidate_source"]) == sources
    assert set(mapping["testability"]) == {"TESTABLE_NOW"}
    assert set(mapping["variable_role"]) == {"exposure"}
    assert list(mapping["claim_id"]) == [f"C-{index:03d}" for index in range(1, len(sources) + 1)]


def test_claim_map_rejects_undeclared_variables() -> None:
    from qing_elite.v03.lit.claim_map import ClaimMapError, build_claim_map, load_variable_map

    claims = pd.DataFrame(
        [
            {
                "lit_claim_id": "LC-001",
                "literature_id": "LIT-0001",
                "claim": "Something invented.",
                "v03_variables": "made_up_variable",
            }
        ]
    )
    with pytest.raises(ClaimMapError, match="not declared"):
        build_claim_map(claims, _frames(), load_variable_map())
