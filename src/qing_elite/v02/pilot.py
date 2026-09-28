"""U03 pilot: execute the frozen source protocol on a per-stratum pilot and gate it.

    uv run python -m qing_elite.v02.pilot

The pilot runs the *same* four-source order for every selected person, logs every step
(including the misses), and computes per-tier completion, identity/office/degree hits,
manual minutes and the projected cost. The stage gate is the one in the upgrade plan:
any tier below 80% source-protocol completion or below 20% effective information rate
fails the phase — the pilot exists to find that out before the full run.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd

from qing_elite.utils.config import PROCESSED_V02_DIR, PROJECT_ROOT
from qing_elite.v02.sample import PILOT_PER_STRATUM, freeze
from qing_elite.v02.sources import (
    OPTIONAL_SOURCES,
    REQUIRED_SOURCES,
    SOURCE_ORDER,
    SourceResult,
    protocol_fingerprint,
    run_protocol,
)

AUDIT_DIR = PROJECT_ROOT / "audit" / "v03"
SEARCH_LOG = PROCESSED_V02_DIR / "source_search_log.parquet"
PILOT_RESULTS = PROCESSED_V02_DIR / "source_protocol_pilot.parquet"

COMPLETION_TARGET = 0.80
INFORMATION_TARGET = 0.20
TERMINAL_OUTCOMES = ("found", "not_found")


def _biography_locators() -> dict[str, str]:
    path = Path("data/interim/passages/harvest_index.parquet")
    if not path.exists():
        return {}
    index = pd.read_parquet(path)
    return dict(zip(index["person_uid"].astype(str), index["source_ids"].astype(str)))


def pilot_people(sample: pd.DataFrame, per_stratum: int = PILOT_PER_STRATUM) -> pd.DataFrame:
    """First N persons of each stratum in the frozen selection order."""
    ordered = sample.sort_values(["stratum", "selection_index"])
    picked = ordered.groupby("stratum", group_keys=False).head(per_stratum).copy()
    locators = _biography_locators()
    picked["biography_source_ids"] = picked["entity_id"].map(locators)
    return picked.reset_index(drop=True)


def _log_rows(person: Mapping[str, Any], results: Sequence[SourceResult], run_id: str) -> list[dict[str, Any]]:
    now = pd.Timestamp(datetime.now(timezone.utc).replace(tzinfo=None))
    rows = []
    for result in results:
        rows.append(
            {
                "search_id": f"{run_id}:{person['entity_id']}:{result.source_id}",
                "entity_id": str(person["entity_id"]),
                "source_id": result.source_id,
                "query": f"name={person.get('name_chn')}"
                + (f";cbdb_id={person['cbdb_personid']}" if pd.notna(person.get("cbdb_personid")) else ""),
                "searched": result.outcome != "unavailable",
                "outcome": result.outcome,
                "searched_at": pd.Timestamp(now),
                "notes": result.detail[:500],
            }
        )
    return rows


def person_flags(results: Sequence[SourceResult]) -> dict[str, Any]:
    """First-source-wins flags for one person, keeping attributed and raw hits apart."""
    by_source = {result.source_id: result for result in results}
    first_identity = next((name for name in SOURCE_ORDER if by_source[name].identity), None)
    first_office = next((name for name in SOURCE_ORDER if by_source[name].office), None)
    first_degree = next((name for name in SOURCE_ORDER if by_source[name].degree), None)
    attributed_identity = next(
        (name for name in SOURCE_ORDER if by_source[name].identity and by_source[name].attributed),
        None,
    )
    attributed_office = next(
        (name for name in SOURCE_ORDER if by_source[name].office and by_source[name].attributed),
        None,
    )
    attributed_degree = next(
        (name for name in SOURCE_ORDER if by_source[name].degree and by_source[name].attributed),
        None,
    )
    identity_attributed = attributed_identity is not None
    ancestry_source = next(
        (name for name in SOURCE_ORDER if by_source[name].ancestry and by_source[name].attributed),
        None,
    )
    return {
        "outcomes": "|".join(f"{name}:{by_source[name].outcome}" for name in SOURCE_ORDER),
        "attributions": "|".join(f"{name}:{by_source[name].attribution}" for name in SOURCE_ORDER),
        "protocol_complete": all(by_source[name].outcome in TERMINAL_OUTCOMES for name in SOURCE_ORDER),
        "protocol_complete_required": all(
            by_source[name].outcome in TERMINAL_OUTCOMES for name in REQUIRED_SOURCES
        ),
        "identity_hit": first_identity is not None,
        "identity_attributed": identity_attributed,
        "office_hit": first_office is not None,
        "office_attributed": attributed_office is not None,
        "degree_hit": first_degree is not None,
        "degree_attributed": attributed_degree is not None,
        "effective_information": identity_attributed
        and (attributed_office is not None or attributed_degree is not None),
        "ancestry_hit": ancestry_source is not None,
        "ancestry_source": ancestry_source,
        "identity_source": first_identity,
        "attributed_identity_source": attributed_identity,
        "office_source": first_office,
        "degree_source": first_degree,
    }


def run_pilot(*, per_stratum: int = PILOT_PER_STRATUM, write: bool = True) -> dict[str, Any]:
    sample, design, power = freeze(write=write)
    people = pilot_people(sample, per_stratum)
    run_id = f"u03-{protocol_fingerprint()[:8]}"

    started = time.time()
    log_rows: list[dict[str, Any]] = []
    result_rows: list[dict[str, Any]] = []
    for person in people.to_dict("records"):
        person_started = time.time()
        results = run_protocol(person)
        log_rows.extend(_log_rows(person, results, run_id))
        flags = person_flags(results)
        result_rows.append(
            {
                "entity_id": str(person["entity_id"]),
                "stratum": person["stratum"],
                "name_chn": person["name_chn"],
                **flags,
                "seconds": round(time.time() - person_started, 3),
            }
        )

    pilot_results = pd.DataFrame.from_records(result_rows)
    search_log = pd.DataFrame.from_records(log_rows)

    metrics = summarise(pilot_results, design)
    gate = evaluate_gate(metrics)
    gate_required = evaluate_gate(metrics, completion_column="protocol_completion_required_sources")
    elapsed = time.time() - started

    if write:
        PROCESSED_V02_DIR.mkdir(parents=True, exist_ok=True)
        AUDIT_DIR.mkdir(parents=True, exist_ok=True)
        pilot_results.to_parquet(PILOT_RESULTS, index=False)
        search_log.to_parquet(SEARCH_LOG, index=False)
        metrics.to_csv(AUDIT_DIR / "u03_pilot_metrics.csv", index=False)
        power.to_csv(AUDIT_DIR / "u03_power_cost.csv", index=False)
        search_log.to_csv(AUDIT_DIR / "u03_search_log_sample.csv", index=False)
        (AUDIT_DIR / "u03_gate.json").write_text(
            json.dumps(
                {
                    "protocol_fingerprint": protocol_fingerprint(),
                    "source_order": list(SOURCE_ORDER),
                    "required_sources": list(REQUIRED_SOURCES),
                    "optional_sources": list(OPTIONAL_SOURCES),
                    "run_id": run_id,
                    "per_stratum": per_stratum,
                    "completion_target": COMPLETION_TARGET,
                    "information_target": INFORMATION_TARGET,
                    "gate_required_sources": gate_required,
                    "gate_all_sources_including_optional": gate,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    return {
        "run_id": run_id,
        "people": int(len(people)),
        "elapsed_seconds": round(elapsed, 1),
        "gate": gate,
        "gate_required_sources_only": gate_required,
        "metrics": metrics,
    }


def summarise(pilot: pd.DataFrame, design: Mapping[str, Any]) -> pd.DataFrame:
    """Per-stratum completion, hits, information rate, minutes and projected cost."""
    rows: list[dict[str, Any]] = []
    per_person_seconds = float(pilot["seconds"].mean()) if len(pilot) else 0.0
    for tier, subset in pilot.groupby("stratum"):
        n = int(len(subset))
        identity = int(subset["identity_hit"].sum())
        identity_attributed = int(subset["identity_attributed"].sum())
        office = int(subset["office_hit"].sum())
        degree = int(subset["degree_hit"].sum())
        effective = int(subset["effective_information"].sum())
        frame_size = int(design["strata"].get(tier, {}).get("sample", 0))
        rows.append(
            {
                "stratum": tier,
                "n_pilot": n,
                "protocol_completion": round(float(subset["protocol_complete"].mean()), 4),
                "protocol_completion_required_sources": round(
                    float(subset["protocol_complete_required"].mean()), 4
                ),
                "identity_hit_rate": round(identity / n, 4) if n else None,
                "identity_attributed_rate": round(identity_attributed / n, 4) if n else None,
                "office_hit_rate": round(office / n, 4) if n else None,
                "degree_hit_rate": round(degree / n, 4) if n else None,
                "effective_information_rate": round(effective / n, 4) if n else None,
                "ancestral_information_rate": round(float(subset["ancestry_hit"].mean()), 4)
                if n
                else None,
                # machine time is measured; human review time is an *assumption* here —
                # U03 ran no human adjudication, so this column is not a measurement
                "machine_seconds_per_person": round(float(subset["seconds"].mean()), 2),
                "manual_minutes_per_person_assumed": 0.5,
                "frozen_sample_size": frame_size,
                "projected_manual_minutes_if_assumed": round(frame_size * 0.5, 1),
                "projected_machine_minutes": round(frame_size * per_person_seconds / 60, 1),
                "projected_api_cost_rmb": 0.0,
            }
        )
    return pd.DataFrame.from_records(rows)


def evaluate_gate(metrics: pd.DataFrame, *, completion_column: str = "protocol_completion") -> dict[str, Any]:
    """The plan's U03 gate: >=80% completion and >=20% information in every tier."""
    failures = []
    for row in metrics.to_dict("records"):
        if row[completion_column] < COMPLETION_TARGET:
            failures.append(
                f"{row['stratum']}: completion {row[completion_column]:.2%} < {COMPLETION_TARGET:.0%}"
            )
        if (row["effective_information_rate"] or 0.0) < INFORMATION_TARGET:
            failures.append(
                f"{row['stratum']}: effective information {row['effective_information_rate']:.2%} "
                f"< {INFORMATION_TARGET:.0%}"
            )
    return {"status": "PASS" if not failures else "FAIL", "failures": failures}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="U03 source-protocol pilot")
    parser.add_argument("--per-stratum", type=int, default=PILOT_PER_STRATUM)
    args = parser.parse_args(argv)
    result = run_pilot(per_stratum=args.per_stratum)
    print(result["metrics"].to_string(index=False))
    print("gate (required sources):", json.dumps(result["gate_required_sources_only"], ensure_ascii=False))
    print("gate (all sources, incl. optional):", json.dumps(result["gate"], ensure_ascii=False))
    print(f"people={result['people']} elapsed={result['elapsed_seconds']}s (median per person "
          f"{statistics.median(result['metrics']['machine_seconds_per_person']):.2f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
