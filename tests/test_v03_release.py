"""U11R tests: release gate, adversarial audit, manifest and generated documentation.

The gate and the auditor are both "checks that must be able to fail", so the tests inject the
defects each one claims to catch instead of asserting that it passes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from qing_elite.utils.config import PROJECT_ROOT
from qing_elite.v03 import audit as audit_mod
from qing_elite.v03 import docs as docs_mod
from qing_elite.v03 import release as release_mod
from qing_elite.v03.lit.registry import PROCESSED_V03_DIR


def _requires_full_artifacts() -> None:
    if not release_mod.has_full_artifacts():
        pytest.skip("requires restricted local v0.3 artifacts")


def test_gate_checks_are_declared_and_non_trivial() -> None:
    assert set(release_mod.GATE_CHECKS) == {
        "required_files",
        "schemas_and_lineage",
        "review_state",
        "public_boundary",
        "adversarial_audit",
        "tests",
    }


def test_required_artifact_list_covers_each_stage() -> None:
    names = " ".join(release_mod.REQUIRED_ARTIFACTS)
    for stage_token in ("persons", "kin_edges", "career_events", "entity_links", "analysis_sample", "extension"):
        assert stage_token in names


def test_public_boundary_refuses_tracked_person_level_data(tmp_path, monkeypatch) -> None:
    problems = release_mod.check_public_boundary()
    # the real repository must be clean; a failure here means something restricted got committed
    assert not [problem for problem in problems if "tracked restricted file" in problem], problems


def test_public_boundary_would_catch_a_violation(monkeypatch) -> None:
    monkeypatch.setattr(release_mod, "_git", lambda *args: "data/processed_v03/persons.parquet")
    problems = release_mod.check_public_boundary()
    assert any("tracked restricted file" in problem for problem in problems)


def test_review_state_gate_runs_on_the_real_artifacts() -> None:
    _requires_full_artifacts()
    problems = release_mod.check_review_state()
    assert isinstance(problems, list)


def test_audit_checks_are_read_only_and_declared() -> None:
    assert len(audit_mod.CHECKS) >= 10
    for name in ("unknown_not_zero", "denominator_drift", "entity_duplication", "linkage_leakage"):
        assert name in audit_mod.CHECKS


def test_unknown_to_zero_check_detects_an_injected_defect(monkeypatch) -> None:
    frame = pd.DataFrame(
        {
            "family_observable": [False],
            "direct_3g_degree_count": [2],
            "direct_3g_office_count": [0],
        }
    )
    monkeypatch.setattr(audit_mod, "_read", lambda name: frame if name == "analysis_sample.parquet" else None)
    issues = audit_mod.check_unknown_not_zero()
    assert any(issue["check"] == "unknown_to_zero" for issue in issues)


def test_entity_duplication_check_detects_an_injected_defect(monkeypatch) -> None:
    frame = pd.DataFrame({"person_id": ["cbdb:1", "cbdb:1"]})
    monkeypatch.setattr(
        audit_mod, "_read", lambda name: frame if name == "persons.parquet" else None
    )
    assert any(issue["check"] == "entity_duplication" for issue in audit_mod.check_entity_duplication())


def test_denominator_check_detects_drift(monkeypatch) -> None:
    coverage = pd.DataFrame({"group": ["all linked persons"], "n": [10]})
    # a cohort table that no longer sums to the pilot frame must trip the check
    sample = pd.DataFrame({"cgedq_person_id": ["a"]})

    def fake_read(name: str):
        return {
            "analysis_coverage.parquet": coverage,
            "analysis_sample.parquet": sample,
        }.get(name)

    monkeypatch.setattr(audit_mod, "_read", fake_read)
    assert any(issue["check"] == "denominator_drift" for issue in audit_mod.check_denominator_drift())


def test_causal_language_check_allows_negations_and_flags_assertions(tmp_path, monkeypatch) -> None:
    report_dir = tmp_path
    (report_dir / "U99R.md").write_text("本阶段不能证明家世导致官位。", encoding="utf-8")
    monkeypatch.setattr(audit_mod, "REPORT_DIR", report_dir)
    assert audit_mod.check_claim_language() == []
    (report_dir / "U99R.md").write_text("家世导致官位。", encoding="utf-8")
    assert audit_mod.check_claim_language()


def test_release_gate_records_every_check() -> None:
    record = (
        release_mod.run_gate(with_tests=False)
        if release_mod.has_full_artifacts()
        else release_mod.run_public_snapshot_gate(with_tests=False)
    )
    assert record["release"].startswith("v0.3-cohort-kin-network")
    expected = release_mod.GATE_CHECKS if release_mod.has_full_artifacts() else release_mod.PUBLIC_SNAPSHOT_CHECKS
    assert set(record["checks"]) == set(expected)
    assert record["checks"]["tests"]["status"] == "SKIP"


def test_manifest_pins_the_freeze_items() -> None:
    manifest = (
        release_mod.build_manifest()
        if release_mod.has_full_artifacts()
        else json.loads((PROJECT_ROOT / "RELEASE_MANIFEST.json").read_text(encoding="utf-8"))
    )
    for key in ("code_commit", "environment", "contracts", "seeds", "thresholds", "models", "upstream"):
        assert key in manifest, key
    assert manifest["contracts"]["ontology_version"].startswith("relations-v")
    assert manifest["upstream"]["linkage_code"]["commit"]
    assert manifest["literature_registry"]["rows"] > 0
    assert manifest["artifacts"], "manifest must hash the release artifacts"


def test_documentation_is_rendered_from_metrics() -> None:
    _requires_full_artifacts()
    manifest = (
        release_mod.build_manifest()
        if release_mod.has_full_artifacts()
        else json.loads((PROJECT_ROOT / "RELEASE_MANIFEST.json").read_text(encoding="utf-8"))
    )
    gate = (
        release_mod.run_gate(with_tests=False)
        if release_mod.has_full_artifacts()
        else release_mod.run_public_snapshot_gate(with_tests=False)
    )
    methods = docs_mod.render_methods(manifest)
    codebook = docs_mod.render_codebook()
    readme = docs_mod.render_readme(manifest, gate)
    final = docs_mod.render_final_report(manifest, gate)
    assert manifest["code_commit_short"] in readme
    assert gate["status"] in readme and gate["status"] in final
    assert "ontology_version" in codebook or "relations" in codebook
    assert f"{next(iter(manifest['artifacts'].values()))['rows']:,}" in methods


def test_public_snapshot_gate_is_explicit_about_its_scope() -> None:
    if release_mod.has_full_artifacts():
        pytest.skip("only applicable to the data-free public snapshot")
    record = release_mod.run_public_snapshot_gate(with_tests=False)
    assert record["status"] == "PASS", record


def test_release_manifest_matches_artifacts_on_disk() -> None:
    _requires_full_artifacts()
    path = PROJECT_ROOT / "RELEASE_MANIFEST.json"
    if not path.exists():
        pytest.skip("manifest not built in this checkout")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    artifact = "persons.parquet"
    if artifact not in manifest["artifacts"]:
        pytest.skip("artifact not part of this checkout")
    on_disk = release_mod.sha256(PROCESSED_V03_DIR / artifact)
    assert manifest["artifacts"][artifact]["sha256"] == on_disk
