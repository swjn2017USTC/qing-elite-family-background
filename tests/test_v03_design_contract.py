"""V0.3 design-contract tests (U03R skeleton).

These tests pin the *research* reset, not the code: family background is the exposure,
the career trajectory is the outcome, the kin scope covers the extended kin group, and
human review is risk-based with absolute budgets instead of a fixed percentage sample.
"""

from __future__ import annotations

import copy

import pytest

from qing_elite.v03.design import (
    DesignContractError,
    load_relation_ontology,
    load_research_contract,
    load_review_policy,
    load_source_priority,
    relation_index,
    validate_relation_ontology,
    validate_research_contract,
    validate_review_policy,
    validate_source_priority,
)
from qing_elite.v03.gate import run_gate


@pytest.fixture()
def contract() -> dict:
    return copy.deepcopy(load_research_contract())


@pytest.fixture()
def policy() -> dict:
    return copy.deepcopy(load_review_policy())


# --------------------------------------------------------------- research contract


def test_family_background_is_the_exposure(contract) -> None:
    assert contract["exposure"]["role"] == "independent_variable"
    assert "direct_3g_office_count" in contract["exposure"]["primary"]
    assert "senior_collateral_office_count" in contract["exposure"]["primary"]


def test_career_trajectory_is_the_outcome(contract) -> None:
    outcome = contract["outcome"]
    assert outcome["role"] == "dependent_variable"
    for name in ("highest_office_rank", "highest_admin_level", "central_local_route"):
        assert name in outcome["primary"]
    # the v0.2 tier survives only as legacy/extension framing
    assert "highest_tier" in outcome["legacy_only"]
    assert not set(outcome["legacy_only"]) & set(outcome["primary"])


def test_kin_scope_covers_direct_and_extended_kin(contract, tmp_path) -> None:
    scope = contract["exposure"]["kin_scope"]
    assert scope["direct_line"] == ["father", "grandfather", "great_grandfather"]
    assert set(scope["extended_kin"]) >= {"uncle", "great_uncle", "brother", "cousin"}


def test_claim_is_descriptive_only(contract) -> None:
    assert contract["claim_level"] == "descriptive_association"
    assert contract["identification"] == "none"
    assert "causal" in " ".join(contract["estimands"]["not_estimable"])


def test_causal_claim_is_rejected(contract) -> None:
    contract["claim_level"] = "causal_effect"
    with pytest.raises(DesignContractError, match="claim_level"):
        validate_research_contract(contract)


def test_tier_may_not_be_a_primary_outcome(contract) -> None:
    contract["outcome"]["primary"].append("highest_tier")
    with pytest.raises(DesignContractError, match="legacy outcomes may not be primary"):
        validate_research_contract(contract)


def test_sampling_on_the_outcome_is_rejected(contract) -> None:
    contract["sampling"]["forbidden"] = []
    with pytest.raises(DesignContractError, match="sampling.forbidden"):
        validate_research_contract(contract)

    contract = copy.deepcopy(load_research_contract())
    contract["sampling"]["frame_rule"] = "final_office_tier"
    with pytest.raises(DesignContractError, match="source_frame"):
        validate_research_contract(contract)


def test_documented_office_by_tier_may_not_be_the_primary_estimand(contract) -> None:
    contract["estimands"]["primary"] = (
        "P(documented ancestor office = 1 | tier): the v0.2 documentation rate"
    )
    with pytest.raises(DesignContractError, match="must not be the v0.2"):
        validate_research_contract(contract)


def test_unknown_may_not_be_reported_as_zero(contract) -> None:
    contract["reporting"]["unknown_is_not_zero"] = False
    with pytest.raises(DesignContractError, match="unknown_is_not_zero"):
        validate_research_contract(contract)


def test_llm_may_not_emit_a_commoner_judgement(contract) -> None:
    contract["exposure"]["forbidden_outputs"] = ["something_else"]
    with pytest.raises(DesignContractError, match="is_commoner"):
        validate_research_contract(contract)


# --------------------------------------------------------------- relation ontology


def test_ontology_definitional_relations() -> None:
    index = relation_index()
    assert index["father"]["generation_delta"] == 1
    assert index["grandfather"]["generation_delta"] == 2
    assert index["great_grandfather"]["generation_delta"] == 3
    assert index["son"]["generation_delta"] == -1
    assert index["uncle_paternal"]["class"] == "senior_collateral"
    assert index["cousin_paternal"]["class"] == "same_generation"


def test_relation_class_must_be_declared() -> None:
    ontology = copy.deepcopy(load_relation_ontology())
    ontology["relations"][0]["class"] = "invented_class"
    with pytest.raises(DesignContractError, match="undeclared class"):
        validate_relation_ontology(ontology)


