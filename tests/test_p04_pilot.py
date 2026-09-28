"""Behaviour tests for the P04 extraction contract and pilot metrics.

What matters here: the response is judged against the declared schema (not mere
parseability), evidence is verified verbatim, the prompt never leaks gold data, and
the cost arithmetic follows the published prices.
"""

from __future__ import annotations

import pytest

from qing_elite.llm.extraction import (
    SLOTS,
    build_user_prompt,
    call_cost_rmb,
    evidence_fidelity,
    evidence_matches_passage,
    schema_validity,
)
from qing_elite.llm.estimation import strip_wiki_markup
from qing_elite.llm.pilot import (
    _name_verdict,
    _office_verdict,
    _value_verdict,
    plan_costs,
    summarize,
)
from qing_elite.llm.pilot import PilotCase
import pandas as pd


def _valid_output(**overrides: object) -> dict:
    slot = {"name": None, "degree": None, "office": None, "evidence": None}
    output = {
        "father": dict(slot),
        "grandfather": dict(slot),
        "great_grandfather": dict(slot),
        "ambiguities": [],
        "confidence": "high",
        "insufficient_evidence": True,
    }
    output.update(overrides)
    return output


# ------------------------------------------------------------ schema contract


def test_schema_validity_accepts_the_declared_shape() -> None:
    ok, reason = schema_validity(_valid_output())
    assert ok and reason == "ok"


def test_schema_validity_rejects_improvised_keys() -> None:
    """The v1 prompt omitted the schema and the model invented Chinese keys."""
    improvised = {"父": None, "祖父": None, "曾祖父": None, "evidence": None, "ambiguities": None}
    ok, reason = schema_validity(improvised)
    assert not ok
    assert "father" in reason


def test_schema_validity_rejects_extra_slot_fields_and_bad_enums() -> None:
    output = _valid_output()
    output["father"] = {"name": "甲", "degree": None, "office": None, "evidence": None, "exam": None}
    assert schema_validity(output)[0] is False
    assert schema_validity(_valid_output(confidence="certain"))[0] is False
    assert schema_validity(_valid_output(insufficient_evidence="yes"))[0] is False


# ---------------------------------------------------------------- evidence


def test_evidence_matches_verbatim_only() -> None:
    passage = "甲，字乙，安徽桐城人。父丙，康熙九年進士。"
    assert evidence_matches_passage("父丙，康熙九年進士", passage)
    assert not evidence_matches_passage("父丙為康熙九年進士", passage)


def test_evidence_tolerates_window_join_markers() -> None:
    # family_window joins merged spans with an ellipsis; a quote may straddle it.
    passage = "父丙，康熙九年進士…祖丁，官知縣"
    assert evidence_matches_passage("進士…祖丁", passage)
    assert evidence_matches_passage("父丙，康熙九年進士", passage)


def test_evidence_fidelity_counts_unsupported_assertions() -> None:
    output = _valid_output()
    output["father"] = {"name": "丙", "degree": "進士", "office": None, "evidence": "父丙，康熙九年進士"}
    output["grandfather"] = {"name": "丁", "degree": None, "office": None, "evidence": "捏造的引文"}
    fidelity = evidence_fidelity(output, "父丙，康熙九年進士。祖丁。")
    assert fidelity["non_null_slots"] == 2
    assert fidelity["evidence_verbatim"] == 1
    assert fidelity["unsupported_assertions"] == 1


def test_wiki_markup_stripping_makes_pilot_quotes_verbatim() -> None:
    raw = "曾祖{{ProperNoun|日燿}}，明末官{{ProperNoun|歙縣}}訓導，鄉里仰其高節。"
    clean = strip_wiki_markup(raw)
    assert clean.startswith("曾祖日燿，明末官歙縣訓導")
    assert evidence_matches_passage("曾祖日燿，明末官歙縣訓導", clean)
    assert not evidence_matches_passage("曾祖日燿，明末官歙縣訓導", raw)
    assert strip_wiki_markup("見[[清史稿/卷288|卷二百八十八]]") == "見卷二百八十八"


