"""P08 packaging: final tables, figures and reproducibility record.

    uv run python -m qing_elite.build_final

Copies the frozen result tables into ``reports/final/tables/``, the vector figures into
``reports/final/figures/``, and writes ``reports/final/reproducibility.json`` (plus a
human-readable companion) from what is actually on disk: data hashes, git commit, test
count and the API spend recorded in the call audit.
"""

from __future__ import annotations

import hashlib
import json
import platform
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from qing_elite.utils.config import OUTPUT_DIR, PROJECT_ROOT, RAW_DIR

FINAL_DIR = PROJECT_ROOT / "reports" / "final"
TABLES_DIR = FINAL_DIR / "tables"
FIGURES_DIR = FINAL_DIR / "figures"

# Frozen table set shipped with the report (source -> final name).
TABLES = {
    "p06_coverage_by_tier.csv": "T1_coverage_by_tier.csv",
    "p06_prevalence.csv": "T2_indicator_prevalence.csv",
    "p06_elite_generations.csv": "T3_elite_generations.csv",
    "p06_na_accounting.csv": "T4_na_accounting.csv",
    "p06_visibility_bias.csv": "T5_visibility_bias.csv",
    "p06_models.csv": "T6_models.csv",
    "p06_model_diagnostics.json": "T7_model_diagnostics.json",
    "p03_family_coverage.csv": "T8_structural_family_coverage.csv",
    "p04_metrics.csv": "T9_pilot_metrics.csv",
    "cost_report.csv": "T10_llm_cost_report.csv",
    "p05_enrich_plan.csv": "T11_enrichment_plan.csv",
}
AUDIT_TABLES = {
    "a_layer_verification.csv": "A1_a_layer_verification.csv",
    "p07_missing_sensitivity.csv": "A2_missing_sensitivity.csv",
    "p07_humble_definitions.csv": "A3_humble_definitions.csv",
    "p07_humble_agreement.csv": "A4_humble_agreement.csv",
    "p07_duplication.csv": "A5_duplication.csv",
    "p07_duplication_sensitivity.csv": "A6_duplication_sensitivity.csv",
    "p07_cgedq_quarters.csv": "A7_cgedq_quarters.csv",
    "p07_office_tier.csv": "A8_office_tier.csv",
    "p07_time_window.csv": "A9_time_window.csv",
    "p07_denominators.csv": "A10_denominators.csv",
    "p07_unknown_coding.csv": "A11_unknown_coding.csv",
    "p07_replication_10pct.csv": "A12_replication_10pct.csv",
}
FIGURES = (
    "p06_coverage_by_tier",
    "p06_prevalence_by_tier",
    "p06_elite_generations",
    "p06_cohort_trend_abc",
)
PROCESSED = PROJECT_ROOT / "data" / "processed"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git(*args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args], cwd=PROJECT_ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()
    except Exception as error:  # noqa: BLE001 - reproducibility record must not fail the build
        return f"<git {' '.join(args)} failed: {error}>"


def _test_summary() -> dict[str, Any]:
    """Run the suite and record a count that does not depend on pytest's progress format.

    pytest 9 here prints no trailing "N passed" line when stdout is not a TTY, so the
    test count comes from a --collect-only run and the pass/fail verdict from the exit code.
    """
    run = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:warnings"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    collect = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:warnings"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    # pytest's quiet collect output is "<file>: <count>" lines here, not "file::test".
    collected = 0
    for line in collect.stdout.splitlines():
        _, _, count = line.rpartition(":")
        if count.strip().isdigit():
            collected += int(count.strip())
    verdict = "all passed" if run.returncode == 0 else f"FAILED (exit {run.returncode})"
    return {
        "exit_code": run.returncode,
        "tests_collected": collected,
        "summary": f"{collected} tests, {verdict}",
        "command": "uv run pytest -q",
    }


