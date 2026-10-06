"""CBDB appointment extraction for tiers A1/A2/A3/B/C (P02).

One row per posting record. Nothing is merged here: 多次任職 stays as multiple
rows, 署理/兼 are recorded as appointment types, and person-level consolidation
happens in ``build_universe``.
"""

from __future__ import annotations

import sqlite3
from typing import Iterable, Mapping

import pandas as pd

from qing_elite.cbdb.offices import OfficeTier

# APPOINTMENT_CODES -> appointment_type.  Codes absent from this map are
# reported as "other"; 0/NULL as "unknown".
APPOINTMENT_TYPE: dict[int, str] = {
    0: "unknown",  # 未詳
    1: "regular",
    45: "regular",
    42: "regular",
    46: "regular",
    49: "regular",
    52: "promotion",
    62: "selection",
    63: "transfer",
    65: "fill",
    67: "seniority",
    72: "transfer",
    89: "transfer",
    105: "contribution",
    100: "candidate",
    32: "acting",  # 署
    3: "acting",  # 行
    4: "acting",  # 守
    5: "acting",  # 試
    2: "acting",  # 權
    13: "acting",  # 攝
    12: "acting",  # 視
    7: "acting",  # 借
    8: "acting",  # 假
    37: "concurrent",  # 兼
    22: "posthumous",
    23: "posthumous",
    24: "posthumous",
    28: "posthumous",
    29: "posthumous",
    27: "posthumous",
    30: "posthumous",
    31: "inherited",
    36: "retired",
    9: "extra",
    10: "extra",
    11: "extra",
}

POSTING_QUERY = """
SELECT
    p.c_personid,
    p.c_office_id,
    o.c_office_chn,
    p.c_posting_id,
    p.c_sequence,
    p.c_firstyear,
    p.c_lastyear,
    p.c_appt_code,
    p.c_assume_office_code,
    p.c_inst_code,
    p.c_inst_name_code,
    p.c_office_category_id,
    p.c_dy           AS posting_dy,
    p.c_notes,
    b.c_name_chn,
    b.c_index_year,
    b.c_index_year_type_code,
    b.c_birthyear,
    b.c_deathyear,
    b.c_dy           AS person_dy,
    b.c_index_addr_id,
    b.c_ethnicity_code,
    b.c_household_status_code
FROM POSTED_TO_OFFICE_DATA p
JOIN OFFICE_CODES o ON o.c_office_id = p.c_office_id
JOIN BIOG_MAIN b ON b.c_personid = p.c_personid
WHERE p.c_office_id IN ({placeholders})
  AND (p.c_dy = 20 OR (p.c_dy IN (0, -1) AND b.c_dy = 20) OR (p.c_dy IS NULL AND b.c_dy = 20))
"""


def _placeholders(ids: Iterable[int]) -> str:
    return ",".join(str(int(i)) for i in ids)


def load_postings(
    conn: sqlite3.Connection,
    office_ids: Iterable[int],
    tiers: Mapping[int, OfficeTier],
) -> pd.DataFrame:
    """Load postings for the configured office ids and attach tier metadata."""
    ids = sorted(set(int(i) for i in office_ids))
    query = POSTING_QUERY.format(placeholders=_placeholders(ids))
    frame = pd.read_sql_query(query, conn)
    if frame.empty:
        raise RuntimeError("no CBDB postings matched the configured tier offices")

    frame["tier"] = frame["c_office_id"].map(lambda oid: tiers[int(oid)].tier)
    frame["tier_rank"] = frame["tier"].map(TIER_RANK)
    frame["office_std"] = frame["c_office_id"].map(lambda oid: tiers[int(oid)].canonical)
    frame["institution"] = frame["c_office_id"].map(lambda oid: tiers[int(oid)].institution)
    frame["office_variant"] = frame["c_office_chn"] != frame["office_std"]
    frame["ambiguous_institution"] = frame["c_office_chn"].isin(["尚書", "侍郎", "左侍郎"])

    frame["appointment_type"] = frame["c_appt_code"].map(
        lambda code: APPOINTMENT_TYPE.get(int(code), "other") if code is not None else "unknown"
    )
    frame["is_acting"] = frame["appointment_type"] == "acting"
    frame["is_concurrent"] = frame["appointment_type"] == "concurrent"

    # Year for era assignment: posting years first, then the person's index year.
    # A handful of CBDB postings carry reign-year fragments (e.g. 17, 25) instead of
    # absolute years; those are treated as unknown rather than trusted.
    start = pd.to_numeric(frame["c_firstyear"], errors="coerce").where(lambda s: s.between(1368, 1912))
    end = pd.to_numeric(frame["c_lastyear"], errors="coerce").where(lambda s: s.between(1368, 1912))
    raw_start = pd.to_numeric(frame["c_firstyear"], errors="coerce")
    raw_end = pd.to_numeric(frame["c_lastyear"], errors="coerce")
    index_year = pd.to_numeric(frame["c_index_year"], errors="coerce")
    frame["date_start"] = start
    frame["date_end"] = end
    frame["year_discarded"] = (
        (raw_start.notna() & start.isna()) | (raw_end.notna() & end.isna())
    )
    frame["date_source"] = "posting"
    index_year = index_year.where(index_year.between(1368, 1912))
    fallback = start.isna() & end.isna() & index_year.notna()
    frame.loc[fallback, "date_start"] = index_year[fallback]
    frame.loc[fallback, "date_source"] = "index_year"
    no_date = frame["date_start"].isna() & frame["date_end"].isna()
    frame.loc[no_date, "date_source"] = "none"
    return frame


