"""U10R orchestration: recover v0.1, build the extension frame, compare, render.

Outputs:

* ``data/processed_v03/extension_early_qing.parquet`` — the A/B/C extension frame with
  recomputed documented family capital
* ``data/processed_v03/extension_variable_mapping.parquet`` — what mapped, what did not, and why
* ``data/processed_v03/extension_comparisons.parquet`` — every comparison row with its
  classification
* ``reports/upgrade_v03/U10R.md`` plus two figures, rendered from those artifacts
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from qing_elite.utils.config import PROJECT_ROOT  # noqa: E402
from qing_elite.v03.extension import compare, legacy  # noqa: E402
from qing_elite.v03.lit.registry import PROCESSED_V03_DIR  # noqa: E402

REPORT_DIR = PROJECT_ROOT / "reports" / "upgrade_v03"
FIGURE_DIR = REPORT_DIR / "figures"
REPORT_MD = REPORT_DIR / "U10R.md"
METRICS_JSON = PROJECT_ROOT / "data" / "interim_v03" / "extension" / "u10r_metrics.json"


def _md_table(frame: pd.DataFrame, *, max_rows: int = 30) -> str:
    if frame.empty:
        return "_no rows_"
    shown = frame.head(max_rows)
    header = "| " + " | ".join(str(column) for column in shown.columns) + " |"
    separator = "| " + " | ".join("---" for _ in shown.columns) + " |"
    body = ["| " + " | ".join(str(value) for value in row) + " |" for row in shown.itertuples(index=False)]
    return "\n".join([header, separator, *body])


def _figure_observability(documentation: pd.DataFrame) -> Path:
    FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    figure, axis = plt.subplots(figsize=(7, 3.4))
    labels = [row.cohort for row in documentation.itertuples(index=False)]
    shares = [row.share for row in documentation.itertuples(index=False)]
    axis.bar(labels, shares, color="#4c72b0")
    for index, (label, share) in enumerate(zip(labels, shares)):
        axis.text(index, share, f"{share:.3f}", ha="center", va="bottom")
    axis.set_ylabel("share with observable family background")
    axis.set_title("U10R: family observability, early/mid vs late cohort")
    figure.tight_layout()
    path = FIGURE_DIR / "u10r_observability.png"
    figure.savefig(path, dpi=150)
    figure.savefig(path.with_suffix(".pdf"))
    plt.close(figure)
    return path


def _figure_capital(capital: pd.DataFrame) -> Path:
    FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    figure, axis = plt.subplots(figsize=(7, 3.4))
    labels = [row.cohort for row in capital.itertuples(index=False)]
    degrees = [row.mean_degree_count for row in capital.itertuples(index=False)]
    offices = [row.mean_office_count for row in capital.itertuples(index=False)]
    positions = range(len(labels))
    axis.bar([value - 0.18 for value in positions], degrees, width=0.36, label="mean degree count")
    axis.bar([value + 0.18 for value in positions], offices, width=0.36, label="mean office count")
    axis.set_xticks(list(positions))
    axis.set_xticklabels(labels)
    axis.set_ylabel("mean count among observable persons")
    axis.set_title("U10R: documented direct family capital (period-specific)")
    axis.legend(fontsize=8)
    figure.tight_layout()
    path = FIGURE_DIR / "u10r_family_capital.png"
    figure.savefig(path, dpi=150)
    figure.savefig(path.with_suffix(".pdf"))
    plt.close(figure)
    return path


def render_report(metrics: dict[str, Any]) -> str:
    lines = [
        "# U10R — early/mid-Qing elite extension（A/B/C 作为扩展，不主导设计）",
        "",
        f"- 阶段：V0.3 U10R；生成时间 {metrics['generated_at']}",
        "- 所有数字由 artifact 渲染；本阶段**不做 pooled regression**",
        "",
        "## 1. 复用而非重采",
        "",
        f"- v0.1 冻结产物从 git 历史恢复：`person_indicators` / `family_final` / `officials_master`"
        f"（{metrics['legacy']['persons']:,} / {metrics['legacy']['slots']:,} / {metrics['legacy']['master']:,} 行）",
        "- 未重新运行 v0.1 流水线、未新增采集",
        "",
        "## 2. 变量映射（旧 → V0.3）",
        "",
        _md_table(pd.DataFrame(metrics["mapping"])),
        "",
        "## 3. 扩展样本",
        "",
        f"- A/B/C 人数：{metrics['early_qing']['persons']:,}（tier 分布 {metrics['early_qing']['tier_counts']}）",
        f"- 其中家世可观测：{metrics['early_qing']['family_observable']:,}"
        f"（{metrics['early_qing']['family_observable_share']}）",
        f"- D 层：**未纳入**（{metrics['early_qing']['d_layer_reason']}）",
        "",
        "## 4. 比较（逐项分类）",
        "",
    ]
    for name, rows in metrics["comparisons"].items():
        lines += [f"### {name}", "", _md_table(pd.DataFrame(rows)), ""]
    lines += [
        f"![observability](figures/{metrics['figures']['observability']})",
        "",
        f"![capital](figures/{metrics['figures']['family_capital']})",
        "",
        "## 5. 结论分类汇总",
        "",
        _md_table(pd.DataFrame([{"classification": key, "rows": value} for key, value in metrics["classification_counts"].items()])),
        "",
        f"**pooled regression**：未运行。原因：{metrics['pooled_regression']['reason']}",
        "",
        "## 6. 限制",
        "",
        "- 两个 cohort 的抽样设计不同（精英普查 vs 链接定义的样本），任何时期差异都混入了设计差异。",
        "- v0.1 的槽位定义与 V0.3 的 CBDB 亲属映射来自两条不同管线；计数可比、水平不可比。",
        "- banner 在晚期 cohort 中记录极少；`unknown` 从不读作非旗人。",
        "- 官品表仍 `verification_status=pending`；本阶段未使用 `rank_class` 做任何跨期比较。",
        "",
    ]
    return "\n".join(lines)


def run() -> dict[str, Any]:
    PROCESSED_V03_DIR.mkdir(parents=True, exist_ok=True)
    METRICS_JSON.parent.mkdir(parents=True, exist_ok=True)
    legacy_frame = legacy.recover_legacy()
    early = legacy.early_qing_frame(legacy_frame)

    analysis_sample = PROCESSED_V03_DIR / "analysis_sample.parquet"
    if not analysis_sample.exists():
        raise FileNotFoundError("U09R analysis sample missing; run the U09R stage first")
    late = pd.read_parquet(analysis_sample)
    late["family_observable"] = late["family_observable"].astype(bool)

    mapping = legacy.mapping_table()
    comparison = compare.evaluate(early, late)

    early.to_parquet(PROCESSED_V03_DIR / "extension_early_qing.parquet", index=False)
    mapping.to_parquet(PROCESSED_V03_DIR / "extension_variable_mapping.parquet", index=False)
    combined = pd.concat(comparison["tables"].values(), ignore_index=True)
    combined.to_parquet(PROCESSED_V03_DIR / "extension_comparisons.parquet", index=False)

    metrics = {
        "stage": "U10R",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "legacy": legacy_frame.to_dict(),
        "early_qing": legacy.summary(early),
        "late_qing": {
            "persons": int(len(late)),
            "family_observable": int(late["family_observable"].sum()),
            "family_observable_share": round(float(late["family_observable"].mean()), 4),
            "source": "U09R link-defined sample",
        },
        "mapping": mapping.to_dict(orient="records"),
        "comparisons": {name: table.to_dict(orient="records") for name, table in comparison["tables"].items()},
        "classification_counts": comparison["classification_counts"],
        "pooled_regression": comparison["pooled_regression"],
        "figures": {},
    }
    metrics["figures"]["observability"] = _figure_observability(
        comparison["tables"]["documentation_share"]
    ).name
    metrics["figures"]["family_capital"] = _figure_capital(
        comparison["tables"]["direct_capital"]
    ).name
    METRICS_JSON.write_text(json.dumps(metrics, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    REPORT_MD.write_text(render_report(metrics), encoding="utf-8")
    return metrics


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - thin CLI
    parser = argparse.ArgumentParser(description="build the U10R extension comparison")
    parser.parse_args(argv)
    metrics = run()
    print(json.dumps({k: v for k, v in metrics.items() if k not in ("mapping", "comparisons")}, ensure_ascii=False, default=str)[:2000])
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
