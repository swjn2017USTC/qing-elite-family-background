"""V0.3 design-contract loaders and validators (U03R).

V0.3 turns the v0.2 research design around: family background becomes the exposure, the
career trajectory becomes the outcome, and the kin scope widens from the direct three
generations to the extended paternal kin group. The machine-readable side of that design
lives in ``config/v03/*.yaml``; this module loads it and refuses configurations that
break the contract:

* ``research.yaml`` must declare a descriptive association with no causal identification,
  a non-empty exposure and outcome, and the v0.2 tier as a *legacy* outcome only;
* ``relations.yaml`` must cover both the direct line and the extended kin classes, and
  every relation's generation sign must agree with its class;
* ``review_policy.yaml`` must be risk-based with absolute human budgets, must not contain
  a fixed-percentage review mode, and its tier routes must be unambiguous;
* ``source_priority.yaml`` must not assume that any source is downloadable before the
  U04R feasibility probe.

Nothing here computes research quantities; it only guards the design.
"""

from __future__ import annotations

import functools
import re
from pathlib import Path
from typing import Any

import yaml

from qing_elite.utils.config import PROJECT_ROOT

CONFIG_V03_DIR = PROJECT_ROOT / "config" / "v03"
RESEARCH_YAML = CONFIG_V03_DIR / "research.yaml"
RELATIONS_YAML = CONFIG_V03_DIR / "relations.yaml"
REVIEW_POLICY_YAML = CONFIG_V03_DIR / "review_policy.yaml"
SOURCE_PRIORITY_YAML = CONFIG_V03_DIR / "source_priority.yaml"

REQUIRED_RELATION_CLASSES = ("direct_line", "senior_collateral")
REQUIRED_RISK_TIERS = ("LOW", "MEDIUM", "HIGH")
EXPECTED_PRINCIPLE = "rule_first_machine_second_human_residual"
FIXED_PERCENTAGE_MODE = "fixed_percentage_sample"

#: Keys that would reintroduce a fixed-share human review policy.
PERCENTAGE_KEY_PATTERN = re.compile(r"percent|share|ratio|fraction|pct", re.IGNORECASE)

#: Everything a V0.3 analysis must keep apart from the v0.2 tier framing.
FORBIDDEN_SAMPLING_RULES = ("sampling_on_outcome_tier", "oversampling_by_final_office")


class DesignContractError(ValueError):
    """Raised when a ``config/v03`` file breaks the V0.3 research contract."""


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


@functools.lru_cache(maxsize=None)
def load_research_contract(path: Path | None = None) -> dict[str, Any]:
    """Load and validate ``config/v03/research.yaml``."""
    contract = _load_yaml(path or RESEARCH_YAML)
    validate_research_contract(contract)
    return contract


@functools.lru_cache(maxsize=None)
def load_relation_ontology(path: Path | None = None) -> dict[str, Any]:
    """Load and validate ``config/v03/relations.yaml``."""
    ontology = _load_yaml(path or RELATIONS_YAML)
    validate_relation_ontology(ontology)
    return ontology


@functools.lru_cache(maxsize=None)
def load_review_policy(path: Path | None = None) -> dict[str, Any]:
    """Load and validate ``config/v03/review_policy.yaml``."""
    policy = _load_yaml(path or REVIEW_POLICY_YAML)
    validate_review_policy(policy)
    return policy


@functools.lru_cache(maxsize=None)
def load_source_priority(path: Path | None = None) -> dict[str, Any]:
    """Load and validate ``config/v03/source_priority.yaml``."""
    priority = _load_yaml(path or SOURCE_PRIORITY_YAML)
    validate_source_priority(priority)
    return priority