# ------------------------------------------------------------------ verdicts


def test_given_name_only_extraction_is_a_match_when_the_text_says_so() -> None:
    # 清史稿 writes 大學士英次子 for 張英; extracting 英 is faithful, not an error.
    assert _name_verdict("英", "張英", True, "英") == "match"
    assert _name_verdict("張英", "張英", True, "英") == "match"
    assert _name_verdict("別的", "張英", True, "英") == "wrong_name"
    assert _name_verdict(None, "張英", True, "英") == "abstain"


def test_assertion_without_gold_is_recorded_as_new_information() -> None:
    assert _name_verdict("執忠", None, False, None) == "no_gold_to_compare"
    assert _office_verdict("大學士", None) == "no_gold_to_compare"
    assert _value_verdict("進士", None) == "no_gold_to_compare"


def test_office_verdict_uses_the_whole_recorded_office_set() -> None:
    gold = "南京工部主事 / 太常寺卿 / 巡按御史"
    assert _office_verdict("御史", gold) == "match"
    assert _office_verdict("大學士", gold) == "mismatch"
    assert _office_verdict(None, gold) == "abstained"


# -------------------------------------------------------------- prompt/cost


def test_prompt_carries_only_the_name_and_passage() -> None:
    prompt = build_user_prompt("張廷玉", "張廷玉，字衡臣，大學士英次子", "清史稿/卷288#張廷玉")
    assert "張廷玉" in prompt and "大學士英次子" in prompt
    assert "張英" not in prompt  # gold must never leak into the request


def test_call_cost_uses_cache_hit_and_miss_prices() -> None:
    class _Call:
        input_tokens = 1000
        cached_tokens = 400
        output_tokens = 100

    # off-peak: 600 miss @1 + 400 hit @0.02 + 100 out @4  (per 1M tokens)
    expected = 600 / 1e6 * 1.0 + 400 / 1e6 * 0.02 + 100 / 1e6 * 4.0
    assert call_cost_rmb(_Call()) == pytest.approx(round(expected, 6))
    assert call_cost_rmb(_Call(), peak=True) == pytest.approx(round(expected * 2, 6))


def _case(name: str, passage: str) -> PilotCase:
    return PilotCase(
        person_uid=f"cbdb:{name}",
        cbdb_personid=1,
        name=name,
        tier="B",
        banner="unknown",
        arm="gold",
        source_ids="清史稿/卷288#x",
        passage=passage,
        window=passage,
        has_cbdb_ancestor=True,
        same_name_cbdb_persons=1,
    )


def test_plan_costs_scales_with_cases_and_escalations() -> None:
    cases = [_case("甲", "文" * 1000), _case("乙", "文" * 2000)]
    plan = plan_costs(cases)
    assert int(plan["cases"].iloc[0]) == 2
    assert float(plan["estimated_rmb_peak"].iloc[0]) > float(plan["estimated_rmb_off_peak"].iloc[0])


def test_summarize_reports_the_six_required_metrics() -> None:
    results = pd.DataFrame(
        [
            {
                "arm": "gold",
                "json_valid": True,
                "non_null_slots": 1,
                "slots_evidence_verbatim": 1,
                "unsupported_assertions": 0,
                "father_gold_in_passage": True,
                "father_name_verdict": "match",
                "grandfather_name_verdict": "correct_abstention",
                "father_degree_verdict": "match",
                "grandfather_degree_verdict": "n/a",
                "father_office_verdict": "mismatch",
                "grandfather_office_verdict": "n/a",
            }
        ]
    )
    summary = summarize(results)
    metrics = set(summary["metric"])
    assert {
        "json_validity_pct",
        "evidence_fidelity_pct",
        "father_identity_accuracy_pct",
        "degree_accuracy_pct",
        "office_accuracy_pct",
        "hallucination_rate_pct",
    } <= metrics
    assert float(summary.set_index("metric").at["father_identity_accuracy_pct", "value"]) == 100.0
