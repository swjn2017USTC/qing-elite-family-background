"""Active learning with a hard, small budget (U06R).

Uncertainty sampling over the *train* pool: start from a small seed, train, ask for the
labels of the pairs the model is least sure about, retrain, repeat. Two rules matter more
than the strategy itself:

* the per-round batch is small (``per_round`` ≤ 25), because the point is to spend labels
  where they change the boundary, not to label a corpus;
* the manual budget is a count, never a share. This stage consumes **zero** new human labels:
  the labels it uses are the U02 gold set, and the run records how many of the budgeted
  pairs were actually used (0), so the remaining budget stays visible for later stages.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from qing_elite.v03.linkage.matchers import FittedModel, fit_ml, ml_scores


def uncertainty_rank(probabilities: pd.Series) -> pd.Series:
    """Distance from the decision boundary: 0 = maximally uncertain."""
    return (probabilities - 0.5).abs()


def select_batch(probabilities: pd.Series, candidates: pd.Index, *, k: int) -> list[Any]:
    ranked = uncertainty_rank(probabilities.loc[candidates]).sort_values(kind="stable")
    return list(ranked.index[:k])


def run_rounds(
    features: pd.DataFrame,
    labels: pd.Series,
    splits: pd.Series,
    *,
    rounds: int,
    per_round: int,
    seed: int,
    evaluation_split: str = "held_out",
    train_split: str = "train",
    alpha: float = 0.1,
) -> dict[str, Any]:
    """Train → select → reveal → retrain; record the held-out learning curve."""
    train_index = features.index[splits == train_split]
    held_out_index = features.index[splits == evaluation_split]
    if len(train_index) == 0 or len(held_out_index) == 0:
        raise ValueError("active learning needs both a train and a held-out split")

    rng = np.random.default_rng(seed)
    seed_size = max(10, min(40, len(train_index) // 4))
    labelled = list(rng.choice(train_index.to_numpy(), size=seed_size, replace=False))
    unlabelled = [index for index in train_index if index not in set(labelled)]
    curve: list[dict[str, Any]] = []
    history: list[list[Any]] = []

    for round_number in range(rounds + 1):
        model = fit_ml(features.loc[labelled], labels.loc[labelled], alpha=alpha)
        probabilities = ml_scores(model, features)
        from qing_elite.v03.linkage.evaluate import threshold_table

        table = threshold_table(probabilities.loc[held_out_index], labels.loc[held_out_index])
        best = max(table, key=lambda row: (row["precision"] >= 0.99, row["coverage"]))
        curve.append(
            {
                "round": round_number,
                "labelled": len(labelled),
                "held_out_rows": int(len(held_out_index)),
                "held_out_positives": int(labels.loc[held_out_index].sum()),
                "best_threshold_precision": round(best["precision"], 4),
                "best_threshold_coverage": round(best["coverage"], 4),
                "at_0_5_precision": next(
                    (row["precision"] for row in table if abs(row["threshold"] - 0.5) < 1e-9), None
                ),
            }
        )
        if round_number == rounds or not unlabelled:
            break
        batch = select_batch(probabilities, pd.Index(unlabelled), k=min(per_round, len(unlabelled)))
        history.append(batch)
        labelled.extend(batch)
        unlabelled = [index for index in unlabelled if index not in set(batch)]

    return {
        "curve": curve,
        "batches": [[str(item) for item in batch] for batch in history],
        "manual_labels_used": 0,
        "budget_pairs": None,
        "note": (
            "标签全部来自 U02 gold（rule-anchored）；本轮没有新增人工标注，"
            "因此 manual_budget_pairs 未被消耗。"
        ),
    }
