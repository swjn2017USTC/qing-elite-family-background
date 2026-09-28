"""U00 baseline defect reproductions (v0.1-one-day, commit a60274e).

Every test below asserts the *evidence-balanced* contract that U01+ will implement.
None of them holds under the frozen v0.1 implementation, so each is marked
``xfail(strict=True)``: it fails now by design and keeps the historical suite green,
while the strict marker turns it into a hard error the moment the behaviour changes
(so a fix cannot land without deliberately retiring the marker here).

They are not a regression suite for the v0.1 pipeline; they are the machine-checkable
form of the seven P0 findings in ``reports/upgrade/U00_BASELINE_AUDIT.md`` and
``audit/v02/u00_findings.csv``. Run them on their own with::

    uv run pytest tests/test_u00_defect_repro.py -rxX
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pandas as pd
import pytest

from qing_elite.analysis.codebook import load_codebook, person_indicators
from qing_elite.build_audit import unknown_coding_audit
from qing_elite.llm.enrich import build_outputs

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROCESSED = PROJECT_ROOT / "data" / "processed"

pytestmark = pytest.mark.xfail(
    strict=True,
    reason="U00: v0.1-one-day implementation is expected to violate this contract; "
    "retire the marker in the phase that fixes it",
)


def _person_indicators_frame(*, sufficient: bool, office: bool) -> pd.DataFrame:
    """A person whose ancestor identities are known but whose office field is empty."""
    return pd.DataFrame(
        [
            {
                "person_uid": "u00:example",
                "ancestor_slot": slot,
                "slot_sufficient": sufficient,
                "slot_has_degree": False,
                "slot_has_office": office,
                "structured_office_tier": None,
                "final_office": None,
            }
            for slot in ("father", "grandfather", "great_grandfather")
        ]
    )


def test_known_ancestor_without_office_field_is_not_coded_as_zero() -> None:
    """U00-01: identity known + no office field must be unknown, not ancestor_official_any=0."""
    indicators = person_indicators(
        _person_indicators_frame(sufficient=True, office=False), load_codebook(), {}
    )
    row = indicators.iloc[0]
    assert pd.isna(row["ancestor_official_any"]), (
        "absence of an office record was coded as a definite 'no office ancestor' "
        f"(observed ancestor_official_any={row['ancestor_official_any']!r}); "
        "unknown != 0"
    )
    assert pd.isna(row["strict_commoner_3g"]), (
        "strict_commoner_3g was derived from missing office/degree fields "
        f"(observed {row['strict_commoner_3g']!r})"
    )


def test_manual_validation_has_no_pending_review() -> None:
    """U00-02: audit/manual_validation.csv must not ship with human_review_status=pending."""
    validation = pd.read_csv(PROJECT_ROOT / "audit" / "manual_validation.csv")
    pending = int((validation["human_review_status"] == "pending").sum())
    assert pending == 0, f"{pending}/{len(validation)} validation cases are still pending"


def test_d_layer_estimate_is_not_linkage_dependent() -> None:
    """U00-03: the D-layer headline rate must not move when medium links are dropped."""
    persons = pd.read_parquet(PROCESSED / "person_indicators.parquet")
    master = pd.read_parquet(PROCESSED / "officials_master.parquet")
    frame = persons.merge(
        master[["person_uid", "linkage_confidence"]], on="person_uid", how="left"
    )
    d_layer = frame[(frame["tier_group"] == "D") & frame["ancestor_official_any"].notna()]
    high = d_layer[d_layer["linkage_confidence"] == "high"]
    rate_all = float(d_layer["ancestor_official_any"].mean())
    rate_high = float(high["ancestor_official_any"].mean())
    assert abs(rate_all - rate_high) <= 0.05, (
        f"D-layer rate all-candidate={rate_all:.3f} (n={len(d_layer)}) vs "
        f"high-only={rate_high:.3f} (n={len(high)}); medium links are driving the result"
    )


def test_one_biography_is_not_shared_by_unconfirmed_persons() -> None:
    """U00-04: a source biography may back at most one person_uid in the enrichment."""
    enriched = pd.read_parquet(PROCESSED / "family_enriched.parquet")
    shared = enriched.groupby("source_ids")["person_uid"].nunique()
    offenders = shared[shared > 1]
    assert len(offenders) == 0, (
        f"{len(offenders)} source_ids are assigned to multiple person_uid "
        f"(e.g. {list(offenders.index[:3])})"
    )


def test_every_escalation_enters_the_adjudication() -> None:
    """U00-05: a second (conflict-resolution) call must be able to change the final slot."""
    primary = {
        "father": {"name": None, "degree": None, "office": None, "evidence": None},
        "grandfather": {"name": None, "degree": None, "office": None, "evidence": None},
        "great_grandfather": {"name": None, "degree": None, "office": None, "evidence": None},
        "ambiguities": [],
        "confidence": "low",
        "insufficient_evidence": True,
    }
    escalated = json.loads(json.dumps(primary))
    escalated["father"] = {
        "name": "輝祖",
        "degree": None,
        "office": "湖廣總督",
        "evidence": "湖廣總督輝祖子",
    }
    record = {
        "person_uid": "u00:example",
        "name": "李鍇",
        "tier": "D",
        "banner": "unknown",
        "source_ids": "清史稿/卷485#李鍇",
        "prompt_window": "湖廣總督輝祖子。",
        "call": {
            "call_id": "u00-example",
            "status": "ok",
            "prompt_version": "p04-family-v3",
            "output_json": json.dumps(primary, ensure_ascii=False),
        },
        "escalated": {
            "call_id": "u00-example-esc",
            "status": "ok",
            "prompt_version": "p04-family-v3",
            "output_json": json.dumps(escalated, ensure_ascii=False),
        },
    }
    enriched, _, _ = build_outputs(
        [record], structured_frame=pd.DataFrame(columns=["person_uid", "ancestor_slot"])
    )
    father = enriched[enriched["ancestor_slot"] == "father"].iloc[0]
    assert father["final_office"] == "湖廣總督", (
        "build_outputs ignored the escalation call and kept the empty primary slot "
        f"(observed final_office={father['final_office']!r})"
    )


def test_unknown_coding_checks_can_actually_fail(monkeypatch, tmp_path) -> None:
    """U00-06: the banner/province unknown checks must not be tautologies."""
    import qing_elite.build_audit as build_audit

    monkeypatch.setattr(build_audit, "AUDIT_DIR", tmp_path)
    persons = pd.DataFrame(
        {
            "person_uid": ["u00:example"],
            "ancestor_official_any": [1],
            "n_slots_sufficient": [1],
            "strict_commoner_3g": [pd.NA],
            "elite_generations_count": [1],
        }
    )
    family = pd.DataFrame(
        {
            "provenance": ["cbdb_structured"],
            "final_name": ["輝祖"],
            "final_degree": [None],
            "final_office": ["湖廣總督"],
        }
    )
    # A master whose unknown banner is recoded into a real analysis category, and whose
    # missing province is recoded into a province label: exactly what §10 claims to catch.
    master = pd.DataFrame(
        {
            "person_uid": ["u00:example"],
            "banner_effective": ["unknown"],
            "native_province_effective": [None],
            "banner_group": ["non_banner"],
            "province": ["unknown_province"],
        }
    )
    table = unknown_coding_audit(persons, family, master)
    violations = dict(zip(table["check"], table["violations"]))
    assert violations["banner_unknown_not_treated_as_non_banner"] >= 1, (
        "check is the tautology eq('unknown') & ne('unknown'): it can never fire"
    )
    assert violations["province_missing_not_a_category"] >= 1, (
        "check is the tautology isna().sum() - isna().sum(): it can never fire"
    )


def test_p08_has_a_release_gate() -> None:
    """U00-07: packaging must stop on pending conflicts and missing required artifacts."""
    spec = importlib.util.find_spec("qing_elite.release_gate")
    assert spec is not None, "no python -m qing_elite.release_gate entry point exists"


def test_p08_ships_the_planned_figure_set() -> None:
    """U00-07: the release must contain the planned data-flow and linkage-quality figures."""
    from qing_elite import build_final

    figures = tuple(build_final.FIGURES)
    assert len(figures) >= 5, f"only {len(figures)} figures are packaged: {figures}"