TIER_RANK = {"A1": 1, "A2": 2, "A3": 3, "B": 4, "C": 5, "D": 6}


def attach_places(conn: sqlite3.Connection, frame: pd.DataFrame) -> pd.DataFrame:
    """Attach the posting place (and its province) for 總督/巡撫 style postings."""
    places = pd.read_sql_query(
        """
        SELECT pa.c_posting_id, pa.c_personid, pa.c_office_id, pa.c_addr_id,
               ac.c_name_chn AS place_chn, ac.c_admin_type
        FROM POSTED_TO_ADDR_DATA pa
        JOIN ADDR_CODES ac ON ac.c_addr_id = pa.c_addr_id
        """,
        conn,
    )
    provinces = build_province_lookup(conn)
    places["province"] = places["c_addr_id"].map(provinces)
    key_cols = ["c_personid", "c_office_id", "c_posting_id"]
    deduped = places.drop_duplicates(subset=key_cols)
    merged = frame.merge(
        deduped[key_cols + ["c_addr_id", "place_chn", "province"]],
        on=key_cols,
        how="left",
    )
    return merged


def build_province_lookup(conn: sqlite3.Connection) -> dict[int, str]:
    """Map every address id to its province by walking ADDR_BELONGS_DATA upward."""
    addr_type = dict(conn.execute("SELECT c_addr_id, c_admin_type FROM ADDR_CODES").fetchall())
    addr_name = dict(conn.execute("SELECT c_addr_id, c_name_chn FROM ADDR_CODES").fetchall())
    parents: dict[int, list[int]] = {}
    for child, parent in conn.execute("SELECT c_addr_id, c_belongs_to FROM ADDR_BELONGS_DATA").fetchall():
        if parent:
            parents.setdefault(int(child), []).append(int(parent))

    province_types = {"Sheng", "sheng", "Xingsheng", "Shengshi"}
    cache: dict[int, str | None] = {}

    def resolve(addr_id: int, depth: int = 0) -> str | None:
        if addr_id in cache:
            return cache[addr_id]
        if depth > 12:
            return None
        cache[addr_id] = None  # cycles / deep chains resolve to unknown
        if addr_type.get(addr_id) in province_types:
            result = addr_name.get(addr_id)
            cache[addr_id] = result
            return result
        for parent in parents.get(addr_id, []):
            found = resolve(parent, depth + 1)
            if found:
                cache[addr_id] = found
                return found
        return None

    return {addr_id: resolve(addr_id) for addr_id in addr_type}


def load_alt_names(conn: sqlite3.Connection, person_ids: Iterable[int]) -> pd.DataFrame:
    """Alt names (異名) per person, needed for later text linkage."""
    ids = sorted(set(int(i) for i in person_ids))
    if not ids:
        return pd.DataFrame(columns=["person_key", "alt_name"])
    rows: list[pd.DataFrame] = []
    for chunk in range(0, len(ids), 900):
        batch = ids[chunk : chunk + 900]
        rows.append(
            pd.read_sql_query(
                f"""
                SELECT a.c_personid, a.c_alt_name_chn, a.c_alt_name_type_code, c.c_name_type_desc_chn
                FROM ALTNAME_DATA a
                LEFT JOIN ALTNAME_CODES c ON c.c_name_type_code = a.c_alt_name_type_code
                WHERE a.c_personid IN ({_placeholders(batch)})
                  AND a.c_alt_name_chn IS NOT NULL AND a.c_alt_name_chn <> ''
                """,
                conn,
            )
        )
    frame = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    frame["person_key"] = "cbdb:" + frame["c_personid"].astype(str)
    return frame
