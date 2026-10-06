"""Threshold calibration, auto-accept region and abstention (U06R).

The stage gate is stated as "auto-accept requires held-out precision ≥ 0.99". That is a
*region* to be found, not a hope: this module sweeps thresholds on the held-out split, and
when no region reaches the precision floor at a usable support it does **not** lower the
floor — it shrinks the region (higher threshold, fewer pairs, more abstention), and says so.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

DEFAULT_THRESHOLDS = tuple(round(value, 2) for value in [i / 20 for i in range(1, 20)])


def threshold_table(
    scores: pd.Series,
    labels: pd.Series,
    *,
    thresholds: tuple[float, ...] = DEFAULT_THRESHOLDS,
) -> list[dict[str, Any]]:
    total_positives = int(labels.sum())
    rows: list[dict[str, Any]] = []
    for threshold in thresholds:
        predicted = scores >= threshold
        true_positive = int((predicted & (labels == 1)).sum())
        false_positive = int((predicted & (labels == 0)).sum())
        precision = true_positive / (true_positive + false_positive) if (true_positive + false_positive) else 0.0
        recall = true_positive / total_positives if total_positives else 0.0
        rows.append(
            {
                "threshold": threshold,
                "predicted": int(predicted.sum()),
                "true_positive": true_positive,
                "false_positive": false_positive,
                "precision": precision,
                "recall": recall,
                "coverage": float(predicted.mean()) if len(scores) else 0.0,
            }
        )
    return rows


def auto_accept_region(
    scores: pd.Series,
    labels: pd.Series,
    *,
    min_precision: float,
    min_support: int,
    thresholds: tuple[float, ...] = DEFAULT_THRESHOLDS,
) -> dict[str, Any]:
    """Smallest threshold whose held-out precision clears the floor with enough support."""
    table = threshold_table(scores, labels, thresholds=thresholds)
    eligible = [
        row
        for row in table
        if row["precision"] >= min_precision and row["true_positive"] >= min_support
    ]
    if eligible:
        best = min(eligible, key=lambda row: row["threshold"])
        return {
            "status": "PASS",
            "threshold": best["threshold"],
            "held_out_precision": round(best["precision"], 4),
            "held_out_recall": round(best["recall"], 4),
            "held_out_true_positives": best["true_positive"],
            "coverage_at_threshold": round(best["coverage"], 4),
            "table": table,
        }
    riskiest = max(table, key=lambda row: (row["precision"], row["true_positive"]))
    return {
        "status": "FAIL",
        "threshold": None,
        "best_available": {
            "threshold": riskiest["threshold"],
            "precision": round(riskiest["precision"], 4),
            "true_positive": riskiest["true_positive"],
        },
        "action": "shrink_region: no auto-accept at this support; keep pairs in review/abstain",
        "table": table,
    }


def abstention_summary(
    scores: pd.Series, labels: pd.Series, *, lower: float, upper: float
) -> dict[str, Any]:
    """How many pairs fall in the grey band, and how good they actually are."""
    band = (scores >= lower) & (scores < upper)
    banded = labels[band]
    return {
        "band": [lower, upper],
        "rows": int(band.sum()),
        "share": round(float(band.mean()), 4) if len(scores) else 0.0,
        "band_precision": round(float(banded.mean()), 4) if len(banded) else None,
    }
