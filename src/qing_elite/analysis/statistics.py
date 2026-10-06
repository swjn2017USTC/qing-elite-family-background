"""Descriptive statistics and simple models for the family indicators (P06).

Deliberately plain: prevalence with Wilson intervals, cross-tabs, and logit models
with at most three categorical predictors. No causal claims, no machine learning.
"""

from __future__ import annotations

import math
from typing import Any, Iterable, Mapping, Sequence

import pandas as pd

from qing_elite.analysis.codebook import Codebook

INDICATORS = (
    "ancestor_official_any",
    "ancestor_degree_any",
    "ancestor_high_official",
    "strict_commoner_3g",
)


def wilson_interval(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval in percent; robust for small cells."""
    if total <= 0:
        return (float("nan"), float("nan"))
    phat = successes / total
    denominator = 1 + z**2 / total
    centre = (phat + z**2 / (2 * total)) / denominator
    half = z * math.sqrt(phat * (1 - phat) / total + z**2 / (4 * total**2)) / denominator
    return (round(100 * max(0.0, centre - half), 2), round(100 * min(1.0, centre + half), 2))


def prevalence_by(
    persons: pd.DataFrame,
    indicator: str,
    group_columns: Sequence[str],
    *,
    dropna: bool = True,
) -> pd.DataFrame:
    """Counts, share and 95% interval per group cell; NA rows are counted separately."""
    frame = persons.dropna(subset=[indicator]) if dropna else persons
    na_count = int(persons[indicator].isna().sum())
    rows: list[dict[str, Any]] = []
    grouped = frame.groupby(list(group_columns), dropna=False) if group_columns else [((), frame)]
    for keys, group in grouped:
        if not isinstance(keys, tuple):
            keys = (keys,)
        successes = int(group[indicator].sum())
        total = int(len(group))
        low, high = wilson_interval(successes, total)
        row = {column: value for column, value in zip(group_columns, keys)}
        row.update(
            {
                "indicator": indicator,
                "n": total,
                "n_yes": successes,
                "pct": round(100 * successes / total, 2) if total else float("nan"),
                "ci_low": low,
                "ci_high": high,
                "n_na_excluded": na_count,
            }
        )
        rows.append(row)
    return pd.DataFrame.from_records(rows)


def generations_distribution(persons: pd.DataFrame, group_column: str = "highest_tier") -> pd.DataFrame:
    """Distribution of ``elite_generations_count`` (0-3, NA reported separately)."""
    frame = persons.copy()
    frame["elite_generations_count"] = frame["elite_generations_count"].astype("Float64")
    table = (
        frame.pivot_table(
            index=group_column,
            columns="elite_generations_count",
            values="person_uid",
            aggfunc="count",
            dropna=False,
        )
        .fillna(0)
        .astype(int)
    )
    table = table.loc[:, [column for column in table.columns if pd.notna(column)]]
    table["n_total"] = table.sum(axis=1)
    na = frame[frame["elite_generations_count"].isna()].groupby(group_column).size().rename("n_na")
    table = table.join(na).fillna(0)
    return table.reset_index()


def _model_frame(
    persons: pd.DataFrame, codebook: Codebook, window: str, tier_groups: Iterable[str]
) -> pd.DataFrame:
    """Restrict to the window and to the given **tier groups** (A/B/C/D labels).

    P07's denominator audit found this filter previously received individual tier codes
    (A1/A2/A3/...), which silently excluded the whole A group from both models.
    """
    spec = codebook.config["time_windows"][window]
    frame = persons[
        persons["ancestor_official_any"].notna() & persons["tier_group"].isin(list(tier_groups))
    ].copy()
    frame = frame[frame["career_year"] >= spec["start"]]
    frame = frame[frame["career_year"] <= spec["end"]]
    frame["tier_group"] = frame["tier_group"].astype(str)
    frame["cohort"] = frame["cohort"].fillna("unknown")
    frame["banner_group"] = frame["banner_group"].fillna("unknown")
    return frame


def fit_logit(frame: pd.DataFrame, formula: str) -> dict[str, Any]:
    """Fit a binomial logit; report coefficients, N and any fitting failure verbatim."""
    import statsmodels.api as sm
    import statsmodels.formula.api as smf

    if frame["ancestor_official_any"].nunique() < 2:
        return {"status": "not_estimable", "reason": "outcome has a single level", "n": len(frame)}
    try:
        model = smf.glm(formula=formula, data=frame, family=sm.families.Binomial())
        result = model.fit()
        params = result.params
        conf = result.conf_int()
        coefficients = [
            {
                "term": term,
                "coef": round(float(params[term]), 4),
                "std_err": round(float(result.bse[term]), 4),
                "odds_ratio": round(float(math.exp(params[term])), 4),
                "ci_low": round(float(math.exp(conf.loc[term, 0])), 4),
                "ci_high": round(float(math.exp(conf.loc[term, 1])), 4),
                "p_value": round(float(result.pvalues[term]), 4),
            }
            for term in params.index
        ]
        return {
            "status": "ok",
            "n": int(result.nobs),
            "formula": formula,
            "converged": bool(result.converged),
            "coefficients": coefficients,
            "warnings": [str(w) for w in getattr(result, "warnings", []) or []],
            "aic": round(float(result.aic), 2),
        }
    except Exception as error:  # noqa: BLE001 - diagnostics must record the failure verbatim
        return {
            "status": "failed",
            "reason": f"{type(error).__name__}: {error}",
            "n": len(frame),
            "formula": formula,
        }


def fit_with_fallback(
    frame: pd.DataFrame, formulas: Sequence[str]
) -> tuple[dict[str, Any], list[str]]:
    """Try each formula in order; the first estimable fit wins.

    Perfect separation on a one-person banner cell made the full model diverge, so the
    fallback chain is explicit and the attempts are reported rather than hidden.
    """
    attempted: list[str] = []
    for formula in formulas:
        attempted.append(formula)
        fit = fit_logit(frame, formula)
        if fit.get("status") == "ok" and fit.get("converged", True):
            return fit, attempted
    return fit_logit(frame, formulas[-1]), attempted


def model_diagnostics(frame: pd.DataFrame, spec: Mapping[str, Any]) -> dict[str, Any]:
    """Cell counts + NA accounting so no result is reported on a hidden denominator."""
    cells = (
        frame.groupby(["tier_group", "cohort"], dropna=False)
        .agg(n=("person_uid", "size"), n_yes=("ancestor_official_any", "sum"))
        .reset_index()
    )
    return {
        "n_persons": int(len(frame)),
        "n_with_outcome": int(frame["ancestor_official_any"].notna().sum()),
        "n_missing_outcome": int(frame["ancestor_official_any"].isna().sum()),
        "cells": cells.to_dict("records"),
        "min_cell": int(cells["n"].min()) if len(cells) else 0,
        "spec": dict(spec),
    }


def run_models(persons: pd.DataFrame, codebook: Codebook) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    """Fit the pre-registered models and return (coefficient table, diagnostics)."""
    models = codebook.config["models"]
    frames: list[pd.DataFrame] = []
    diagnostics: list[dict[str, Any]] = []

    common = _model_frame(
        persons,
        codebook,
        models["common_window"]["window"],
        tier_groups=list(codebook.tier_groups),
    )
    common_fit, common_attempts = fit_with_fallback(
        common,
        [
            models["common_window"]["formula"],
            "ancestor_official_any ~ C(tier_group) + C(cohort)",
            "ancestor_official_any ~ C(tier_group)",
        ],
    )
    diagnostics.append(
        {
            "model": "common_window",
            **model_diagnostics(common, models["common_window"]),
            "formulas_attempted": common_attempts,
            "formula_used": common_fit.get("formula"),
        }
    )
    frames.append(_coefficients(common_fit, "common_window"))

    abc_groups = [group for group in codebook.tier_groups if group != "D"]
    long_run = _model_frame(persons, codebook, models["long_run"]["window"], tier_groups=abc_groups)
    long_fit, long_attempts = fit_with_fallback(
        long_run, [models["long_run"]["formula"], "ancestor_official_any ~ C(tier_group)"]
    )
    diagnostics.append(
        {
            "model": "long_run",
            **model_diagnostics(long_run, models["long_run"]),
            "formulas_attempted": long_attempts,
            "formula_used": long_fit.get("formula"),
        }
    )
    frames.append(_coefficients(long_fit, "long_run"))

    table = pd.concat([frame for frame in frames if not frame.empty], ignore_index=True)
    for fit, name in ((common_fit, "common_window"), (long_fit, "long_run")):
        if fit["status"] != "ok":
            diagnostics.append({"model": name, "status": fit["status"], "reason": fit.get("reason")})
    return table, diagnostics


def _coefficients(fit: Mapping[str, Any], model_name: str) -> pd.DataFrame:
    if fit.get("status") != "ok":
        return pd.DataFrame(
            [
                {
                    "model": model_name,
                    "term": "NOT_ESTIMABLE",
                    "coef": None,
                    "odds_ratio": None,
                    "note": fit.get("reason", fit.get("status")),
                    "n": fit.get("n"),
                }
            ]
        )
    rows = []
    for coefficient in fit["coefficients"]:
        rows.append(
            {
                "model": model_name,
                "term": coefficient["term"],
                "coef": coefficient["coef"],
                "odds_ratio": coefficient["odds_ratio"],
                "ci_low": coefficient["ci_low"],
                "ci_high": coefficient["ci_high"],
                "p_value": coefficient["p_value"],
                "n": fit["n"],
                "note": "; ".join(fit.get("warnings") or []) or None,
            }
        )
    return pd.DataFrame.from_records(rows)
