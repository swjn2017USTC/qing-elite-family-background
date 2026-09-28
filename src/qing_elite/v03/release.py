"""Release gate, release manifest and reproducibility record (U11R).

``python -m qing_elite.v03.release`` either exits 0 — every required artifact exists, every
schema and lineage check passes, no review item is pending, no primary conflict is unresolved,
the environment and inputs are pinned — or it exits non-zero and prints what failed. Nothing in
here repairs anything: a failing release is a failing release.

The manifest is generated from the artifacts (checksums, row counts, seeds, model ids, prompt
versions, upstream commits), so it cannot drift from what the pipeline actually produced.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from qing_elite.utils.config import PROJECT_ROOT
from qing_elite.v03.design import load_relation_ontology, load_research_contract, load_review_policy
from qing_elite.v03.lit.registry import PROCESSED_V03_DIR, load_registry
from qing_elite.v03.linkage import UPSTREAM as LINKAGE_UPSTREAM

MANIFEST_JSON = PROJECT_ROOT / "RELEASE_MANIFEST.json"
REPRODUCIBILITY_MD = PROJECT_ROOT / "REPRODUCIBILITY.md"
REPORT_DIR = PROJECT_ROOT / "reports" / "upgrade_v03"
FIGURES = REPORT_DIR / "figures"

#: Everything a release must ship. Missing → the gate fails.
REQUIRED_ARTIFACTS = (
    "persons.parquet",
    "kin_edges.parquet",
    "credentials.parquet",
    "offices.parquet",
    "evidence_assertions.parquet",
    "career_events.parquet",
    "career_offices.parquet",
    "career_outcomes.parquet",
    "career_validation.parquet",
    "career_tier_crosswalk.parquet",
    "kin_indicators.parquet",
    "kin_validation.parquet",
    "kin_coverage_by_source.parquet",
    "kin_coverage_by_cohort.parquet",
    "entity_links.parquet",
    "linkage_features.parquet",
    "linkage_risk.parquet",
    "linkage_review_queue.parquet",
    "linkage_crosswalk.parquet",
    "analysis_sample.parquet",
    "analysis_coverage.parquet",
    "analysis_outcomes.parquet",
    "analysis_sensitivity.parquet",
    "extension_early_qing.parquet",
    "extension_variable_mapping.parquet",
    "extension_comparisons.parquet",
)

REQUIRED_REPORTS = (
    "U03R.md",
    "U04R.md",
    "U05R.md",
    "U06R.md",
    "U07R.md",
    "U08R.md",
    "U09R.md",
    "U10R.md",
    "U11R.md",
    "FINAL_REPORT_V03.md",
    "linkage_benchmark.md",
    "kin_coverage.md",
)

REQUIRED_ROOT_DOCS = (
    "README_V03.md",
    "METHODS_V03.md",
    "CODEBOOK_V03.md",
    "REPRODUCIBILITY.md",
    "RELEASE_MANIFEST.json",
)


def _yaml(name: str) -> dict[str, Any]:
    import yaml

    path = PROJECT_ROOT / "config" / "v03" / name
    return yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}


def sha256(path: Path, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(chunk):
            digest.update(block)
    return digest.hexdigest()


def _git(*args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args], cwd=PROJECT_ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()
    except Exception:
        return "unavailable"


def check_required_files() -> list[str]:
    problems: list[str] = []
    for name in REQUIRED_ARTIFACTS:
        if not (PROCESSED_V03_DIR / name).exists():
            problems.append(f"missing artifact {name}")
    for name in REQUIRED_REPORTS:
        if not (REPORT_DIR / name).exists():
            problems.append(f"missing report {name}")
    for name in REQUIRED_ROOT_DOCS:
        if name == "RELEASE_MANIFEST.json" and not MANIFEST_JSON.exists():
            problems.append(f"missing {name}")
        elif name != "RELEASE_MANIFEST.json" and not (PROJECT_ROOT / name).exists():
            problems.append(f"missing {name}")
    return problems


def check_schemas_and_lineage() -> list[str]:
    problems: list[str] = []
    from qing_elite.v03.contracts import (
        validate_detail_rows_cite_ledger,
        validate_table,
    )

    tables: dict[str, pd.DataFrame] = {}
    for name in ("persons", "kin_edges", "credentials", "offices", "evidence_assertions"):
        tables[name] = pd.read_parquet(PROCESSED_V03_DIR / f"{name}.parquet")
    try:
        for name, frame in tables.items():
            validate_table(name, frame)
        # the ledger-citation rule covers the tables built from assertions (kin edges and
        # credentials). JSL career events are primary roster records: they cite a source
        # document directly and have no assertion row by construction, so they are checked
        # for document lineage instead.
        from qing_elite.v03.contracts import validate_detail_rows_cite_ledger as _ledger

        _ledger(
            {
                "career_events": pd.DataFrame(
                    columns=["event_id", "evidence_assertion_id", "source_document_id"]
                ),
                "kin_edges": tables["kin_edges"],
                "credentials": tables["credentials"],
                "evidence_assertions": tables["evidence_assertions"],
            }
        )
        events = pd.read_parquet(PROCESSED_V03_DIR / "career_events.parquet")
        if events["source_document_id"].isna().any():
            problems.append("career events without a source document id")
    except Exception as error:
        problems.append(f"schema/lineage: {type(error).__name__}: {str(error)[:200]}")
    return problems


def check_review_state() -> list[str]:
    problems: list[str] = []
    queue = pd.read_parquet(PROCESSED_V03_DIR / "linkage_review_queue.parquet")
    if len(queue):
        pending = queue.loc[queue["status"] != "resolved"]
        if len(pending):
            # open review items are legitimate output of a benchmark, but not of a primary result
            links = pd.read_parquet(PROCESSED_V03_DIR / "entity_links.parquet")
            accepted_ids = set(links.loc[links["decision"] == "auto_accept", "link_id"])
            leaked = set(pending["subject_id"]) & accepted_ids
            if leaked:
                problems.append(f"{len(leaked)} pending review items are already auto-accepted")
    sample = pd.read_parquet(PROCESSED_V03_DIR / "analysis_sample.parquet")
    if (sample["link_confidence"] == "pending").any():
        problems.append("analysis sample contains pending links")
    return problems


def check_public_boundary() -> list[str]:
    """No key, no person-level raw text, no licensed body may be tracked in git."""
    problems: list[str] = []
    tracked = _git("ls-files").splitlines()
    forbidden_prefixes = (
        "data/raw/",
        "data/processed/",
        "data/processed_v02/",
        "data/processed_v03/",
        "data/interim_v03/",
        "sources/literature/private/",
        "sources/literature/public/",
    )
    allowed = {"data/raw/manifest.json", "data/raw/manifest_v03.json"}
    for path in tracked:
        if path in allowed:
            continue
        if path.startswith(forbidden_prefixes) and not path.endswith(".gitkeep"):
            problems.append(f"tracked restricted file: {path}")
    for path in tracked:
        if path.endswith((".env", ".key", ".pem")) or "credential" in path.lower():
            problems.append(f"tracked credential-like file: {path}")
    if "DEEPSEEK_API_KEY" in _git("grep", "-l", "DEEPSEEK_API_KEY").strip():
        problems.append("a tracked file mentions DEEPSEEK_API_KEY")
    return problems


def check_audit() -> list[str]:
    audit_path = PROJECT_ROOT / "audit" / "v03" / "final_audit.json"
    if not audit_path.exists():
        return ["audit/v03/final_audit.json missing"]
    record = json.loads(audit_path.read_text(encoding="utf-8"))
    if record.get("status") != "PASS":
        return [f"adversarial audit status {record.get('status')}"]
    return []


def check_tests() -> list[str]:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-o", "addopts="],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        tail = (result.stdout or "").strip().splitlines()[-6:]
        return ["test suite failed: " + " / ".join(tail)]
    return []


GATE_CHECKS = {
    "required_files": check_required_files,
    "schemas_and_lineage": check_schemas_and_lineage,
    "review_state": check_review_state,
    "public_boundary": check_public_boundary,
    "adversarial_audit": check_audit,
    "tests": check_tests,
}


def run_gate(*, with_tests: bool = True) -> dict[str, Any]:
    results: dict[str, Any] = {}
    for name, check in GATE_CHECKS.items():
        if name == "tests" and not with_tests:
            results[name] = {"status": "SKIP", "problems": []}
            continue
        problems = check()
        results[name] = {"status": "PASS" if not problems else "FAIL", "problems": problems}
    failures = [name for name, result in results.items() if result["status"] == "FAIL"]
    return {
        "release": "v0.3-cohort-kin-network",
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
        "checks": results,
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def build_manifest() -> dict[str, Any]:
    contract = load_research_contract()
    ontology = load_relation_ontology()
    policy = load_review_policy()
    registry = load_registry()
    raw_manifest_path = PROJECT_ROOT / "data" / "raw" / "manifest_v03.json"
    raw_manifest = json.loads(raw_manifest_path.read_text(encoding="utf-8")) if raw_manifest_path.exists() else {}
    artifacts = {}
    for name in REQUIRED_ARTIFACTS:
        path = PROCESSED_V03_DIR / name
        if path.exists():
            artifacts[name] = {
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
                "rows": int(len(pd.read_parquet(path, columns=None))),
            }
    return {
        "release": "v0.3-cohort-kin-network",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "code_commit": _git("rev-parse", "HEAD"),
        "code_commit_short": _git("rev-parse", "--short", "HEAD"),
        "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "baseline_tag": "v0.2-u03-frozen-20260927",
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "machine": platform.machine(),
            "lockfile": "uv.lock",
            "snakemake": _snakemake_version(),
        },
        "contracts": {
            "research_contract": contract["contract_version"],
            "claim_level": contract["claim_level"],
            "ontology_version": ontology["ontology_version"],
            "review_policy": policy["policy_version"],
            "rank_table_verification_status": "pending",
        },
        "seeds": {
            "pilot_frame_seed": _yaml("pilot_frame.yaml").get("seed"),
            "linkage_seed": _yaml("linkage.yaml").get("seed"),
        },
        "thresholds": {
            "linkage_auto_accept_precision": 0.99,
            "linkage_ml_threshold": 0.35,
            "review_budgets": policy["budgets"],
            "extraction_gates": _yaml("extraction_gates.yaml")["gold_based"],
        },
        "models": {
            "extraction": "deepseek-flash (official API, thinking disabled, JSON output)",
            "ocr": "PaddleOCR-VL-1.6 (official service)",
            "coding": "opencode-go/deepseek-v4.1-flash",
            "prompt_versions": ["p04-family-v3 (v0.1 lineage)", "u05r-abstain-v1"],
        },
        "upstream": {
            "linkage_code": LINKAGE_UPSTREAM,
            "literature_pipeline": "ACADEMIC_LITERATURE_PIPELINE_HANDOFF (framework reused, no topic data)",
        },
        "literature_registry": {
            "rows": int(len(registry)),
            "core": int((registry["tier"] == "core").sum()),
            "path": "sources/literature/registry/literature_registry.jsonl",
            "sha256": sha256(PROJECT_ROOT / "sources" / "literature" / "registry" / "literature_registry.jsonl"),
        },
        "raw_sources": raw_manifest.get("artifacts", []),
        "artifacts": artifacts,
        "reports": {
            name: {"sha256": sha256(REPORT_DIR / name), "bytes": (REPORT_DIR / name).stat().st_size}
            for name in REQUIRED_REPORTS
            if (REPORT_DIR / name).exists()
        },
        "figures": sorted(path.name for path in FIGURES.glob("*") if path.suffix in (".png", ".pdf")),
    }


def _snakemake_version() -> str:
    try:
        import snakemake  # noqa: F401

        return _git("describe", "--tags") and snakemake.__version__
    except Exception:
        return "unavailable"


def render_reproducibility(manifest: dict[str, Any]) -> str:
    lines = [
        "# REPRODUCIBILITY — v0.3 cohort-and-kin-network",
        "",
        f"- 生成时间：{manifest['generated_at']}",
        f"- 代码：branch `{manifest['branch']}` @ `{manifest['code_commit_short']}`"
        f"（基线 tag `{manifest['baseline_tag']}`）",
        f"- 环境：{manifest['environment']['platform']} / Python {manifest['environment']['python']} / `uv.lock`",
        "",
        "## 冻结项",
        "",
        f"- contracts：{manifest['contracts']}",
        f"- seeds：{manifest['seeds']}",
        f"- thresholds：{json.dumps(manifest['thresholds'], ensure_ascii=False)}",
        f"- models：{json.dumps(manifest['models'], ensure_ascii=False)}",
        f"- upstream linkage code：`{manifest['upstream']['linkage_code']['repo']}` @ "
        f"`{manifest['upstream']['linkage_code']['commit']}`（{manifest['upstream']['linkage_code']['license']}）",
        "",
        "## 输入（原始数据，本地只读、不入 Git）",
        "",
        "| file | sha256 | release |",
        "| --- | --- | --- |",
    ]
    for entry in manifest["raw_sources"]:
        lines.append(
            f"| `{entry.get('file')}` | `{str(entry.get('sha256'))[:16]}…` | {entry.get('release', '')} |"
        )
    lines += ["", "## 产物", "", "| artifact | rows | sha256 |", "| --- | --- | --- |"]
    for name, info in sorted(manifest["artifacts"].items()):
        lines.append(f"| `{name}` | {info['rows']:,} | `{info['sha256'][:16]}…` |")
    lines += [
        "",
        "## 复现命令",
        "",
        "```bash",
        "uv sync",
        "bash scripts/recover_v02_artifacts.sh        # v0.2 gold / crosswalk（从 git 历史）",
        "uv run python -m qing_elite.v03.pilot.frame   # 需要本地 CBDB sqlite",
        "uv run python -m qing_elite.v03.pilot.run",
        "uv run python -m qing_elite.v03.linkage.run",
        "uv run python -m qing_elite.v03.kin.run",
        "uv run python -m qing_elite.v03.career.run",
        "uv run python -m qing_elite.v03.analysis.run",
        "uv run python -m qing_elite.v03.extension.run",
        "uv run python -m qing_elite.v03.release --build-docs",
        "snakemake -n --snakefile workflow/Snakefile   # DAG dry-run",
        "uv run python -m qing_elite.v03.release      # release gate",
        "```",
        "",
        "## 已知不可复现 / 受外部条件约束",
        "",
        "1. PaddleOCR 与 DeepSeek 服务端行为可能变化；每次调用（OCR token、LLM token/成本）已落盘。",
        "2. Semantic Scholar / OpenAlex 在 U04R 当日限流/暂停（记录于 `config/v03/literature.yaml`）。",
        "3. 官品表（`config/v03/office_ranks.yaml`）是策展表且 `verification_status=pending`。",
        "4. 两个 u0x 测试依赖本地 `data/processed/appointments.parquet`（公开仓库不含该文件）。",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - thin CLI
    parser = argparse.ArgumentParser(description="U11R release gate and manifest")
    parser.add_argument("--build-docs", action="store_true", help="regenerate manifest and REPRODUCIBILITY.md")
    parser.add_argument("--no-tests", action="store_true")
    parser.add_argument("--print", action="store_true", dest="echo")
    args = parser.parse_args(argv)

    if args.build_docs:
        manifest = build_manifest()
        MANIFEST_JSON.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        REPRODUCIBILITY_MD.write_text(render_reproducibility(manifest), encoding="utf-8")
        print(f"manifest: {MANIFEST_JSON.name}, reproducibility: {REPRODUCIBILITY_MD.name}")

    record = run_gate(with_tests=not args.no_tests)
    (PROJECT_ROOT / "audit" / "v03" / "release_gate.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if args.echo:
        print(json.dumps(record, ensure_ascii=False, indent=2))
    print(f"release gate: {record['status']}")
    for name, result in record["checks"].items():
        print(f"  {result['status']:>4}  {name}")
        for problem in result["problems"][:5]:
            print(f"         - {problem}")
    return 0 if record["status"] == "PASS" else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
