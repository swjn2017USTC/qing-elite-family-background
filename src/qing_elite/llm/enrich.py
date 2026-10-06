"""P05 driver: formal DeepSeek enrichment of the passage-backed subset.

    uv run python -m qing_elite.llm.enrich --plan          # eligibility + budget plan
    uv run python -m qing_elite.llm.enrich                 # run (resumable, checkpointed)

Only persons that are both *structured missing* and *backed by a real source passage*
are processed. A slot without passage support stays ``unknown``: nothing is inferred.

Guards: concurrency 6 (configurable), checkpoint every 50 persons, resume from disk,
soft budget 20 RMB / hard budget 30 RMB (hard stop issues no further requests).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import pandas as pd

from qing_elite.llm.extraction import (
    SLOTS,
    append_audit,
    audit_record,
    call_cost_rmb,
    call_extraction,
    call_to_row,
    evidence_matches_passage,
    schema_validity,
)
from qing_elite.llm.estimation import family_window, strip_wiki_markup
from qing_elite.llm.harvest import HARVEST_INDEX
from qing_elite.utils.config import CBDB_SQLITE, OUTPUT_DIR, PROCESSED_DIR

ENRICH_DIR = Path("data/interim/enrich")
DONE_FILE = ENRICH_DIR / "done.jsonl"
FAILED_FILE = ENRICH_DIR / "failed.jsonl"
ENRICHED_PARQUET = PROCESSED_DIR / "family_enriched.parquet"
COST_REPORT = OUTPUT_DIR / "tables" / "cost_report.csv"
FAILED_CASES = OUTPUT_DIR / "tables" / "failed_cases.csv"

SOFT_BUDGET_RMB = 20.0
HARD_BUDGET_RMB = 30.0
CHECKPOINT_EVERY = 50
DEFAULT_CONCURRENCY = 6


def load_eligible() -> pd.DataFrame:
    """Persons with a structured gap *and* a harvested 《清史稿》 biography."""
    family = pd.read_parquet(PROCESSED_DIR / "family_structured.parquet")
    master = pd.read_parquet(PROCESSED_DIR / "officials_master.parquet")
    index = pd.read_parquet(HARVEST_INDEX)
    gaps = (
        family[family["llm_needed"]]
        .groupby("person_uid")["ancestor_slot"]
        .size()
        .rename("n_missing_slots")
        .reset_index()
    )
    eligible = gaps.merge(
        master[["person_uid", "c_name_chn", "highest_tier", "banner_effective", "cbdb_personid"]],
        on="person_uid",
        how="inner",
    ).merge(
        index[["person_uid", "passage", "source_ids", "passage_chars", "volume"]],
        on="person_uid",
        how="inner",
    )
    return eligible.sort_values(["highest_tier", "person_uid"]).reset_index(drop=True)


def same_name_cbdb_counts(names: Iterable[str]) -> dict[str, int]:
    """How many CBDB Qing persons share each name (drives the escalation decision)."""
    import sqlite3

    wanted = sorted({str(name) for name in names if isinstance(name, str) and name})
    if not wanted:
        return {}
    conn = sqlite3.connect(CBDB_SQLITE)
    try:
        conn.execute("CREATE TEMP TABLE wanted(name TEXT PRIMARY KEY)")
        conn.executemany("INSERT OR IGNORE INTO wanted(name) VALUES (?)", [(n,) for n in wanted])
        rows = conn.execute(
            """
            SELECT b.c_name_chn, count(*) FROM BIOG_MAIN b
            JOIN wanted w ON w.name = b.c_name_chn
            WHERE b.c_dy = 20 GROUP BY 1
            """
        ).fetchall()
    finally:
        conn.close()
    return {str(name): int(count) for name, count in rows}


def enrich_one(
    row: Mapping[str, Any], *, escalation_candidates: int, prompt_window: str
) -> dict[str, Any]:
    """Run the ordinary call, plus the conflict-resolution call when warranted."""
    person_uid = str(row["person_uid"])
    call_id = f"p05-{person_uid.replace(':', '_')}"
    call = call_extraction(
        call_id=call_id,
        person_uid=person_uid,
        person_name=str(row["c_name_chn"]),
        passage=prompt_window,
        source_ids=str(row["source_ids"]),
    )
    records = [audit_record(call, person_id=person_uid, source_ids=str(row["source_ids"]), task_type="family_enrichment")]
    calls = [call]
    escalated: dict[str, Any] | None = None
    ambiguities = (call.output or {}).get("ambiguities") if call.output else None
    if call.status == "ok" and (escalation_candidates >= 2 or ambiguities):
        escalated_call = call_extraction(
            call_id=f"{call_id}-esc",
            person_uid=person_uid,
            person_name=str(row["c_name_chn"]),
            passage=prompt_window,
            source_ids=str(row["source_ids"]),
            escalation=True,
            escalation_of=call_id,
        )
        records.append(
            audit_record(
                escalated_call,
                person_id=person_uid,
                source_ids=str(row["source_ids"]),
                task_type="family_enrichment_conflict",
            )
        )
        calls.append(escalated_call)
        escalated = call_to_row(escalated_call)
    return {
        "person_uid": person_uid,
        "name": str(row["c_name_chn"]),
        "tier": str(row["highest_tier"]),
        "banner": str(row["banner_effective"]),
        "source_ids": str(row["source_ids"]),
        "passage_chars": int(row.get("passage_chars") or len(prompt_window)),
        "window_chars": len(prompt_window),
        "prompt_window": prompt_window,
        "call": call_to_row(call),
        "escalated": escalated,
        "audit": records,
        "cost_rmb": round(sum(call_cost_rmb(item) for item in calls), 6),
        "escalation_candidates": escalation_candidates,
    }


def run(
    eligible: pd.DataFrame,
    *,
    concurrency: int,
    limit: int | None = None,
    checkpoint_every: int = CHECKPOINT_EVERY,
) -> dict[str, Any]:
    """Enrich with a rolling submission window so the hard budget can stop the run."""
    ENRICH_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "tables").mkdir(parents=True, exist_ok=True)
    done = _load_done()
    already = {record["person_uid"] for record in done}
    pending = eligible[~eligible["person_uid"].isin(already)]
    if limit:
        pending = pending.head(limit)
    queue = pending.to_dict("records")
    print(f"eligible={len(eligible):,} already_done={len(already):,} pending={len(queue):,}")

    name_counts = same_name_cbdb_counts(pending["c_name_chn"].tolist())
    spent = sum(float(record.get("cost_rmb") or 0) for record in done)
    completed = 0
    new_records: list[dict[str, Any]] = []
    audit_buffer: list[dict[str, Any]] = []
    hard_stop = spent >= HARD_BUDGET_RMB
    soft_warned = spent >= SOFT_BUDGET_RMB
    skipped_for_budget = 0

    def task(row: Mapping[str, Any]) -> dict[str, Any]:
        window = family_window(strip_wiki_markup(str(row["passage"])))
        return enrich_one(
            row,
            escalation_candidates=name_counts.get(str(row["c_name_chn"]), 0),
            prompt_window=window,
        )

    window_size = max(concurrency * 3, concurrency)
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        inflight: dict[Any, Mapping[str, Any]] = {}
        index = 0

        def submit_next() -> bool:
            nonlocal index
            if hard_stop or index >= len(queue):
                return False
            inflight[pool.submit(task, queue[index])] = queue[index]
            index += 1
            return True

        for _ in range(window_size):
            submit_next()
        while inflight:
            finished, _ = wait(list(inflight), return_when=FIRST_COMPLETED)
            for future in finished:
                inflight.pop(future, None)
                record = future.result()
                spent += record["cost_rmb"]
                new_records.append(record)
                audit_buffer.extend(record["audit"])
                completed += 1
                if completed % checkpoint_every == 0:
                    _flush(done, new_records, audit_buffer)
                    print(
                        f"checkpoint: {completed} done this run, spent {spent:.4f} RMB",
                        flush=True,
                    )
                    new_records, audit_buffer = [], []
                if spent >= HARD_BUDGET_RMB:
                    hard_stop = True
                elif spent >= SOFT_BUDGET_RMB and not soft_warned:
                    soft_warned = True
                    print(
                        f"WARNING: soft budget reached ({spent:.4f} RMB) — no new batches started",
                        flush=True,
                    )
            if not hard_stop:
                submit_next()
    _flush(done, new_records, audit_buffer)
    skipped_for_budget = len(queue) - index
    if hard_stop and skipped_for_budget:
        print(f"HARD BUDGET: stopped with {skipped_for_budget} persons unprocessed", flush=True)
    return {
        "completed_this_run": completed,
        "spent_rmb_total": round(spent, 4),
        "hard_stop": hard_stop,
        "soft_budget_reached": soft_warned or spent >= SOFT_BUDGET_RMB,
        "persons_skipped_for_budget": skipped_for_budget,
    }


def _load_done() -> list[dict[str, Any]]:
    if not DONE_FILE.exists():
        return []
    records = []
    with DONE_FILE.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def _flush(done: list[dict[str, Any]], records: list[dict[str, Any]], audit: list[dict[str, Any]]) -> None:
    if records:
        with DONE_FILE.open("a", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        done.extend(records)
    if audit:
        append_audit(audit)


def build_outputs(
    records: list[dict[str, Any]], structured_frame: pd.DataFrame | None = None
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """family_enriched.parquet + cost_report.csv + failed_cases.csv."""
    family = (
        structured_frame
        if structured_frame is not None
        else pd.read_parquet(PROCESSED_DIR / "family_structured.parquet")
    )
    structured = {
        (row["person_uid"], row["ancestor_slot"]): row
        for row in family.to_dict("records")
    }
    rows: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for record in records:
        call = record["call"]
        output = json.loads(call["output_json"]) if call.get("output_json") else None
        valid, issue = schema_validity(output)
        window = record["prompt_window"]
        if not valid:
            failures.append(
                {
                    "person_uid": record["person_uid"],
                    "name": record["name"],
                    "status": call["status"],
                    "reason": f"schema:{issue}",
                    "source_ids": record["source_ids"],
                }
            )
        for slot in SLOTS:
            structured_row = structured.get((record["person_uid"], slot), {})
            block = (output or {}).get(slot) or {}
            llm_name = _text(block.get("name"))
            llm_degree = _text(block.get("degree"))
            llm_office = _text(block.get("office"))
            evidence = _text(block.get("evidence"))
            evidence_ok = bool(evidence) and evidence_matches_passage(evidence, window)
            structured_known = bool(structured_row.get("ancestor_known"))
            # A slot is accepted from the LLM only with verbatim passage support;
            # anything else stays unknown (没有史料就是 unknown).
            asserted = bool(llm_name or llm_degree or llm_office)
            accepted = asserted and evidence_ok
            if asserted and not evidence_ok:
                failures.append(
                    {
                        "person_uid": record["person_uid"],
                        "name": record["name"],
                        "status": "assertion_without_verbatim_evidence",
                        "reason": f"{slot}: evidence not verbatim in the prompt window",
                        "source_ids": record["source_ids"],
                    }
                )
            rows.append(
                {
                    "person_uid": record["person_uid"],
                    "name": record["name"],
                    "tier": record["tier"],
                    "banner": record["banner"],
                    "ancestor_slot": slot,
                    "structured_known": structured_known,
                    "structured_name": structured_row.get("ancestor_name"),
                    "structured_degree": structured_row.get("ancestor_degree"),
                    "structured_evidence_source": structured_row.get("source_title"),
                    "llm_name": llm_name,
                    "llm_degree": llm_degree,
                    "llm_office": llm_office,
                    "llm_evidence": evidence,
                    "llm_evidence_verbatim": evidence_ok,
                    "llm_confidence": (output or {}).get("confidence"),
                    "llm_insufficient_evidence": (output or {}).get("insufficient_evidence"),
                    "schema_valid": valid,
                    "final_name": structured_row.get("ancestor_name") if structured_known else (llm_name if accepted else None),
                    "final_degree": structured_row.get("ancestor_degree") if structured_known else (llm_degree if accepted else None),
                    "final_office": structured_row.get("ancestor_office_sample") if structured_known else (llm_office if accepted else None),
                    "final_source": "cbdb_structured" if structured_known else ("llm_extraction" if accepted else "unknown"),
                    "unknown": not structured_known and not accepted,
                    "source_ids": record["source_ids"],
                    "call_id": call["call_id"],
                    "prompt_version": call["prompt_version"],
                    "escalated": bool(record.get("escalated")),
                }
            )
    enriched = pd.DataFrame.from_records(rows)
    failed = pd.DataFrame.from_records(failures)
    if enriched.empty:
        return enriched, failed, pd.DataFrame()
    cost = pd.DataFrame(
        [
            {
                "persons_processed": int(enriched["person_uid"].nunique()),
                "slots": int(len(enriched)),
                "slots_with_structured_data": int(enriched["structured_known"].sum()),
                "slots_new_from_llm": int((~enriched["structured_known"] & ~enriched["unknown"]).sum()),
                "slots_still_unknown": int(enriched["unknown"].sum()),
                "new_information_ratio_pct": round(
                    100 * (~enriched["structured_known"] & ~enriched["unknown"]).sum() / max(len(enriched), 1), 2
                ),
                "unknown_ratio_pct": round(100 * enriched["unknown"].sum() / max(len(enriched), 1), 2),
                "schema_valid_pct": round(100 * enriched["schema_valid"].mean(), 2),
                "evidence_verbatim_pct": round(
                    100
                    * enriched["llm_evidence_verbatim"].sum()
                    / max(int((enriched["llm_name"].notna() | enriched["llm_degree"].notna() | enriched["llm_office"].notna()).sum()), 1),
                    2,
                ),
                "escalated_persons": int(enriched[enriched["escalated"]]["person_uid"].nunique()),
            }
        ]
    )
    return enriched, failed, cost


def _text(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def build_plan(eligible: pd.DataFrame, *, mean_window_chars: float = 900.0) -> pd.DataFrame:
    """Pre-run token/RMB plan (RULES.md: count before spending)."""
    persons = len(eligible)
    input_tokens = int(persons * (mean_window_chars * 0.6 + 420))
    output_tokens = int(persons * 246)
    return pd.DataFrame(
        [
            {
                "eligible_persons": persons,
                "slot_gaps": int(eligible["n_missing_slots"].sum()) if persons else 0,
                "mean_passage_chars": round(float(eligible["passage_chars"].mean()), 1) if persons else 0.0,
                "by_tier": ", ".join(
                    f"{tier}:{n}" for tier, n in eligible["highest_tier"].value_counts().items()
                )
                if persons
                else "",
                "estimated_calls": persons,
                "estimated_input_tokens": input_tokens,
                "estimated_output_tokens": output_tokens,
                # 4 decimals: two would round a small pilot's plan to 0.00 元
                "estimated_rmb_off_peak": round(
                    input_tokens / 1e6 * 1.0 + output_tokens / 1e6 * 4.0, 4
                ),
                "estimated_rmb_peak": round(
                    input_tokens / 1e6 * 2.0 + output_tokens / 1e6 * 8.0, 4
                ),
                "soft_budget_rmb": SOFT_BUDGET_RMB,
                "hard_budget_rmb": HARD_BUDGET_RMB,
            }
        ]
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="P05 formal enrichment")
    parser.add_argument("--plan", action="store_true", help="eligibility and budget plan only")
    parser.add_argument("--limit", type=int, default=0, help="cap persons (debug)")
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    args = parser.parse_args(argv)

    eligible = load_eligible()
    plan = build_plan(eligible)
    print(plan.to_string(index=False))
    (OUTPUT_DIR / "tables").mkdir(parents=True, exist_ok=True)
    plan.to_csv(OUTPUT_DIR / "tables" / "p05_enrich_plan.csv", index=False)
    if args.plan:
        return 0

    summary = run(eligible, concurrency=args.concurrency, limit=args.limit or None)
    records = _load_done()
    enriched, failed, cost = build_outputs(records)
    enriched.to_parquet(ENRICHED_PARQUET, index=False)
    failed.to_csv(FAILED_CASES, index=False)
    cost.insert(0, "run_summary", [json.dumps(summary, ensure_ascii=False)])
    cost.to_csv(COST_REPORT, index=False)
    print(f"family_enriched.parquet: {len(enriched):,} rows -> {ENRICHED_PARQUET}")
    print(cost.to_string(index=False))
    if len(failed):
        print(f"failed cases: {len(failed)} -> {FAILED_CASES}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