def relation_index(ontology: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    """Return ``relation_code -> relation`` for the versioned ontology."""
    ontology = ontology or load_relation_ontology()
    return {entry["code"]: entry for entry in ontology["relations"]}


def relation_codes(ontology: dict[str, Any] | None = None) -> frozenset[str]:
    return frozenset(relation_index(ontology))


def direct_line_codes() -> tuple[str, ...]:
    return tuple(
        code for code, entry in relation_index().items() if entry["class"] == "direct_line"
    )


def senior_collateral_codes() -> tuple[str, ...]:
    return tuple(
        code
        for code, entry in relation_index().items()
        if entry["class"] == "senior_collateral"
    )


# --------------------------------------------------------------------------- research


def validate_research_contract(contract: dict[str, Any]) -> None:
    """Raise :class:`DesignContractError` unless the research contract holds."""
    if contract.get("claim_level") != "descriptive_association":
        raise DesignContractError(
            "research.yaml claim_level must be 'descriptive_association'"
        )
    if contract.get("identification") != "none":
        raise DesignContractError("research.yaml identification must be 'none'")

    exposure = contract.get("exposure") or {}
    outcome = contract.get("outcome") or {}
    if exposure.get("role") != "independent_variable":
        raise DesignContractError("exposure must be the independent variable")
    if outcome.get("role") != "dependent_variable":
        raise DesignContractError("outcome must be the dependent variable")
    if not exposure.get("primary"):
        raise DesignContractError("exposure.primary must not be empty")
    if not outcome.get("primary"):
        raise DesignContractError("outcome.primary must not be empty")

    kin_scope = exposure.get("kin_scope") or {}
    if list(kin_scope.get("direct_line") or [])[:3] != ["father", "grandfather", "great_grandfather"]:
        raise DesignContractError(
            "exposure.kin_scope.direct_line must start with father/grandfather/great_grandfather"
        )
    if not kin_scope.get("extended_kin"):
        raise DesignContractError("exposure.kin_scope.extended_kin must not be empty")

    # tier must be an outcome of the v0.2 lineage, never a V0.3 primary outcome
    if "highest_tier" not in (outcome.get("legacy_only") or []):
        raise DesignContractError("outcome.legacy_only must contain highest_tier")
    overlap = set(outcome.get("legacy_only") or []) & set(outcome.get("primary") or [])
    if overlap:
        raise DesignContractError(f"legacy outcomes may not be primary: {sorted(overlap)}")

    # the v0.2 D-layer documentation-rate contrast must not be the primary estimand
    estimands = contract.get("estimands") or {}
    for key in ("primary", "secondary", "legacy_benchmark"):
        if not estimands.get(key):
            raise DesignContractError(f"estimands.{key} must be declared")
    if "tier" not in str(estimands["legacy_benchmark"]):
        raise DesignContractError(
            "estimands.legacy_benchmark must name the demoted v0.2 tier comparison"
        )
    if "tier" in str(estimands["primary"]) and "documented ancestor office" in str(
        estimands["primary"]
    ):
        raise DesignContractError(
            "estimands.primary must not be the v0.2 documented-ancestor-office-by-tier rate"
        )

    sampling = contract.get("sampling") or {}
    if sampling.get("frame_rule") != "source_frame":
        raise DesignContractError("sampling.frame_rule must be 'source_frame'")
    missing = set(FORBIDDEN_SAMPLING_RULES) - set(sampling.get("forbidden") or [])
    if missing:
        raise DesignContractError(
            f"sampling.forbidden must list {sorted(FORBIDDEN_SAMPLING_RULES)}; missing {sorted(missing)}"
        )
    if sampling.get("unit") != "person":
        raise DesignContractError("sampling.unit must be 'person'")

    reporting = contract.get("reporting") or {}
    if reporting.get("unknown_is_not_zero") is not True:
        raise DesignContractError("reporting.unknown_is_not_zero must be true")
    if reporting.get("missing_is_not_commoner") is not True:
        raise DesignContractError("reporting.missing_is_not_commoner must be true")
    if not exposure.get("forbidden_outputs"):
        raise DesignContractError("exposure.forbidden_outputs must name the judgments the LLM may not make")
    if "is_commoner" not in exposure["forbidden_outputs"]:
        raise DesignContractError("exposure.forbidden_outputs must include is_commoner")


# --------------------------------------------------------------------------- relations


def validate_relation_ontology(ontology: dict[str, Any]) -> None:
    """Raise unless every relation is classed, signed and unique."""
    classes = ontology.get("classes") or {}
    for name in REQUIRED_RELATION_CLASSES:
        if name not in classes:
            raise DesignContractError(f"relations.yaml must define the '{name}' class")

    relations = ontology.get("relations") or []
    if not relations:
        raise DesignContractError("relations.yaml must define relations")

    seen: set[str] = set()
    for entry in relations:
        code = entry.get("code")
        if not code:
            raise DesignContractError("every relation needs a code")
        if code in seen:
            raise DesignContractError(f"duplicate relation code: {code}")
        seen.add(code)
        klass = entry.get("class")
        if klass not in classes:
            raise DesignContractError(f"relation {code} uses undeclared class {klass!r}")
        delta = entry.get("generation_delta")
        if not isinstance(delta, int):
            raise DesignContractError(f"relation {code} needs an integer generation_delta")
        sign = classes[klass].get("generation_delta_sign")
        if sign == "positive" and delta <= 0:
            raise DesignContractError(f"relation {code} must have a positive generation_delta")
        if sign == "zero" and delta != 0:
            raise DesignContractError(f"relation {code} must have generation_delta 0")
        if sign == "negative" and delta >= 0:
            raise DesignContractError(f"relation {code} must have a negative generation_delta")

    for code, expected in (
        ("father", 1),
        ("grandfather", 2),
        ("great_grandfather", 3),
        ("son", -1),
    ):
        index = relation_index(ontology)
        if code not in index:
            raise DesignContractError(f"relations.yaml must define '{code}'")
        if index[code]["generation_delta"] != expected:
            raise DesignContractError(f"relation '{code}' must have generation_delta {expected}")

    families = ontology.get("metric_families") or {}
    for key in ("direct_line", "senior_collateral", "new_entrant_definitions"):
        if not families.get(key):
            raise DesignContractError(f"metric_families.{key} must be declared")


# --------------------------------------------------------------------------- review


def validate_review_policy(policy: dict[str, Any]) -> None:
    """Raise unless the review policy is risk-based and percentage-free."""
    if policy.get("principle") != EXPECTED_PRINCIPLE:
        raise DesignContractError(
            f"review_policy principle must be '{EXPECTED_PRINCIPLE}'"
        )
    forbidden = policy.get("forbidden_review_modes") or []
    if FIXED_PERCENTAGE_MODE not in forbidden:
        raise DesignContractError(
            f"review_policy must forbid '{FIXED_PERCENTAGE_MODE}'"
        )

    order = policy.get("automation_order") or []
    if order[:2] != ["deterministic_rule", "machine_verifier"] or order[-1] != "human_review":
        raise DesignContractError(
            "automation_order must start with rule + machine verification and end with human_review"
        )

    components = policy.get("components") or {}
    if not components:
        raise DesignContractError("review_policy must declare risk components")
    total_weight = 0.0
    for name, spec in components.items():
        weight = spec.get("weight")
        if not isinstance(weight, (int, float)) or not 0 <= weight <= 1:
            raise DesignContractError(f"risk component {name} needs a weight in [0, 1]")
        total_weight += float(weight)
    if abs(total_weight - 1.0) > 1e-9:
        raise DesignContractError(f"risk component weights must sum to 1.0, got {total_weight}")

    tiers = policy.get("tiers") or {}
    if tuple(tiers) != REQUIRED_RISK_TIERS:
        raise DesignContractError(
            f"review_policy tiers must be exactly {REQUIRED_RISK_TIERS}, got {tuple(tiers)}"
        )
    previous = 0.0
    for name in REQUIRED_RISK_TIERS:
        bound = tiers[name].get("max_score")
        if not isinstance(bound, (int, float)) or not 0 < bound <= 1:
            raise DesignContractError(f"tier {name} needs a max_score in (0, 1]")
        if bound <= previous:
            raise DesignContractError("tier thresholds must strictly increase")
        previous = float(bound)
    if tiers["LOW"].get("route") != "auto_accept":
        raise DesignContractError("LOW risk must auto-accept")
    if tiers["HIGH"].get("route") != "human_review":
        raise DesignContractError("HIGH risk must route to human review")
    routes = policy.get("routes") or {}
    for name in ("auto_accept", "machine_adjudicate", "human_review"):
        if name not in routes:
            raise DesignContractError(f"review_policy routes must define '{name}'")

    budgets = policy.get("budgets") or {}
    if budgets.get("unit") != "absolute_count":
        raise DesignContractError("human review budgets must be absolute counts, not shares")
    for name in ("manual_link_new_target", "manual_assertion_review_target"):
        value = budgets.get(name)
        if not isinstance(value, int) or value < 0:
            raise DesignContractError(f"budgets.{name} must be a non-negative integer")

    _reject_percentage_keys(policy)


def _reject_percentage_keys(policy: dict[str, Any], prefix: str = "") -> None:
    for key, value in policy.items():
        path = f"{prefix}{key}"
        if isinstance(key, str) and PERCENTAGE_KEY_PATTERN.search(key):
            raise DesignContractError(
                f"review_policy may not express human review as a share ({path})"
            )
        if isinstance(value, dict):
            _reject_percentage_keys(value, prefix=f"{path}.")


# --------------------------------------------------------------------------- sources


def validate_source_priority(priority: dict[str, Any]) -> None:
    """Raise unless every candidate source is explicitly unprobed and typed."""
    if priority.get("assumption_rule") != "no_source_is_assumed_downloadable_before_probe":
        raise DesignContractError(
            "source_priority must forbid assuming a source is downloadable before probing"
        )
    access_types = set(priority.get("access_types") or [])
    expected = {
        "PUBLIC_STRUCTURED",
        "PUBLIC_SCAN",
        "PUBLIC_UI_ONLY",
        "ACCESS_REQUEST_REQUIRED",
        "PAPER_TABLE_ONLY",
        "UNAVAILABLE",
    }
    if access_types != expected:
        raise DesignContractError(f"access_types must be exactly {sorted(expected)}")
    candidates = priority.get("candidates") or []
    if not candidates:
        raise DesignContractError("source_priority must list candidate sources")
    tiers = priority.get("tiers") or {}
    for entry in candidates:
        name = entry.get("name")
        if entry.get("tier") not in tiers:
            raise DesignContractError(f"candidate {name} uses an undeclared tier")
        if entry.get("probe_status") != priority.get("default_probe_status"):
            raise DesignContractError(
                f"candidate {name} must stay '{priority.get('default_probe_status')}' until U04R probes it"
            )
    split = priority.get("role_split") or {}
    for key in ("exposure_side", "outcome_side"):
        if not split.get(key):
            raise DesignContractError(f"role_split.{key} must be declared")
