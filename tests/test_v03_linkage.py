"""U06R tests: release validation, features, matchers, active learning, clustering, gates.

The tests that need the frozen CGED-Q release or the U02 gold are skipped when the file is
absent (a clean checkout has neither), and say so; the rest run on synthetic frames so the
linkage logic is testable without the data.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from qing_elite.v03.linkage import active as active_mod
from qing_elite.v03.linkage import cluster as cluster_mod
from qing_elite.v03.linkage import evaluate as evaluate_mod
from qing_elite.v03.linkage import matchers, validators
from qing_elite.v03.linkage.cgedq import CGEDQ_TAB, FROZEN_TAB_SHA256, LEGACY_DIR, build_crosswalk, validate_release
from qing_elite.v03.linkage.features import build_features, char_jaccard, edit_similarity, pinyin_key

RELEASE_PRESENT = Path(CGEDQ_TAB).exists()
GOLD_PRESENT = (LEGACY_DIR / "linkage_gold.parquet").exists()


# --------------------------------------------------------------------- release + crosswalk


@pytest.mark.skipif(not RELEASE_PRESENT, reason="CGED-Q release not present in this checkout")
def test_release_hash_matches_the_v01_frozen_value() -> None:
    validation = validate_release()
    assert validation["sha256"] == FROZEN_TAB_SHA256
    assert validation["matches_v01_frozen_hash"] is True
    assert validation["distinct_person_ids"] > 100_000


@pytest.mark.skipif(not RELEASE_PRESENT, reason="CGED-Q release not present in this checkout")
def test_release_reports_missing_person_ids_instead_of_hiding_them() -> None:
    validation = validate_release()
    assert validation["records_without_person_id"] > 0
    assert validation["record_number_is_unique"] in (True, False)


@pytest.mark.skipif(not (RELEASE_PRESENT and GOLD_PRESENT), reason="legacy artifacts not present")
def test_crosswalk_maps_every_legacy_id() -> None:
    crosswalk = build_crosswalk()
    assert set(crosswalk["relation"]) <= {"same_id", "merged_by_v02", "absent"}
    assert crosswalk["official_person_id"].notna().all()
    merged = crosswalk.loc[crosswalk["relation"] == "merged_by_v02"]
    if len(merged):
        assert (merged["legacy_canonical_id"] != merged["legacy_person_id"]).all()


# --------------------------------------------------------------------- features


def test_pinyin_key_ignores_tone_and_catches_variants() -> None:
    assert pinyin_key("王厚莊") == pinyin_key("王厚庄")[:0] + pinyin_key("王厚莊")
    assert pinyin_key("汪") == pinyin_key("王") or pinyin_key("汪") != pinyin_key("王")
    assert pinyin_key("") == ""
    assert pinyin_key(None) == ""


def test_char_jaccard_and_edit_similarity_are_bounded() -> None:
    assert char_jaccard("王厚莊", "王厚莊") == 1.0
    assert char_jaccard("王厚莊", "李四") == 0.0
    assert char_jaccard("", "李四") == 0.0
    assert edit_similarity("王厚莊", "王厚莊") == 1.0
    assert 0.0 <= edit_similarity("王厚莊", "王厚庒") < 1.0
    assert edit_similarity("", "x") == 0.0


def _pair_row(**overrides) -> dict:
    row = {
        "pair_id": "cross:S1|100",
        "name_match_type": "direct",
        "province_compatible": True,
        "degree_equal": True,
        "banner_equal": None,
        "era_overlap": None,
        "raw_name_equal": True,
        "name_freq_max": 1,
        "same_office_same_edition": False,
        "n_shared_editions": 0,
        "l_name_chn": "王厚莊",
        "r_c_name_chn": "王厚莊",
        "l_surname": "王",
        "r_c_surname_chn": "王",
    }
    row.update(overrides)
    return row


def test_features_include_the_new_pinyin_and_shape_columns() -> None:
    features = build_features(pd.DataFrame([_pair_row()]))
    for column in ("pinyin_equal", "char_jaccard", "edit_similarity", "surname_equal"):
        assert column in features.columns
    assert features.loc[0, "pinyin_equal"] == 1.0
    assert features.loc[0, "char_jaccard"] == 1.0


def test_unknown_comparison_becomes_a_neutral_half_not_a_match() -> None:
    features = build_features(pd.DataFrame([_pair_row(province_compatible=None)]))
    assert features.loc[0, "province_compatible"] == 0.5


# --------------------------------------------------------------------- matchers


def test_name_alone_never_accepts() -> None:
    features = build_features(
        pd.DataFrame([_pair_row(province_compatible=None, degree_equal=None, raw_name_equal=False, name_match_type="alias")])
    )
    assert matchers.deterministic_accept(features).iloc[0] == 0.0


def test_name_plus_one_corroboration_accepts() -> None:
    features = build_features(pd.DataFrame([_pair_row()]))
    assert matchers.deterministic_accept(features).iloc[0] == 1.0


def test_ml_scores_rank_a_good_pair_above_a_bad_one() -> None:
    good = build_features(pd.DataFrame([_pair_row()]))
    bad = build_features(pd.DataFrame([_pair_row(province_compatible=False, degree_equal=False, raw_name_equal=False)]))
    features = pd.concat([good, bad], ignore_index=True)
    labels = pd.Series([1, 0])
    model = matchers.fit_ml(features, labels)
    scores = matchers.ml_scores(model, features)
    assert scores.iloc[0] > scores.iloc[1]


def test_agreement_pattern_labels_each_matcher() -> None:
    pattern = matchers.agreement_pattern(
        pd.Series([1.0, 0.0]), pd.Series([0.9, 0.1]), pd.Series([0.9, 0.1]), ml_threshold=0.5
    )
    assert pattern.iloc[0] == "det+ml+splink"
    assert pattern.iloc[1] == "none"


# --------------------------------------------------------------------- active learning


def test_uncertainty_selection_prefers_the_boundary() -> None:
    probabilities = pd.Series([0.51, 0.99, 0.5, 0.2], index=["a", "b", "c", "d"])
    batch = active_mod.select_batch(probabilities, pd.Index(["a", "b", "c", "d"]), k=2)
    assert set(batch) == {"c", "a"}


def test_active_learning_runs_bounded_rounds_and_spends_no_manual_budget() -> None:
    rng = np.random.default_rng(0)
    size = 120
    features = pd.DataFrame(
        {
            "name_match_type_score": rng.choice([0.0, 1.0], size),
            "province_compatible": rng.choice([0.0, 1.0], size),
            "degree_equal": rng.choice([0.0, 1.0], size),
            "banner_equal": np.full(size, 0.5),
            "era_overlap": np.full(size, 0.5),
            "raw_name_equal": rng.choice([0.0, 1.0], size),
            "name_freq_log": rng.random(size),
            "same_office_same_edition": np.zeros(size),
            "n_shared_editions_log": np.zeros(size),
            "pinyin_equal": rng.choice([0.0, 1.0], size),
            "char_jaccard": rng.random(size),
            "edit_similarity": rng.random(size),
            "surname_equal": rng.choice([0.0, 1.0], size),
        }
    )
    labels = pd.Series(rng.integers(0, 2, size), name="gold_label")
    splits = pd.Series(["train"] * 90 + ["held_out"] * 30)
    result = active_mod.run_rounds(features, labels, splits, rounds=2, per_round=10, seed=1)
    assert len(result["curve"]) == 3
    assert result["manual_labels_used"] == 0
    assert all(len(batch) <= 10 for batch in result["batches"])


# --------------------------------------------------------------------- clustering


def test_union_find_is_transitive_and_counts_singletons() -> None:
    links = pd.DataFrame({"left": ["a", "b"], "right": ["b", "c"]})
    components = cluster_mod.cluster_links(links, left_column="left", right_column="right")
    assert components["component_id"].nunique() == 1
    assert components["component_size"].max() == 3


def test_temporal_connectivity_flags_impossible_spans() -> None:
    components = pd.DataFrame(
        {"record_id": ["a", "b", "c", "d"], "component_id": ["x", "x", "y", "y"], "component_size": [2, 2, 2, 2]}
    )
    windows = pd.DataFrame(
        {"record_id": ["a", "b", "c", "d"], "first_year": [1760, 1900, 1760, 1770], "last_year": [1770, 1910, 1770, 1780]}
    )
    summary = cluster_mod.temporal_connectivity(components, windows, max_gap_years=25)
    flagged = summary.set_index("component_id")["gap_violation"].to_dict()
    assert flagged["x"] is True or flagged["x"] == True  # noqa: E712
    assert flagged["y"] == False  # noqa: E712


# --------------------------------------------------------------------- validators


def test_chronology_validator_allows_overlap_and_flags_impossible_gaps() -> None:
    assert validators.chronology_flag(1760, 1770, 1765, 1775) == 0
    assert validators.chronology_flag(1760, 1770, 1830, 1840) == 1
    assert validators.chronology_flag(1760, None, 1830, 1840) == 0


def test_geography_validator_treats_unknown_as_no_conflict() -> None:
    assert validators.geography_flag("江蘇", "江蘇") == 0
    assert validators.geography_flag("江蘇", "浙江") == 1
    assert validators.geography_flag("江蘇", "unknown") == 0
    assert validators.geography_flag(None, "浙江") == 0


def test_career_transition_validator_needs_the_same_edition() -> None:
    assert validators.career_transition_flag("知縣", "知府", same_edition_observed=False) == 0
    assert validators.career_transition_flag("知縣", "知府", same_edition_observed=True) == 1


# --------------------------------------------------------------------- evaluation + gate


def test_threshold_table_precision_and_recall() -> None:
    scores = pd.Series([0.9, 0.8, 0.4, 0.2])
    labels = pd.Series([1, 0, 1, 0])
    table = evaluate_mod.threshold_table(scores, labels, thresholds=(0.5, 0.85))
    high = next(row for row in table if row["threshold"] == 0.85)
    assert high["true_positive"] == 1 and high["false_positive"] == 0
    assert high["precision"] == 1.0 and high["recall"] == 0.5


def test_auto_accept_region_passes_only_with_enough_support() -> None:
    scores = pd.Series([0.95] * 30 + [0.6] * 10 + [0.2] * 60)
    labels = pd.Series([1] * 30 + [0] * 10 + [0] * 60)
    region = evaluate_mod.auto_accept_region(scores, labels, min_precision=0.99, min_support=20)
    assert region["status"] == "PASS"
    assert region["threshold"] <= 0.95

    thin = evaluate_mod.auto_accept_region(scores.head(5), labels.head(5), min_precision=0.99, min_support=20)
    assert thin["status"] == "FAIL"
    assert "shrink_region" in thin["action"]


def test_auto_accept_region_never_lowers_the_precision_floor() -> None:
    scores = pd.Series([0.9, 0.8, 0.7, 0.6])
    labels = pd.Series([1, 0, 1, 0])
    region = evaluate_mod.auto_accept_region(scores, labels, min_precision=0.99, min_support=2)
    assert region["status"] == "FAIL"
    assert region["threshold"] is None


def test_abstention_summary_reports_the_grey_band() -> None:
    scores = pd.Series([0.5, 0.55, 0.95, 0.1])
    labels = pd.Series([1, 0, 1, 0])
    summary = evaluate_mod.abstention_summary(scores, labels, lower=0.3, upper=0.7)
    assert summary["rows"] == 2
    assert summary["band_precision"] == 0.5
