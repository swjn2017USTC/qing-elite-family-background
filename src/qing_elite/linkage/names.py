"""Rule-based CBDB x CGED-Q name linkage (P02).

The public JSL release carries no CBDB id, so the link has to be built here. The
matcher is deliberately conservative: name equality proposes candidates, and
secondary attributes (native province, banner, degree) must agree before a link
is called reliable. Ambiguity is reported, never resolved by guessing.
"""

from __future__ import annotations

import sqlite3

import pandas as pd

from qing_elite.utils.text import normalize_office_chars

# 江南/湖廣 were split during the Qing; a JSL province must still match a CBDB
# address that predates the split.
PROVINCE_ALIASES: dict[str, set[str]] = {
    "江南": {"江蘇", "安徽", "江南"},
    "江蘇": {"江蘇", "江南"},
    "安徽": {"安徽", "江南"},
    "湖廣": {"湖北", "湖南", "湖廣"},
    "湖北": {"湖北", "湖廣"},
    "湖南": {"湖南", "湖廣"},
    "盛京": {"奉天", "盛京"},
    "奉天": {"奉天", "盛京"},
}


def normalize_name(value: object, simplify_map: dict[str, str], variants: dict[str, str]) -> str | None:
    """Fold case/whitespace/variant characters for name comparison."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return None
    return normalize_office_chars(text, simplify_map, variants)


def normalize_province(value: object) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return None
    for suffix in ("布政使司", "省"):
        if text.endswith(suffix) and len(text) > len(suffix):
            text = text[: -len(suffix)]
    return text


def provinces_compatible(jsl_province: str | None, cbdb_province: str | None) -> bool:
    if not jsl_province or not cbdb_province:
        return False
    allowed = PROVINCE_ALIASES.get(jsl_province, {jsl_province})
    return cbdb_province in allowed


def build_cbdb_candidates(conn: sqlite3.Connection, province_lookup: dict[int, str]) -> pd.DataFrame:
    """CBDB Qing persons with the attributes the matcher needs."""
    frame = pd.read_sql_query(
        """
        SELECT b.c_personid, b.c_name_chn, b.c_index_year, b.c_birthyear, b.c_deathyear,
               b.c_index_addr_id, b.c_dy, b.c_ethnicity_code
        FROM BIOG_MAIN b
        WHERE b.c_dy = 20
        """,
        conn,
    )
    frame["cbdb_province"] = frame["c_index_addr_id"].map(province_lookup).map(normalize_province)
    return frame


def link_persons(
    jsl_persons: pd.DataFrame,
    cbdb: pd.DataFrame,
    simplify_map: dict[str, str],
    variants: dict[str, str],
) -> pd.DataFrame:
    """Link JSL persons to CBDB persons; one row per JSL person."""
    cbdb = cbdb.copy()
    cbdb["name_norm"] = [
        normalize_name(value, simplify_map, variants) for value in cbdb["c_name_chn"]
    ]
    by_name: dict[str, list[int]] = {}
    for name, person_id in zip(cbdb["name_norm"], cbdb["c_personid"]):
        if name:
            by_name.setdefault(name, []).append(int(person_id))
    attributes = cbdb.set_index("c_personid")

    records: list[dict[str, object]] = []
    for row in jsl_persons.itertuples(index=False):
        name_norm = normalize_name(row.name_chn, simplify_map, variants)
        province = normalize_province(getattr(row, "native_province", None))
        record: dict[str, object] = {
            "cgedq_person_id": row.cgedq_person_id,
            "name_norm": name_norm,
            "jsl_province": province,
            "cbdb_personid": None,
            "linkage_confidence": "unmatched",
            "linkage_evidence": "",
            "n_name_candidates": 0,
        }
        if not name_norm or not getattr(row, "name_has_surname", False):
            record["linkage_confidence"] = "unlinkable_no_surname"
            records.append(record)
            continue
        candidates = by_name.get(name_norm, [])
        record["n_name_candidates"] = len(candidates)
        if not candidates:
            records.append(record)
            continue

        province_matches = [
            pid
            for pid in candidates
            if provinces_compatible(province, attributes.at[pid, "cbdb_province"])
        ]
        if len(candidates) == 1:
            pid = candidates[0]
            if pid in province_matches:
                record.update(
                    cbdb_personid=pid,
                    linkage_confidence="high",
                    linkage_evidence="unique_name+province",
                )
            else:
                record.update(
                    cbdb_personid=pid,
                    linkage_confidence="medium",
                    linkage_evidence="unique_name",
                )
        elif len(province_matches) == 1:
            record.update(
                cbdb_personid=province_matches[0],
                linkage_confidence="high",
                linkage_evidence=f"name+province (1/{len(candidates)})",
            )
        else:
            record["linkage_confidence"] = "ambiguous"
            record["linkage_evidence"] = (
                f"{len(candidates)} name candidates, {len(province_matches)} province matches"
            )
        records.append(record)
    return pd.DataFrame.from_records(records)