def test_generation_sign_must_match_the_class() -> None:
    ontology = copy.deepcopy(load_relation_ontology())
    father = next(entry for entry in ontology["relations"] if entry["code"] == "father")
    father["generation_delta"] = -1
    with pytest.raises(DesignContractError, match="positive generation_delta"):
        validate_relation_ontology(ontology)


def test_duplicate_relation_code_is_rejected() -> None:
    ontology = copy.deepcopy(load_relation_ontology())
    ontology["relations"].append(dict(ontology["relations"][0]))
    with pytest.raises(DesignContractError, match="duplicate relation code"):
        validate_relation_ontology(ontology)


def test_extended_kin_metrics_are_declared() -> None:
    families = load_relation_ontology()["metric_families"]
    assert families["senior_collateral"] == [
        "senior_collateral_degree_count",
        "senior_collateral_office_count",
        "all_senior_elite_kin_count",
    ]
    assert set(families["new_entrant_definitions"]) == {
        "direct_line_new_entrant",
        "extended_family_new_entrant",
    }


# --------------------------------------------------------------- review policy


def test_review_is_risk_based_with_absolute_budgets(policy) -> None:
    assert policy["principle"] == "rule_first_machine_second_human_residual"
    assert policy["tiers"]["LOW"]["route"] == "auto_accept"
    assert policy["tiers"]["MEDIUM"]["route"] == "machine_adjudicate"
    assert policy["tiers"]["HIGH"]["route"] == "human_review"
    assert policy["budgets"]["unit"] == "absolute_count"
    assert isinstance(policy["budgets"]["manual_assertion_review_target"], int)


def test_fixed_percentage_review_is_forbidden(policy) -> None:
    assert "fixed_percentage_sample" in policy["forbidden_review_modes"]
    policy["forbidden_review_modes"] = []
    with pytest.raises(DesignContractError, match="must forbid"):
        validate_review_policy(policy)


def test_percentage_style_budget_is_rejected(policy) -> None:
    policy["budgets"]["manual_review_share"] = 0.1
    with pytest.raises(DesignContractError, match="share"):
        validate_review_policy(policy)


def test_human_review_cannot_be_the_default_route(policy) -> None:
    policy["tiers"]["LOW"]["route"] = "human_review"
    with pytest.raises(DesignContractError, match="LOW risk must auto-accept"):
        validate_review_policy(policy)


def test_automation_order_starts_with_rules_and_ends_with_humans(policy) -> None:
    assert policy["automation_order"][:2] == ["deterministic_rule", "machine_verifier"]
    assert policy["automation_order"][-1] == "human_review"
    policy["automation_order"] = ["llm_second_pass", "human_review"]
    with pytest.raises(DesignContractError, match="automation_order"):
        validate_review_policy(policy)


def test_component_weights_must_sum_to_one(policy) -> None:
    policy["components"]["rare_relation"]["weight"] = 0.5
    with pytest.raises(DesignContractError, match="sum to 1.0"):
        validate_review_policy(policy)


def test_tier_thresholds_must_increase(policy) -> None:
    policy["tiers"]["MEDIUM"]["max_score"] = 0.2
    with pytest.raises(DesignContractError, match="strictly increase"):
        validate_review_policy(policy)


# --------------------------------------------------------------- sources


def test_no_source_is_assumed_downloadable() -> None:
    priority = load_source_priority()
    assert priority["assumption_rule"] == "no_source_is_assumed_downloadable_before_probe"
    assert all(entry["probe_status"] == "pending" for entry in priority["candidates"])


def test_a_source_claiming_availability_before_probe_is_rejected() -> None:
    priority = copy.deepcopy(load_source_priority())
    priority["candidates"][0]["probe_status"] = "PUBLIC_STRUCTURED"
    with pytest.raises(DesignContractError, match="until U04R probes it"):
        validate_source_priority(priority)


def test_exposure_and_outcome_sources_are_kept_apart() -> None:
    split = load_source_priority()["role_split"]
    assert "cgedq_jsl" in split["outcome_side"]
    assert "cgedq_jsl" not in split["exposure_side"]


# --------------------------------------------------------------- gate


def test_u03r_gate_passes_without_running_the_suite() -> None:
    record = run_gate(with_tests=False)
    assert record["gate"] == "PASS", record["failures"]
    assert record["checks"]["tier_is_outcome"]["status"] == "PASS"
    assert record["checks"]["unknown_is_not_negative"]["status"] == "PASS"
    assert record["checks"]["risk_based_review_without_fixed_percentage"]["status"] == "PASS"
    assert record["checks"]["legacy_d_layer_is_not_primary_estimand"]["status"] == "PASS"
    assert record["checks"]["kin_scope_direct_and_extended"]["status"] == "PASS"
