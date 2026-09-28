"""U03 sampling design: frame, strata, seed, inclusion probabilities, power.

The frame is the v0.2 primary entity list (U01 contracts + U02 accepted links). Strata
are the four tier groups of the study. Sizes follow the upgrade plan's defaults
(A census, B 200, C 200, D 400) capped by the frame, and the deviation is recorded with
its reason. Selection is systematic after sorting by (cohort, banner, person id), which
spreads the sample over the implicit strata without creating empty cells.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

from qing_elite.utils.config import PROCESSED_DIR, PROCESSED_V02_DIR, PROJECT_ROOT

SEED = 20260914
DEFAULT_SIZES = {"A": None, "B": 200, "C": 200, "D": 400}  # None = census
PILOT_PER_STRATUM = 30

ANALYSIS_SAMPLE = PROCESSED_V02_DIR / "analysis_sample.parquet"
DESIGN_PATH = PROJECT_ROOT / "config" / "v02" / "sample_design.json"

TIER_GROUP = {"A1": "A", "A2": "A", "A3": "A", "B": "B", "C": "C", "D": "D"}

PRIMARY_ESTIMAND = (
    "P(documented ancestor office = 1 | tier group) on the frozen frame: the share of "
    "each tier for which the record attests an ancestor who held office. The denominator "
    "is the tier itself and the complement includes unknown, so this is a *documentation "
    "rate*, never an assertion that an undocumented ancestor held no office. Reported per "
    "tier with Wilson intervals and as descriptive contrasts between tiers (association "
    "only, no causal claim). For the D tier the frozen frame yields a documentation rate "
    "of 0, so D enters this estimand only as an ascertainment result and its upper bound."
)
SECONDARY_ESTIMAND = (
    "P(documented ancestor office = 1 | ancestor identity known, tier): the same rate on "
    "the ascertained denominator, reported with that denominator per tier."
)
TERTIARY_ESTIMAND = (
    "D tier restricted to entities with an accepted CBDB link (link_status = "
    "accepted_cross_link): the same documentation rate on that sub-frame. Pre-registered "
    "as a secondary quantity because the restriction selects on linkage success, which "
    "correlates with documented ancestry; it may be reported only together with that "
    "selection caveat and never as the tier's headline number."
)


def build_frame() -> pd.DataFrame:
    """Sampling frame: primary-eligible entities with tier, cohort and banner."""
    entities = pd.read_parquet(PROCESSED_V02_DIR / "entities.parquet")
    master = pd.read_parquet(PROCESSED_DIR / "officials_master.parquet")
    frame = entities.loc[
        entities["primary_eligible"],
        ["entity_id", "name_chn", "cbdb_personid", "highest_tier", "link_status", "career_first_year"],
    ].merge(
        master[["person_uid", "cgedq_person_id", "banner_effective", "native_province_effective"]],
        left_on="entity_id",
        right_on="person_uid",
        how="left",
    )
    frame["tier_group"] = frame["highest_tier"].map(TIER_GROUP).fillna("other")
    frame["cohort"] = pd.cut(
        frame["career_first_year"],
        bins=[0, 1735, 1770, 1795, 1830, 9999],
        labels=["pre-1736", "1736-1770", "1771-1795", "1796-1830", "post-1830"],
        include_lowest=True,
    ).astype("object")
    frame["banner_group"] = frame["banner_effective"].fillna("unknown")
    return frame


def freeze_sample(frame: pd.DataFrame | None = None) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Systematic sample with inclusion probabilities, reproducible from the seed."""
    frame = build_frame() if frame is None else frame
    rng = np.random.default_rng(SEED)
    rows: list[pd.DataFrame] = []
    design: dict[str, Any] = {"seed": SEED, "strata": {}}
    for tier in ("A", "B", "C", "D"):
        stratum = frame[frame["tier_group"] == tier].sort_values(
            ["cohort", "banner_group", "entity_id"], kind="mergesort"
        )
        population = int(len(stratum))
        target = DEFAULT_SIZES[tier]
        size = population if target is None else min(target, population)
        if population == 0:
            design["strata"][tier] = {"population": 0, "sample": 0, "inclusion_probability": None}
            continue
        step = population / size
        start = float(rng.uniform(0, step))
        positions = np.floor(start + step * np.arange(size)).astype(int)
        positions = positions[positions < population]
        picked = stratum.iloc[positions].copy()
        picked["stratum"] = tier
        picked["stratum_population"] = population
        picked["inclusion_probability"] = size / population
        picked["sampling_weight"] = 1.0 / (size / population)
        picked["selection_index"] = np.arange(len(picked))
        rows.append(picked)
        design["strata"][tier] = {
            "population": population,
            "sample": int(len(picked)),
            "inclusion_probability": round(size / population, 6),
            "weight": round(population / size, 6),
            "capped_by_frame": bool(target is not None and target > population),
            "requested": target,
        }
    sample = pd.concat(rows, ignore_index=True)
    design["frame"] = {"total": int(len(frame)), "by_stratum": frame["tier_group"].value_counts().to_dict()}
    design["estimand"] = PRIMARY_ESTIMAND
    design["exclusions"] = [
        "entities that are not primary_eligible (unresolved link, pending review or "
        "merged into another entity) are outside the frame",
        "no further exclusions: the frame is the frozen primary entity list",
    ]
    design["ordering"] = "sort by (cohort, banner_group, entity_id), then systematic step"
    design["tertiary_estimand"] = TERTIARY_ESTIMAND
    if "link_status" in frame.columns:
        design["tertiary_frame"] = {
            "definition": "tier_group == 'D' and link_status == 'accepted_cross_link'",
            "population": int(
                ((frame["tier_group"] == "D") & (frame["link_status"] == "accepted_cross_link")).sum()
            ),
            "caveat": "selection on linkage success; report only with the caveat, never as D's headline",
        }
    return sample, design


