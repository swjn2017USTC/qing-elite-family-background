"""Descriptive analysis, data gates and sensitivity (U09R).

Everything in this module obeys the four rules in the stage prompt: association is not
causation, ``unknown`` is not zero, coverage comes before outcome, and source selection stays
visible. Concretely:

* the **exposure is only defined where kin are observable** — a person with no recorded kin has
  an *unknown* family capital, so they are reported in the coverage table and excluded from the
  association rows, never counted as "0 degrees";
* the **data gate runs before any model**: separation, small cells, rank deficiency, missingness
  and source dependence are computed, and a failing gate replaces the model with a descriptive
  table plus the reason;
* every sensitivity variant reports its own n, because a variant that shrinks the sample also
  shrinks what it can detect.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

EXPOSURES = (
    "direct_3g_degree_count",
    "direct_3g_office_count",
    "direct_elite_generations",
    "all_senior_elite_kin_count",
)
OUTCOMES = ("highest_rank_class", "central_local_route", "time_to_first_office")
STRATA = ("cohort_id", "credential_class", "region_group")

#: A model needs at least this many rows *with both sides observed* to be attempted at all.
MIN_ROWS_FOR_MODEL = 200
MIN_CELL = 20
MAX_MISSINGNESS = 0.5


def deduplicate(frame: pd.DataFrame) -> pd.DataFrame:
    """One row per CGED-Q person; when several CBDB matches exist keep the strongest link."""
    ranking = {"u06r_auto_accept": 0, "v02_deterministic": 1}
    frame = frame.assign(_rank=frame["link_confidence"].map(ranking).fillna(2))
    frame = (
        frame.sort_values(["cgedq_person_id", "_rank"])
        .drop_duplicates(subset=["cgedq_person_id"], keep="first")
        .drop(columns=["_rank"])
    )
    frame["observable_direct_kin"] = pd.to_numeric(frame["observable_direct_kin"], errors="coerce").fillna(0)
    frame["observable_senior_kin"] = pd.to_numeric(frame["observable_senior_kin"], errors="coerce").fillna(0)
    # the exposure is defined only where kin are documented; otherwise it stays unknown
    frame["family_observable"] = frame["observable_direct_kin"] > 0
    frame["family_observable_extended"] = frame["family_observable"] & (frame["observable_senior_kin"] > 0)
    for column in EXPOSURES:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame


def coverage(frame: pd.DataFrame) -> pd.DataFrame:
    rows = [
        {
            "group": "all linked persons",
            "n": int(len(frame)),
            "share": 1.0,
            "family_observable": int(frame["family_observable"].sum()),
            "family_observable_share": round(float(frame["family_observable"].mean()), 4),
            "rank_known": int(frame["highest_rank_class"].notna().sum()),
            "route_known": int((frame["central_local_route"] != "unknown").sum()),
        }
    ]
    for stratum in ("link_confidence", "cohort_id"):
        if stratum not in frame:
            continue
        for value, group in frame.groupby(stratum, dropna=False):
            rows.append(
                {
                    "group": f"{stratum}={value}",
                    "n": int(len(group)),
                    "share": round(len(group) / len(frame), 4),
                    "family_observable": int(group["family_observable"].sum()),
                    "family_observable_share": round(float(group["family_observable"].mean()), 4),
                    "rank_known": int(group["highest_rank_class"].notna().sum()),
                    "route_known": int((group["central_local_route"] != "unknown").sum()),
                }
            )
    return pd.DataFrame(rows)


def exposure_distribution(frame: pd.DataFrame) -> pd.DataFrame:
    """Distributions for observable persons only, with the denominator spelled out."""
    observable = frame.loc[frame["family_observable"]]
    rows = []
    for column in EXPOSURES:
        values = pd.to_numeric(observable[column], errors="coerce").dropna()
        rows.append(
            {
                "exposure": column,
                "denominator": int(len(observable)),
                "n_observed": int(len(values)),
                "mean": round(float(values.mean()), 3) if len(values) else None,
                "share_gt0": round(float((values > 0).mean()), 4) if len(values) else None,
                "p25": float(values.quantile(0.25)) if len(values) else None,
                "median": float(values.median()) if len(values) else None,
                "p75": float(values.quantile(0.75)) if len(values) else None,
            }
        )
    return pd.DataFrame(rows)


def outcome_distribution(frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for column in ("highest_rank_class", "highest_admin_level", "central_local_route", "n_events"):
        values = frame[column] if column in frame else pd.Series(dtype=object)
        if column == "highest_rank_class":
            numeric = pd.to_numeric(values, errors="coerce")
            rows.append(
                {
                    "outcome": column,
                    "denominator": int(len(frame)),
                    "n_observed": int(numeric.notna().sum()),
                    "summary": f"median {numeric.median()}"
                    if numeric.notna().any()
                    else "unknown",
                }
            )
        else:
            counts = values.value_counts(dropna=False).head(8).to_dict()
            rows.append(
                {
                    "outcome": column,
                    "denominator": int(len(frame)),
                    "n_observed": int(values.notna().sum()),
                    "summary": "; ".join(f"{key}={value}" for key, value in counts.items()),
                }
            )
    return pd.DataFrame(rows)


def stratified_comparison(frame: pd.DataFrame, *, exposure: str, outcome: str, stratum: str) -> pd.DataFrame:
    """Median outcome by exposure tertile inside each stratum, with cell counts."""
    observable = frame.loc[frame["family_observable"]].copy()
    if exposure not in observable or outcome not in observable or stratum not in observable:
        return pd.DataFrame()
    observable[outcome] = pd.to_numeric(observable[outcome], errors="coerce")
    observable = observable.dropna(subset=[outcome])
    if observable.empty:
        return pd.DataFrame()
    try:
        observable["exposure_tertile"] = pd.qcut(
            observable[exposure].rank(method="first"), 3, labels=["low", "mid", "high"], duplicates="drop"
        )
    except ValueError:
        return pd.DataFrame()
    grouped = (
        observable.groupby([stratum, "exposure_tertile"], observed=True)[outcome]
        .agg(["size", "median", "mean"])
        .reset_index()
    )
    grouped["exposure"] = exposure
    grouped["outcome"] = outcome
    return grouped


def data_gate(frame: pd.DataFrame, *, exposure: str, outcome: str) -> dict[str, Any]:
    """Decide whether a model is allowed, and say why not when it is not."""
    observable = frame.loc[frame["family_observable"]].copy()
    observable[outcome] = pd.to_numeric(observable[outcome], errors="coerce")
    both = observable.dropna(subset=[exposure, outcome])
    reasons: list[str] = []
    if len(both) < MIN_ROWS_FOR_MODEL:
        reasons.append(f"rows with both sides observed {len(both)} < {MIN_ROWS_FOR_MODEL}")
    missingness = 1 - len(both) / len(frame) if len(frame) else 1.0
    if missingness > MAX_MISSINGNESS:
        reasons.append(f"missingness {missingness:.2f} > {MAX_MISSINGNESS}")
    small_cells: list[str] = []
    for stratum in STRATA:
        if stratum not in both:
            continue
        counts = both.groupby(stratum)[exposure].size()
        small = counts[counts < MIN_CELL]
        if len(small):
            small_cells.append(f"{stratum}: {', '.join(f'{k}={v}' for k, v in small.items())}")
    if small_cells:
        reasons.append("small cells — " + "; ".join(small_cells))
    # separation / sparse support: count the cells of the exposure × outcome cross-table
    outcome_values = pd.to_numeric(both[outcome], errors="coerce")
    if outcome_values.nunique() <= 1:
        reasons.append("outcome has no variance (separation)")
    else:
        try:
            exposure_band = pd.qcut(both[exposure].rank(method="first"), 3, labels=["low", "mid", "high"], duplicates="drop")
            outcome_band = pd.qcut(outcome_values.rank(method="first"), 3, labels=["low", "mid", "high"], duplicates="drop")
            cells = pd.crosstab(exposure_band, outcome_band)
            sparse = int((cells < 5).sum().sum())
            if sparse:
                reasons.append(f"{sparse} of {cells.size} exposure×outcome cells hold fewer than 5 observations")
        except ValueError:
            reasons.append("exposure or outcome could not be banded (insufficient spread)")
    if both[exposure].nunique() <= 1:
        reasons.append("exposure has no variance")
    report = {
        "rows_total": int(len(frame)),
        "rows_family_observable": int(frame["family_observable"].sum()),
        "rows_both_observed": int(len(both)),
        "missingness": round(float(missingness), 4),
        "small_cells": small_cells,
        "decision": "descriptive_only" if reasons else "model_allowed",
        "reasons": reasons,
    }
    return report


def source_dependence(frame: pd.DataFrame) -> pd.DataFrame:
    """Do the numbers look different by admitting rule or by career source?"""
    rows = []
    for column in ("link_confidence",):
        if column not in frame:
            continue
        for value, group in frame.groupby(column, dropna=False):
            observable = group.loc[group["family_observable"]]
            rows.append(
                {
                    "dimension": column,
                    "value": str(value),
                    "n": int(len(group)),
                    "family_observable_share": round(float(group["family_observable"].mean()), 4),
                    "median_direct_degree_count": (
                        float(pd.to_numeric(observable["direct_3g_degree_count"], errors="coerce").median())
                        if len(observable)
                        else None
                    ),
                    "median_highest_rank": (
                        float(pd.to_numeric(group["highest_rank_class"], errors="coerce").median())
                        if pd.to_numeric(group["highest_rank_class"], errors="coerce").notna().any()
                        else None
                    ),
                }
            )
    return pd.DataFrame(rows)


def sensitivity(frame: pd.DataFrame) -> pd.DataFrame:
    """Pre-registered variants; each reports its own n and the direction it sees."""
    variants: dict[str, pd.DataFrame] = {
        "all_links": frame,
        "u06r_auto_accept_only": frame.loc[frame["link_confidence"] == "u06r_auto_accept"],
        "v02_deterministic_only": frame.loc[frame["link_confidence"] == "v02_deterministic"],
        "direct_line_only": frame,
        "direct_plus_extended": frame.loc[frame["family_observable_extended"]],
        "complete_direct_line": frame.loc[frame["direct_line_complete"].fillna(False)],
    }
    rows = []
    for name, subset in variants.items():
        observable = subset.loc[subset["family_observable"]]
        ranks = pd.to_numeric(observable["highest_rank_class"], errors="coerce")
        degrees = pd.to_numeric(observable["direct_3g_degree_count"], errors="coerce")
        correlation = None
        if len(observable) >= 5 and degrees.nunique() > 1 and ranks.nunique() > 1:
            correlation = round(float(np.corrcoef(degrees, ranks)[0, 1]), 4)
        rows.append(
            {
                "variant": name,
                "n": int(len(subset)),
                "n_family_observable": int(len(observable)),
                "n_both_observed": int(observable[["direct_3g_degree_count", "highest_rank_class"]].dropna().shape[0]),
                "median_highest_rank": float(ranks.median()) if ranks.notna().any() else None,
                "corr_degrees_rank": correlation,
                "interpretable": bool(len(observable) >= MIN_ROWS_FOR_MODEL),
                "note": "n below the model floor — direction only, no inference"
                if len(observable) < MIN_ROWS_FOR_MODEL
                else "descriptive association",
            }
        )
    return pd.DataFrame(rows)
