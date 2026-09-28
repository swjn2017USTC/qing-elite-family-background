"""Behaviour tests for the P05 harvest/enrichment contract.

The rules that matter: only passage-backed persons are eligible, a slot is accepted
from the model only with verbatim evidence (otherwise it stays unknown), the plan and
budget arithmetic are explicit, and resume skips finished work.
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

from qing_elite.llm.enrich import (
    HARD_BUDGET_RMB,
    SOFT_BUDGET_RMB,
    build_outputs,
    build_plan,
)
from qing_elite.llm.harvest import parse_volume_range


def test_volume_range_parsing() -> None:
    assert parse_volume_range("250-253") == [
        "清史稿/卷250",
        "清史稿/卷251",
        "清史稿/卷252",
        "清史稿/卷253",
    ]
    assert parse_volume_range("288") == ["清史稿/卷288"]


def test_plan_counts_persons_and_stays_within_budget_terms() -> None:
    eligible = pd.DataFrame(
        [
            {"person_uid": "a", "n_missing_slots": 3, "passage_chars": 1000, "highest_tier": "A1"},
            {"person_uid": "b", "n_missing_slots": 2, "passage_chars": 500, "highest_tier": "D"},
        ]
    )
    plan = build_plan(eligible, mean_window_chars=1000)
    row = plan.iloc[0]
    assert int(row["eligible_persons"]) == 2
    assert int(row["slot_gaps"]) == 5
    # 2 persons x (1000 x 0.6 + 420) input tokens; 2 x 246 output tokens
    assert int(row["estimated_input_tokens"]) == 2 * 1020
    assert int(row["estimated_output_tokens"]) == 2 * 246
    assert row["soft_budget_rmb"] == SOFT_BUDGET_RMB
    assert row["hard_budget_rmb"] == HARD_BUDGET_RMB
    assert float(row["estimated_rmb_off_peak"]) == pytest.approx(2040 / 1e6 * 1.0 + 492 / 1e6 * 4.0, abs=1e-4)
    assert float(row["estimated_rmb_peak"]) > float(row["estimated_rmb_off_peak"])


def _structured_frame() -> pd.DataFrame:
    rows = []
    for slot, known in (("father", False), ("grandfather", True), ("great_grandfather", False)):
        rows.append(
            {
                "person_uid": "cbdb:1",
                "ancestor_slot": slot,
                "ancestor_known": known,
                "ancestor_name": "某祖" if known else None,
                "ancestor_degree": "進士" if known else None,
                "ancestor_office_sample": "知府" if known else None,
                "source_title": "人名權威資料" if known else None,
            }
        )
    return pd.DataFrame(rows)


def _record(output: dict) -> dict:
    return {
        "person_uid": "cbdb:1",
        "name": "甲",
        "tier": "B",
        "banner": "unknown",
        "source_ids": "清史稿/卷288#甲",
        "prompt_window": "甲，字乙。父丙，康熙九年進士。祖丁，官知縣。",
        "call": {
            "call_id": "p05-cbdb_1",
            "status": "ok",
            "prompt_version": "p04-family-v3",
            "output_json": json.dumps(output, ensure_ascii=False),
        },
        "escalated": None,
        "cost_rmb": 0.001,
    }


def _slot(name=None, degree=None, office=None, evidence=None) -> dict:
    return {"name": name, "degree": degree, "office": office, "evidence": evidence}


def test_only_verbatim_evidence_is_accepted_otherwise_unknown() -> None:
    output = {
        "father": _slot("丙", "進士", None, "父丙，康熙九年進士"),  # verbatim
        "grandfather": _slot("丁", "知縣", None, "祖丁曾任知縣"),  # paraphrased -> rejected
        "great_grandfather": _slot(None, None, None, None),  # nothing to say
        "ambiguities": [],
        "confidence": "high",
        "insufficient_evidence": False,
    }
    enriched, failed, cost = build_outputs([_record(output)], _structured_frame())
    by_slot = enriched.set_index("ancestor_slot")
    assert by_slot.at["father", "final_source"] == "llm_extraction"
    assert by_slot.at["father", "final_name"] == "丙"
    assert by_slot.at["grandfather", "final_source"] == "cbdb_structured"  # structured wins
    assert by_slot.at["great_grandfather", "final_source"] == "unknown"
    assert bool(by_slot.at["great_grandfather", "unknown"])
    assert not failed.empty  # the paraphrased assertion is recorded as a failure
    assert "verbatim" in failed.iloc[0]["reason"]
    assert float(cost.iloc[0]["new_information_ratio_pct"]) == pytest.approx(33.33, abs=0.1)
    assert float(cost.iloc[0]["unknown_ratio_pct"]) == pytest.approx(33.33, abs=0.1)


def test_invalid_schema_marks_the_case_failed_and_keeps_slots_unknown() -> None:
    output = {"父": None, "祖父": None, "曾祖父": None}  # the v1-style improvisation
    enriched, failed, _cost = build_outputs([_record(output)], _structured_frame())
    assert (enriched["final_source"] == "unknown").sum() == 2  # grandfather stays structured
    assert len(failed) == 1
    assert failed.iloc[0]["status"] == "ok"
    assert "schema" in failed.iloc[0]["reason"]
