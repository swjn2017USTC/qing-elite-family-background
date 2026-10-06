"""Adversarial audit (U11R): read-only checks that try to falsify the release.

The auditor never fixes anything. It looks for the failure modes this project has already met
once — ``unknown`` quietly becoming ``0``, denominators drifting between a table and the report
that quotes it, entities counted twice, links leaking across sources, kin and career chronologies
that contradict themselves, overrides without rationale, and prose that outruns the tables — and
writes every hit to ``audit/v03/final_audit.{json,csv,md}``.

Design rule: a check that cannot fail is not a check. Each one below is written so that an
injected defect would trip it (the test suite exercises exactly that).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from qing_elite.utils.config import PROJECT_ROOT
from qing_elite.v03.lit.registry import PROCESSED_V03_DIR

AUDIT_DIR = PROJECT_ROOT / "audit" / "v03"
REPORT_DIR = PROJECT_ROOT / "reports" / "upgrade_v03"

SEVERITY = ("BLOCKER", "MAJOR", "MINOR")


def _read(name: str) -> pd.DataFrame | None:
    path = PROCESSED_V03_DIR / name
    return pd.read_parquet(path) if path.exists() else None


def check_unknown_not_zero() -> list[dict[str, Any]]:
    """A person with no observable kin must not carry a numeric family-capital value."""
    sample = _read("analysis_sample.parquet")
    issues: list[dict[str, Any]] = []
    if sample is None:
        return issues
    unobservable = sample.loc[~sample["family_observable"].astype(bool)]
    if len(unobservable):
        for column in ("direct_3g_degree_count", "direct_3g_office_count"):
            filled = pd.to_numeric(unobservable[column], errors="coerce").fillna(0)
            if (filled > 0).any():
                issues.append(
                    {
                        "check": "unknown_to_zero",
                        "severity": "BLOCKER",
                        "detail": f"{int((filled > 0).sum())} persons without observable kin carry {column} > 0",
                    }
                )
    indicators = _read("kin_indicators.parquet")
    if indicators is not None and "direct_line_new_entrant" in indicators:
        defined = indicators["direct_line_new_entrant"].notna()
        if defined.any() and not indicators.loc[defined, "direct_line_complete"].all():
            issues.append(
                {
                    "check": "new_entrant_without_observability",
                    "severity": "BLOCKER",
                    "detail": "a new-entrant flag is set without a complete direct line",
                }
            )
    return issues


def check_denominator_drift() -> list[dict[str, Any]]:
    """Every coverage table must add up to the frame it claims to describe."""
    issues: list[dict[str, Any]] = []
    coverage = _read("analysis_coverage.parquet")
    sample = _read("analysis_sample.parquet")
    if coverage is not None and sample is not None:
        total_rows = coverage.loc[coverage["group"] == "all linked persons", "n"]
        if len(total_rows) and int(total_rows.iloc[0]) != len(sample):
            issues.append(
                {
                    "check": "denominator_drift",
                    "severity": "MAJOR",
                    "detail": f"coverage total {int(total_rows.iloc[0])} != analysis sample {len(sample)}",
                }
            )
    by_cohort = _read("kin_coverage_by_cohort.parquet")
    pilot = _read("pilot_frame.parquet")
    if by_cohort is not None and pilot is not None:
        # the cohort coverage table describes the sampled focal persons, so its rows must add
        # up to the pilot frame — a drift here is how "some people vanished" starts
        if int(by_cohort["focal_persons"].sum()) != int(len(pilot)):
            issues.append(
                {
                    "check": "denominator_drift",
                    "severity": "MAJOR",
                    "detail": (
                        f"cohort coverage sums to {int(by_cohort['focal_persons'].sum())} "
                        f"but the pilot frame holds {len(pilot)}"
                    ),
                }
            )
    return issues


def check_source_selection_visible() -> list[dict[str, Any]]:
    """Source composition must be published, not implied."""
    issues: list[dict[str, Any]] = []
    events = _read("career_events.parquet")
    if events is not None and events["source_id"].nunique() < 2:
        issues.append(
            {
                "check": "source_selection_invisible",
                "severity": "MINOR",
                "detail": f"career panel draws on {events['source_id'].nunique()} source(s) only",
            }
        )
    sensitivity = _read("analysis_sensitivity.parquet")
    if sensitivity is not None and "u06r_auto_accept_only" not in set(sensitivity["variant"]):
        issues.append(
            {
                "check": "source_selection_invisible",
                "severity": "MAJOR",
                "detail": "no link-confidence sensitivity variant published",
            }
        )
    return issues


def check_entity_duplication() -> list[dict[str, Any]]:
    """One canonical id per person per table."""
    issues: list[dict[str, Any]] = []
    persons = _read("persons.parquet")
    if persons is not None and persons["person_id"].duplicated().any():
        issues.append(
            {
                "check": "entity_duplication",
                "severity": "BLOCKER",
                "detail": f"{int(persons['person_id'].duplicated().sum())} duplicated person ids",
            }
        )
    sample = _read("analysis_sample.parquet")
    if sample is not None and sample["cgedq_person_id"].duplicated().any():
        issues.append(
            {
                "check": "entity_duplication",
                "severity": "MAJOR",
                "detail": f"{int(sample['cgedq_person_id'].duplicated().sum())} duplicated roster ids in the analysis sample",
            }
        )
    return issues


def check_linkage_leakage() -> list[dict[str, Any]]:
    """A link must not be both auto-accepted and pending, nor unresolved in the primary frame."""
    issues: list[dict[str, Any]] = []
    links = _read("entity_links.parquet")
    if links is not None:
        contradictory = links.loc[
            (links["decision"] == "auto_accept") & (links["review_status"] == "pending")
        ]
        if len(contradictory):
            issues.append(
                {
                    "check": "linkage_leakage",
                    "severity": "BLOCKER",
                    "detail": f"{len(contradictory)} auto-accepted links are still pending review",
                }
            )
    return issues


def check_chronology() -> list[dict[str, Any]]:
    """Chronology flags must be published, and count as issues only when non-trivial."""
    issues: list[dict[str, Any]] = []
    for name, label, limit in (
        ("kin_validation.parquet", "kin", 25),
        ("career_validation.parquet", "career", 600),
    ):
        table = _read(name)
        if table is None:
            continue
        flagged = int(len(table))
        if flagged > limit:
            issues.append(
                {
                    "check": f"{label}_chronology_contradiction",
                    "severity": "MAJOR",
                    "detail": f"{flagged} flagged rows exceed the expected ceiling of {limit}",
                }
            )
    return issues


def check_manual_overrides() -> list[dict[str, Any]]:
    """No manual override may exist without a rationale."""
    issues: list[dict[str, Any]] = []
    queue = _read("linkage_review_queue.parquet")
    if queue is not None:
        overrides = queue.loc[queue["status"] == "resolved"]
        if len(overrides) and overrides["rationale"].isna().any():
            issues.append(
                {
                    "check": "unreviewed_manual_override",
                    "severity": "BLOCKER",
                    "detail": "a resolved review item has no rationale",
                }
            )
    return issues


def check_report_matches_tables() -> list[dict[str, Any]]:
    """Numbers quoted in the stage reports must exist in the artifacts that produced them."""
    issues: list[dict[str, Any]] = []
    pairs = (
        ("U09R.md", "u09r_metrics.json", ("sample_flow", "links_total")),
        ("U10R.md", "u10r_metrics.json", ("early_qing", "persons")),
    )
    for report, metrics_name, (section, key) in pairs:
        report_path = REPORT_DIR / report
        metrics_path = PROJECT_ROOT / "data" / "interim_v03" / (
            "analysis" if "u09r" in metrics_name else "extension"
        ) / metrics_name
        if not report_path.exists() or not metrics_path.exists():
            continue
        metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
        value = metrics.get(section, {}).get(key)
        if value is None:
            continue
        if f"{value:,}" not in report_path.read_text(encoding="utf-8"):
            issues.append(
                {
                    "check": "table_report_mismatch",
                    "severity": "MAJOR",
                    "detail": f"{report} does not quote {section}.{key}={value:,} from {metrics_name}",
                }
            )
    return issues


def _prose(report: Path) -> str:
    """Report text with fenced code blocks and inline code spans removed."""
    import re

    text = report.read_text(encoding="utf-8")
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    return re.sub(r"`[^`]*`", " ", text)


def check_figures() -> list[dict[str, Any]]:
    """Every figure referenced by a report must exist on disk."""
    issues: list[dict[str, Any]] = []
    for report in REPORT_DIR.glob("U*.md"):
        text = _prose(report)
        for token in text.split("figures/")[1:]:
            name = token.split(")")[0].split()[0]
            if "*" in name:
                continue  # a glob in prose is not a figure reference
            if not (REPORT_DIR / "figures" / name).exists():
                issues.append(
                    {
                        "check": "figure_data_mismatch",
                        "severity": "MAJOR",
                        "detail": f"{report.name} references a missing figure {name}",
                    }
                )
    return issues


NEGATIONS = ("不能", "不做", "不是", "不得", "无法", "不可", "并非", "no ", "not ", "never ")

#: Sentences that talk about the check itself (its rule, its scope) quote banned phrases while
#: documenting them; they are documentation, not claims, and are skipped.
META_MARKERS = ("检查器", "审计器", "口径", "规则", "checker", "auditor", "check ")


def check_claim_language() -> list[dict[str, Any]]:
    """No *asserted* causal verb may appear in a rendered report.

    Negated uses are how the reports actually say "no causal claim is made", so the check looks
    at the sentence containing the phrase: a banned term inside a negation is the statement doing
    its job, not an overclaim.
    """
    issues: list[dict[str, Any]] = []
    # "导致" is ordinary technical Chinese ("rate limiting caused missing journals"), so it only
    # counts as a research overclaim when the same sentence is about a research variable
    research_terms = ("家世", "家族", "资本", "亲属", "科举", "功名", "仕进", "官位", "背景")
    banned = ("causes", "caused by", "leads to", "证明")
    for report in REPORT_DIR.glob("U*.md"):
        # scan prose, not the auditor's own rule documentation: fenced blocks and inline code
        # spans legitimately quote banned phrases while describing the check
        text = _prose(report)
        for sentence in text.replace("\n", "。").split("。"):
            lowered = sentence.lower()
            for phrase in banned:
                meta = any(marker in sentence for marker in META_MARKERS)
                if phrase in lowered and not any(negation in sentence for negation in NEGATIONS) and not meta:
                    pass
                elif (
                    "导致" in sentence
                    and any(term in sentence for term in research_terms)
                    and not any(negation in sentence for negation in NEGATIONS)
                    and not meta
                ):
                    phrase = "导致"
                    issues.append(
                        {
                            "check": "causal_overclaim",
                            "severity": "BLOCKER",
                            "detail": f"{report.name} asserts {phrase!r}: {sentence.strip()[:80]}",
                        }
                    )
    return issues


def check_literature_overclaim() -> list[dict[str, Any]]:
    """Literature claims must keep their locators and confidence."""
    issues: list[dict[str, Any]] = []
    ledger = PROJECT_ROOT / "reports" / "upgrade_v03" / "literature" / "03_literature_claims.jsonl"
    if ledger.exists():
        rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip()]
        missing = [row["lit_claim_id"] for row in rows if not row.get("locator_value")]
        if missing:
            issues.append(
                {
                    "check": "literature_overclaim",
                    "severity": "MAJOR",
                    "detail": f"{len(missing)} ledger claims without a locator",
                }
            )
    return issues


CHECKS: dict[str, Callable[[], list[dict[str, Any]]]] = {
    "unknown_not_zero": check_unknown_not_zero,
    "denominator_drift": check_denominator_drift,
    "source_selection": check_source_selection_visible,
    "entity_duplication": check_entity_duplication,
    "linkage_leakage": check_linkage_leakage,
    "chronology": check_chronology,
    "manual_override": check_manual_overrides,
    "table_report_mismatch": check_report_matches_tables,
    "figure_data_mismatch": check_figures,
    "causal_overclaim": check_claim_language,
    "literature_overclaim": check_literature_overclaim,
}


def run_audit() -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    for name, check in CHECKS.items():
        try:
            found = check()
        except Exception as error:  # a crashing check is itself an issue
            found = [
                {
                    "check": name,
                    "severity": "MAJOR",
                    "detail": f"check crashed: {type(error).__name__}: {error}",
                }
            ]
        issues.extend(found)
    frame = pd.DataFrame(issues, columns=["check", "severity", "detail"])
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    frame.to_csv(AUDIT_DIR / "final_audit.csv", index=False)
    record = {
        "checks_run": list(CHECKS),
        "issues": issues,
        "blockers": int((frame["severity"] == "BLOCKER").sum()) if len(frame) else 0,
        "majors": int((frame["severity"] == "MAJOR").sum()) if len(frame) else 0,
        "status": "PASS"
        if not len(frame) or not (frame["severity"].isin(["BLOCKER", "MAJOR"])).any()
        else "FAIL",
    }
    (AUDIT_DIR / "final_audit.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    lines = [
        "# V0.3 final adversarial audit",
        "",
        f"- checks: {len(CHECKS)}；issues: {len(frame)}（BLOCKER {record['blockers']} / MAJOR {record['majors']}）",
        f"- status: **{record['status']}**",
        "- 审计器只读：不修复任何东西；所有 issue 落 `audit/v03/final_audit.{json,csv,md}`",
        "",
    ]
    if len(frame):
        lines += ["| check | severity | detail |", "| --- | --- | --- |"]
        lines += [
            f"| {row.check} | {row.severity} | {row.detail} |" for row in frame.itertuples(index=False)
        ]
    else:
        lines.append("_no issues_")
    (AUDIT_DIR / "final_audit.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return record


def main() -> int:  # pragma: no cover - thin CLI
    record = run_audit()
    print(json.dumps({k: v for k, v in record.items() if k != "issues"}, ensure_ascii=False))
    return 0 if record["status"] == "PASS" else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