def package() -> dict[str, Any]:
    TABLES_DIR.mkdir(parents=True, exist_ok=True)
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)

    table_rows: list[dict[str, Any]] = []
    for source, final_name in {**TABLES, **AUDIT_TABLES}.items():
        origin = OUTPUT_DIR / "tables" / source
        if not origin.exists():
            origin = PROJECT_ROOT / "audit" / source
        if not origin.exists():
            table_rows.append({"file": final_name, "status": "missing", "source": source})
            continue
        target = TABLES_DIR / final_name
        shutil.copy2(origin, target)
        rows = None
        if target.suffix == ".csv":
            rows = int(len(pd.read_csv(target)))
        table_rows.append(
            {
                "file": f"tables/{final_name}",
                "source": str(origin.relative_to(PROJECT_ROOT)),
                "rows": rows,
                "sha256": _sha256(target),
                "status": "copied",
            }
        )

    figure_rows: list[dict[str, Any]] = []
    for name in FIGURES:
        for suffix in ("pdf", "png"):
            origin = OUTPUT_DIR / "figures" / f"{name}.{suffix}"
            if not origin.exists():
                continue
            target = FIGURES_DIR / f"{name}.{suffix}"
            shutil.copy2(origin, target)
            figure_rows.append(
                {"file": f"figures/{name}.{suffix}", "sha256": _sha256(target), "bytes": target.stat().st_size}
            )

    derived_rows: list[dict[str, Any]] = []
    for name in (
        "officials_master.parquet",
        "appointments.parquet",
        "family_structured.parquet",
        "family_enriched.parquet",
        "family_final.parquet",
        "person_indicators.parquet",
    ):
        path = PROCESSED / name
        if path.exists():
            derived_rows.append(
                {"file": f"data/processed/{name}", "rows": int(len(pd.read_parquet(path))), "sha256": _sha256(path)}
            )

    raw_manifest = json.loads((RAW_DIR / "manifest.json").read_text(encoding="utf-8"))
    audit_path = PROJECT_ROOT / "audit" / "llm_calls" / "calls.jsonl"
    calls = [json.loads(line) for line in audit_path.open(encoding="utf-8")] if audit_path.exists() else []
    spend = sum(float(call.get("estimated_cost_rmb") or 0) for call in calls)

    record = {
        "project": "qing-elite-family-background",
        "release": "v0.1-one-day",
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "git": {
            "commit": _git("rev-parse", "HEAD"),
            "commit_short": _git("rev-parse", "--short", "HEAD"),
            "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
            "tags": _git("tag", "--points-at", "HEAD").splitlines(),
            "dirty": bool(_git("status", "--porcelain")),
        },
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "machine": platform.machine(),
        },
        "sources": [
            {
                "id": artifact["id"],
                "release": artifact.get("release_id"),
                "release_date": artifact.get("release_date"),
                "files": [
                    {"path": entry["path"], "sha256": entry.get("sha256"), "verified": entry.get("checksum_verified")}
                    for entry in artifact.get("files", [])
                ],
            }
            for artifact in raw_manifest.get("artifacts", [])
        ],
        "derived_data": derived_rows,
        "tables": table_rows,
        "figures": figure_rows,
        "tests": _test_summary(),
        "llm": {
            "provider": "DeepSeek official API (api.deepseek.com)",
            "model": "deepseek-flash",
            "calls_recorded": len(calls),
            "estimated_cost_rmb_total": round(spend, 4),
            "by_prompt_version": {
                version: {
                    "calls": int(group["calls"]),
                    "input_tokens": int(group["input_tokens"]),
                    "output_tokens": int(group["output_tokens"]),
                    "cost_rmb": round(float(group["cost_rmb"]), 4),
                }
                for version, group in pd.DataFrame(calls)
                .groupby("prompt_version")
                .agg(
                    calls=("call_id", "size"),
                    input_tokens=("input_tokens", "sum"),
                    output_tokens=("output_tokens", "sum"),
                    cost_rmb=("estimated_cost_rmb", "sum"),
                )
                .reset_index()
                .to_dict("records")
                and {
                    row["prompt_version"]: {
                        "calls": row["calls"],
                        "input_tokens": row["input_tokens"],
                        "output_tokens": row["output_tokens"],
                        "cost_rmb": row["cost_rmb"],
                    }
                    for row in pd.DataFrame(calls)
                    .groupby("prompt_version")
                    .agg(
                        calls=("call_id", "size"),
                        input_tokens=("input_tokens", "sum"),
                        output_tokens=("output_tokens", "sum"),
                        cost_rmb=("estimated_cost_rmb", "sum"),
                    )
                    .reset_index()
                    .to_dict("records")
                }.items()
            },
        },
        "commands": [
            "uv sync",
            "uv run python -m qing_elite.build_universe",
            "uv run python -m qing_elite.build_family",
            "uv run python -m qing_elite.llm.harvest --volumes 250-399",
            "uv run python -m qing_elite.llm.harvest --build-index",
            "uv run python -m qing_elite.llm.enrich --plan",
            "uv run python -m qing_elite.llm.enrich --concurrency 6",
            "uv run python -m qing_elite.build_analysis",
            "uv run python -m qing_elite.build_audit [--replicate]",
            "uv run python -m qing_elite.build_final",
            "uv run pytest",
        ],
    }
    (FINAL_DIR / "reproducibility.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (FINAL_DIR / "reproducibility.md").write_text(_markdown(record), encoding="utf-8")
    return record


def _markdown(record: dict[str, Any]) -> str:
    lines = [
        "# 复现信息（v0.1-one-day）",
        "",
        f"- 生成时间（UTC）：{record['generated_at_utc']}",
        f"- 记录生成时的 git commit：`{record['git']['commit_short']}`（{record['git']['branch']}）"
        + ("；生成后工作区仍有改动，即本文件所在的打包提交" if record["git"]["dirty"] else "；工作区干净"),
        f"- Python：{record['environment']['python']}（{record['environment']['platform']}）",
        f"- 测试：{record['tests']['summary']}（exit {record['tests']['exit_code']}）",
        f"- LLM 调用：{record['llm']['calls_recorded']} 次，累计 **{record['llm']['estimated_cost_rmb_total']} 元**（模型 `{record['llm']['model']}`，官方 API）",
        "",
        "## 原始数据（只读，不入 Git）",
        "",
        "| 数据集 | release | 日期 | 文件 | SHA256（前 16 位） | 校验 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for source in record["sources"]:
        for entry in source["files"] or [{"path": "（无本地文件）", "sha256": None, "verified": None}]:
            digest = (entry["sha256"] or "")[:16] or "—"
            lines.append(
                f"| {source['id']} | {source['release'] or '—'} | {source['release_date'] or '—'} | "
                f"`{entry['path']}` | `{digest}` | {entry['verified']} |"
            )
    lines += [
        "",
        "完整 manifest 见 `data/raw/manifest.json`，逐文件校验值见 `reports/final/reproducibility.json`。",
        "",
        "## 派生数据",
        "",
        "| 文件 | 行数 | SHA256（前 16 位） |",
        "| --- | --- | --- |",
    ]
    for row in record["derived_data"]:
        lines.append(f"| `{row['file']}` | {row['rows']:,} | `{row['sha256'][:16]}` |")
    lines += ["", "## 图表", "", "| 文件 | 字节 | SHA256（前 16 位） |", "| --- | --- | --- |"]
    for row in record["figures"]:
        lines.append(f"| `{row['file']}` | {row['bytes']:,} | `{row['sha256'][:16]}` |")
    lines += ["", "## 复现命令（按顺序）", "", "```bash"]
    lines += record["commands"]
    lines += ["```", ""]
    missing = [row["file"] for row in record["tables"] if row.get("status") == "missing"]
    if missing:
        lines += ["> 注意：以下表在打包时缺失：" + ", ".join(missing), ""]
    return "\n".join(lines)


if __name__ == "__main__":
    result = package()
    print(f"tables: {len(result['tables'])} | figures: {len(result['figures'])} | "
          f"derived: {len(result['derived_data'])} | cost: {result['llm']['estimated_cost_rmb_total']} RMB")
    print(f"tests: {result['tests']['summary']}")
