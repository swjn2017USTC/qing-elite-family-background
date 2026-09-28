"""U03R stage gate: the V0.3 research reset, machine-checked.

``python -m qing_elite.v03.gate`` verifies the six U03R gate conditions named in the V0.3
plan and writes ``audit/v03/u03r_gate.json``. It exits non-zero on any FAIL, so a stage
cannot be declared complete by prose alone.

The gate is deliberately about the *design*, not about data: U03R produces no research
quantities.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import pandera.errors

from qing_elite.utils.config import PROJECT_ROOT
from qing_elite.v03 import SCHEMA_VERSION
from qing_elite.v03.contracts import (
    CAREER_EVENTS_SCHEMA,
    PERSONS_SCHEMA,
    SCHEMAS,
    V03_TABLES,
    validate_table,
)
from qing_elite.v03.design import (
    DesignContractError,
    load_relation_ontology,
    load_research_contract,
    load_review_policy,
    load_source_priority,
    validate_review_policy,
)

BASELINE_REF = "v0.2-u03-frozen-20260927"
GATE_OUT = PROJECT_ROOT / "audit" / "v03" / "u03r_gate.json"
REQUIRED_DIRS = (
    "config/v03",
    "data/processed_v03",
    "data/interim_v03",
    "audit/v03",
    "reports/upgrade_v03",
    "src/qing_elite/v03",
)
#: Paths that hold the frozen v0.2 study; U03R must not modify any of them.
V02_FROZEN_PATHS = (
    "data/processed_v02",
    "data/processed",
    "config/v02",
    "config/research.yaml",
    "config/offices.yaml",
    "audit/v02",
    "audit/p07_unknown_coding.csv",
    "reports/upgrade",
    "reports/final",
    "src/qing_elite/v02",
    "tests/test_u01_contracts.py",
    "tests/test_u02_linkage.py",
    "tests/test_u03_sample_protocol.py",
)
#: Files inside the frozen v0.2 area that a later stage was allowed to touch, each with the
#: reason. The record of what U03R froze stays intact: a change is only tolerated when it is
#: named here, and the check reports excused paths alongside any violation.
V02_FROZEN_EXCEPTIONS = {
    "tests/test_u02_linkage.py": (
        "U11R added the missing-input skip guard; without it a clean checkout fails two tests "
        "for a file the public repository deliberately does not ship"
    ),
}

CONTRACT_TESTS = ("tests/test_v03_contracts.py", "tests/test_v03_design_contract.py")
LITERATURE_TESTS = ("tests/test_v03_literature.py",)
PILOT_TESTS = ("tests/test_v03_pilot.py",)
LINKAGE_TESTS = ("tests/test_v03_linkage.py",)
KIN_TESTS = ("tests/test_v03_kin.py",)
CAREER_TESTS = ("tests/test_v03_career.py",)
ANALYSIS_TESTS = ("tests/test_v03_analysis.py",)
EXTENSION_TESTS = ("tests/test_v03_extension.py",)


def _check_dirs() -> dict[str, Any]:
    missing = [name for name in REQUIRED_DIRS if not (PROJECT_ROOT / name).is_dir()]
    return {
        "status": "FAIL" if missing else "PASS",
        "evidence": {"required": list(REQUIRED_DIRS), "missing": missing},
    }


def _check_v02_untouched() -> dict[str, Any]:
    try:
        head = subprocess.run(
            ["git", "rev-parse", "--verify", BASELINE_REF],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        changed = subprocess.run(
            ["git", "diff", "--name-only", head, "--", *V02_FROZEN_PATHS],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()
    except (subprocess.CalledProcessError, FileNotFoundError) as error:  # pragma: no cover
        return {"status": "SKIP", "evidence": {"reason": f"git unavailable: {error}"}}
    excused = sorted(path for path in changed if path in V02_FROZEN_EXCEPTIONS)
    violations = sorted(path for path in changed if path not in V02_FROZEN_EXCEPTIONS)
    return {
        "status": "FAIL" if violations else "PASS",
        "evidence": {
            "baseline_ref": BASELINE_REF,
            "modified_frozen_paths": changed,
            "excused_paths": {path: V02_FROZEN_EXCEPTIONS[path] for path in excused},
            "violations": violations,
        },
    }


def _check_tables_registered() -> dict[str, Any]:
    registered = tuple(SCHEMAS)
    ok = registered == V03_TABLES and len(SCHEMAS) == 10
    return {
        "status": "PASS" if ok else "FAIL",
        "evidence": {"registered": list(registered), "required": list(V03_TABLES)},
    }


def _check_kin_scope() -> dict[str, Any]:
    ontology = load_relation_ontology()
    families = ontology["metric_families"]
    direct = [entry["code"] for entry in ontology["relations"] if entry["class"] == "direct_line"]
    collateral = [
        entry["code"] for entry in ontology["relations"] if entry["class"] == "senior_collateral"
    ]
    ok = (
        bool(direct)
        and bool(collateral)
        and "kin_edges" in SCHEMAS
        and "all_senior_elite_kin_count" in families["senior_collateral"]
    )
    return {
        "status": "PASS" if ok else "FAIL",
        "evidence": {
            "ontology_version": ontology["ontology_version"],
            "direct_line_relations": direct,
            "senior_collateral_relations": collateral,
            "extended_metrics": families["senior_collateral"],
        },
    }


def _check_unknown_is_not_negative() -> dict[str, Any]:
    """An ``unknown`` assertion carrying a value must be rejected by the schema."""
    frame = pd.DataFrame(
        [
            {
                "assertion_id": "a1",
                "subject_type": "kin_edge",
                "subject_id": "e1",
                "field": "office",
                "assertion_state": "unknown",
                "value_raw": "知縣",  # <- the violation: unknown is not a value
                "value_normalized": None,
                "source_document_id": None,
                "source_locator": None,
                "quote": None,
                "quote_start": None,
                "quote_end": None,
                "extractor": "rule_parser",
                "extractor_version": None,
                "extraction_run_id": None,
                "review_status": "not_required",
                "confidence": None,
                "conflict_group_id": None,
            }
        ]
    )
    try:
        validate_table("evidence_assertions", frame)
    except pandera.errors.SchemaErrors:
        return {
            "status": "PASS",
            "evidence": "unknown assertion carrying value_raw=知縣 was rejected",
        }
    return {
        "status": "FAIL",
        "evidence": "unknown assertion carrying a value passed validation",
    }


def _check_tier_is_outcome() -> dict[str, Any]:
    contract = load_research_contract()
    tier_inputs = {
        "persons": [c for c in PERSONS_SCHEMA.columns if "tier" in c],
        "career_events": [c for c in CAREER_EVENTS_SCHEMA.columns if "tier" in c],
    }
    ok = (
        not any(tier_inputs.values())
        and "highest_tier" in contract["outcome"]["legacy_only"]
        and "highest_tier" not in contract["outcome"]["primary"]
    )
    return {
        "status": "PASS" if ok else "FAIL",
        "evidence": {
            "tier_columns_in_input_tables": tier_inputs,
            "primary_outcomes": contract["outcome"]["primary"],
            "legacy_only": contract["outcome"]["legacy_only"],
        },
    }


def _check_risk_based_review() -> dict[str, Any]:
    policy = load_review_policy()
    tiers = policy["tiers"]
    ok = (
        tiers["HIGH"]["route"] == "human_review"
        and tiers["LOW"]["route"] == "auto_accept"
        and policy["budgets"]["unit"] == "absolute_count"
        and policy["forbidden_review_modes"] == ["fixed_percentage_sample"]
    )
    # a fixed-share policy must be rejected outright
    probe = json.loads(json.dumps(policy))
    probe["budgets"]["manual_review_share"] = 0.1

    try:
        validate_review_policy(probe)
    except DesignContractError:
        rejected = True
    else:
        rejected = False
    ok = ok and rejected
    return {
        "status": "PASS" if ok else "FAIL",
        "evidence": {
            "policy_version": policy["policy_version"],
            "tiers": {name: tiers[name]["route"] for name in tiers},
            "budgets": policy["budgets"],
            "percentage_policy_rejected": rejected,
        },
    }


def _check_d_layer_not_primary() -> dict[str, Any]:
    contract = load_research_contract()
    estimands = contract["estimands"]
    primary = str(estimands["primary"])
    legacy = str(estimands["legacy_benchmark"])
    ok = (
        "documented ancestor office" not in primary
        and "tier" in legacy
        and contract["sampling"]["forbidden"] == [
            "sampling_on_outcome_tier",
            "oversampling_by_final_office",
        ]
        and contract["sampling"]["frame_rule"] == "source_frame"
    )
    return {
        "status": "PASS" if ok else "FAIL",
        "evidence": {
            "primary_estimand": primary.strip(),
            "legacy_benchmark": legacy.strip(),
            "sampling": contract["sampling"],
        },
    }


def _check_contract_tests() -> dict[str, Any]:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", *CONTRACT_TESTS, "-q", "-o", "addopts="],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    tail = (result.stdout or "").strip().splitlines()[-1:] or [""]
    summary = re.sub(r"\s+in\s+[\d.]+s$", "", tail[0])
    return {
        "status": "PASS" if result.returncode == 0 else "FAIL",
        "evidence": {"command": f"pytest {' '.join(CONTRACT_TESTS)}", "summary": summary},
    }


def _check_design_configs() -> dict[str, Any]:
    try:
        load_research_contract()
        load_relation_ontology()
        load_review_policy()
        load_source_priority()
    except DesignContractError as error:
        return {"status": "FAIL", "evidence": str(error)}
    return {
        "status": "PASS",
        "evidence": ["config/v03/research.yaml", "config/v03/relations.yaml",
                     "config/v03/review_policy.yaml", "config/v03/source_priority.yaml"],
    }


# ------------------------------------------------------------------ U04R (literature)


def _check_literature_layout() -> dict[str, Any]:
    required = [
        PROJECT_ROOT / "sources" / "literature" / "registry" / "literature_registry.jsonl",
        PROJECT_ROOT / "sources" / "literature" / "registry" / "README.md",
        PROJECT_ROOT / "sources" / "literature" / "digests",
        PROJECT_ROOT / "sources" / "literature" / "queues",
        PROJECT_ROOT / "config" / "v03" / "literature.yaml",
        PROJECT_ROOT / "reports" / "upgrade_v03" / "literature" / "01_search_plan.md",
        PROJECT_ROOT / "reports" / "upgrade_v03" / "literature" / "02_coverage.yaml",
        PROJECT_ROOT / "reports" / "upgrade_v03" / "literature" / "03_literature_claims.jsonl",
        PROJECT_ROOT / "reports" / "upgrade_v03" / "literature" / "04_historiography.md",
        PROJECT_ROOT / "reports" / "upgrade_v03" / "literature" / "05_claim_delta.md",
        PROJECT_ROOT / "reports" / "upgrade_v03" / "literature" / "06_claim_source_map.csv",
    ]
    missing = [str(path.relative_to(PROJECT_ROOT)) for path in required if not path.exists()]
    return {"status": "FAIL" if missing else "PASS", "evidence": {"missing": missing}}


def _check_literature_pipeline() -> dict[str, Any]:
    from qing_elite.v03.lit.harvest import load_plan
    from qing_elite.v03.lit.registry import (
        CLAIMS_JSONL,
        CLAIM_MAP_CSV,
        FEASIBILITY_PARQUET,
        load_registry,
        read_jsonl,
        validate_pipeline,
    )

    try:
        registry = load_registry()
        claims = pd.DataFrame(read_jsonl(CLAIMS_JSONL))
        mapping = pd.read_csv(CLAIM_MAP_CSV)
        feasibility = pd.read_parquet(FEASIBILITY_PARQUET)
        required_sections = tuple(
            load_plan()["digest_requirements"]["required_sections"]
        )
        validate_pipeline(
            registry=registry,
            claims=claims,
            mapping=mapping,
            feasibility=feasibility,
            required_digest_sections=required_sections,
        )
    except Exception as error:
        return {"status": "FAIL", "evidence": f"{type(error).__name__}: {error}"}
    return {
        "status": "PASS",
        "evidence": {
            "registry_rows": int(len(registry)),
            "core": int((registry["tier"] == "core").sum()),
            "fulltext_verified": int(registry["fulltext_verified"].sum()),
            "claims": int(len(claims)),
            "claim_map_rows": int(len(mapping)),
            "feasibility_rows": int(len(feasibility)),
        },
    }


def _check_probe_evidence() -> dict[str, Any]:
    """Every source must carry observed probe evidence; no source may be assumed."""
    from qing_elite.v03.feasibility import build_feasibility, run_probes

    try:
        records = run_probes(offline=True)
        feasibility = build_feasibility(records)
    except Exception as error:
        return {"status": "FAIL", "evidence": f"{type(error).__name__}: {error}"}
    unreachable = sorted(
        feasibility.loc[feasibility["probe_observed"] == "no probe declared for this source", "source_name"]
    )
    failed = sorted(
        feasibility.loc[
            feasibility["probe_observed"].str.contains("Traceback|ConnectError", regex=True),
            "source_name",
        ]
    )
    status = "PASS" if not unreachable else "FAIL"
    return {
        "status": status,
        "evidence": {
            "sources": int(len(feasibility)),
            "probes_run": int(len(records)),
            "sources_without_probe": unreachable,
            "sources_with_failed_probe": failed,
        },
    }


def _check_plan_selection() -> dict[str, Any]:
    from qing_elite.v03.feasibility import (
        build_feasibility,
        run_probes,
        select_default_plan,
        write_plan,
    )

    try:
        feasibility = build_feasibility(run_probes(offline=True))
        selection = select_default_plan(feasibility)
        write_plan(selection)
    except Exception as error:
        return {"status": "FAIL", "evidence": f"{type(error).__name__}: {error}"}
    ok = selection["selected_plan"] in {"PLAN_A", "PLAN_B", "PLAN_C"} and (
        selection["supporting_sources"] or selection["rule_id"] == "fallback"
    )
    return {
        "status": "PASS" if ok else "FAIL",
        "evidence": {
            "selected_plan": selection["selected_plan"],
            "rule_id": selection["rule_id"],
            "supporting_sources": selection["supporting_sources"],
        },
    }


def _check_no_access_bypass() -> dict[str, Any]:
    """No probe may target a login/captcha/paywall path."""
    from qing_elite.v03.feasibility import load_probe_config

    config = load_probe_config()
    suspicious = [
        entry["url"]
        for entry in config["probes"]
        if re.search(r"login|signin|sign-in|captcha|paywall|auth", entry["url"], re.IGNORECASE)
    ]
    forbidden = config.get("forbidden_actions") or []
    ok = not suspicious and {
        "bypass_login_or_captcha",
        "bulk_download",
        "paywall_access",
    } <= set(forbidden)
    return {
        "status": "PASS" if ok else "FAIL",
        "evidence": {"suspicious_probe_urls": suspicious, "forbidden_actions": forbidden},
    }


def _check_literature_tests() -> dict[str, Any]:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", *LITERATURE_TESTS, "-q", "-o", "addopts="],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    tail = (result.stdout or "").strip().splitlines()[-1:] or [""]
    return {
        "status": "PASS" if result.returncode == 0 else "FAIL",
        "evidence": {
            "command": f"pytest {' '.join(LITERATURE_TESTS)}",
            "summary": re.sub(r"\s+in\s+[\d.]+s$", "", tail[0]),
        },
    }


def _check_pilot_artifacts() -> dict[str, Any]:
    """Frame, extraction, verification and evaluation outputs must all exist and validate."""
    from qing_elite.v03.lit.registry import PROCESSED_V03_DIR
    from qing_elite.v03.pilot.frame import frame_summary, load_frame_config
    from qing_elite.v03.pilot.verifier import RISK_COMPONENT_NAMES

    metrics_path = PROJECT_ROOT / "reports" / "upgrade_v03" / "pilot" / "extraction_metrics.json"
    required = [
        PROCESSED_V03_DIR / "pilot_frame.parquet",
        PROCESSED_V03_DIR / "pilot_risk.parquet",
        PROCESSED_V03_DIR / "pilot_kin_edges.parquet",
        PROCESSED_V03_DIR / "pilot_credentials.parquet",
        PROCESSED_V03_DIR / "pilot_assertions.parquet",
        PROCESSED_V03_DIR / "pilot_review_queue.parquet",
        metrics_path,
        PROJECT_ROOT / "reports" / "upgrade_v03" / "pilot" / "gold_ocr.jsonl",
    ]
    missing = [str(path.relative_to(PROJECT_ROOT)) for path in required if not path.exists()]
    if missing:
        return {"status": "FAIL", "evidence": {"missing": missing}}

    frame = pd.read_parquet(PROCESSED_V03_DIR / "pilot_frame.parquet")
    config = load_frame_config()
    risk = pd.read_parquet(PROCESSED_V03_DIR / "pilot_risk.parquet")
    problems: list[str] = []
    if not 200 <= len(frame) <= 500:
        problems.append(f"pilot size {len(frame)} outside the 200–500 budget")
    if not set(risk["risk_tier"]) <= {"LOW", "MEDIUM", "HIGH"}:
        problems.append("unknown risk tier present")
    missing_components = [name for name in RISK_COMPONENT_NAMES if name not in risk.columns]
    if missing_components:
        problems.append(f"risk components missing: {missing_components}")
    if config["seed"] not in set(frame["seed"].astype(int)):
        problems.append("frame was not built with the configured seed")
    return {
        "status": "FAIL" if problems else "PASS",
        "evidence": {
            "frame": frame_summary(frame),
            "risk_rows": int(len(risk)),
            "problems": problems,
        },
    }


def _check_pilot_gates() -> dict[str, Any]:
    """Source gates must be derived from metrics, and a failing source must be acted on."""
    metrics_path = PROJECT_ROOT / "reports" / "upgrade_v03" / "pilot" / "extraction_metrics.json"
    try:
        metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    except Exception as error:
        return {"status": "FAIL", "evidence": f"{type(error).__name__}: {error}"}
    gates = metrics.get("gates", {})
    if not gates:
        return {"status": "FAIL", "evidence": "no source gates recorded"}
    problems: list[str] = []
    for name, gate in gates.items():
        if str(gate.get("gate", "")).startswith("FAIL") and gate.get("action") not in (
            "drop_source",
            "keep_identity_only",
        ):
            problems.append(f"{name}: failing gate without an action")
        if "metrics" not in gate:
            problems.append(f"{name}: gate without metrics")
    review = metrics.get("ocr_scan_arm", {}).get("evaluation", {})
    if review and review.get("gold_entries", 0) < 5:
        problems.append("gold set too small to support field-level metrics")
    return {
        "status": "FAIL" if problems else "PASS",
        "evidence": {
            "gates": {
                name: {"gate": gate.get("gate"), "action": gate.get("action")}
                for name, gate in gates.items()
            },
            "problems": problems,
        },
    }


def _check_pilot_tests() -> dict[str, Any]:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", *PILOT_TESTS, "-q", "-o", "addopts="],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    tail = (result.stdout or "").strip().splitlines()[-1:] or [""]
    return {
        "status": "PASS" if result.returncode == 0 else "FAIL",
        "evidence": {
            "command": f"pytest {' '.join(PILOT_TESTS)}",
            "summary": re.sub(r"\s+in\s+[\d.]+s$", "", tail[0]),
        },
    }


def _check_linkage_artifacts() -> dict[str, Any]:
    """Linkage artifacts must exist and conform to the V0.3 link/risk/review contracts."""
    from qing_elite.v03.contracts import (
        ENTITY_LINKS_SCHEMA,
        REVIEW_QUEUE_SCHEMA,
        RISK_SCORES_SCHEMA,
        validate_review_routing,
        validate_risk_tiers,
        validate_table,
    )
    from qing_elite.v03.lit.registry import PROCESSED_V03_DIR

    required = [
        PROCESSED_V03_DIR / "entity_links.parquet",
        PROCESSED_V03_DIR / "linkage_features.parquet",
        PROCESSED_V03_DIR / "linkage_risk.parquet",
        PROCESSED_V03_DIR / "linkage_review_queue.parquet",
        PROCESSED_V03_DIR / "linkage_crosswalk.parquet",
        PROJECT_ROOT / "reports" / "upgrade_v03" / "linkage_benchmark.md",
    ]
    missing = [str(path.relative_to(PROJECT_ROOT)) for path in required if not path.exists()]
    if missing:
        return {"status": "FAIL", "evidence": {"missing": missing}}

    links = pd.read_parquet(PROCESSED_V03_DIR / "entity_links.parquet")
    risk = pd.read_parquet(PROCESSED_V03_DIR / "linkage_risk.parquet")
    queue = pd.read_parquet(PROCESSED_V03_DIR / "linkage_review_queue.parquet")
    problems: list[str] = []
    try:
        validate_table("entity_links", links.loc[:, list(ENTITY_LINKS_SCHEMA.columns)])
        validate_table("risk_scores", risk.loc[:, list(RISK_SCORES_SCHEMA.columns)])
        if len(queue):
            validate_table("review_queue", queue.loc[:, list(REVIEW_QUEUE_SCHEMA.columns)])
            validate_risk_tiers(risk)
            validate_review_routing(queue, risk)
    except Exception as error:
        problems.append(f"{type(error).__name__}: {str(error)[:200]}")
    if len(links) and not set(links["decision"]) <= {"auto_accept", "grey", "reject"}:
        problems.append("unknown link decision present")
    return {
        "status": "FAIL" if problems else "PASS",
        "evidence": {
            "links": int(len(links)),
            "decisions": links["decision"].value_counts().to_dict() if len(links) else {},
            "risk_rows": int(len(risk)),
            "review_items": int(len(queue)),
            "problems": problems,
        },
    }


def _check_linkage_crosswalk() -> dict[str, Any]:
    """The legacy↔official crosswalk must be complete and honest about the release."""
    from qing_elite.v03.lit.registry import PROCESSED_V03_DIR
    from qing_elite.v03.linkage.cgedq import (
        crosswalk_summary,
        load_config,
        validate_release,
    )

    crosswalk_path = PROCESSED_V03_DIR / "linkage_crosswalk.parquet"
    if not crosswalk_path.exists():
        return {"status": "FAIL", "evidence": f"missing {crosswalk_path.name}"}
    crosswalk = pd.read_parquet(crosswalk_path)
    try:
        validation = validate_release()
    except Exception as error:
        return {"status": "FAIL", "evidence": f"{type(error).__name__}: {error}"}
    problems: list[str] = []
    if not validation["matches_v01_frozen_hash"]:
        problems.append("release hash no longer matches the frozen v0.1 value")
    if crosswalk["official_person_id"].isna().any():
        problems.append("some legacy ids have no official person id")
    if not set(crosswalk["relation"]) <= {"same_id", "merged_by_v02", "absent"}:
        problems.append("unknown crosswalk relation value")
    if "person_id" not in load_config()["cgedq"]["official_id_column"]:
        problems.append("official id column misconfigured")
    return {
        "status": "FAIL" if problems else "PASS",
        "evidence": {
            "release": {
                "sha256_matches_frozen": validation["matches_v01_frozen_hash"],
                "records": validation["records"],
                "distinct_person_ids": validation["distinct_person_ids"],
                "records_without_person_id": validation["records_without_person_id"],
                "persons_in_multiple_periods": validation["persons_in_multiple_periods"],
            },
            "crosswalk": crosswalk_summary(crosswalk),
            "problems": problems,
        },
    }


def _check_linkage_auto_accept() -> dict[str, Any]:
    """A failing auto-accept region must show up as abstention/review, never as silence."""
    metrics_path = PROJECT_ROOT / "data" / "interim_v03" / "linkage" / "u06r_metrics.json"
    try:
        metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    except Exception as error:
        return {"status": "FAIL", "evidence": f"{type(error).__name__}: {error}"}
    auto = metrics.get("auto_accept", {})
    problems: list[str] = []
    statuses = {name: auto.get(name, {}).get("status") for name in ("ml", "deterministic")}
    if not statuses or all(value is None for value in statuses.values()):
        problems.append("no auto-accept region recorded")
    if all(value == "FAIL" for value in statuses.values() if value is not None):
        if not auto.get("abstention"):
            problems.append("no auto-accept region passed and no abstention band recorded")
        if metrics.get("risk", {}).get("by_tier", {}).get("HIGH") is None and not auto.get("abstention"):
            problems.append("failing region with no human review routing")
    if metrics.get("matchers", {}).get("splink", {}).get("status") in (None, ""):
        problems.append("splink baseline status not recorded")
    return {
        "status": "FAIL" if problems else "PASS",
        "evidence": {
            "regions": statuses,
            "abstention": auto.get("abstention"),
            "risk": metrics.get("risk"),
            "problems": problems,
        },
    }


def _check_linkage_tests() -> dict[str, Any]:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", *LINKAGE_TESTS, "-q", "-o", "addopts="],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    tail = (result.stdout or "").strip().splitlines()[-1:] or [""]
    return {
        "status": "PASS" if result.returncode == 0 else "FAIL",
        "evidence": {
            "command": f"pytest {' '.join(LINKAGE_TESTS)}",
            "summary": re.sub(r"\s+in\s+[\d.]+s$", "", tail[0]),
        },
    }


def _check_kin_tables() -> dict[str, Any]:
    """The five canonical tables must exist and satisfy the V0.3 contracts."""
    from qing_elite.v03.contracts import validate_detail_rows_cite_ledger, validate_table
    from qing_elite.v03.lit.registry import PROCESSED_V03_DIR

    required = [
        "persons.parquet",
        "kin_edges.parquet",
        "credentials.parquet",
        "offices.parquet",
        "evidence_assertions.parquet",
        "kin_indicators.parquet",
        "kin_validation.parquet",
        "kin_coverage_by_source.parquet",
        "kin_coverage_by_cohort.parquet",
    ]
    missing = [name for name in required if not (PROCESSED_V03_DIR / name).exists()]
    benchmark = PROJECT_ROOT / "reports" / "upgrade_v03" / "kin_coverage.md"
    if not benchmark.exists():
        missing.append("reports/upgrade_v03/kin_coverage.md")
    if missing:
        return {"status": "FAIL", "evidence": {"missing": missing}}

    tables = {name.split(".")[0]: pd.read_parquet(PROCESSED_V03_DIR / name) for name in required[:5]}
    problems: list[str] = []
    try:
        for name, frame in tables.items():
            validate_table(name, frame)
        validate_detail_rows_cite_ledger(
            {
                "career_events": pd.read_parquet(
                    PROCESSED_V03_DIR / "pilot_career_events.parquet"
                ),
                "kin_edges": tables["kin_edges"],
                "credentials": tables["credentials"],
                "evidence_assertions": tables["evidence_assertions"],
            }
        )
    except Exception as error:
        problems.append(f"{type(error).__name__}: {str(error)[:300]}")
    indicators = pd.read_parquet(PROCESSED_V03_DIR / "kin_indicators.parquet")
    for column in (
        "denominator_direct_kin",
        "observable_direct_kin",
        "lineage_edge_ids",
    ):
        if column not in indicators.columns:
            problems.append(f"indicator frame lacks {column}")
    return {
        "status": "FAIL" if problems else "PASS",
        "evidence": {
            "rows": {name: int(len(frame)) for name, frame in tables.items()},
            "indicator_rows": int(len(indicators)),
            "problems": problems,
        },
    }


def _check_kin_validators() -> dict[str, Any]:
    """Structural checks must have run, and a contradiction must not be silently accepted."""
    from qing_elite.v03.lit.registry import PROCESSED_V03_DIR

    validation_path = PROCESSED_V03_DIR / "kin_validation.parquet"
    metrics_path = PROJECT_ROOT / "data" / "interim_v03" / "kin" / "u07r_metrics.json"
    if not validation_path.exists() or not metrics_path.exists():
        return {"status": "FAIL", "evidence": "validation or metrics artifact missing"}
    validation = pd.read_parquet(validation_path)
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    known = {
        "duplicated_kin",
        "generation_paradox",
        "chronology_contradiction",
        "relation_cycle",
        "conflicting_relationship",
    }
    unknown = sorted(set(validation["check"]) - known) if len(validation) else []
    problems: list[str] = []
    if unknown:
        problems.append(f"unknown validator check names: {unknown}")
    graph_info = metrics.get("graph", {})
    if "networkx" not in str(graph_info.get("engine", "")):
        problems.append("graph engine not recorded")
    if metrics.get("graph", {}).get("edges") is None:
        problems.append("graph edge count missing")
    notes = " ".join(metrics.get("notes", []))
    if "no substantive regression" not in notes:
        problems.append("stage must state that no regression was run")
    return {
        "status": "FAIL" if problems else "PASS",
        "evidence": {
            "flagged": int(len(validation)),
            "by_check": validation["check"].value_counts().to_dict() if len(validation) else {},
            "graph": graph_info,
            "problems": problems,
        },
    }


def _check_kin_tests() -> dict[str, Any]:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", *KIN_TESTS, "-q", "-o", "addopts="],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    tail = (result.stdout or "").strip().splitlines()[-1:] or [""]
    return {
        "status": "PASS" if result.returncode == 0 else "FAIL",
        "evidence": {
            "command": f"pytest {' '.join(KIN_TESTS)}",
            "summary": re.sub(r"\s+in\s+[\d.]+s$", "", tail[0]),
        },
    }


def _check_career_panel() -> dict[str, Any]:
    """Career panel artifacts: events, ontology, outcomes, validation, tier crosswalk."""
    from qing_elite.v03.lit.registry import PROCESSED_V03_DIR

    required = [
        "career_events.parquet",
        "career_offices.parquet",
        "career_outcomes.parquet",
        "career_validation.parquet",
        "career_tier_crosswalk.parquet",
    ]
    missing = [name for name in required if not (PROCESSED_V03_DIR / name).exists()]
    metrics_path = PROJECT_ROOT / "data" / "interim_v03" / "career" / "u08r_metrics.json"
    if not metrics_path.exists():
        missing.append("u08r_metrics.json")
    if missing:
        return {"status": "FAIL", "evidence": {"missing": missing}}

    events = pd.read_parquet(PROCESSED_V03_DIR / "career_events.parquet")
    outcomes = pd.read_parquet(PROCESSED_V03_DIR / "career_outcomes.parquet")
    crosswalk = pd.read_parquet(PROCESSED_V03_DIR / "career_tier_crosswalk.parquet")
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    problems: list[str] = []
    if events["source_document_id"].isna().any():
        problems.append("career events without a source document id")
    if (outcomes["lineage_event_ids"].str.len() == 0).any():
        problems.append("outcomes without lineage")
    if "legacy" not in " ".join(crosswalk["note"].astype(str)):
        problems.append("tier crosswalk must mark tier as legacy-only")
    ontology = metrics.get("ontology", {})
    if ontology.get("rank_verification_status") in (None, "verified"):
        problems.append("curated rank table must carry its verification status (pending)")
    if "no substantive regression" in " ".join(metrics.get("notes", [])):
        problems.append("U08R is the stage that derives outcomes; the note list looks pasted")
    return {
        "status": "FAIL" if problems else "PASS",
        "evidence": {
            "events": int(len(events)),
            "outcomes": int(len(outcomes)),
            "tier_rows": int(len(crosswalk)),
            "ontology": ontology,
            "problems": problems,
        },
    }


def _check_career_validators() -> dict[str, Any]:
    """Chronology and impossible-transition checks must have run and be typed."""
    from qing_elite.v03.lit.registry import PROCESSED_V03_DIR

    path = PROCESSED_V03_DIR / "career_validation.parquet"
    if not path.exists():
        return {"status": "FAIL", "evidence": f"missing {path.name}"}
    validation = pd.read_parquet(path)
    known = {
        "chronology_inverted",
        "chronology_out_of_window",
        "impossible_transition",
        "same_year_rank_change",
    }
    unknown = sorted(set(validation["check"]) - known) if len(validation) else []
    return {
        "status": "FAIL" if unknown else "PASS",
        "evidence": {
            "flagged": int(len(validation)),
            "by_check": validation["check"].value_counts().to_dict() if len(validation) else {},
            "unknown_checks": unknown,
        },
    }


def _check_career_tests() -> dict[str, Any]:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", *CAREER_TESTS, "-q", "-o", "addopts="],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    tail = (result.stdout or "").strip().splitlines()[-1:] or [""]
    return {
        "status": "PASS" if result.returncode == 0 else "FAIL",
        "evidence": {
            "command": f"pytest {' '.join(CAREER_TESTS)}",
            "summary": re.sub(r"\s+in\s+[\d.]+s$", "", tail[0]),
        },
    }


def _check_analysis_artifacts() -> dict[str, Any]:
    """The analysis must publish its sample, coverage, gates and a rendered report."""
    from qing_elite.v03.lit.registry import PROCESSED_V03_DIR

    required = [
        "analysis_sample.parquet",
        "analysis_coverage.parquet",
        "analysis_outcomes.parquet",
        "analysis_sensitivity.parquet",
    ]
    missing = [name for name in required if not (PROCESSED_V03_DIR / name).exists()]
    metrics_path = PROJECT_ROOT / "data" / "interim_v03" / "analysis" / "u09r_metrics.json"
    report = PROJECT_ROOT / "reports" / "upgrade_v03" / "U09R.md"
    for path in (metrics_path, report):
        if not path.exists():
            missing.append(path.name)
    if missing:
        return {"status": "FAIL", "evidence": {"missing": missing}}

    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    frame = pd.read_parquet(PROCESSED_V03_DIR / "analysis_sample.parquet")
    problems: list[str] = []
    if metrics["gate"]["decision"] not in ("model_allowed", "descriptive_only"):
        problems.append("unknown gate decision")
    if metrics["gate"]["decision"] == "descriptive_only" and not metrics["gate"]["reasons"]:
        problems.append("descriptive-only decision without reasons")
    if frame["family_observable"].sum() == 0 and not metrics["gate"]["reasons"]:
        problems.append("no observable family capital and no gate reason")
    if "unknown" not in json.dumps(metrics["constraints"]):
        problems.append("constraints must state unknown != zero")
    return {
        "status": "FAIL" if problems else "PASS",
        "evidence": {
            "rows": int(len(frame)),
            "family_observable": int(frame["family_observable"].sum()),
            "gate": metrics["gate"]["decision"],
            "gate_reasons": metrics["gate"]["reasons"],
            "problems": problems,
        },
    }


def _check_analysis_report_generated() -> dict[str, Any]:
    """The report must be rendered from the metrics, not hand-typed."""
    metrics_path = PROJECT_ROOT / "data" / "interim_v03" / "analysis" / "u09r_metrics.json"
    report = PROJECT_ROOT / "reports" / "upgrade_v03" / "U09R.md"
    if not metrics_path.exists() or not report.exists():
        return {"status": "FAIL", "evidence": "metrics or report missing"}
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    text = report.read_text(encoding="utf-8")
    problems: list[str] = []
    for token in (
        f"{metrics['sample_flow']['links_total']:,}",
        metrics["gate"]["decision"],
    ):
        if token not in text:
            problems.append(f"report does not contain the artifact value {token!r}")
    if metrics["generated_at"] not in text:
        problems.append("report is not stamped with the metrics timestamp")
    figures = [name for name in metrics["figures"].values() if name]
    for name in figures:
        if not (PROJECT_ROOT / "reports" / "upgrade_v03" / "figures" / name).exists():
            problems.append(f"figure missing: {name}")
    return {
        "status": "FAIL" if problems else "PASS",
        "evidence": {"figures": figures, "problems": problems},
    }


def _check_analysis_tests() -> dict[str, Any]:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", *ANALYSIS_TESTS, "-q", "-o", "addopts="],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    tail = (result.stdout or "").strip().splitlines()[-1:] or [""]
    return {
        "status": "PASS" if result.returncode == 0 else "FAIL",
        "evidence": {
            "command": f"pytest {' '.join(ANALYSIS_TESTS)}",
            "summary": re.sub(r"\s+in\s+[\d.]+s$", "", tail[0]),
        },
    }


def _check_extension_artifacts() -> dict[str, Any]:
    """The extension must publish its frame, mapping and classified comparisons."""
    from qing_elite.v03.lit.registry import PROCESSED_V03_DIR

    required = [
        "extension_early_qing.parquet",
        "extension_variable_mapping.parquet",
        "extension_comparisons.parquet",
    ]
    missing = [name for name in required if not (PROCESSED_V03_DIR / name).exists()]
    metrics_path = PROJECT_ROOT / "data" / "interim_v03" / "extension" / "u10r_metrics.json"
    report = PROJECT_ROOT / "reports" / "upgrade_v03" / "U10R.md"
    for path in (metrics_path, report):
        if not path.exists():
            missing.append(path.name)
    if missing:
        return {"status": "FAIL", "evidence": {"missing": missing}}

    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    mapping = pd.read_parquet(PROCESSED_V03_DIR / "extension_variable_mapping.parquet")
    comparisons = pd.read_parquet(PROCESSED_V03_DIR / "extension_comparisons.parquet")
    problems: list[str] = []
    if "strict_commoner_3g" not in set(mapping["legacy_variable"]):
        problems.append("the rejected legacy variable must be listed as not_comparable")
    if not (mapping["status"] == "not_comparable").any():
        problems.append("no not_comparable rows recorded")
    if comparisons["classification"].isin(
        ["CROSS_PERIOD_CONSISTENT", "PERIOD_SPECIFIC", "NOT_COMPARABLE"]
    ).all() is False:
        problems.append("unknown classification value")
    if metrics["pooled_regression"]["run"] is not False:
        problems.append("pooled regression must not run in this stage")
    if metrics["early_qing"].get("d_layer_included") is not False:
        problems.append("the D layer must not be revived as a control group")
    report_text = report.read_text(encoding="utf-8")
    if metrics["generated_at"] not in report_text:
        problems.append("report is not stamped with the metrics timestamp")
    return {
        "status": "FAIL" if problems else "PASS",
        "evidence": {
            "early_qing_persons": int(metrics["early_qing"]["persons"]),
            "mapping_rows": int(len(mapping)),
            "comparison_rows": int(len(comparisons)),
            "classification_counts": metrics["classification_counts"],
            "problems": problems,
        },
    }


def _check_extension_tests() -> dict[str, Any]:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", *EXTENSION_TESTS, "-q", "-o", "addopts="],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    tail = (result.stdout or "").strip().splitlines()[-1:] or [""]
    return {
        "status": "PASS" if result.returncode == 0 else "FAIL",
        "evidence": {
            "command": f"pytest {' '.join(EXTENSION_TESTS)}",
            "summary": re.sub(r"\s+in\s+[\d.]+s$", "", tail[0]),
        },
    }


CHECKS = {
    "dirs_exist": _check_dirs,
    "design_configs_load": _check_design_configs,
    "tables_registered": _check_tables_registered,
    "kin_scope_direct_and_extended": _check_kin_scope,
    "unknown_is_not_negative": _check_unknown_is_not_negative,
    "tier_is_outcome": _check_tier_is_outcome,
    "risk_based_review_without_fixed_percentage": _check_risk_based_review,
    "legacy_d_layer_is_not_primary_estimand": _check_d_layer_not_primary,
    "v02_untouched": _check_v02_untouched,
    "contract_tests": _check_contract_tests,
}

U04R_CHECKS = {
    "design_still_holds": _check_design_configs,
    "tier_is_outcome": _check_tier_is_outcome,
    "legacy_d_layer_is_not_primary_estimand": _check_d_layer_not_primary,
    "v02_untouched": _check_v02_untouched,
    "literature_layout": _check_literature_layout,
    "literature_pipeline": _check_literature_pipeline,
    "probe_evidence": _check_probe_evidence,
    "plan_selection": _check_plan_selection,
    "no_access_bypass": _check_no_access_bypass,
    "literature_tests": _check_literature_tests,
}

U05R_CHECKS = {
    "design_still_holds": _check_design_configs,
    "tier_is_outcome": _check_tier_is_outcome,
    "v02_untouched": _check_v02_untouched,
    "pilot_artifacts": _check_pilot_artifacts,
    "pilot_source_gates": _check_pilot_gates,
    "pilot_tests": _check_pilot_tests,
}

U06R_CHECKS = {
    "design_still_holds": _check_design_configs,
    "tier_is_outcome": _check_tier_is_outcome,
    "v02_untouched": _check_v02_untouched,
    "linkage_artifacts": _check_linkage_artifacts,
    "linkage_crosswalk": _check_linkage_crosswalk,
    "linkage_auto_accept": _check_linkage_auto_accept,
    "linkage_tests": _check_linkage_tests,
}

U07R_CHECKS = {
    "design_still_holds": _check_design_configs,
    "tier_is_outcome": _check_tier_is_outcome,
    "unknown_is_not_negative": _check_unknown_is_not_negative,
    "v02_untouched": _check_v02_untouched,
    "kin_tables": _check_kin_tables,
    "kin_validators": _check_kin_validators,
    "kin_tests": _check_kin_tests,
}

STAGE_CHECKS = {
    "u03r": CHECKS,
    "u04r": U04R_CHECKS,
    "u05r": U05R_CHECKS,
    "u06r": U06R_CHECKS,
    "u07r": U07R_CHECKS,
    "u08r": {
        "design_still_holds": _check_design_configs,
        "tier_is_outcome": _check_tier_is_outcome,
        "v02_untouched": _check_v02_untouched,
        "career_panel": _check_career_panel,
        "career_validators": _check_career_validators,
        "career_tests": _check_career_tests,
    },
    "u09r": {
        "design_still_holds": _check_design_configs,
        "tier_is_outcome": _check_tier_is_outcome,
        "legacy_d_layer_is_not_primary_estimand": _check_d_layer_not_primary,
        "v02_untouched": _check_v02_untouched,
        "analysis_artifacts": _check_analysis_artifacts,
        "analysis_report_generated": _check_analysis_report_generated,
        "analysis_tests": _check_analysis_tests,
    },
    "u10r": {
        "design_still_holds": _check_design_configs,
        "tier_is_outcome": _check_tier_is_outcome,
        "legacy_d_layer_is_not_primary_estimand": _check_d_layer_not_primary,
        "v02_untouched": _check_v02_untouched,
        "extension_artifacts": _check_extension_artifacts,
        "extension_tests": _check_extension_tests,
    },
}
STAGE_OUTPUTS = {
    "u03r": PROJECT_ROOT / "audit" / "v03" / "u03r_gate.json",
    "u04r": PROJECT_ROOT / "audit" / "v03" / "u04r_gate.json",
    "u05r": PROJECT_ROOT / "audit" / "v03" / "u05r_gate.json",
    "u06r": PROJECT_ROOT / "audit" / "v03" / "u06r_gate.json",
    "u07r": PROJECT_ROOT / "audit" / "v03" / "u07r_gate.json",
    "u08r": PROJECT_ROOT / "audit" / "v03" / "u08r_gate.json",
    "u09r": PROJECT_ROOT / "audit" / "v03" / "u09r_gate.json",
    "u10r": PROJECT_ROOT / "audit" / "v03" / "u10r_gate.json",
}
STAGE_TESTS = {
    "contract_tests",
    "literature_tests",
    "pilot_tests",
    "linkage_tests",
    "kin_tests",
    "career_tests",
    "analysis_tests",
    "extension_tests",
}


def run_gate(*, with_tests: bool = True, stage: str = "u03r") -> dict[str, Any]:
    """Run every check for ``stage`` and return the machine-readable gate record."""
    if stage not in STAGE_CHECKS:
        raise KeyError(f"unknown stage: {stage}")
    results: dict[str, Any] = {}
    for name, check in STAGE_CHECKS[stage].items():
        if name in STAGE_TESTS and not with_tests:
            results[name] = {"status": "SKIP", "evidence": "disabled by --no-tests"}
            continue
        results[name] = check()
    failures = [name for name, result in results.items() if result["status"] == "FAIL"]
    return {
        "stage": stage.upper(),
        "schema_version": SCHEMA_VERSION,
        "gate": "PASS" if not failures else "FAIL",
        "failures": failures,
        "checks": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="V0.3 stage gate")
    parser.add_argument("--stage", default="u03r", choices=sorted(STAGE_CHECKS))
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--no-tests", action="store_true", help="skip the test runs")
    parser.add_argument("--print", action="store_true", dest="echo", help="echo the record")
    args = parser.parse_args(argv)

    out = args.out or STAGE_OUTPUTS[args.stage]
    record = run_gate(with_tests=not args.no_tests, stage=args.stage)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.echo:
        print(json.dumps(record, ensure_ascii=False, indent=2))
    print(f"{record['stage']} gate: {record['gate']} -> {out}")
    for name, result in record["checks"].items():
        print(f"  {result['status']:>4}  {name}")
    return 0 if record["gate"] == "PASS" else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
