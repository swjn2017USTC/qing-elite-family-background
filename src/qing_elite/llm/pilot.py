"""P04 pilot driver: stratified sample, gold set, extraction, metrics.

    uv run python -m qing_elite.llm.pilot --dry-run     # cost plan only, no API call
    uv run python -m qing_elite.llm.pilot               # run the pilot

Calls go to the official DeepSeek API with the key from the environment. The pilot
is small on purpose: its job is to measure extraction quality against a gold set
before any batch enrichment is allowed.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import pandas as pd

from qing_elite.llm.extraction import (
    PROMPT_VERSION,
    SLOTS,
    schema_validity,
    ExtractionCall,
    append_audit,
    audit_record,
    call_cost_rmb,
    call_extraction,
    call_to_row,
    evidence_fidelity,
)
from qing_elite.llm.estimation import (
    PASSAGE_CACHE,
    TOKENS_PER_CJK_CHAR,
    family_window,
    fetch_passage,
)
from qing_elite.utils.config import OUTPUT_DIR, PROCESSED_DIR, load_offices

GOLD_ARM_TARGET = 22
TARGET_ARM_TARGET = 18
BIOGRAPHY_VOLUME_DIR = PASSAGE_CACHE / "volumes"
PILOT_DIR = Path("data/interim/pilot")
MANUAL_VALIDATION = Path("audit/manual_validation.csv")


@dataclass(frozen=True, slots=True)
class PilotCase:
    person_uid: str
    cbdb_personid: int | None
    name: str
    tier: str
    banner: str
    arm: str  # "gold" | "target"
    source_ids: str
    passage: str
    window: str
    has_cbdb_ancestor: bool
    same_name_cbdb_persons: int


# ------------------------------------------------------------------ selection


def load_biography_pool() -> dict[str, list[tuple[str, str]]]:
    """``name -> [(volume, biography_text)]`` from the cached 《清史稿》 volumes."""
    from qing_elite.llm.estimation import biography_spans

    pool: dict[str, list[tuple[str, str]]] = {}
    for path in sorted(BIOGRAPHY_VOLUME_DIR.glob("清史稿_卷*.txt")):
        volume = "清史稿/" + path.name.split("清史稿_")[1].replace(".txt", "")
        for name, body in biography_spans(path.read_text(encoding="utf-8")):
            pool.setdefault(name, []).append((volume, body))
    return pool


def select_cases(
    master: pd.DataFrame, family: pd.DataFrame, pool: Mapping[str, list[tuple[str, str]]]
) -> list[PilotCase]:
    """Build the stratified gold + target arms from the biography pool."""
    known = (
        family[family["ancestor_known"]]
        .groupby("person_uid")["ancestor_slot"]
        .size()
        .rename("n_known")
    )
    work = master.copy()
    work["name"] = work["c_name_chn"].astype(str)
    work["in_pool"] = work["name"].isin(pool.keys())
    work = work.merge(known, left_on="person_uid", right_index=True, how="left")
    work["n_known"] = work["n_known"].fillna(0).astype(int)
    work["same_name_cbdb_persons"] = work.groupby("name")["name"].transform("size")
    # Information content of the biography: does it speak about forebears at all?
    # The plan asks for both high- and low-information cases; low-information cases
    # are where a correct answer is an abstention.
    family_pattern = re.compile(r"(父|祖|曾祖|大父|王父)")
    work["family_kw_hits"] = [
        len(family_pattern.findall(pool[name][0][1])) if name in pool else 0
        for name in work["name"]
    ]

    cases: list[PilotCase] = []
    used: set[str] = set()

    def add(row: pd.Series, arm: str) -> None:
        name = str(row["name"])
        candidates = pool.get(name, [])
        if not candidates:
            return
        volume, body = candidates[0]
        cases.append(
            PilotCase(
                person_uid=str(row["person_uid"]),
                cbdb_personid=int(row["cbdb_personid"]) if pd.notna(row["cbdb_personid"]) else None,
                name=name,
                tier=str(row["highest_tier"]),
                banner=str(row["banner_effective"]),
                arm=arm,
                source_ids=f"{volume}#{name}",
                passage=body,
                window=family_window(body),
                has_cbdb_ancestor=bool(row["n_known"]),
                same_name_cbdb_persons=int(row["same_name_cbdb_persons"]),
            )
        )
        used.add(str(row["person_uid"]))

    # Gold arm: persons whose ancestors CBDB already documents (independent truth),
    # preferring the most documented cases and covering all tiers.
    gold_pool = work[(work["n_known"] > 0) & work["in_pool"] & ~work["person_uid"].isin(used)]
    # Highest information first, then most documented: measurable accuracy needs the
    # text to actually state something.
    gold_pool = gold_pool.sort_values(["family_kw_hits", "n_known"], ascending=[False, False])
    per_tier: dict[str, int] = {}
    for _, row in gold_pool.iterrows():
        tier = str(row["highest_tier"])
        if per_tier.get(tier, 0) >= 6:
            continue
        per_tier[tier] = per_tier.get(tier, 0) + 1
        add(row, "gold")
        if len([c for c in cases if c.arm == "gold"]) >= GOLD_ARM_TARGET:
            break

    # Target arm: the actual enrichment population (no CBDB ancestors), tier-spread,
    # with bannermen deliberately included.
    target_pool = work[(work["n_known"] == 0) & work["in_pool"] & ~work["person_uid"].isin(used)]
    banner_pool = target_pool[~target_pool["banner_effective"].isin(["unknown"])]
    for _, row in banner_pool.sort_values("family_kw_hits", ascending=False).head(6).iterrows():
        add(row, "target")
    for _, row in target_pool.sort_values(
        ["family_kw_hits", "highest_tier"], ascending=[False, True]
    ).iterrows():
        if len([c for c in cases if c.arm == "target"]) >= TARGET_ARM_TARGET:
            break
        if str(row["person_uid"]) in used:
            continue
        add(row, "target")
    return cases


def extend_gold_arm(
    master: pd.DataFrame, family: pd.DataFrame, used: set[str], need: int
) -> tuple[list[PilotCase], list[dict[str, Any]]]:
    """Top up the gold arm with by-name 《清史稿》 lookups.

    The cached volumes only cover part of the study window, so the gold arm is
    completed by looking the remaining well-documented persons up by name. Lookups
    are paced and every attempt is logged.
    """
    known = (
        family[family["ancestor_known"]]
        .groupby("person_uid")["ancestor_slot"]
        .size()
        .rename("n_known")
    )
    ranked = (
        master[master["cbdb_personid"].notna()]
        .merge(known, left_on="person_uid", right_index=True, how="left")
        .sort_values("n_known", ascending=False)
    )
    added: list[PilotCase] = []
    log: list[dict[str, Any]] = []
    attempts = 0
    for _, row in ranked.iterrows():
        if len(added) >= need or attempts >= 15:
            break
        uid = str(row["person_uid"])
        if uid in used or pd.isna(row["n_known"]) or row["n_known"] <= 0:
            continue
        name = str(row["c_name_chn"])
        attempts += 1
        passage = fetch_passage(name, sleep_seconds=2.5)
        log.append({"name": name, "found": passage.found, "volume": passage.volume_title, "error": passage.error})
        if not passage.found:
            continue
        case = PilotCase(
            person_uid=uid,
            cbdb_personid=int(row["cbdb_personid"]),
            name=name,
            tier=str(row["highest_tier"]),
            banner=str(row["banner_effective"]),
            arm="gold",
            source_ids=f"{passage.volume_title}#{name}",
            passage=passage.text,
            window=family_window(passage.text),
            has_cbdb_ancestor=True,
            same_name_cbdb_persons=int((master["c_name_chn"] == name).sum()),
        )
        added.append(case)
        used.add(uid)
    return added, log


# ----------------------------------------------------------------------- gold


def _mentions(passage: str, full_name: object, given_name: object, alt_names: object) -> bool:
    """Does the passage refer to this person?

    《清史稿》 often names an ancestor by given name or title only ("大學士英次子"),
    so a full-name substring test would understate what the text actually states.
    """
    # 清史稿 writes ancestors by given name alone ("大學士英次子"), and a given name
    # can be a single character, so the length floor would understate the text.
    candidates = [value for value in (full_name, given_name) if isinstance(value, str) and len(value) >= 1]
    if isinstance(alt_names, str):
        candidates.extend(part for part in alt_names.split("|") if len(part) >= 2)
    return any(candidate in passage for candidate in candidates)


def load_gold_offices(ancestor_ids: Iterable[int]) -> dict[int, str]:
    """Every office CBDB records for an ancestor (the passage may name any of them).

    ``family_structured.ancestor_office_sample`` is capped at eight names, which made
    a correct extraction look like a mismatch (張英's 大學士 was outside the sample).
    """
    import sqlite3

    from qing_elite.utils.config import CBDB_SQLITE

    ids = sorted({int(value) for value in ancestor_ids})
    if not ids:
        return {}
    conn = sqlite3.connect(CBDB_SQLITE)
    try:
        rows = conn.execute(
            f"""
            SELECT p.c_personid, o.c_office_chn
            FROM POSTED_TO_OFFICE_DATA p JOIN OFFICE_CODES o ON o.c_office_id = p.c_office_id
            WHERE p.c_personid IN ({",".join(str(i) for i in ids)})
            """
        ).fetchall()
    finally:
        conn.close()
    grouped: dict[int, set[str]] = {}
    for personid, office in rows:
        grouped.setdefault(int(personid), set()).add(str(office))
    return {personid: " / ".join(sorted(offices)) for personid, offices in grouped.items()}


def build_gold(family: pd.DataFrame, cases: Sequence[PilotCase]) -> pd.DataFrame:
    """Gold rows: CBDB structured ancestors (independent of the passage)."""
    subset = family[family["person_uid"].isin([case.person_uid for case in cases if case.arm == "gold"])]
    offices = load_gold_offices(subset["ancestor_personid"].dropna().tolist())
    rows: list[dict[str, Any]] = []
    for case in cases:
        case_rows = subset[subset["person_uid"] == case.person_uid]
        record: dict[str, Any] = {
            "person_uid": case.person_uid,
            "name": case.name,
            "arm": case.arm,
            "tier": case.tier,
            "gold_source": "CBDB_KIN_DATA" if case.arm == "gold" else "none",
        }
        for slot in SLOTS:
            slot_row = case_rows[case_rows["ancestor_slot"] == slot]
            known = bool(len(slot_row) and slot_row.iloc[0]["ancestor_known"])
            name = slot_row.iloc[0]["ancestor_name"] if known else None
            degree = slot_row.iloc[0].get("ancestor_degree") if known else None
            office_sample = slot_row.iloc[0].get("ancestor_office_sample") if known else None
            record[f"gold_{slot}_name"] = name
            record[f"gold_{slot}_degree"] = degree
            ancestor_id = slot_row.iloc[0].get("ancestor_personid") if known else None
            full_offices = offices.get(int(ancestor_id)) if pd.notna(ancestor_id) else None
            record[f"gold_{slot}_office"] = full_offices or (
                str(office_sample).replace("|", " / ") if isinstance(office_sample, str) and office_sample else None
            )
            given = slot_row.iloc[0].get("ancestor_mingzi") if known else None
            alts = slot_row.iloc[0].get("ancestor_alt_names") if known else None
            record[f"gold_{slot}_given_name"] = given
            record[f"gold_{slot}_in_passage"] = bool(known) and _mentions(case.passage, name, given, alts)
        rows.append(record)
    return pd.DataFrame.from_records(rows)


# -------------------------------------------------------------------- metrics


def _normalize(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _names_match(
    extracted: str | None, gold: str | None, gold_given: str | None = None, gold_alt: str | None = None
) -> bool:
    if not extracted or not gold:
        return False
    forms = {gold}
    if gold_given:
        forms.add(gold_given)
    if gold_alt:
        forms.update(part for part in gold_alt.split("|") if part)
    return any(extracted == form or extracted in form or form in extracted for form in forms)


def case_metrics(case: PilotCase, gold_row: Mapping[str, Any], call: ExtractionCall) -> dict[str, Any]:
    """Per-case accuracy / evidence / hallucination accounting."""
    output = call.output or {}
    fidelity = evidence_fidelity(call.output, case.window)
    schema_ok, schema_issue = schema_validity(call.output)
    row: dict[str, Any] = {
        "person_uid": case.person_uid,
        "name": case.name,
        "arm": case.arm,
        "tier": case.tier,
        "banner": case.banner,
        "source_ids": case.source_ids,
        "passage_chars": len(case.passage),
        "window_chars": len(case.window),
        "json_valid": bool(schema_ok),
        "schema_issue": schema_issue,
        "status": call.status,
        "input_tokens": call.input_tokens,
        "output_tokens": call.output_tokens,
        "thinking_mode": call.thinking_mode,
        "latency_ms": call.latency_ms,
        "cost_rmb": call_cost_rmb(call),
        "non_null_slots": fidelity["non_null_slots"],
        "slots_with_evidence": fidelity["with_evidence"],
        "slots_evidence_verbatim": fidelity["evidence_verbatim"],
        "unsupported_assertions": fidelity["unsupported_assertions"],
        "confidence": output.get("confidence"),
        "insufficient_evidence": output.get("insufficient_evidence"),
        "ambiguities": json.dumps(output.get("ambiguities") or [], ensure_ascii=False),
        "same_name_cbdb_persons": case.same_name_cbdb_persons,
        "family_kw_hits": len(re.findall(r"(父|祖|曾祖|大父|王父)", case.passage)),
    }
    for slot in SLOTS:
        block = output.get(slot) or {}
        extracted_name = _normalize(block.get("name"))
        gold_name = _normalize(gold_row.get(f"gold_{slot}_name"))
        in_passage = bool(gold_row.get(f"gold_{slot}_in_passage"))
        row[f"{slot}_extracted_name"] = extracted_name
        row[f"{slot}_gold_name"] = gold_name
        row[f"{slot}_gold_in_passage"] = in_passage
        row[f"{slot}_extracted_degree"] = _normalize(block.get("degree"))
        row[f"{slot}_gold_degree"] = _normalize(gold_row.get(f"gold_{slot}_degree"))
        row[f"{slot}_extracted_office"] = _normalize(block.get("office"))
        row[f"{slot}_gold_office"] = _normalize(gold_row.get(f"gold_{slot}_office"))
        row[f"{slot}_evidence"] = _normalize(block.get("evidence"))
        row[f"{slot}_name_form"] = (
            "given_name_only"
            if extracted_name and gold_name and extracted_name != gold_name and extracted_name in gold_name
            else ("full" if extracted_name else None)
        )
        if case.arm == "gold":
            row[f"{slot}_name_verdict"] = _name_verdict(
                extracted_name,
                gold_name,
                in_passage,
                _normalize(gold_row.get(f"gold_{slot}_given_name")),
            )
            row[f"{slot}_degree_verdict"] = _value_verdict(
                row[f"{slot}_extracted_degree"], row[f"{slot}_gold_degree"]
            )
            row[f"{slot}_office_verdict"] = _office_verdict(
                row[f"{slot}_extracted_office"], row[f"{slot}_gold_office"]
            )
    return row


def _name_verdict(
    extracted: str | None, gold: str | None, gold_in_passage: bool, gold_given: str | None = None
) -> str:
    if gold and gold_in_passage:
        return (
            "match"
            if _names_match(extracted, gold, gold_given)
            else ("abstain" if not extracted else "wrong_name")
        )
    if gold and not gold_in_passage:
        return "asserted_without_passage_evidence" if extracted else "correct_abstention"
    if not gold:
        return "no_gold_to_compare" if extracted else "correct_abstention"
    return "n/a"


def _value_verdict(extracted: str | None, gold: str | None) -> str:
    if gold and extracted:
        return "match" if extracted in gold or gold in extracted else "mismatch"
    if gold and not extracted:
        return "abstained"
    if not gold and extracted:
        return "no_gold_to_compare"
    return "n/a"


def _office_verdict(extracted: str | None, gold: str | None) -> str:
    """A match means the office the model read is one of the ancestor's offices.

    CBDB records several offices per person and the passage may name any of them, so
    gold is the whole recorded set (``A / B / C``) rather than one arbitrary entry.
    """
    if not extracted:
        return "abstained" if gold else "n/a"
    if not gold:
        return "no_gold_to_compare"
    if extracted in gold:
        return "match"
    extracted_tokens = set(re.findall(r"[\u4e00-\u9fff]{2,}", extracted))
    gold_tokens = set(re.findall(r"[\u4e00-\u9fff]{2,}", gold))
    return "match" if extracted_tokens & gold_tokens else "mismatch"


def summarize(results: pd.DataFrame) -> pd.DataFrame:
    """Aggregate the six required metrics."""
    gold = results[results["arm"] == "gold"]
    all_cases = results
    rows: list[dict[str, Any]] = []

    def share(numerator: int, denominator: int) -> float:
        return round(100 * numerator / denominator, 2) if denominator else 0.0

    json_valid = int(results["json_valid"].sum())
    rows.append({"metric": "json_validity_pct", "value": share(json_valid, len(results)), "n": len(results)})

    non_null = int(results["non_null_slots"].sum())
    verbatim = int(results["slots_evidence_verbatim"].sum())
    unsupported = int(results["unsupported_assertions"].sum())
    rows.append({"metric": "evidence_fidelity_pct", "value": share(verbatim, non_null), "n": non_null})
    rows.append({"metric": "evidence_unsupported_assertions", "value": unsupported, "n": non_null})

    father_cases = gold[gold["father_gold_in_passage"]]
    father_match = int((father_cases["father_name_verdict"] == "match").sum())
    rows.append(
        {"metric": "father_identity_accuracy_pct", "value": share(father_match, len(father_cases)), "n": len(father_cases)}
    )
    father_wrong = int((father_cases["father_name_verdict"] == "wrong_name").sum())
    rows.append({"metric": "father_wrong_name_cases", "value": father_wrong, "n": len(father_cases)})
    no_evidence = gold[~gold["father_gold_in_passage"]]
    correct_abstain = int((no_evidence["father_name_verdict"] == "correct_abstention").sum())
    asserted = int((no_evidence["father_name_verdict"] == "asserted_without_passage_evidence").sum())
    rows.append(
        {"metric": "father_abstention_pct", "value": share(correct_abstain, len(no_evidence)), "n": len(no_evidence)}
    )
    rows.append(
        {
            "metric": "hallucination_rate_pct",
            "value": share(unsupported + asserted, max(non_null, 1) + asserted),
            "n": non_null + asserted,
        }
    )
    # Assertions CBDB cannot contradict are candidate *new* facts, not errors: they
    # still need a human check that the quoted evidence really supports them.
    new_facts = int((gold["father_office_verdict"] == "no_gold_to_compare").sum()) + int(
        (gold["grandfather_office_verdict"] == "no_gold_to_compare").sum()
    )
    rows.append({"metric": "office_no_gold_to_compare_cases", "value": new_facts, "n": len(gold)})

    for field_name in ("degree", "office"):
        verdicts = pd.concat([gold[f"father_{field_name}_verdict"], gold[f"grandfather_{field_name}_verdict"]])
        match = int((verdicts == "match").sum())
        mismatch = int((verdicts == "mismatch").sum())
        abstain = int((verdicts == "abstained").sum())
        gold_present = int((verdicts != "n/a").sum())
        rows.append(
            {
                "metric": f"{field_name}_accuracy_pct",
                "value": share(match, match + mismatch),
                "n": match + mismatch,
            }
        )
        rows.append(
            {
                "metric": f"{field_name}_coverage_note",
                "value": f"gold_present={gold_present}, match={match}, mismatch={mismatch}, abstained={abstain}",
                "n": gold_present,
            }
        )
    rows.append(
        {
            "metric": "cases",
            "value": f"total={len(all_cases)}, gold_arm={len(gold)}, target_arm={len(all_cases) - len(gold)}",
            "n": len(all_cases),
        }
    )
    return pd.DataFrame.from_records(rows)


# ----------------------------------------------------------------------- main


def plan_costs(cases: Sequence[PilotCase], escalations: int = 0) -> pd.DataFrame:
    """Print-before-run token and RMB plan (RULES.md rule 15)."""
    windows = [len(case.window) for case in cases]
    mean_window = sum(windows) / len(windows) if windows else 0
    input_tokens = (mean_window * TOKENS_PER_CJK_CHAR + 420) * len(cases)
    output_tokens = 246 * len(cases)
    esc_input = input_tokens / max(len(cases), 1) * escalations
    esc_output = output_tokens / max(len(cases), 1) * escalations * 1.5
    total_in = input_tokens + esc_input
    total_out = output_tokens + esc_output
    return pd.DataFrame(
        [
            {
                "cases": len(cases),
                "planned_escalations": escalations,
                "mean_window_chars": round(mean_window, 1),
                "estimated_input_tokens": round(total_in),
                "estimated_output_tokens": round(total_out),
                "estimated_rmb_off_peak": round(total_in / 1e6 * 1.0 + total_out / 1e6 * 4.0, 4),
                "estimated_rmb_peak": round(total_in / 1e6 * 2.0 + total_out / 1e6 * 8.0, 4),
            }
        ]
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="P04 DeepSeek pilot")
    parser.add_argument("--dry-run", action="store_true", help="plan only; no API call")
    parser.add_argument("--limit", type=int, default=0, help="cap the number of cases (debug)")
    args = parser.parse_args(argv)

    load_offices()  # fail fast if the config is broken
    PILOT_DIR.mkdir(parents=True, exist_ok=True)
    master = pd.read_parquet(PROCESSED_DIR / "officials_master.parquet")
    family = pd.read_parquet(PROCESSED_DIR / "family_structured.parquet")
    pool = load_biography_pool()
    cases = select_cases(master, family, pool)
    gold_selected = [case for case in cases if case.arm == "gold"]
    if len(gold_selected) < 20 and not args.dry_run:
        used = {case.person_uid for case in cases}
        extra, lookup_log = extend_gold_arm(master, family, used, 20 - len(gold_selected))
        if lookup_log:
            pd.DataFrame.from_records(lookup_log).to_csv(PILOT_DIR / "p04_lookups.csv", index=False)
        cases.extend(extra)
    if args.limit:
        cases = cases[: args.limit]
    cases = [case for case in cases if case.passage]
    gold = build_gold(family, cases)
    plan = plan_costs(cases, escalations=min(len(cases) // 5, 8))

    PILOT_DIR.mkdir(parents=True, exist_ok=True)
    plan.to_csv(PILOT_DIR / "p04_cost_plan.csv", index=False)
    print("P04 pilot plan (before any call):")
    print(plan.to_string(index=False))
    if args.dry_run:
        return 0

    results, calls = run_pilot(cases, gold)
    summary = summarize(results)
    (OUTPUT_DIR / "tables").mkdir(parents=True, exist_ok=True)
    summary.to_csv(OUTPUT_DIR / "tables" / "p04_metrics.csv", index=False)
    results.to_csv(OUTPUT_DIR / "tables" / "p04_results.csv", index=False)
    calls.to_csv(OUTPUT_DIR / "tables" / "p04_calls.csv", index=False)
    write_manual_validation(results, gold, cases)
    total_cost = float(results["cost_rmb"].sum())
    print(f"cases={len(results)} gold_arm={int((results['arm'] == 'gold').sum())} "
          f"json_valid={int(results['json_valid'].sum())} cost={total_cost:.4f} RMB")
    print(summary.to_string(index=False))
    return 0


def run_pilot(
    cases: Sequence[PilotCase], gold: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Execute the pilot calls and return (per-case results, call table)."""
    gold_by_person = {row["person_uid"]: row for row in gold.to_dict("records")}
    results: list[dict[str, Any]] = []
    call_rows: list[dict[str, Any]] = []
    audits: list[dict[str, Any]] = []

    for index, case in enumerate(cases, start=1):
        call_id = f"p04-{index:03d}-{case.person_uid.replace(':', '_')}"
        call = call_extraction(
            call_id=call_id,
            person_uid=case.person_uid,
            person_name=case.name,
            passage=case.window,
            source_ids=case.source_ids,
        )
        audits.append(
            audit_record(call, person_id=case.person_uid, source_ids=case.source_ids, task_type="family_extraction")
        )
        call_rows.append(call_to_row(call))
        gold_row = gold_by_person.get(case.person_uid, {})
        row = case_metrics(case, gold_row, call)

        # Escalation: only when the rule-based pass cannot settle the case
        # (same-name CBDB conflict) or the model itself flags ambiguity.
        needs_escalation = bool(case.same_name_cbdb_persons > 1) or bool(
            (call.output or {}).get("ambiguities")
        )
        if needs_escalation and call.status == "ok":
            escalated = call_extraction(
                call_id=f"{call_id}-esc",
                person_uid=case.person_uid,
                person_name=case.name,
                passage=case.window,
                source_ids=case.source_ids,
                escalation=True,
                escalation_of=call_id,
            )
            audits.append(
                audit_record(
                    escalated,
                    person_id=case.person_uid,
                    source_ids=case.source_ids,
                    task_type="family_extraction_conflict",
                )
            )
            call_rows.append(call_to_row(escalated))
            row["escalated"] = True
            row["escalation_json_valid"] = schema_validity(escalated.output)[0]
            row["escalation_cost_rmb"] = call_cost_rmb(escalated)
            row["cost_rmb"] = row["cost_rmb"] + row["escalation_cost_rmb"]
            if escalated.status == "ok":
                row["escalated_output"] = json.dumps(escalated.output, ensure_ascii=False)
        else:
            row["escalated"] = False
        results.append(row)

    append_audit(audits)
    return pd.DataFrame.from_records(results), pd.DataFrame.from_records(call_rows)


def write_manual_validation(results: pd.DataFrame, gold: pd.DataFrame, cases: Sequence[PilotCase]) -> None:
    """Gold sheet for human review: gold vs extracted, with the evidence quoted."""
    gold_by_person = {row["person_uid"]: row for row in gold.to_dict("records")}
    passages = {case.person_uid: case.window for case in cases}
    rows: list[dict[str, Any]] = []
    for record in results.to_dict("records"):
        gold_row = gold_by_person.get(record["person_uid"], {})
        entry: dict[str, Any] = {
            "person_uid": record["person_uid"],
            "name": record["name"],
            "arm": record["arm"],
            "tier": record["tier"],
            "banner": record["banner"],
            "source_ids": record["source_ids"],
            "gold_source": gold_row.get("gold_source", "none"),
            "human_review_status": "pending",
        }
        for slot in SLOTS:
            entry[f"{slot}_gold_name"] = gold_row.get(f"gold_{slot}_name")
            entry[f"{slot}_llm_name"] = record.get(f"{slot}_extracted_name")
            entry[f"{slot}_llm_degree"] = record.get(f"{slot}_extracted_degree")
            entry[f"{slot}_llm_office"] = record.get(f"{slot}_extracted_office")
            entry[f"{slot}_llm_evidence"] = record.get(f"{slot}_evidence")
            entry[f"{slot}_verdict"] = record.get(f"{slot}_name_verdict")
        entry["passage_excerpt"] = str(passages.get(record["person_uid"], ""))[:600]
        rows.append(entry)
    frame = pd.DataFrame.from_records(rows)
    MANUAL_VALIDATION.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(MANUAL_VALIDATION, index=False)


if __name__ == "__main__":
    raise SystemExit(main())
