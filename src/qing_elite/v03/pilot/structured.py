"""CBDB structured records → V0.3 fact rows with evidence (U05R).

This is the "rules first" arm: everything here is read from the frozen CBDB release with SQL,
so no model is involved and every emitted fact can be traced to a row of a CBDB table. The
module produces the same shapes the OCR arm produces (kin edges, credentials, offices), plus
the evidence ledger rows both arms share:

* one ``source_documents`` row per CBDB record cited (structured records carry no text, so
  they may not carry character offsets — the V0.3 contract allows that explicitly);
* one ``evidence_assertions`` row per fact, whose ``quote`` is the CBDB field value and whose
  ``source_locator`` names table, record and page.

Kin codes are translated through ``config/v03/cbdb_kin_map.yaml``; codes the map does not
cover are emitted with ``relation_code=None`` and counted as ``unmapped`` rather than dropped.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
import yaml

from qing_elite.utils.config import PROJECT_ROOT
from qing_elite.v03.design import load_relation_ontology, relation_index

KIN_MAP_YAML = PROJECT_ROOT / "config" / "v03" / "cbdb_kin_map.yaml"
CBDB_SQLITE = PROJECT_ROOT / "data" / "raw" / "cbdb" / "cbdb_20260926.sqlite3"
CBDB_SOURCE_ID = "cbdb"
CBDB_RELEASE = "cbdb_20260926"


@dataclass
class StructuredExtraction:
    """Everything the CBDB arm contributes to the pilot."""

    source_documents: pd.DataFrame
    assertions: pd.DataFrame
    kin_edges: pd.DataFrame
    credentials: pd.DataFrame
    career_events: pd.DataFrame
    unmapped_kin_codes: pd.DataFrame


@functools.lru_cache(maxsize=None)
def load_kin_map(path: Path | None = None) -> dict[str, Any]:
    with (path or KIN_MAP_YAML).open(encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    if not config.get("by_code"):
        raise ValueError("cbdb_kin_map.yaml must declare by_code")
    return config


def map_kin_code(code: int | None, label: str | None) -> str | None:
    """Translate a CBDB kin code/label to a V0.3 relation code (``None`` when unmapped)."""
    config = load_kin_map()
    if code is not None and int(code) in {int(key) for key in config["by_code"]}:
        return config["by_code"][int(code)]
    if label:
        first = str(label).split(";")[0].strip()
        if first in config["by_label"]:
            return config["by_label"][first]
    return None


def _document_row(record: str, locator: str, pages: str | None) -> dict[str, Any]:
    return {
        "document_id": record,
        "source_id": CBDB_SOURCE_ID,
        "source_release": CBDB_RELEASE,
        "document_type": "cbdb_structured_record",
        "title": record.split("|")[0],
        "volume": None,
        "locator": locator,
        "text": None,
        "text_sha256": None,
        "chars": None,
        "rights_status": "unknown",
        "license": None,
        "access_type": "PUBLIC_STRUCTURED",
        "machine_readable": True,
        "ocr_used": False,
        "url": "https://huggingface.co/datasets/cbdb/cbdb-sqlite",
        "retrieved_at": pd.Timestamp("2026-09-27"),
        "_pages": pages,
    }


def _assertion(
    assertion_id: str,
    subject_type: str,
    subject_id: str,
    field_name: str,
    value: str,
    document_id: str,
    locator: str,
) -> dict[str, Any]:
    return {
        "assertion_id": assertion_id,
        "subject_type": subject_type,
        "subject_id": subject_id,
        "field": field_name,
        "assertion_state": "positive",
        "value_raw": value,
        "value_normalized": None,
        "source_document_id": document_id,
        "source_locator": locator,
        "quote": value,
        "quote_start": None,
        "quote_end": None,
        "extractor": "cbdb_data",
        "extractor_version": CBDB_RELEASE,
        "extraction_run_id": "u05r-cbdb",
        "review_status": "not_required",
        "confidence": "high",
        "conflict_group_id": None,
    }


def extract_cbdb(person_ids: list[int], *, db_path: Path | None = None) -> StructuredExtraction:
    """Read kin, credentials and offices for ``person_ids`` from the frozen CBDB release."""
    if not person_ids:
        raise ValueError("no person ids supplied")
    db = str(db_path or CBDB_SQLITE)
    if not Path(db).exists():
        raise FileNotFoundError(f"CBDB sqlite not present: {db}")
    con = duckdb.connect()
    id_list = ",".join(str(int(value)) for value in person_ids)

    kin = con.execute(
        f"""
        SELECT k.c_personid AS ego_person_id, k.c_kin_id, k.c_kin_code, k.c_source, k.c_pages,
               kc.c_kinrel_chn AS kin_label,
               b.c_name_chn AS kin_name, b.c_index_year AS kin_index_year, b.c_dy AS kin_dy,
               eg.c_name_chn AS ego_name, eg.c_index_year AS ego_index_year
        FROM sqlite_scan('{db}','KIN_DATA') k
        LEFT JOIN sqlite_scan('{db}','KINSHIP_CODES') kc ON kc.c_kincode = k.c_kin_code
        LEFT JOIN sqlite_scan('{db}','BIOG_MAIN') b ON b.c_personid = k.c_kin_id
        LEFT JOIN sqlite_scan('{db}','BIOG_MAIN') eg ON eg.c_personid = k.c_personid
        WHERE k.c_personid IN ({id_list})
        ORDER BY k.c_personid, k.c_kin_id
        """
    ).fetchdf()

    kin_ids = sorted({int(value) for value in kin["c_kin_id"].dropna().tolist()} | set(person_ids))
    kin_list = ",".join(str(value) for value in kin_ids)

    entries = con.execute(
        f"""
        SELECT e.c_personid, e.c_entry_code, e.c_year, e.c_source, e.c_pages,
               ec.c_entry_desc_chn AS entry_label
        FROM sqlite_scan('{db}','ENTRY_DATA') e
        LEFT JOIN sqlite_scan('{db}','ENTRY_CODES') ec ON ec.c_entry_code = e.c_entry_code
        WHERE e.c_personid IN ({kin_list})
        ORDER BY e.c_personid, e.c_year
        """
    ).fetchdf()

    offices = con.execute(
        f"""
        SELECT o.c_personid, o.c_office_id, o.c_posting_id, o.c_firstyear, o.c_lastyear, o.c_appt_code,
               o.c_source, o.c_pages, oc.c_office_chn
        FROM sqlite_scan('{db}','POSTED_TO_OFFICE_DATA') o
        LEFT JOIN sqlite_scan('{db}','OFFICE_CODES') oc ON oc.c_office_id = o.c_office_id
        WHERE o.c_personid IN ({kin_list})
        ORDER BY o.c_personid, o.c_firstyear
        """
    ).fetchdf()
    con.close()

    ontology = relation_index(load_relation_ontology())
    documents: dict[str, dict[str, Any]] = {}
    assertions: list[dict[str, Any]] = []
    edge_rows: list[dict[str, Any]] = []
    credential_rows: list[dict[str, Any]] = []
    career_rows: list[dict[str, Any]] = []
    unmapped: list[dict[str, Any]] = []

    def document(record: str, locator: str, pages: str | None) -> str:
        documents.setdefault(record, _document_row(record, locator, pages))
        return record

    for index, row in enumerate(kin.itertuples(index=False), start=1):
        relation_code = map_kin_code(row.c_kin_code, row.kin_label)
        record = f"cbdb|KIN_DATA|{row.ego_person_id}|{row.c_kin_id}|{row.c_kin_code}"
        locator = f"KIN_DATA person={row.ego_person_id} kin={row.c_kin_id} code={row.c_kin_code}"
        document(record, locator, row.c_pages)
        if relation_code is None:
            unmapped.append(
                {
                    "ego_person_id": row.ego_person_id,
                    "kin_id": row.c_kin_id,
                    "kin_code": row.c_kin_code,
                    "kin_label": row.kin_label,
                    "reason": "kin code/label absent from cbdb_kin_map",
                }
            )
            continue
        assertion_id = f"cbdb-kin-{index:06d}"
        assertions.append(
            _assertion(
                assertion_id,
                "kin_edge",
                f"cbdb-kin-{row.ego_person_id}-{row.c_kin_id}-{row.c_kin_code}",
                "relation_type",
                str(row.kin_label or relation_code),
                record,
                locator,
            )
        )
        edge_rows.append(
            {
                "edge_id": f"cbdb-kin-{row.ego_person_id}-{row.c_kin_id}-{row.c_kin_code}",
                "ego_person_id": f"cbdb:{int(row.ego_person_id)}",
                "alter_person_id": f"cbdb:{int(row.c_kin_id)}" if pd.notna(row.c_kin_id) else None,
                "alter_name_raw": row.kin_name,
                "relation_code": relation_code,
                "relation_class": ontology[relation_code]["class"],
                "lineage_side": "paternal",
                "generation_delta": ontology[relation_code]["generation_delta"],
                "edge_origin": "source_explicit",
                "assertion_state": "positive",
                "evidence_assertion_id": assertion_id,
                "source_document_id": record,
                "confidence": "high",
                "review_status": "not_required",
                # pilot-only extras (not part of the V0.3 kin_edges contract): they feed the
                # chronology check in the verifier
                "alter_index_year": int(row.kin_index_year) if pd.notna(row.kin_index_year) else None,
                "ego_index_year": int(row.ego_index_year) if pd.notna(row.ego_index_year) else None,
            }
        )

    credential_index = 0
    for row in entries.itertuples(index=False):
        credential_index += 1
        record = f"cbdb|ENTRY_DATA|{row.c_personid}|{row.c_entry_code}|{row.c_year}"
        locator = f"ENTRY_DATA person={row.c_personid} code={row.c_entry_code} year={row.c_year}"
        document(record, locator, row.c_pages)
        assertion_id = f"cbdb-entry-{credential_index:06d}"
        credential_id = f"cbdb-cred-{row.c_personid}-{row.c_entry_code}-{row.c_year}"
        assertions.append(
            _assertion(
                assertion_id,
                "credential",
                credential_id,
                "credential",
                str(row.entry_label or row.c_entry_code),
                record,
                locator,
            )
        )
        credential_rows.append(
            {
                "credential_id": credential_id,
                "person_id": f"cbdb:{int(row.c_personid)}",
                "credential_type": _credential_type(row.entry_label),
                "exam_route": row.entry_label,
                "credential_year": int(row.c_year) if pd.notna(row.c_year) and int(row.c_year) > 0 else None,
                "rank_in_exam": None,
                "assertion_state": "positive",
                "evidence_assertion_id": assertion_id,
                "source_document_id": record,
                "confidence": "high",
                "review_status": "not_required",
            }
        )

    office_index = 0
    for row in offices.itertuples(index=False):
        office_index += 1
        # c_posting_id is the posting's own key; c_firstyear is null for many rows, and an
        # event id built on a null field collides (measured: duplicate ids, which the U07R
        # ledger check caught)
        posting = row.c_posting_id if pd.notna(row.c_posting_id) else f"row{office_index}"
        record = f"cbdb|POSTED_TO_OFFICE_DATA|{row.c_personid}|{row.c_office_id}|{posting}"
        locator = (
            f"POSTED_TO_OFFICE_DATA person={row.c_personid} office={row.c_office_id} posting={posting}"
        )
        document(record, locator, row.c_pages)
        assertion_id = f"cbdb-office-{office_index:06d}"
        event_id = f"cbdb-office-{row.c_personid}-{row.c_office_id}-{posting}"
        assertions.append(
            _assertion(
                assertion_id,
                "career_event",
                event_id,
                "office",
                str(row.c_office_chn or row.c_office_id),
                record,
                locator,
            )
        )
        career_rows.append(
            {
                "event_id": event_id,
                "person_id": f"cbdb:{int(row.c_personid)}",
                "office_id": None,
                "office_raw": row.c_office_chn,
                "office_normalized": None,
                "start_year": int(row.c_firstyear) if pd.notna(row.c_firstyear) and int(row.c_firstyear) > 0 else None,
                "end_year": int(row.c_lastyear) if pd.notna(row.c_lastyear) and int(row.c_lastyear) > 0 else None,
                "date_precision": "exact_year",
                "reign": None,
                "province": None,
                "jurisdiction": None,
                "rank_label": None,
                "administrative_level": "unknown",
                "central_local": "unknown",
                "appointment_type": "unknown",
                "selection_method": None,
                "assertion_state": "positive",
                "evidence_assertion_id": assertion_id,
                "source_document_id": record,
                "confidence": "high",
                "review_status": "not_required",
            }
        )

    document_rows = [
        {key: value for key, value in item.items() if key != "_pages"} for item in documents.values()
    ]
    return StructuredExtraction(
        source_documents=pd.DataFrame(document_rows),
        assertions=pd.DataFrame(assertions),
        kin_edges=pd.DataFrame(edge_rows),
        credentials=pd.DataFrame(credential_rows),
        career_events=pd.DataFrame(career_rows),
        unmapped_kin_codes=pd.DataFrame(unmapped),
    )


def _credential_type(label: str | None) -> str | None:
    """Map a CBDB entry label to the V0.3 credential vocabulary (``None`` when unclear)."""
    text = str(label or "")
    for keyword, value in (
        ("武進士", "wuju"),
        ("武舉", "wuju"),
        ("進士", "jinshi"),
        ("舉人", "juren"),
        ("貢生", "gongsheng"),
        ("監生", "jiansheng"),
        ("生員", "shengyuan"),
        ("庠生", "shengyuan"),
        ("廩生", "shengyuan"),
        ("增生", "shengyuan"),
        ("附生", "shengyuan"),
        ("蔭", "yinsheng"),
        ("捐", "juanna"),
    ):
        if keyword in text:
            return value
    return None
