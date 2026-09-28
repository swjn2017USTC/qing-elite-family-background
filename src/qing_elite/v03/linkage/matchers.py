"""Three matchers and their agreement pattern (U06R).

The stage requires deterministic / ML / Splink scores over the same pairs, because the
disagreements are what tells us where the boundary of automatic acceptance is:

* **deterministic** — explicit rules on independently observable fields; the v0.2 lineage.
  A link is accepted only with at least one non-name corroboration, never on the name alone.
* **ML** — L2-regularised logistic regression over the feature table, fitted locally with a
  short gradient descent. ``statsmodels`` was tried first and rejected for a concrete reason:
  its regularised fit still inverts a Hessian at the end, which fails (``LinAlgError:
  Hessian matrix is singular``) exactly when the training set is small and the features
  separate — the situation this pipeline is actually in. Regularisation is not decoration
  here: the weakly related features separate perfectly on small samples, and an unregularised
  fit would report a beautiful, meaningless boundary.
* **Splink** — the v0.2 baseline configuration (block on the normalised name, compare banner /
  province / degree with per-record sentinels for "unknown"), kept so the new numbers can be
  read against the old ones.

Agreement is reported per pair, because "two of three agree" is a different review decision
from "all three agree".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from qing_elite.v03.linkage.features import FEATURE_COLUMNS, feature_matrix


@dataclass
class FittedModel:
    """A fitted logistic regression plus the split it was evaluated on."""

    params: dict[str, float]
    train_rows: int
    train_positives: int
    converged: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "params": {key: round(value, 4) for key, value in self.params.items()},
            "train_rows": self.train_rows,
            "train_positives": self.train_positives,
            "converged": self.converged,
        }


# ------------------------------------------------------------------ deterministic


def deterministic_accept(features: pd.DataFrame, *, require_evidence: bool = True) -> pd.Series:
    """Rules first: name agreement plus a corroborating field.

    ``require_evidence`` encodes the v0.2 lesson (U00-01/U00-03): a unique name is not
    identification, so at least one of province / degree / banner / same-edition-office has
    to agree.
    """
    name_ok = (features["name_match_type_score"] >= 0.8) | (features["raw_name_equal"] >= 1.0)
    evidence = (
        (features["province_compatible"] >= 1.0)
        | (features["degree_equal"] >= 1.0)
        | (features["banner_equal"] >= 1.0)
        | (features["same_office_same_edition"] >= 1.0)
    )
    if require_evidence:
        accept = name_ok & evidence
    else:
        accept = name_ok
    return accept.astype(float)


# ------------------------------------------------------------------ ML


def fit_ml(
    features: pd.DataFrame,
    labels: pd.Series,
    *,
    alpha: float = 0.1,
    learning_rate: float = 0.5,
    iterations: int = 600,
) -> FittedModel:
    """L2-regularised logistic regression by gradient descent (deterministic, no Hessian)."""
    matrix = feature_matrix(features)
    design = np.column_stack([np.ones(len(matrix)), matrix.to_numpy()])
    target = labels.astype(float).to_numpy()
    weights = np.zeros(design.shape[1])
    penalty = alpha * np.ones(design.shape[1])
    penalty[0] = 0.0  # never penalise the intercept

    previous_loss = np.inf
    converged = False
    for _ in range(iterations):
        logits = design @ weights
        probabilities = 1.0 / (1.0 + np.exp(-logits))
        gradient = design.T @ (probabilities - target) / len(target) + penalty * weights / len(target)
        weights -= learning_rate * gradient
        loss = float(
            -np.mean(
                target * np.log(np.clip(probabilities, 1e-9, 1 - 1e-9))
                + (1 - target) * np.log(np.clip(1 - probabilities, 1e-9, 1 - 1e-9))
            )
            + float(np.sum(penalty * weights**2)) / (2 * len(target))
        )
        if abs(previous_loss - loss) < 1e-10:
            converged = True
            break
        previous_loss = loss

    params = dict(zip(["const", *matrix.columns], weights))
    return FittedModel(
        params=params,
        train_rows=int(len(matrix)),
        train_positives=int(labels.sum()),
        converged=converged,
    )


def ml_scores(model: FittedModel, features: pd.DataFrame) -> pd.Series:
    matrix = feature_matrix(features)
    names = [name for name in matrix.columns]
    coefficients = np.array([model.params.get(name, 0.0) for name in names])
    intercept = model.params.get("const", 0.0)
    logits = intercept + matrix.to_numpy() @ coefficients
    return pd.Series(1.0 / (1.0 + np.exp(-logits)), index=features.index, name="ml_probability")


# ------------------------------------------------------------------ Splink baseline


def splink_probabilities(
    left: pd.DataFrame, right: pd.DataFrame, *, seed: int, block_on: str = "name_norm"
) -> tuple[pd.DataFrame, dict[str, Any]]:  # pragma: no cover - exercised through run.py
    """v0.2 baseline: name-blocked, banner/province/degree compared with unknown sentinels.

    Two things about this Splink build (4.0.17) are worth recording, because both cost real
    debugging time and both are version quirks rather than data problems:

    * ``block_on("name_norm")`` renders ``ON (name_norm = name_norm)``, which DuckDB rejects
      as an ambiguous reference; the rule has to be written with table aliases
      (``l.name_norm = r.name_norm``). Verified on a two-row frame, so it is not a data issue.
    * the same alias requirement applies to the EM training rule; without it the m-values
      stay untrained and Splink falls back to defaults (it warns rather than failing).

    Returns the scores plus a status dict, so a partially trained model is visible instead of
    silently reported as a good baseline.
    """
    from splink import DuckDBAPI, Linker, SettingsCreator

    import splink.comparison_library as cl

    aliased_rule = f"l.{block_on} = r.{block_on}"
    status: dict[str, Any] = {"blocking_rule": aliased_rule, "em_converged": None}
    settings = SettingsCreator(
        link_type="link_only",
        blocking_rules_to_generate_predictions=[aliased_rule],
        comparisons=[
            cl.ExactMatch("banner_key"),
            cl.ExactMatch("province_key"),
            cl.ExactMatch("degree_key"),
        ],
    )
    linker = Linker([left, right], settings, db_api=DuckDBAPI())
    linker.training.estimate_u_using_random_sampling(max_pairs=1_000_000, seed=seed)
    try:
        linker.training.estimate_parameters_using_expectation_maximisation(aliased_rule)
        status["em_converged"] = True
    except Exception as error:
        status["em_converged"] = False
        status["em_error"] = f"{type(error).__name__}: {error}"[:200]
    frame = linker.inference.predict(threshold_match_probability=0.0).as_pandas_dataframe()
    frame = frame.rename(columns={"match_probability": "splink_probability"})
    status["scored_pairs"] = int(len(frame))
    return frame.loc[:, ["unique_id_l", "unique_id_r", "splink_probability"]], status


# ------------------------------------------------------------------ agreement


def agreement_pattern(
    deterministic: pd.Series, ml_probability: pd.Series, splink_probability: pd.Series, *, ml_threshold: float
) -> pd.Series:
    """Label each pair with which matchers accept it."""
    det = deterministic >= 1.0
    ml = ml_probability >= ml_threshold
    spl = splink_probability >= ml_threshold
    return pd.Series(
        [
            "+".join(
                name
                for name, flag in (("det", a), ("ml", b), ("splink", c))
                if flag
            )
            or "none"
            for a, b, c in zip(det, ml, spl)
        ],
        index=deterministic.index,
        name="agreement",
    )
