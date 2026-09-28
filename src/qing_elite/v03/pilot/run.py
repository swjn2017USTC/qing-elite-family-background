"""U05R pilot orchestration: frame → extraction → verification → gates.

``python -m qing_elite.v03.pilot.run`` executes the whole pilot and writes:

* ``data/processed_v03/pilot_frame.parquet``          the sampled focal persons
* ``data/processed_v03/pilot_kin_edges.parquet``      kin facts from both arms
* ``data/processed_v03/pilot_credentials.parquet``    credentials from the CBDB arm
* ``data/processed_v03/pilot_risk.parquet``           verified facts with risk components
* ``data/processed_v03/pilot_review_queue.parquet``   only HIGH-tier items
* ``reports/upgrade_v03/pilot/extraction_metrics.json`` metrics, taxonomy and gate decisions

Person-level outputs stay out of Git (``data/*`` is ignored); the metrics and the gold set
are tracked so the evaluation can be audited.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from qing_elite.utils.config import PROJECT_ROOT
from qing_elite.v03.contracts import (
    RISK_COMPONENTS,
    validate_table,
)
from qing_elite.v03.lit.registry import PROCESSED_V03_DIR
from qing_elite.v03.pilot import evaluate as evaluate_mod
from qing_elite.v03.pilot.frame import build_frame, frame_summary
from qing_elite.v03.pilot.llm import load_config as load_llm_config
from qing_elite.v03.pilot.llm import resolve_abstentions
from qing_elite.v03.pilot.ocr import load_pages, page_text_map, run_ocr_range, ocr_page_quality
from qing_elite.v03.pilot.rules import ParseOutcome, parse_pages
from qing_elite.v03.pilot.structured import extract_cbdb
from qing_elite.v03.pilot.verifier import (
    RISK_COMPONENT_NAMES,
    score,
    summary as risk_summary,
    verify_rows,
)

GATES_YAML = PROJECT_ROOT / "config" / "v03" / "extraction_gates.yaml"
OCR_SOURCE_ID = "tongguanlu_commons_jiangnan_lingshu"
REPORT_DIR = PROJECT_ROOT / "reports" / "upgrade_v03" / "pilot"

#: Pages that carry roster content (verified by rendering and looking at them).
OCR_PAGE_RANGES = ((1, 8), (9, 16))


def load_gates(path: Path | None = None) -> dict[str, Any]:
    with (path or GATES_YAML).open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _cbdb_gate(
    extraction, gates: dict[str, Any], *, schema_ok: bool, total_kin_rows: int
) -> dict[str, Any]:
    integrity = gates["integrity_based"]
    mapped = len(extraction.kin_edges)
    mapping_rate = mapped / total_kin_rows if total_kin_rows else 0.0
    assertions = set(extraction.assertions["assertion_id"])
    documents = set(extraction.source_documents["document_id"])
    evidence_linkage = (
        extraction.kin_edges["evidence_assertion_id"].isin(assertions).mean()
        if mapped
        else 0.0
    )
    document_linkage = (
        extraction.assertions["source_document_id"].isin(documents).mean()
        if len(extraction.assertions)
        else 0.0
    )
    failures: list[str] = []
    if mapping_rate < integrity["min_kin_mapping_rate"]:
        failures.append(
            f"kin mapping rate {mapping_rate:.3f} < {integrity['min_kin_mapping_rate']}"
        )
    if evidence_linkage < integrity["required_evidence_linkage"]:
        failures.append(f"evidence linkage {evidence_linkage:.3f} < 1.0")
    if document_linkage < integrity["required_document_linkage"]:
        failures.append(f"document linkage {document_linkage:.3f} < 1.0")
    if integrity["required_schema_conformance"] and not schema_ok:
        failures.append("fact tables fail the V0.3 contract projection")
    return {
        "source": "cbdb",
        "kind": "integrity_based",
        "metrics": {
            "kin_rows": total_kin_rows,
            "mapped": mapped,
            "unmapped": total_kin_rows - mapped,
            "mapping_rate": round(mapping_rate, 4),
            "evidence_linkage": round(float(evidence_linkage), 4),
            "document_linkage": round(float(document_linkage), 4),
            "schema_conformance": bool(schema_ok),
        },
        "gate": "PASS" if not failures else "FAIL",
        "failures": failures,
        "action": integrity["action_on_fail"] if failures else "keep",
    }


def _ocr_gate(metrics: dict[str, Any], gates: dict[str, Any], *, ocr_failure_rate: float) -> dict[str, Any]:
    """Two-level decision: identity/relation failures drop the source, attribute failures
    demote it to identity-only (attributes then stay ``unknown``, never guessed)."""
    spec = gates["gold_based"]
    identity_failures: list[str] = []
    if metrics["precision"] < spec["min_precision"]:
        identity_failures.append(f"precision {metrics['precision']} < {spec['min_precision']}")
    if metrics["recall"] < spec["min_recall"]:
        identity_failures.append(f"recall {metrics['recall']} < {spec['min_recall']}")
    name_accuracy = metrics["field_accuracy"].get("kin_name")
    if name_accuracy is not None and name_accuracy < spec["min_kin_name_accuracy"]:
        identity_failures.append(f"kin_name accuracy {name_accuracy} < {spec['min_kin_name_accuracy']}")
    if ocr_failure_rate > spec["max_ocr_failure_rate"]:
        identity_failures.append(
            f"OCR failure rate {ocr_failure_rate:.3f} > {spec['max_ocr_failure_rate']}"
        )

    degree = metrics["field_accuracy"].get("degree_raw")
    office = metrics["field_accuracy"].get("office_raw_strict")
    # strict (exact office string) is the gate; promotion-pair tolerance is reported separately
    scoreable = [value for value in (degree, office) if value is not None]
    attribute_accuracy = round(sum(scoreable) / len(scoreable), 4) if scoreable else None
    attribute_failures: list[str] = []
    if attribute_accuracy is not None and attribute_accuracy < spec["min_attribute_accuracy"]:
        attribute_failures.append(
            f"attribute accuracy {attribute_accuracy} < {spec['min_attribute_accuracy']}"
        )

    if identity_failures:
        action, gate = spec["action_on_fail"], "FAIL"
    elif attribute_failures:
        action, gate = spec["action_on_attribute_fail"], "FAIL(attributes)"
    else:
        action, gate = "keep", "PASS"
    return {
        "source": OCR_SOURCE_ID,
        "kind": "gold_based",
        "metrics": {
            **metrics,
            "ocr_failure_rate": round(ocr_failure_rate, 4),
            "attribute_accuracy": attribute_accuracy,
        },
        "gate": gate,
        "failures": identity_failures + attribute_failures,
        "identity_failures": identity_failures,
        "attribute_failures": attribute_failures,
        "action": action,
    }


LLM_FALLBACK_JSON = PROJECT_ROOT / "data" / "interim_v03" / "pilot" / "llm_fallback.json"


def run(*, rebuild_frame: bool = False, reuse_ocr: bool = True, rerun_llm: bool = False) -> dict[str, Any]:
    PROCESSED_V03_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    frame_path = PROCESSED_V03_DIR / "pilot_frame.parquet"

    if rebuild_frame or not frame_path.exists():
        frame = build_frame()
        frame.to_parquet(frame_path, index=False)
    else:
        frame = pd.read_parquet(frame_path)

    # ---------------------------------------------------------------- structured arm
    extraction = extract_cbdb(frame["person_id"].tolist())
    total_kin_rows = len(extraction.kin_edges) + len(extraction.unmapped_kin_codes)
    schema_ok = True
    try:
        validate_table(
            "kin_edges",
            extraction.kin_edges[list(__import__("qing_elite.v03.contracts", fromlist=["KIN_EDGES_SCHEMA"]).KIN_EDGES_SCHEMA.columns)],
        )
    except Exception as error:  # schema conformance is reported, never hidden
        schema_ok = False
        schema_error = f"{type(error).__name__}: {str(error)[:200]}"
    else:
        schema_error = None

    cbdb_rows = extraction.kin_edges.rename(columns={"relation_code": "relation_type"}).copy()
    cbdb_rows["generation"] = cbdb_rows["generation_delta"]
    cbdb_rows["page_number"] = None
    cbdb_rows["quote"] = cbdb_rows["alter_name_raw"].fillna("")
    cbdb_rows["offsets"] = None
    cbdb_rows["degree_raw"] = None
    cbdb_rows["office_raw"] = None
    cbdb_rows["focal_person"] = cbdb_rows["ego_person_id"]
    cbdb_rows["kin_name"] = cbdb_rows["alter_name_raw"]
    cbdb_rows["source_id"] = "cbdb"
    cbdb_validated = verify_rows(cbdb_rows.to_dict(orient="records"))
    cbdb_scored = score(cbdb_validated)
    cbdb_scored["arm"] = "cbdb_structured"

    # ---------------------------------------------------------------- OCR arm
    page_records: list[dict[str, Any]] = []
    ocr_stats: dict[int, dict[str, Any]] = {}
    for start, end in OCR_PAGE_RANGES:
        result = run_ocr_range(start=start, end=end, reuse=reuse_ocr)
        pages = load_pages(result["path"]) if result.get("path") else []
        page_records.extend(pages)
        for page in pages:
            ocr_stats[int(page["page_number_1based"])] = {
                "chars": len(page.get("markdown_text") or ""),
                "quality": ocr_page_quality(page.get("markdown_text") or ""),
            }

    page_texts = page_text_map(page_records)
    ocr_outcome: ParseOutcome = parse_pages(
        page_records, focal_person="(page-entry)", source_id=OCR_SOURCE_ID
    )
    ocr_flags = {number: stats["quality"] for number, stats in ocr_stats.items()}

    # rules first: only the abstentions may reach the model, and the answer must quote the
    # window verbatim. The result is cached so the pilot re-runs without new API spend.
    llm_result: dict[str, Any] = {"attempted": 0, "recovered": 0, "rows": [], "rejected": []}
    if rerun_llm and ocr_outcome.abstained:
        llm_result = resolve_abstentions(ocr_outcome.abstained, page_texts)
        LLM_FALLBACK_JSON.write_text(
            json.dumps(llm_result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    elif LLM_FALLBACK_JSON.exists():
        llm_result = json.loads(LLM_FALLBACK_JSON.read_text(encoding="utf-8"))

    ocr_rows = ocr_outcome.rows + list(llm_result.get("rows", []))
    ocr_validated = verify_rows(
        ocr_rows, page_text=page_texts, ocr_flags=ocr_flags
    )
    if not ocr_validated.empty:
        ocr_validated["abstained"] = False
    ocr_scored = score(ocr_validated) if not ocr_validated.empty else ocr_validated
    if not ocr_scored.empty:
        ocr_scored["arm"] = "ocr_scan"

    # ---------------------------------------------------------------- evaluation
    gold = evaluate_mod.load_gold()
    ocr_metrics = evaluate_mod.evaluate(pd.DataFrame(ocr_rows), gold, page_texts=page_texts)
    ocr_failure_rate = (
        sum(1 for stats in ocr_stats.values() if stats["quality"] >= 0.5) / len(ocr_stats)
        if ocr_stats
        else 1.0
    )

    gates = load_gates()
    cbdb_gate = _cbdb_gate(extraction, gates, schema_ok=schema_ok, total_kin_rows=total_kin_rows)
    ocr_gate = _ocr_gate(ocr_metrics, gates, ocr_failure_rate=ocr_failure_rate)

    # ---------------------------------------------------------------- outputs
    kin_frames = [cbdb_scored]
    if not ocr_scored.empty:
        kin_frames.append(ocr_scored)
    combined = pd.concat(kin_frames, ignore_index=True)
    combined["duplicate_of_previous"] = combined.get("duplicate_of_previous", False)
    combined.to_parquet(PROCESSED_V03_DIR / "pilot_risk.parquet", index=False)
    extraction.kin_edges.to_parquet(PROCESSED_V03_DIR / "pilot_kin_edges.parquet", index=False)
    extraction.credentials.to_parquet(PROCESSED_V03_DIR / "pilot_credentials.parquet", index=False)
    extraction.career_events.to_parquet(
        PROCESSED_V03_DIR / "pilot_career_events.parquet", index=False
    )
    extraction.assertions.to_parquet(PROCESSED_V03_DIR / "pilot_assertions.parquet", index=False)
    extraction.source_documents.to_parquet(
        PROCESSED_V03_DIR / "pilot_source_documents.parquet", index=False
    )
    extraction.unmapped_kin_codes.to_json(
        PROCESSED_V03_DIR / "pilot_unmapped_kin_codes.json", orient="records", force_ascii=False
    )
    high_risk = combined[combined["risk_tier"] == "HIGH"].copy()
    if not high_risk.empty:
        high_risk["item_id"] = [f"u05r-{index:05d}" for index in range(1, len(high_risk) + 1)]
        high_risk["reason_codes"] = [
            ";".join(
                name
                for name in RISK_COMPONENT_NAMES
                if float(row.get(name, 0.0)) > 0
            )
            for _, row in high_risk.iterrows()
        ]
    high_risk.to_parquet(PROCESSED_V03_DIR / "pilot_review_queue.parquet", index=False)

    llm_config = load_llm_config()
    metrics = {
        "stage": "U05R",
        "llm_fallback": {
            "enabled": bool(llm_config.get("max_calls")),
            "model": llm_config["model"],
            "thinking_mode": llm_config["thinking_mode"],
            "abstentions_before": int(len(ocr_outcome.abstained)),
            "attempted": int(llm_result.get("attempted", 0)),
            "recovered": int(llm_result.get("recovered", 0)),
            "rejected": int(len(llm_result.get("rejected", []))),
            "rejected_reasons": [
                item.get("reason") for item in llm_result.get("rejected", [])
            ],
        },
        "pilot": {
            "frame": frame_summary(frame),
            "seed": int(frame["seed"].iloc[0]),
            "sources_used": ["cbdb", OCR_SOURCE_ID],
        },
        "cbdb_structured_arm": {
            "kin_edges": int(len(extraction.kin_edges)),
            "credentials": int(len(extraction.credentials)),
            "career_events": int(len(extraction.career_events)),
            "assertions": int(len(extraction.assertions)),
            "source_documents": int(len(extraction.source_documents)),
            "unmapped_kin_codes": int(len(extraction.unmapped_kin_codes)),
            "relation_distribution": extraction.kin_edges["relation_code"].value_counts().to_dict(),
            "risk": risk_summary(cbdb_scored),
            "schema_error": schema_error,
        },
        "ocr_scan_arm": {
            "pages_ocr": len(ocr_stats),
            "entries": int(len(ocr_rows)),
            "abstained": int(len(ocr_outcome.abstained)),
            "relation_mentions": int(ocr_outcome.relation_mentions),
            "convergence": round(ocr_outcome.convergence, 4),
            "evaluation": ocr_metrics,
            "risk": risk_summary(ocr_scored) if not ocr_scored.empty else {},
        },
        "gates": {"cbdb": cbdb_gate, "ocr_scan": ocr_gate},
        "ocr_pages": {str(key): value for key, value in sorted(ocr_stats.items())},
    }
    (REPORT_DIR / "extraction_metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return metrics


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - thin CLI
    parser = argparse.ArgumentParser(description="run the U05R pilot")
    parser.add_argument("--rebuild-frame", action="store_true")
    parser.add_argument("--no-ocr", action="store_true", help="use cached OCR pages only")
    args = parser.parse_args(argv)
    metrics = run(rebuild_frame=args.rebuild_frame, reuse_ocr=not args.no_ocr)
    print(json.dumps({k: v for k, v in metrics.items() if k != "ocr_pages"}, ensure_ascii=False, indent=2)[:4000])
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
