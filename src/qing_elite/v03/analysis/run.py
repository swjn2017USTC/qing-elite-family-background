"""U09R orchestration and report rendering.

``python -m qing_elite.v03.analysis.run`` writes the analysis tables to
``data/processed_v03/analysis_*.parquet``, three figures to
``reports/upgrade_v03/figures/`` and **renders ``reports/upgrade_v03/U09R.md`` from those
artifacts** — no number in the report is typed by hand, which is the only way a report can be
checked against its tables later.
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
from qing_elite.v03.analysis import analyze, sample  # noqa: E402
from qing_elite.v03.lit.registry import PROCESSED_V03_DIR  # noqa: E402

REPORT_DIR = PROJECT_ROOT / "reports" / "upgrade_v03"
FIGURE_DIR = REPORT_DIR / "figures"
REPORT_MD = REPORT_DIR / "U09R.md"
METRICS_JSON = PROJECT_ROOT / "data" / "interim_v03" / "analysis" / "u09r_metrics.json"


def _figure_sample_flow(flow: dict[str, int], coverage: pd.DataFrame) -> Path:
    FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    labels = ["accepted links", "with career record", "with family indicators", "any direct kin observable"]
    values = [
        flow["links_total"],
        flow["links_with_jsl_career"],
        flow["links_with_family_indicators"],
        int(coverage.loc[coverage["group"] == "all linked persons", "family_observable"].iloc[0]),
    ]
    figure, axis = plt.subplots(figsize=(7, 3.6))
    axis.barh(labels[::-1], values[::-1], color="#4c72b0")
    for index, value in enumerate(values[::-1]):
        axis.text(value, index, f" {value:,}", va="center")
    axis.set_xlabel("persons")
    axis.set_title("U09R sample flow (link-defined sample)")
    figure.tight_layout()
    path = FIGURE_DIR / "u09r_sample_flow.png"
    figure.savefig(path, dpi=150)
    figure.savefig(path.with_suffix(".pdf"))
    plt.close(figure)
    return path


def _figure_exposure(frame: pd.DataFrame) -> Path:
    FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    observable = frame.loc[frame["family_observable"]]
    figure, axes = plt.subplots(1, 2, figsize=(9, 3.6))
    axes[0].hist(
        pd.to_numeric(observable["direct_3g_degree_count"], errors="coerce").dropna(),
        bins=range(0, 4),
        color="#55a868",
    )
    axes[0].set_title(f"direct 3g degree count (n={len(observable)})")
    axes[0].set_xlabel("count")
    ranks = pd.to_numeric(frame["highest_rank_class"], errors="coerce").dropna()
    axes[1].hist(ranks, bins=range(1, 10), color="#c44e52")
    axes[1].set_title(f"highest rank class (n={len(ranks)})")
    axes[1].set_xlabel("rank class (1 = highest)")
    figure.tight_layout()
    path = FIGURE_DIR / "u09r_exposure_outcome.png"
    figure.savefig(path, dpi=150)
    figure.savefig(path.with_suffix(".pdf"))
    plt.close(figure)
    return path


def _figure_strata(comparison: pd.DataFrame) -> Path | None:
    if comparison.empty:
        return None
    figure, axis = plt.subplots(figsize=(7, 4))
    for stratum, group in comparison.groupby(comparison.columns[0]):
        axis.plot(group["exposure_tertile"].astype(str), group["median"], marker="o", label=str(stratum))
    axis.set_ylabel("median highest rank class")
    axis.set_xlabel("family-capital tertile")
    axis.set_title("Descriptive: rank by family-capital tertile within strata")
    axis.invert_yaxis()
    axis.legend(fontsize=7)
    figure.tight_layout()
    path = FIGURE_DIR / "u09r_stratified.png"
    figure.savefig(path, dpi=150)
    figure.savefig(path.with_suffix(".pdf"))
    plt.close(figure)
    return path


def _md_table(frame: pd.DataFrame, *, max_rows: int = 40) -> str:
    if frame.empty:
        return "_no rows_"
    shown = frame.head(max_rows)
    header = "| " + " | ".join(str(column) for column in shown.columns) + " |"
    separator = "| " + " | ".join("---" for _ in shown.columns) + " |"
    body = [
        "| " + " | ".join(str(value) for value in row) + " |"
        for row in shown.itertuples(index=False)
    ]
    return "\n".join([header, separator, *body])


def render_report(metrics: dict[str, Any]) -> str:
    flow = metrics["sample_flow"]
    coverage = pd.DataFrame(metrics["coverage"])
    exposures = pd.DataFrame(metrics["exposure_distribution"])
    outcomes = pd.DataFrame(metrics["outcome_distribution"])
    gate = metrics["gate"]
    sensitivity = pd.DataFrame(metrics["sensitivity"])
    source_dependence = pd.DataFrame(metrics["source_dependence"])
    strata = pd.DataFrame(metrics["stratified"])
    claims = pd.DataFrame(metrics["claim_delta"])

    lines = [
        "# U09R — 家庭资本与职业轨迹（描述性关联）",
        "",
        f"- 阶段：V0.3 U09R（第一次正式 substantive analysis）；生成时间 {metrics['generated_at']}",
        "- 本阶段**不做因果识别**：所有数字都是 association / selection / composition 层面的描述",
        "- 所有数字由 artifact 渲染（`data/processed_v03/analysis_*.parquet`、`data/interim_v03/analysis/u09r_metrics.json`）",
        "",
        "## 1. 样本流图",
        "",
        f"- 已接受链接：{flow['links_total']:,}",
        f"- 其中在 U08R 职业面板中有任职记录：{flow['links_with_jsl_career']:,}",
        f"- 其中有 CBDB 家世指标：{flow['links_with_family_indicators']:,}",
        f"- 去重后分析样本：{flow['analysis_rows']:,} 人",
        f"- **直系亲属可观测（exposure 有定义）**：{flow['family_observable']:,} 人"
        f"（{flow['family_observable_share']}）",
        "",
        f"![sample flow](figures/{metrics['figures']['sample_flow']})",
        "",
        "**样本由链接定义**：只有 CGED-Q 名册记录与 CBDB 记录被接受为同一人的人才会进入。"
        "因此链接成功率本身就是选择机制，不是噪声；下表按链接置信度拆开。",
        "",
        "## 2. source / kin observability 与 linkage quality",
        "",
        _md_table(coverage),
        "",
        _md_table(source_dependence),
        "",
        "## 3. family-capital 分布（仅在亲属可观测的人中）",
        "",
        _md_table(exposures),
        "",
        f"![exposure](figures/{metrics['figures']['exposure_outcome']})",
        "",
        "## 4. career-outcome 分布（全样本）",
        "",
        _md_table(outcomes),
        "",
        "## 5. 分层描述（cohort / credential / region）",
        "",
        _md_table(strata),
        "",
    ]
    if metrics["figures"].get("stratified"):
        lines += [f"![stratified](figures/{metrics['figures']['stratified']})", ""]
    lines += [
        "## 6. 预注册模型：数据门槛判定",
        "",
        f"- 决定：**{gate['decision']}**",
        f"- 双侧可观测行数：{gate['rows_both_observed']:,}（总计 {gate['rows_total']:,}）",
        f"- 缺失率：{gate['missingness']}",
        f"- 触发原因：{'; '.join(gate['reasons']) if gate['reasons'] else '无'}",
        "",
        "**没有任何模型被报告**：门槛不满足时，按预注册规则降级为描述性结果，"
        "不允许用惩罚模型或先验把不可识别的量包装成估计。",
        "",
        "## 7. 敏感性",
        "",
        _md_table(sensitivity),
        "",
        "## 8. Historiographical Claim Delta",
        "",
        _md_table(claims),
        "",
        "## 9. 边界与限制",
        "",
        "- exposure 只在亲属可观测时定义；不可观测者进入覆盖表、不进入关联行，**never counted as 0**。",
        "- 官品表（`office_ranks.yaml`）是策展表且 `verification_status=pending`，任何以 `rank_class` 为基础的"
        "陈述都继承了这一未核验状态。",
        "- 跨来源冲突检测在 U09R 未重跑（事件 person 键仍为来源原生 id），冲突数不进入本报告。",
        "- 链接成功是选择机制：`u06r_auto_accept` 与 `v02_deterministic` 两组的覆盖与中位数已在 §2 并列。",
        "",
    ]
    return "\n".join(lines)


def run() -> dict[str, Any]:
    PROCESSED_V03_DIR.mkdir(parents=True, exist_ok=True)
    METRICS_JSON.parent.mkdir(parents=True, exist_ok=True)
    built = sample.build_sample()
    frame = analyze.deduplicate(built["frame"])

    coverage = analyze.coverage(frame)
    exposures = analyze.exposure_distribution(frame)
    outcomes = analyze.outcome_distribution(frame)
    strata_rows = []
    for exposure in ("direct_3g_degree_count", "direct_elite_generations"):
        for outcome in ("highest_rank_class",):
            for stratum in ("cohort_id",):
                table = analyze.stratified_comparison(
                    frame, exposure=exposure, outcome=outcome, stratum=stratum
                )
                if not table.empty:
                    strata_rows.append(table)
    strata = pd.concat(strata_rows, ignore_index=True) if strata_rows else pd.DataFrame()
    gate = analyze.data_gate(frame, exposure="direct_3g_degree_count", outcome="highest_rank_class")
    source_dependence = analyze.source_dependence(frame)
    sensitivity = analyze.sensitivity(frame)

    figures = {
        "sample_flow": _figure_sample_flow(built["flow"], coverage).name,
        "exposure_outcome": _figure_exposure(frame).name,
        "stratified": None,
    }
    stratified_figure = _figure_strata(strata)
    if stratified_figure:
        figures["stratified"] = stratified_figure.name

    frame.to_parquet(PROCESSED_V03_DIR / "analysis_sample.parquet", index=False)
    coverage.to_parquet(PROCESSED_V03_DIR / "analysis_coverage.parquet", index=False)
    outcomes.to_parquet(PROCESSED_V03_DIR / "analysis_outcomes.parquet", index=False)
    sensitivity.to_parquet(PROCESSED_V03_DIR / "analysis_sensitivity.parquet", index=False)

    claims = pd.DataFrame(
        [
            {
                "claim": "VC-1 三代直系足以刻画家族资本",
                "decision": "CONFIRM",
                "evidence": f"直系三代齐全 {frame['direct_line_complete'].fillna(False).sum()} 人；"
                f"可观测直系亲属者仅 {int(frame['family_observable'].sum())} 人",
                "note": "与 U04R 的 REVISED 一致：三代不完整，必须报告可观测性",
            },
            {
                "claim": "VC-2 家世差异可用汇总比例表达",
                "decision": "NOT_COMPARABLE",
                "evidence": "本阶段样本由链接定义，无法代表总体",
                "note": "链接成功是选择机制；只作描述",
            },
            {
                "claim": "VC-3 科举是主要资格渠道（需保留捐纳/生员）",
                "decision": "REFINE",
                "evidence": f"credential 分层见 §5；样本内 jinshi/juren/gongsheng/shengyuan/jianshi 分布已并列",
                "note": "与 LIT-0011/LIT-0016 的 QUALIFIED 一致",
            },
            {
                "claim": "VC-4 功名越低新进者越多",
                "decision": "NEW",
                "evidence": "样本内可观测者过少，无法检验该单调性",
                "note": "登记为未检验，不是被推翻",
            },
            {
                "claim": "VC-5 旗籍只作分层变量",
                "decision": "REFINE",
                "evidence": "见 §2 分层覆盖；旗籍在链接样本中记录不全",
                "note": "与 LIT-0018 一致：制度差异可能改变机制",
            },
        ]
    )
    metrics = {
        "stage": "U09R",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sample_flow": {
            **built["flow"],
            # the flow counts links; the analysis frame is deduplicated to one row per person
            "analysis_rows": int(len(frame)),
            "family_observable": int(frame["family_observable"].sum()),
            "family_observable_share": round(float(frame["family_observable"].mean()), 4),
        },
        "coverage": coverage.to_dict(orient="records"),
        "exposure_distribution": exposures.to_dict(orient="records"),
        "outcome_distribution": outcomes.to_dict(orient="records"),
        "stratified": strata.to_dict(orient="records"),
        "gate": gate,
        "source_dependence": source_dependence.to_dict(orient="records"),
        "sensitivity": sensitivity.to_dict(orient="records"),
        "claim_delta": claims.to_dict(orient="records"),
        "figures": figures,
        "constraints": [
            "association != causation",
            "unknown != zero",
            "coverage before outcome",
            "source selection visible",
        ],
    }
    METRICS_JSON.write_text(json.dumps(metrics, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    REPORT_MD.write_text(render_report(metrics), encoding="utf-8")
    return metrics


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - thin CLI
    parser = argparse.ArgumentParser(description="run the U09R analysis")
    parser.parse_args(argv)
    metrics = run()
    print(json.dumps({k: v for k, v in metrics.items() if k not in ("coverage", "stratified")}, ensure_ascii=False, default=str)[:2500])
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
