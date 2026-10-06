"""U03 tests: sample reproducibility, source-protocol logging and the stage gate."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from qing_elite.v02.pilot import evaluate_gate, person_flags
from qing_elite.v02.sample import (
    DEFAULT_SIZES,
    SEED,
    freeze_sample,
    observed_rates,
    power_report,
)
from qing_elite.v02.sources import OUTCOMES, SOURCE_ORDER, SourceResult

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROCESSED_V02 = PROJECT_ROOT / "data" / "processed_v02"


def _frame() -> pd.DataFrame:
    rows = []
    for tier, tiers in (("A", ["A1"]), ("B", ["B"]), ("C", ["C"]), ("D", ["D"])):
        for index in range(12):
            rows.append(
                {
                    "entity_id": f"{tier}{index:03d}",
                    "name_chn": f"人{index}",
                    "cbdb_personid": index,
                    "highest_tier": tiers[0],
                    "link_status": "explicit_id",
                    "career_first_year": 1760 + index,
                    "banner_effective": "unknown",
                    "native_province_effective": None,
                    "cohort": "1771-1795",
                    "banner_group": "unknown",
                    "tier_group": tier,
                }
            )
    return pd.DataFrame.from_records(rows)


def test_sample_is_reproducible_from_the_seed() -> None:
    first, design = freeze_sample(_frame())
    second, _ = freeze_sample(_frame())
    assert list(first["entity_id"]) == list(second["entity_id"])
    assert design["seed"] == SEED


def test_inclusion_probability_and_weight_are_reciprocal() -> None:
    sample, design = freeze_sample(_frame())
    assert sample["inclusion_probability"].notna().all()
    assert ((sample["sampling_weight"] * sample["inclusion_probability"] - 1).abs() < 1e-9).all()
    for tier, values in design["strata"].items():
        assert values["inclusion_probability"] == pytest.approx(
            values["sample"] / values["population"]
        ), tier


def test_sample_respects_the_requested_sizes_and_caps_by_frame() -> None:
    frame = _frame()
    sample, design = freeze_sample(frame)
    for tier, target in DEFAULT_SIZES.items():
        population = int((frame["tier_group"] == tier).sum())
        expected = population if target is None else min(target, population)
        assert int((sample["stratum"] == tier).sum()) == expected
        assert design["strata"][tier]["sample"] == expected


def test_selection_is_spread_over_the_stratum() -> None:
    frame = _frame()
    frame["cohort"] = [f"c{index % 4}" for index in range(len(frame))]
    sample, _ = freeze_sample(frame)
    d = sample[sample["stratum"] == "D"]
    assert d["selection_index"].is_monotonic_increasing
    assert d["cohort"].nunique() > 1


def test_power_report_covers_both_estimands_and_a_minimum_detectable_difference() -> None:
    _, design = freeze_sample(_frame())
    table = power_report(design, {"A": 0.2, "B": 0.05, "C": 0.07, "D": 0.01})
    assert "difference_A_vs_D" in set(table["quantity"])
    mdd = table.loc[table["quantity"] == "difference_A_vs_D", "min_detectable_difference_80pct"]
    assert (mdd > 0).all()
    assert set(table["scenario"]) == {"v01_reference_rates", "v02_documented_rates"}


def test_observed_rate_uses_the_frame_denominator() -> None:
    sample = pd.DataFrame(
        {
            "entity_id": ["e1", "e2", "e3", "e4"],
            "stratum": ["A"] * 4,
            "person_uid": ["e1", "e2", "e3", "e4"],
        }
    )
    # e1 documented, e2 undocumented-but-known, e3/e4 unknown
    indicators = pd.DataFrame(
        {
            "person_uid": ["e1", "e2", "e3", "e4"],
            "n_known_slots": [1, 1, 0, 0],
            "documented_ancestor_official_any": pd.array([1, pd.NA, pd.NA, pd.NA], dtype="Int64"),
        }
    )
    rates = observed_rates(sample, indicators=indicators)
    assert rates["A"] == pytest.approx(0.25)  # 1 of 4, unknown stays unknown, not 0


def test_source_result_rejects_unknown_outcome() -> None:
    with pytest.raises(ValueError):
        SourceResult("cbdb_frozen", "maybe")
    assert set(OUTCOMES) == {"found", "not_found", "unavailable", "error"}


def _results(**by_source) -> list[SourceResult]:
    return [by_source[name] for name in SOURCE_ORDER]


def _base_results(**overrides) -> dict[str, SourceResult]:
    results = {
        "cbdb_frozen": SourceResult("cbdb_frozen", "not_found"),
        "cgedq_frozen": SourceResult("cgedq_frozen", "not_found"),
        "cbdb_api": SourceResult("cbdb_api", "not_found"),
        "sinica_lod": SourceResult("sinica_lod", "unavailable"),
        "wikisource_dump": SourceResult("wikisource_dump", "not_found"),
    }
    results.update(overrides)
    return results


def test_name_only_hit_is_not_effective_information() -> None:
    flags = person_flags(
        _results(
            **_base_results(
                cbdb_frozen=SourceResult("cbdb_frozen", "found", identity=True, office=True,
                                         degree=True, attribution="name_only"),
            )
        )
    )
    assert flags["identity_hit"] is True
    assert flags["identity_attributed"] is False
    assert flags["effective_information"] is False


def test_roster_source_attributes_identity_without_ancestry() -> None:
    flags = person_flags(
        _results(
            **_base_results(
                cgedq_frozen=SourceResult("cgedq_frozen", "found", identity=True, office=True,
                                          degree=True, attribution="explicit_id"),
            )
        )
    )
    assert flags["identity_attributed"] is True
    assert flags["effective_information"] is True
    assert flags["ancestry_hit"] is False


def test_attributed_hit_with_an_office_is_effective_information() -> None:
    flags = person_flags(
        _results(
            **_base_results(
                cbdb_frozen=SourceResult("cbdb_frozen", "found", identity=True, office=True,
                                         ancestry=True, attribution="explicit_id"),
                cbdb_api=SourceResult("cbdb_api", "found", identity=True, office=True,
                                      attribution="explicit_id"),
            )
        )
    )
    assert flags["effective_information"] is True
    assert flags["ancestry_hit"] is True
    assert flags["identity_source"] == "cbdb_frozen"
    assert flags["protocol_complete"] is False
    assert flags["protocol_complete_required"] is True


def test_gate_fails_on_low_completion_or_low_information() -> None:
    metrics = pd.DataFrame(
        [
            {"stratum": "A", "protocol_completion": 1.0, "effective_information_rate": 0.9},
            {"stratum": "D", "protocol_completion": 1.0, "effective_information_rate": 0.1},
        ]
    )
    verdict = evaluate_gate(metrics)
    assert verdict["status"] == "FAIL"
    assert any("D" in failure for failure in verdict["failures"])
    ok = evaluate_gate(
        pd.DataFrame([{"stratum": "A", "protocol_completion": 0.9, "effective_information_rate": 0.5}])
    )
    assert ok["status"] == "PASS"


# ------------------------------------------------------------- committed artefacts


def test_frozen_sample_has_weights_and_matches_the_frozen_sizes() -> None:
    path = PROCESSED_V02 / "analysis_sample.parquet"
    if not path.exists():
        pytest.skip("analysis sample not built")
    sample = pd.read_parquet(path)
    assert sample["inclusion_probability"].notna().all()
    assert sample["sampling_weight"].notna().all()
    counts = sample.groupby("stratum").size().to_dict()
    assert counts["A"] >= 30 and counts["B"] == 200 and counts["C"] == 200 and counts["D"] == 400


def test_search_log_records_every_source_and_outcome() -> None:
    path = PROCESSED_V02 / "source_search_log.parquet"
    if not path.exists():
        pytest.skip("pilot not run")
    log = pd.read_parquet(path)
    assert set(log["source_id"]) == set(SOURCE_ORDER)
    assert set(log["outcome"]) <= set(OUTCOMES)
    per_person = log.groupby("entity_id")["source_id"].nunique()
    assert (per_person == len(SOURCE_ORDER)).all()
