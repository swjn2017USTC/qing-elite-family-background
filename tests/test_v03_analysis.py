"""U09R tests: sample construction rules, observability gating, data gate, sensitivity.

The tests pin the four stage constraints: a person without observable kin is never treated as
having zero family capital, the data gate refuses to model unidentifiable data, sensitivity
variants report their own n, and the report is rendered from the artifacts.
"""

from __future__ import annotations

import pandas as pd

from qing_elite.v03.analysis import analyze


def _frame(**overrides) -> pd.DataFrame:
    base = pd.DataFrame(
        {
            "cgedq_person_id": ["S1", "S2", "S3"],
            "link_confidence": ["u06r_auto_accept", "v02_deterministic", "v02_deterministic"],
            "observable_direct_kin": [0, 3, 0],
            "observable_senior_kin": [0, 1, 0],
            "direct_3g_degree_count": [0, 2, 0],
            "direct_3g_office_count": [0, 1, 0],
            "direct_elite_generations": [0, 2, 0],
            "all_senior_elite_kin_count": [0, 1, 0],
            "direct_line_complete": [False, True, False],
            "highest_rank_class": [7, 4, None],
            "highest_admin_level": ["county", "central", "unknown"],
            "central_local_route": ["local_only", "mixed", "unknown"],
            "cohort_id": ["1760-1775", "1760-1775", "1776-1785"],
            "n_events": [3, 9, 1],
            "time_to_first_office": [1765, 1760, 1790],
        }
    )
    for column, value in overrides.items():
        base[column] = value
    return base


def test_observability_gates_the_exposure() -> None:
    frame = analyze.deduplicate(_frame())
    assert frame["family_observable"].tolist() == [False, True, False]
    exposure = analyze.exposure_distribution(frame)
    row = exposure.loc[exposure["exposure"] == "direct_3g_degree_count"].iloc[0]
    # the denominator is the observable subset, not the whole frame
    assert row["denominator"] == 1


def test_duplicate_links_keep_the_strongest_confidence() -> None:
    duplicated = _frame().iloc[[1, 1]].copy()
    duplicated["cgedq_person_id"] = ["S1", "S1"]
    duplicated["link_confidence"] = ["v02_deterministic", "u06r_auto_accept"]
    frame = analyze.deduplicate(duplicated)
    assert len(frame) == 1
    assert frame.iloc[0]["link_confidence"] == "u06r_auto_accept"


def test_gate_degrades_to_descriptive_when_the_data_cannot_support_a_model() -> None:
    gate = analyze.data_gate(
        analyze.deduplicate(_frame()), exposure="direct_3g_degree_count", outcome="highest_rank_class"
    )
    assert gate["decision"] == "descriptive_only"
    assert any("both sides observed" in reason for reason in gate["reasons"])
    assert gate["missingness"] > 0.5


def test_gate_allows_a_model_only_when_rows_cells_and_variance_suffice() -> None:
    size = 400
    frame = pd.DataFrame(
        {
            "cgedq_person_id": [f"S{i}" for i in range(size)],
            "observable_direct_kin": [3] * size,
            "observable_senior_kin": [1] * size,
            "direct_3g_degree_count": [i % 3 for i in range(size)],
            "direct_3g_office_count": [i % 2 for i in range(size)],
            "direct_elite_generations": [i % 3 for i in range(size)],
            "all_senior_elite_kin_count": [i % 2 for i in range(size)],
            "direct_line_complete": [True] * size,
            "highest_rank_class": [1 + (i % 8) for i in range(size)],
            "cohort_id": ["1760-1775" if i % 2 else "1776-1785" for i in range(size)],
            "credential_class": ["jinshi" if i % 2 else "juren" for i in range(size)],
            "region_group": ["jiangnan" if i % 2 else "north" for i in range(size)],
            "central_local_route": ["local_only"] * size,
            "highest_admin_level": ["county"] * size,
            "n_events": [5] * size,
            "time_to_first_office": [1765] * size,
            "link_confidence": ["u06r_auto_accept"] * size,
        }
    )
    gate = analyze.data_gate(
        analyze.deduplicate(frame), exposure="direct_3g_degree_count", outcome="highest_rank_class"
    )
    assert gate["decision"] == "model_allowed", gate["reasons"]


def test_sensitivity_variants_report_their_own_n() -> None:
    frame = analyze.deduplicate(_frame())
    sensitivity = analyze.sensitivity(frame)
    assert set(sensitivity["variant"]) >= {"all_links", "u06r_auto_accept_only", "complete_direct_line"}
    assert (sensitivity["n"] <= len(frame)).all()
    small = sensitivity.loc[sensitivity["n"] < analyze.MIN_ROWS_FOR_MODEL]
    assert (small["interpretable"] == False).all()  # noqa: E712


def test_source_dependence_exposes_the_link_rule_gap() -> None:
    table = analyze.source_dependence(analyze.deduplicate(_frame()))
    assert set(table["value"]) == {"u06r_auto_accept", "v02_deterministic"}


def test_coverage_table_carries_denominators() -> None:
    table = analyze.coverage(analyze.deduplicate(_frame()))
    total = table.loc[table["group"] == "all linked persons"].iloc[0]
    assert total["n"] == 3
    assert total["family_observable"] == 1
    assert total["family_observable_share"] == round(1 / 3, 4)


def test_outcome_distribution_reports_unknown_separately() -> None:
    table = analyze.outcome_distribution(analyze.deduplicate(_frame()))
    route = table.loc[table["outcome"] == "central_local_route"].iloc[0]
    assert "unknown=1" in route["summary"]
    assert route["n_observed"] == 3


def test_report_is_rendered_from_the_metrics() -> None:
    from pathlib import Path

    from qing_elite.utils.config import PROJECT_ROOT

    metrics_path = PROJECT_ROOT / "data" / "interim_v03" / "analysis" / "u09r_metrics.json"
    if not metrics_path.exists():
        return  # the artifact is produced by the stage run; nothing to assert without it
    import json

    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    from qing_elite.v03.analysis.run import render_report

    text = render_report(metrics)
    assert f"{metrics['sample_flow']['links_total']:,}" in text
    assert metrics["gate"]["decision"] in text
    assert metrics["generated_at"] in text
    assert Path(PROJECT_ROOT / "reports" / "upgrade_v03" / "figures").exists()