def _wilson_half_width(p: float, n: int, z: float = 1.96) -> float:
    if n <= 0:
        return float("nan")
    denominator = 1 + z**2 / n
    centre = (p + z**2 / (2 * n)) / denominator
    half = z * np.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / denominator
    return float(half)


def power_report(design: Mapping[str, Any], observed: Mapping[str, float] | None = None) -> pd.DataFrame:
    """Precision of the per-tier prevalence and of the A-vs-D contrast.

    Normal approximation; the point is to show whether the frozen sample can support the
    descriptive contrast at all, not to run a hypothesis test.
    """
    sizes = {tier: int(values["sample"]) for tier, values in design["strata"].items()}
    rng = np.random.default_rng(SEED + 1)
    rows: list[dict[str, Any]] = []
    scenarios: dict[str, dict[str, float]] = {
        "v01_reference_rates": {"A": 0.55, "D": 0.555},
        "v02_documented_rates": {
            "A": float((observed or {}).get("A", 0.05)),
            "D": float((observed or {}).get("D", 0.005)),
        },
    }
    for label, rates in scenarios.items():
        for tier, rate in rates.items():
            n = sizes.get(tier, 0)
            rows.append(
                {
                    "scenario": label,
                    "quantity": f"prevalence_{tier}",
                    "n": n,
                    "assumed_rate": rate,
                    "expected_se": float(np.sqrt(max(rate * (1 - rate), 1e-9) / n)) if n else None,
                    "wilson_half_width_95": _wilson_half_width(rate, n),
                }
            )
        p_a, p_d = rates["A"], rates["D"]
        n_a, n_d = sizes.get("A", 0), sizes.get("D", 0)
        if n_a and n_d:
            se_diff = float(np.sqrt(max(p_a * (1 - p_a), 1e-9) / n_a + max(p_d * (1 - p_d), 1e-9) / n_d))
            mdd = float((1.96 + 0.84) * se_diff)
            rows.append(
                {
                    "scenario": label,
                    "quantity": "difference_A_vs_D",
                    "n": n_a + n_d,
                    "assumed_rate": round(p_a - p_d, 4),
                    "expected_se": se_diff,
                    "wilson_half_width_95": 1.96 * se_diff,
                    "min_detectable_difference_80pct": mdd,
                }
            )
    return pd.DataFrame.from_records(rows)


def _indicator_frame() -> pd.DataFrame:
    return pd.read_parquet(PROCESSED_V02_DIR / "person_indicators_v02.parquet")[
        ["person_uid", "n_known_slots", "documented_ancestor_official_any"]
    ]


def observed_rates(sample: pd.DataFrame, indicators: pd.DataFrame | None = None) -> dict[str, float]:
    """Documented-official *documentation rate* per stratum (frame denominator).

    ``documented_ancestor_official_any == 1`` is a positive fact; the complement contains
    unknown, so the quantity estimated is how often the record attests an official
    ancestor — not how often an ancestor was not one.
    """
    indicators = _indicator_frame() if indicators is None else indicators
    merged = sample.merge(
        indicators, left_on="entity_id", right_on="person_uid", how="left"
    )
    rates: dict[str, float] = {}
    for tier in ("A", "B", "C", "D"):
        subset = merged[merged["stratum"] == tier]
        rates[tier] = (
            float(subset["documented_ancestor_official_any"].eq(1).fillna(False).mean())
            if len(subset)
            else 0.0
        )
    return rates


def ascertained_rates(sample: pd.DataFrame, indicators: pd.DataFrame | None = None) -> dict[str, dict[str, float]]:
    """Secondary estimand: rate on the ascertained denominator, with that denominator."""
    indicators = _indicator_frame() if indicators is None else indicators
    merged = sample.merge(
        indicators, left_on="entity_id", right_on="person_uid", how="left"
    )
    result: dict[str, dict[str, float]] = {}
    for tier in ("A", "B", "C", "D"):
        subset = merged[merged["stratum"] == tier]
        ascertained = subset[subset["n_known_slots"] >= 1]
        result[tier] = {
            "n_ascertained": int(len(ascertained)),
            "rate": float(
                ascertained["documented_ancestor_official_any"].eq(1).fillna(False).mean()
            )
            if len(ascertained)
            else 0.0,
        }
    return result


def freeze(*, write: bool = True) -> tuple[pd.DataFrame, dict[str, Any], pd.DataFrame]:
    sample, design = freeze_sample()
    rates = observed_rates(sample)
    table = power_report(design, rates)
    design["observed_documented_official_rate"] = rates
    design["observed_ascertained"] = ascertained_rates(sample)
    design["secondary_estimand"] = SECONDARY_ESTIMAND
    if write:
        PROCESSED_V02_DIR.mkdir(parents=True, exist_ok=True)
        DESIGN_PATH.parent.mkdir(parents=True, exist_ok=True)
        sample.to_parquet(ANALYSIS_SAMPLE, index=False)
        DESIGN_PATH.write_text(json.dumps(design, ensure_ascii=False, indent=2), encoding="utf-8")
    return sample, design, table
