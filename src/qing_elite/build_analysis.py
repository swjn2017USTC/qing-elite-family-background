"""P06 driver: coverage first, then classification, grouping and simple models.

    uv run python -m qing_elite.build_analysis

All 寒門/官宦 classification is computed here in Python from ``config/research.yaml``;
the LLM contributed *facts* (with verbatim evidence) and never a label.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Mapping

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from qing_elite.analysis.codebook import (
    Codebook,
    attach_groups,
    build_family_final,
    coverage_table,
    load_codebook,
    office_tier_matcher,
    person_indicators,
)
from qing_elite.analysis.statistics import (
    INDICATORS,
    generations_distribution,
    prevalence_by,
    run_models,
)
from qing_elite.utils.config import CBDB_SQLITE, OUTPUT_DIR, PROCESSED_DIR, load_offices

FIGURES = OUTPUT_DIR / "figures"
TABLES = OUTPUT_DIR / "tables"


def main() -> int:
    codebook = load_codebook()
    structured = pd.read_parquet(PROCESSED_DIR / "family_structured.parquet")
    enriched_path = PROCESSED_DIR / "family_enriched.parquet"
    enriched = pd.read_parquet(enriched_path) if enriched_path.exists() else None
    master = pd.read_parquet(PROCESSED_DIR / "officials_master.parquet")

    conn = sqlite3.connect(CBDB_SQLITE)
    try:
        tier_names = office_tier_matcher(load_offices(), conn)
    finally:
        conn.close()

    family_final = build_family_final(structured, enriched)
    persons = person_indicators(family_final, codebook, tier_names)
    persons = attach_groups(persons, master, codebook)
    persons["career_year"] = persons["career_first_year"]

    TABLES.mkdir(parents=True, exist_ok=True)
    FIGURES.mkdir(parents=True, exist_ok=True)
    family_final.to_parquet(PROCESSED_DIR / "family_final.parquet", index=False)
    persons.to_parquet(PROCESSED_DIR / "person_indicators.parquet", index=False)

    # ---------------------------------------------------------------- coverage first
    coverage = coverage_table(family_final, master)
    coverage.to_csv(TABLES / "p06_coverage_by_tier.csv", index=False)
    figure_coverage(coverage)

    # ---------------------------------------------------------------- descriptions
    prevalence = pd.concat(
        [
            prevalence_by(persons, indicator, ["highest_tier"])
            for indicator in INDICATORS
        ]
        + [
            prevalence_by(persons, "ancestor_official_any", ["tier_group", "cohort"]),
            prevalence_by(persons, "ancestor_official_any", ["banner_group"]),
            prevalence_by(persons, "ancestor_official_any", ["jiangnan_core"]),
            prevalence_by(persons, "ancestor_high_official", ["highest_tier"]),
        ],
        ignore_index=True,
    )
    prevalence.to_csv(TABLES / "p06_prevalence.csv", index=False)
    generations = generations_distribution(persons)
    generations.to_csv(TABLES / "p06_elite_generations.csv", index=False)
    na_table = na_accounting(persons)
    na_table.to_csv(TABLES / "p06_na_accounting.csv", index=False)
    bias = visibility_bias(persons)
    bias.to_csv(TABLES / "p06_visibility_bias.csv", index=False)

    # ------------------------------------------------------------------- models
    coefficients, diagnostics = run_models(persons, codebook)
    coefficients.to_csv(TABLES / "p06_models.csv", index=False)
    (TABLES / "p06_model_diagnostics.json").write_text(
        json.dumps(diagnostics, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # ------------------------------------------------------------------- figures
    figure_prevalence(prevalence)
    figure_generations(generations)
    figure_cohort_trend(persons, codebook)

    print("=== coverage by tier (first formal result) ===")
    print(coverage.to_string(index=False))
    print()
    print("=== indicator prevalence by tier ===")
    print(
        prevalence[prevalence["highest_tier"].notna()][
            ["indicator", "highest_tier", "n", "n_yes", "pct", "ci_low", "ci_high"]
        ].to_string(index=False)
    )
    print()
    print("=== models ===")
    print(coefficients.to_string(index=False))
    return 0


def visibility_bias(persons: pd.DataFrame) -> pd.DataFrame:
    """Do the indicators depend on how much we know? (plan §十一 visibility bias)

    If the share of "ancestor was an official" rises with the number of documented
    generations, then higher tiers simply have more documentation, not necessarily
    more elite ancestry. Reported so no gradient is read causally.
    """
    frame = persons[persons["ancestor_official_any"].notna()].copy()
    rows: list[dict[str, Any]] = []
    for slots, group in frame.groupby("n_slots_sufficient"):
        rows.append(
            {
                "n_slots_sufficient": int(slots),
                "n_persons": len(group),
                "ancestor_official_any_pct": round(100 * float(group["ancestor_official_any"].mean()), 2),
                "ancestor_degree_any_pct": round(100 * float(group["ancestor_degree_any"].mean()), 2),
                "mean_elite_generations": round(
                    float(group["elite_generations_count"].astype("Float64").mean()), 2
                ),
            }
        )
    return pd.DataFrame.from_records(rows)


def na_accounting(persons: pd.DataFrame) -> pd.DataFrame:
    """How many persons carry NA for each indicator, per tier (missing != 0)."""
    rows: list[dict[str, Any]] = []
    for tier, group in persons.groupby("highest_tier", dropna=False):
        row: dict[str, Any] = {"highest_tier": tier, "n_persons": len(group)}
        for indicator in INDICATORS:
            row[f"{indicator}_na"] = int(group[indicator].isna().sum())
            row[f"{indicator}_yes"] = int(group[indicator].fillna(0).sum())
        rows.append(row)
    return pd.DataFrame.from_records(rows)


# ---------------------------------------------------------------------- figures


def _save(fig, name: str) -> None:
    """Write both a raster and a vector copy; the final report uses the PDF."""
    fig.savefig(FIGURES / f"{name}.png")
    fig.savefig(FIGURES / f"{name}.pdf")
    plt.close(fig)


def _style() -> None:
    plt.rcParams.update(
        {
            # macOS CJK fonts, so the labels are legible instead of tofu boxes
            "font.sans-serif": [
                "Hiragino Sans GB",
                "PingFang SC",
                "Arial Unicode MS",
                "Noto Sans CJK SC",
                "DejaVu Sans",
            ],
            "axes.unicode_minus": False,
            "figure.dpi": 140,
            "font.size": 10,
            "axes.grid": True,
            "grid.alpha": 0.25,
            "axes.spines.top": False,
            "axes.spines.right": False,
        }
    )


def figure_coverage(coverage: pd.DataFrame) -> None:
    """Figure 1 of the study: family coverage by tier (visibility comes first)."""
    _style()
    frame = coverage.copy()
    labels = frame["tier"].astype(str)
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    width = 0.38
    x = range(len(frame))
    axes[0].bar([i - width / 2 for i in x], frame["N_any_generation"], width, label="≥1 代已知")
    axes[0].bar([i + width / 2 for i in x], frame["N_3gen_observed"], width, label="3 代已知")
    for i, total in enumerate(frame["N_total"]):
        axes[0].annotate(f"n={total:,}", (i, total), ha="center", va="bottom", fontsize=8)
    axes[0].set_xticks(list(x))
    axes[0].set_xticklabels(labels)
    axes[0].set_ylabel("人数")
    axes[0].set_title("各层级家世信息量（绝对人数）")
    axes[0].legend(frameon=False)

    axes[1].bar([i - width / 2 for i in x], frame["coverage_rate_any"], width, label="≥1 代覆盖率")
    axes[1].bar([i + width / 2 for i in x], frame["coverage_rate_3gen"], width, label="3 代覆盖率")
    axes[1].set_xticks(list(x))
    axes[1].set_xticklabels(labels)
    axes[1].set_ylabel("%")
    axes[1].set_title("各层级家世覆盖率（首要结果）")
    axes[1].legend(frameon=False)
    fig.suptitle("P06 首要结果：家世 coverage 随官位层级下降", fontsize=12)
    fig.tight_layout()
    _save(fig, "p06_coverage_by_tier")


def figure_prevalence(prevalence: pd.DataFrame) -> None:
    _style()
    frame = prevalence[prevalence["highest_tier"].notna()]
    if frame.empty:
        return
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2), sharey=True)
    for axis, indicator in zip(axes, ("ancestor_official_any", "ancestor_degree_any")):
        subset = frame[frame["indicator"] == indicator].sort_values("highest_tier")
        if subset.empty:
            continue
        positions = range(len(subset))
        axis.errorbar(
            list(positions),
            subset["pct"],
            yerr=[subset["pct"] - subset["ci_low"], subset["ci_high"] - subset["pct"]],
            fmt="o",
            capsize=3,
        )
        for position, (_, row) in zip(positions, subset.iterrows()):
            axis.annotate(f"{int(row['n'])}", (position, row["pct"]), textcoords="offset points", xytext=(0, 8), fontsize=8)
        axis.set_xticks(list(positions))
        axis.set_xticklabels(subset["highest_tier"].astype(str))
        axis.set_title(indicator)
        axis.set_ylabel("有家世证据的比例 (%)")
    fig.suptitle("家世指标随层级的分布（点=比例，须=95% Wilson 区间，数字=分母）", fontsize=12)
    fig.tight_layout()
    _save(fig, "p06_prevalence_by_tier")


def figure_generations(generations: pd.DataFrame) -> None:
    _style()
    frame = generations.copy()
    columns = [
        column
        for column in frame.columns
        if isinstance(column, (int, float)) and pd.notna(column) and int(column) in (0, 1, 2, 3)
    ]
    if not columns:
        return
    fig, axis = plt.subplots(figsize=(8, 4.2))
    bottom = [0] * len(frame)
    for column in columns:
        values = frame[column].tolist()
        axis.bar(frame["highest_tier"].astype(str), values, bottom=bottom, label=f"{int(column)} 代")
        bottom = [b + v for b, v in zip(bottom, values)]
    if "n_na" in frame:
        axis.bar(frame["highest_tier"].astype(str), frame["n_na"], bottom=bottom, label="NA（信息不足）", color="#cccccc")
    axis.set_ylabel("人数")
    axis.set_title("elite_generations_count 分布（灰色 = 信息不足，不用 0 填充）")
    axis.legend(frameon=False)
    fig.tight_layout()
    _save(fig, "p06_elite_generations")


def figure_cohort_trend(persons: pd.DataFrame, codebook: Codebook) -> None:
    _style()
    frame = persons[persons["ancestor_official_any"].notna() & persons["cohort"].notna()]
    frame = frame[frame["highest_tier"].isin(["A1", "A2", "A3", "B", "C"])]
    if frame.empty:
        return
    table = (
        frame.groupby(["cohort", "tier_group"])
        .agg(n=("person_uid", "size"), yes=("ancestor_official_any", "sum"))
        .reset_index()
    )
    table["pct"] = 100 * table["yes"] / table["n"]
    fig, axis = plt.subplots(figsize=(8, 4.2))
    for tier_group, group in table.groupby("tier_group"):
        group = group.sort_values("cohort")
        axis.plot(group["cohort"], group["pct"], marker="o", label=f"{tier_group} (A/B/C)")
        for _, row in group.iterrows():
            axis.annotate(f"n={int(row['n'])}", (row["cohort"], row["pct"]), textcoords="offset points", xytext=(0, 6), fontsize=8)
    axis.set_ylabel("至少一代任官的比例 (%)")
    axis.set_title("1644–1820 A/B/C 长时段趋势（仅长时段，不含 D）")
    axis.legend(frameon=False)
    fig.tight_layout()
    _save(fig, "p06_cohort_trend_abc")


if __name__ == "__main__":
    raise SystemExit(main())
