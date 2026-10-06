"""U02 tests: dedupe, cross-source linking, gold evaluation and biography ownership.

These cover the deterministic rules and the acceptance criteria of U02: name-only
evidence never accepts a link, the primary frame contains no unresolved link, and one
biography can never feed two entities.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from qing_elite.v02.linkage import (
    build_dedupe_gold,
    canonical_ids,
    coverage_audit,
    decide_cross,
    decide_dedupe,
    evaluate,
    link_biographies,
    resolve_entity_links,
    subgroup_errors,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROCESSED_V02 = PROJECT_ROOT / "data" / "processed_v02"


def _cross_row(**overrides) -> dict:
    row = {
        "pair_id": "cross:S1|100",
        "l_cgedq_person_id": "S1",
        "r_cbdb_personid": 100,
        "name_match_type": "direct",
        "province_compatible": None,
        "degree_equal": None,
        "banner_equal": None,
        "era_overlap": None,
        "raw_name_equal": True,
        "name_freq_max": 1,
    }
    row.update(overrides)
    return row


def _decide(rows: list[dict], scores: dict | None = None) -> pd.DataFrame:
    pairs = pd.DataFrame.from_records(rows)
    scores = scores or {row["pair_id"]: 0.0 for row in rows}
    score_frame = pd.DataFrame(
        [
            {"pair_id": pair_id, "probability": value,
             "l_cgedq_person_id": "S1", "r_cbdb_personid": 0}
            for pair_id, value in scores.items()
        ]
    )
    return decide_cross(pairs, score_frame, threshold=0.5)


def test_unique_name_alone_never_accepts_a_link() -> None:
    decided = _decide([_cross_row()])
    assert decided.loc[0, "link_decision"] == "probabilistic_grey"
    assert decided.loc[0, "review_status"] == "pending"


def test_multifield_agreement_accepts_a_link() -> None:
    decided = _decide([_cross_row(province_compatible=True, degree_equal=True)])
    assert decided.loc[0, "link_decision"] == "deterministic_multifield"
    assert decided.loc[0, "review_status"] == "accepted_deterministic"


def test_alias_needs_one_corroborating_field() -> None:
    accepted = _decide([_cross_row(name_match_type="alias", degree_equal=True)])
    assert accepted.loc[0, "link_decision"] == "deterministic_alias"
    plain = _decide([_cross_row(name_match_type="alias")])
    assert plain.loc[0, "link_decision"] == "probabilistic_grey"


def test_high_probability_only_reaches_the_review_queue() -> None:
    decided = _decide([_cross_row()], {"cross:S1|100": 0.99})
    assert decided.loc[0, "link_decision"] == "probabilistic_high"
    assert decided.loc[0, "review_status"] == "pending"


def _dedupe_row(**overrides) -> dict:
    row = {
        "pair_id": "dedupe:S1|S2",
        "l_cgedq_person_id": "S1",
        "r_cgedq_person_id": "S2",
        "same_office_same_edition": False,
        "n_shared_editions": 0,
        "offices": None,
        "province_compatible": None,
        "degree_equal": None,
        "banner_equal": None,
        "era_overlap": None,
        "name_freq_max": 1,
    }
    row.update(overrides)
    return row


def _decide_dedupe(rows: list[dict]) -> pd.DataFrame:
    pairs = pd.DataFrame.from_records(rows)
    scores = pd.DataFrame({"pair_id": [row["pair_id"] for row in rows],
                           "l_cgedq_person_id": "S1", "r_cgedq_person_id": "S2",
                           "probability": 0.0})
    return decide_dedupe(pairs, scores, threshold=0.5)


def test_same_office_in_the_same_edition_merges() -> None:
    decided = _decide_dedupe([_dedupe_row(same_office_same_edition=True, n_shared_editions=2)])
    assert decided.loc[0, "decision"] == "merge"


def test_conflicting_banner_or_province_stays_distinct() -> None:
    banner = _decide_dedupe([_dedupe_row(banner_equal=False, same_office_same_edition=True)])
    assert banner.loc[0, "decision"] == "distinct"
    province = _decide_dedupe([_dedupe_row(province_compatible=False)])
    assert province.loc[0, "decision"] == "distinct"


def test_corroborated_pair_merges_and_the_rest_goes_to_review() -> None:
    decided = _decide_dedupe(
        [
            _dedupe_row(province_compatible=True, degree_equal=True, era_overlap=True),
            _dedupe_row(pair_id="dedupe:S1|S3"),
        ]
    )
    assert decided.loc[0, "decision"] == "merge"
    assert decided.loc[1, "decision"] == "review"


def test_canonical_ids_merges_only_accepted_pairs() -> None:
    dedupe = pd.DataFrame(
        [
            {"l_cgedq_person_id": "S1", "r_cgedq_person_id": "S2", "decision": "merge"},
            {"l_cgedq_person_id": "S2", "r_cgedq_person_id": "S3", "decision": "review"},
        ]
    )
    canonical = canonical_ids(dedupe).set_index("cgedq_person_id")
    assert canonical.loc["S1", "cgedq_canonical_id"] == canonical.loc["S2", "cgedq_canonical_id"]
    assert canonical.loc["S3", "cgedq_canonical_id"] == "S3"


def test_weighted_evaluation_uses_stratum_weights() -> None:
    gold = pd.DataFrame(
        {
            "pair_id": ["a", "b", "c", "d"],
            "l_cgedq_person_id": "S1",
            "r_cbdb_personid": 1,
            "gold_label": [1, 1, 0, 0],
            "split": ["held_out"] * 4,
            "stratum_population": [100, 100, 1000, 1000],
            "stratum_sampled": [1, 1, 1, 1],
        }
    )
    scores = pd.DataFrame({"pair_id": ["a", "b", "c", "d"], "probability": [0.9, 0.1, 0.8, 0.1]})
    metrics = evaluate(scores, gold, threshold=0.5)
    # one weighted false positive outweighs a single true positive
    assert metrics["fp"] == 1000.0
    assert metrics["tp"] == 100.0
    assert metrics["precision"] == pytest.approx(100 / 1100)


def test_coverage_sets_are_nested() -> None:
    master = pd.DataFrame({"person_uid": ["e1", "e2", "e3"], "highest_tier": ["A1", "D", "D"]})
    entities = pd.DataFrame(
        {
            "entity_id": ["e1", "e2", "e3"],
            "link_status": ["explicit_id", "review_pending", "standalone"],
        }
    )
    table = coverage_audit(master, entities).set_index(["coverage_set", "tier"])
    assert table.loc[("high_only", "ALL"), "n_linked"] == 1
    assert table.loc[("reviewed_accepted", "ALL"), "n_linked"] == 2
    assert table.loc[("all_candidate", "ALL"), "n_linked"] == 3


# the biography matcher reads the frozen appointment table for office corroboration; the
# public repository deliberately does not ship person-level derived data, so the test skips
# with a reason (U11R; the same reason the sibling artefact tests already use)
@pytest.mark.skipif(
    not (PROJECT_ROOT / "data" / "processed" / "appointments.parquet").exists(),
    reason="frozen appointment table missing (public checkout ships no person-level data)",
)
def test_biography_is_merged_only_when_corroborated_and_unique() -> None:
    index = pd.DataFrame(
        {
            "source_ids": ["清史稿/卷1#甲", "清史稿/卷1#甲", "清史稿/卷2#乙"],
            "person_uid": ["cbdb:1", "cgedq:S1", "cbdb:2"],
            "passage": ["甲，江蘇人，官巡撫", "甲，江蘇人，官巡撫", "乙，湖北人"],
        }
    )
    master = pd.DataFrame(
        {
            "person_uid": ["cbdb:1", "cgedq:S1", "cbdb:2"],
            "native_province_effective": ["江蘇", "浙江", "湖北"],
            "banner_effective": ["unknown", "unknown", "unknown"],
            "career_first_year": [1760, 1760, 1760],
            "career_last_year": [1770, 1770, 1770],
        }
    )
    entities = pd.DataFrame({"entity_id": ["cbdb:1", "cgedq:S1", "cbdb:2"]})
    cbdb = pd.DataFrame({"cbdb_personid": [1, 2], "alt_names": [None, "字某"]})
    links = link_biographies(index, master, entities, cbdb)
    shared = links[links["source_ids"] == "清史稿/卷1#甲"].set_index("person_uid")
    assert shared.loc["cbdb:1", "decision"] == "merge"
    assert shared.loc["cgedq:S1", "decision"] == "confirmed_distinct"
    assert links.loc[links["source_ids"] == "清史稿/卷2#乙", "decision"].iloc[0] == "merge"


# the biography matcher reads the frozen appointment table for office corroboration; the
# public repository deliberately does not ship person-level derived data, so the test skips
# with a reason (U11R; the same reason the sibling artefact tests already use)
@pytest.mark.skipif(
    not (PROJECT_ROOT / "data" / "processed" / "appointments.parquet").exists(),
    reason="frozen appointment table missing (public checkout ships no person-level data)",
)
def test_biography_without_corroboration_is_quarantined() -> None:
    index = pd.DataFrame(
        {"source_ids": ["清史稿/卷3#丙"], "person_uid": ["cbdb:3"], "passage": ["丙，不知何許人"]}
    )
    master = pd.DataFrame(
        {
            "person_uid": ["cbdb:3"],
            "native_province_effective": [None],
            "banner_effective": ["unknown"],
            "career_first_year": [1760],
            "career_last_year": [1770],
        }
    )
    links = link_biographies(index, master, pd.DataFrame({"entity_id": ["cbdb:3"]}),
                             pd.DataFrame({"cbdb_personid": [3], "alt_names": [None]}))
    assert links.loc[0, "decision"] == "quarantine"


def test_entity_status_never_primary_when_the_link_is_unresolved() -> None:
    master = pd.DataFrame(
        {
            "person_uid": ["cbdb:1", "cgedq:S9", "cbdb:2"],
            "cgedq_person_id": ["S9", "S9", None],
        }
    )
    entities = pd.DataFrame(
        {
            "entity_id": ["cbdb:1", "cgedq:S9", "cbdb:2"],
            "cbdb_personid": pd.array([1, pd.NA, 2], dtype="Int64"),
            "source": ["CBDB+CGEDQ", "CGEDQ", "CBDB"],
        }
    )
    cross = pd.DataFrame(
        {
            "l_cgedq_person_id": ["S9"],
            "r_cbdb_personid": [1],
            "review_status": ["accepted_deterministic"],
            "link_decision": ["deterministic_multifield"],
        }
    )
    canonical = pd.DataFrame({"cgedq_person_id": ["S9"], "merged": [False]})
    resolved = resolve_entity_links(entities, master, cross, canonical).set_index("entity_id")
    assert resolved.loc["cbdb:1", "link_status"] == "accepted_cross_link"
    assert resolved.loc["cgedq:S9", "link_status"] == "merged_into_cbdb"
    assert resolved.loc["cgedq:S9", "primary_eligible"] == False  # noqa: E712
    assert resolved.loc["cbdb:2", "link_status"] == "explicit_id"
    assert resolved.loc["cbdb:2", "primary_eligible"] == True  # noqa: E712


# ------------------------------------------------------------- committed artefacts


@pytest.mark.skipif(not (PROCESSED_V02 / "entities.parquet").exists(), reason="v0.2 tables missing")
def test_primary_frame_carries_no_unresolved_link() -> None:
    entities = pd.read_parquet(PROCESSED_V02 / "entities.parquet")
    allowed = {"explicit_id", "accepted_cross_link", "standalone"}
    primary = entities[entities["primary_eligible"]]
    assert set(primary["link_status"]) <= allowed


@pytest.mark.skipif(not (PROCESSED_V02 / "biography_links.parquet").exists(), reason="v0.2 tables missing")
def test_no_biography_feeds_two_entities() -> None:
    links = pd.read_parquet(PROCESSED_V02 / "biography_links.parquet")
    merged = links[links["decision"] == "merge"]
    assert int((merged.groupby("source_ids").size() > 1).sum()) == 0


@pytest.mark.skipif(not (PROCESSED_V02 / "linkage_gold.parquet").exists(), reason="v0.2 tables missing")
def test_gold_is_stratified_with_both_labels_and_a_split() -> None:
    gold = pd.read_parquet(PROCESSED_V02 / "linkage_gold.parquet")
    labelled = gold[gold["gold_label"].notna()]
    assert len(labelled) >= 300
    assert set(labelled["gold_label"].unique()) == {0.0, 1.0}
    assert set(labelled["split"].unique()) == {"train", "held_out"}
    for stratum in (
        "high_frequency_name",
        "variant_char",
        "banner_no_surname",
        "province_conflict",
        "degree_conflict",
    ):
        assert stratum in set(gold["stratum"]), stratum


def test_dedupe_gold_builder_labels_structural_duplicates() -> None:
    pairs = pd.DataFrame(
        [
            _dedupe_row(same_office_same_edition=True, n_shared_editions=1),
            _dedupe_row(pair_id="dedupe:S1|S3", banner_equal=False),
        ]
    )
    gold = build_dedupe_gold(pairs).set_index("pair_id")
    assert gold.loc["dedupe:S1|S2", "gold_label"] == 1
    assert gold.loc["dedupe:S1|S3", "gold_label"] == 0


def test_subgroup_table_marks_unlabelled_review_strata() -> None:
    gold = pd.DataFrame(
        {
            "pair_id": ["a", "b"],
            "task": ["cross_source", "cross_source"],
            "stratum": ["era_conflict", "multi_field_positive"],
            "gold_label": [pd.NA, 1.0],
            "split": ["held_out", "held_out"],
            "stratum_population": [100, 100],
            "stratum_sampled": [1, 1],
            "l_cgedq_person_id": "S1",
            "r_cbdb_personid": 1,
        }
    )
    scores = pd.DataFrame({"task": "cross_source", "pair_id": ["b"], "probability": [0.9]})
    table = subgroup_errors(scores, gold, {"cross_source": 0.5}).set_index("stratum")
    assert table.loc["era_conflict", "note"] == "review stratum: no usable gold conflict"
    assert table.loc["multi_field_positive", "precision"] == 1.0
