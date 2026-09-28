"""U05R pilot tests: frame sampling, rule parser, verifier, evaluator and gate decisions.

The parser and verifier tests use the real OCR text of two scanned roster pages (kept as
fixtures inline) because the hard part of this stage is exactly the unsegmented, OCR-damaged
text — synthetic clean strings would not exercise it.
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

from qing_elite.v03.pilot import evaluate as evaluate_mod
from qing_elite.v03.pilot import ocr as ocr_mod
from qing_elite.v03.pilot import rules
from qing_elite.v03.pilot import verifier
from qing_elite.v03.pilot.frame import FrameError, build_frame, load_frame_config

# Two real page texts (PaddleOCR-VL output, normalised) from the public-domain 同官录 scan.
PAGE2 = (
    "金奎光科舉人餘杭敎諭字梅庵號小農行一嘉慶戊寅年正月初四日生順人祖籍浙江山陰縣"
    "式丁酉科副榜就職直隸州判祖枝芳邑庠生曾祖玉章乾壬午隆胞叔照原任縣典典史"
    "胞弟基原任阜甯縣入灘司巡檢塘候選未入流子人鏡業"
)
PAGE4 = (
    "父諱向榮原任河南安陽縣縣丞升用知縣王厚莊晉祖諱煜太學生候選運同諱仕基庠生"
    "原任廣東廣韶南連兵備道胞兄良治縣候丞選子萬牲承繼長房理問銜知縣用江蘇候補縣丞"
    "萬裕候選縣丞萬安就塾孫國光幼"
)


# --------------------------------------------------------------------- frame config


def test_frame_config_stays_inside_the_pilot_budget() -> None:
    config = load_frame_config()
    assert 200 <= config["target_size"] <= 500
    assert config["min_per_cell"] >= 1


def test_frame_config_rejects_an_oversized_target(tmp_path) -> None:
    config = load_frame_config()
    config["target_size"] = 900
    path = tmp_path / "frame.yaml"
    path.write_text(json.dumps(config), encoding="utf-8")
    load_frame_config.cache_clear()
    with pytest.raises(FrameError, match="200–500"):
        build_frame.__wrapped__ if False else load_frame_config(path)
    load_frame_config.cache_clear()


# --------------------------------------------------------------------- rule parser


def test_relation_terms_are_matched_longest_first() -> None:
    outcome = rules.parse_roster_text("堂伯叔祖炤原任知縣", focal_person="ego", source_id="s", locator_prefix="s")
    assert outcome.rows[0]["relation_type"] == "great_uncle_paternal"


def test_ancestral_registration_is_not_a_kin_mention() -> None:
    outcome = rules.parse_roster_text("祖籍浙江山陰縣人", focal_person="ego", source_id="s", locator_prefix="s")
    assert outcome.rows == []
    assert outcome.abstained == []


def test_single_glyph_names_are_recovered() -> None:
    outcome = rules.parse_roster_text("胞叔照原任縣典史", focal_person="ego", source_id="s", locator_prefix="s")
    assert outcome.rows[0]["kin_name"] == "照"
    assert outcome.rows[0]["office_raw"] == "典史"


def test_two_glyph_names_survive_reign_glyph_collisions() -> None:
    outcome = rules.parse_roster_text("曾祖玉章乾隆壬午", focal_person="ego", source_id="s", locator_prefix="s")
    assert outcome.rows[0]["kin_name"] == "玉章"


def test_name_ending_in_a_reign_glyph_is_not_truncated() -> None:
    outcome = rules.parse_roster_text("孫國光幼", focal_person="ego", source_id="s", locator_prefix="s")
    assert outcome.rows[0]["kin_name"] == "國光"


def test_degrees_and_offices_are_not_borrowed_from_the_next_kin() -> None:
    outcome = rules.parse_roster_text(
        "胞弟基原任阜甯縣入灘司巡檢子人鏡業", focal_person="ego", source_id="s", locator_prefix="s"
    )
    by_relation = {row["relation_type"]: row for row in outcome.rows}
    assert by_relation["brother"]["office_raw"] == "巡檢"
    assert by_relation["son"]["office_raw"] is None
    assert by_relation["son"]["degree_raw"] is None


def test_offsets_reproduce_the_quote() -> None:
    text = rules.normalize_ocr_markdown(PAGE2)
    outcome = rules.parse_roster_text(text, focal_person="金奎光", source_id="s", locator_prefix="s")
    for row in outcome.rows:
        start, end = row["offsets"]
        assert text[start:end] == row["quote"]


def test_generation_follows_the_relation_ontology() -> None:
    outcome = rules.parse_roster_text("父諱向榮原任縣丞祖諱仕基庠生", focal_person="ego", source_id="s", locator_prefix="s")
    generations = {row["relation_type"]: row["generation"] for row in outcome.rows}
    assert generations["father"] == 1
    assert generations["grandfather"] == 2


def test_abstains_when_no_name_can_be_recovered() -> None:
    outcome = rules.parse_roster_text("胞叔原任縣典史", focal_person="ego", source_id="s", locator_prefix="s")
    assert outcome.rows == []
    assert outcome.abstained[0]["reason"] == "no_name_token_after_relation"
    assert outcome.convergence == 0.0


def test_table_markdown_is_flattened_without_markup() -> None:
    markdown = "<table><tr><td>父煒</td><td>原任清河縣</td></tr></table>"
    text = rules.normalize_ocr_markdown(markdown)
    assert "<td>" not in text
    assert "父煒" in text and "原任清河縣" in text


def test_parser_finds_the_expected_kin_on_a_real_page() -> None:
    outcome = rules.parse_pages(
        [{"page_number_1based": 2, "markdown_text": PAGE2}],
        focal_person="金奎光",
        source_id="tg",
    )
    names = {row["kin_name"] for row in outcome.rows}
    assert {"枝芳", "玉章", "照", "基", "人鏡"} <= names
    assert outcome.convergence == 1.0


# --------------------------------------------------------------------- verifier


def _row(**overrides) -> dict:
    row = {
        "focal_person": "ego",
        "relation_type": "father",
        "kin_name": "某",
        "generation": 1,
        "degree_raw": None,
        "office_raw": None,
        "source_id": "s",
        "locator": "s#p1:0-4",
        "quote": "父某",
        "offsets": [0, 2],
        "page_number": 1,
        "alter_person_id": "cbdb:1",
    }
    row.update(overrides)
    return row


def test_quote_and_offset_mismatch_is_flagged() -> None:
    verified = verifier.verify_rows([_row()], page_text={1: "父某"})
    assert verified.loc[0, "evidence_span_failure"] == 0.0
    broken = verifier.verify_rows([_row(offsets=[0, 1])], page_text={1: "父某"})
    assert broken.loc[0, "evidence_span_failure"] == 1.0


def test_unknown_relation_raises_rare_relation_risk() -> None:
    verified = verifier.verify_rows([_row(relation_type="foster_father")], page_text={1: "父某"})
    assert verified.loc[0, "rare_relation"] == 1.0


def test_multi_valued_relations_are_not_conflicts() -> None:
    rows = [
        _row(relation_type="son", kin_name="甲", generation=-1, alter_person_id="cbdb:1"),
        _row(relation_type="son", kin_name="乙", generation=-1, alter_person_id="cbdb:2"),
    ]
    verified = verifier.verify_rows(rows, page_text={1: "父某"})
    assert verified["source_conflict"].sum() == 0.0


def test_single_valued_relations_with_two_names_are_conflicts() -> None:
    rows = [_row(relation_type="father", kin_name="甲"), _row(relation_type="father", kin_name="乙")]
    verified = verifier.verify_rows(rows, page_text={1: "父某"})
    assert verified["source_conflict"].sum() == 2.0


def test_impossible_chronology_is_flagged() -> None:
    rows = [_row(relation_type="father", kin_name="甲", generation=1)]
    verified = verifier.verify_rows(
        rows, page_text={1: "父某"}, person_years={"ego": 1700, "甲": 1750}
    )
    assert verified.loc[0, "chronology_violation"] == 1.0


def test_unresolved_alter_raises_linkage_uncertainty() -> None:
    verified = verifier.verify_rows([_row(alter_person_id=None)], page_text={1: "父某"})
    assert verified.loc[0, "linkage_uncertainty"] == 0.5


def test_scoring_uses_the_frozen_policy_tiers() -> None:
    mild = verifier.score(verifier.verify_rows([_row(offsets=[0, 1])], page_text={1: "父某"}))
    assert mild.loc[0, "route"] == "auto_accept"  # a single 0.10-weight failure stays LOW
    assert mild.loc[0, "policy_version"] == "review-policy-v1"

    severe_row = _row(
        offsets=[0, 1],
        relation_type="foster_father",
        alter_person_id=None,
        office_raw="某某",
        parser_disagreement=1.0,
    )
    severe = verifier.score(
        verifier.verify_rows(
            [severe_row],
            page_text={1: "父某"},
            ocr_flags={1: 1.0},
            person_years={"ego": 1700, "某": 1750},
        )
    )
    assert severe.loc[0, "total_score"] > 0.6
    assert severe.loc[0, "route"] == "human_review"


def test_ocr_proxy_needs_the_normalised_text(tmp_path) -> None:
    assert ocr_mod.ocr_page_quality("") == 1.0
    assert ocr_mod.ocr_page_quality(PAGE2) == 0.0
    # a table dump is markup-heavy but the normalised text is fine, so it must not be flagged
    long_table = "<table><tr><td>" + PAGE4 + "</td></tr></table>"
    assert ocr_mod.ocr_page_quality(long_table) == 0.0


def test_ocr_cache_paths_are_content_addressed(tmp_path) -> None:
    pdf = tmp_path / "book.pdf"
    pdf.write_text("x", encoding="utf-8")
    first = ocr_mod.cache_path(pdf, 1, 8, "document")
    assert first == ocr_mod.cache_path(pdf, 1, 8, "document")
    assert first != ocr_mod.cache_path(pdf, 9, 16, "document")


# --------------------------------------------------------------------- evaluation


def _gold() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"gold_id": "G-1", "page_number": 2, "relation_type": "father", "kin_name": "甲",
             "degree_raw": None, "office_raw": "縣丞"},
            {"gold_id": "G-2", "page_number": 2, "relation_type": "son", "kin_name": "乙",
             "degree_raw": "庠生", "office_raw": None},
        ]
    )


def test_evaluation_counts_only_gold_pages() -> None:
    predictions = pd.DataFrame(
        [
            {"page_number": 2, "relation_type": "father", "kin_name": "甲", "degree_raw": None, "office_raw": "縣丞"},
            {"page_number": 7, "relation_type": "son", "kin_name": "丙", "degree_raw": None, "office_raw": None},
        ]
    )
    metrics = evaluate_mod.evaluate(predictions, _gold(), page_texts={2: "父甲原任縣丞"})
    assert metrics["scored_pages"] == [2]
    assert metrics["precision"] == 1.0
    assert metrics["predictions_on_unscored_pages"] == 1


def test_office_promotion_pair_is_not_a_field_error() -> None:
    predictions = pd.DataFrame(
        [{"page_number": 2, "relation_type": "father", "kin_name": "甲", "degree_raw": None, "office_raw": "知縣"}]
    )
    metrics = evaluate_mod.evaluate(predictions, _gold(), page_texts={2: ""})
    assert metrics["field_accuracy"]["office_raw_strict"] == 0.0
    assert metrics["error_classes"].get("office_priority_confusion") == 1


def test_missing_nan_fields_are_not_counted_as_matches() -> None:
    predictions = pd.DataFrame(
        [{"page_number": 2, "relation_type": "son", "kin_name": "乙", "degree_raw": None, "office_raw": None}]
    )
    metrics = evaluate_mod.evaluate(predictions, _gold(), page_texts={2: ""})
    assert metrics["scoreable_fields"]["degree_raw"] == 1
    assert metrics["field_accuracy"]["degree_raw"] == 0.0


def test_missed_gold_entry_is_classified() -> None:
    predictions = pd.DataFrame(
        [{"page_number": 2, "relation_type": "father", "kin_name": "甲", "degree_raw": None, "office_raw": "縣丞"}]
    )
    metrics = evaluate_mod.evaluate(predictions, _gold(), page_texts={2: "父甲原任縣丞子乙庠生"})
    assert metrics["recall"] == 0.5
    assert metrics["error_classes"].get("missed_entry") == 1


# --------------------------------------------------------------------- gate decisions


def _ocr_metrics(**overrides) -> dict:
    base = {
        "precision": 0.9,
        "recall": 0.7,
        "field_accuracy": {"kin_name": 1.0, "degree_raw": 1.0, "office_raw_strict": 0.8},
    }
    base.update(overrides)
    return base


def test_ocr_gate_keeps_a_good_source() -> None:
    from qing_elite.v03.pilot.run import _ocr_gate, load_gates

    gate = _ocr_gate(_ocr_metrics(), load_gates(), ocr_failure_rate=0.0)
    assert gate["action"] == "keep"


def test_ocr_gate_demotes_when_only_attributes_fail() -> None:
    from qing_elite.v03.pilot.run import _ocr_gate, load_gates

    gate = _ocr_gate(
        _ocr_metrics(field_accuracy={"kin_name": 1.0, "degree_raw": 0.2, "office_raw_strict": 0.2}),
        load_gates(),
        ocr_failure_rate=0.0,
    )
    assert gate["action"] == "keep_identity_only"
    assert gate["attribute_failures"] and not gate["identity_failures"]


def test_ocr_gate_drops_a_source_with_weak_identity_extraction() -> None:
    from qing_elite.v03.pilot.run import _ocr_gate, load_gates

    gate = _ocr_gate(_ocr_metrics(precision=0.4), load_gates(), ocr_failure_rate=0.0)
    assert gate["action"] == "drop_source"
    assert gate["identity_failures"]
