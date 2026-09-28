"""U02 data loading: CGED-Q persons and CBDB persons with match features.

Reads the frozen releases only (no network, no LLM) and caches two flat frames that
the dedupe/cross-source matchers consume:

* ``cgedq_persons.parquet`` — CGED-Q person_id level, rebuilt from the JSL release
  through the existing P02 helpers, plus normalized name / province / banner /
  degree and career years;
* ``cbdb_persons.parquet`` — CBDB Qing persons with normalized name, aliases, merged
  ids, province, banner group, degree and a usable year window.
"""

from __future__ import annotations

import sqlite3

import pandas as pd

from qing_elite.build_universe import BANNER_ETHNICITY
from qing_elite.cbdb.degrees import classify_entry_desc
from qing_elite.cgedq.officials import (
    degree_lookup,
    d_layer_appointments,
    load_chushen_recodes,
    load_d_layer,
    person_level,
)
from qing_elite.cgedq.offices import CgedqOfficeClassifier
from qing_elite.linkage.names import normalize_name, normalize_province
from qing_elite.utils.config import CBDB_SQLITE, PROCESSED_V02_DIR, RAW_DIR, load_offices
from qing_elite.v02.linkage_pairs import COOBSERVATIONS

CGEDQ_PERSONS = PROCESSED_V02_DIR / "cgedq_persons.parquet"
CBDB_PERSONS = PROCESSED_V02_DIR / "cbdb_persons.parquet"
CGEDQ_TAB = RAW_DIR / "cgeq" / "cgedq_jsl_public_1760-1912_personid_2026-08-28.tab"

CGEDQ_YEAR_MIN, CGEDQ_YEAR_MAX = 1760, 1798

PROVINCE_NAMES = (
    "江蘇", "安徽", "浙江", "江西", "福建", "湖北", "湖南", "河南", "山東", "山西",
    "陝西", "甘肅", "四川", "廣東", "廣西", "雲南", "貴州", "直隸", "奉天", "盛京",
    "江南", "湖廣",
)


def province_lookup() -> dict[int, str]:
    """CBDB address id -> province name (prefecture strings are ignored)."""
    conn = sqlite3.connect(CBDB_SQLITE)
    try:
        rows = conn.execute("SELECT c_addr_id, c_name_chn FROM ADDR_CODES").fetchall()
    finally:
        conn.close()
    lookup: dict[int, str] = {}
    for addr_id, name in rows:
        if not isinstance(name, str):
            continue
        for province in PROVINCE_NAMES:
            if province in name:
                lookup[int(addr_id)] = province
                break
    return lookup


def _name_folding() -> tuple[dict[str, str], dict[str, str]]:
    cfg = load_offices()
    return cfg["cgedq"]["simplify_map"], cfg["cgedq"]["char_variants"]


def build_cgedq_persons(*, force: bool = False) -> pd.DataFrame:
    """CGED-Q person-level frame for the 1760-1798 slice (cached on disk)."""
    if CGEDQ_PERSONS.exists() and not force:
        return pd.read_parquet(CGEDQ_PERSONS)
    cfg = load_offices()
    classifier = CgedqOfficeClassifier(cfg)
    frame = load_d_layer(CGEDQ_TAB, classifier, year_min=CGEDQ_YEAR_MIN, year_max=CGEDQ_YEAR_MAX)
    spells = d_layer_appointments(frame)
    persons = person_level(frame, spells)
    recodes = load_chushen_recodes(RAW_DIR / "cgeq" / "cgedq_jsl_chushen_recodes.tab")
    chushen = degree_lookup(recodes, cfg["cgedq"]["degree_category_rank"])
    persons["degree_category"] = persons["degree_raw"].map(
        lambda value: chushen.get(value, (None, 0))[0] if isinstance(value, str) else None
    )
    persons["degree_rank"] = persons["degree_raw"].map(
        lambda value: chushen.get(value, (None, 0))[1] if isinstance(value, str) else 0
    )
    simplify, variants = _name_folding()
    persons["name_norm"] = [
        normalize_name(value, simplify, variants) for value in persons["name_chn"]
    ]
    persons["province_norm"] = persons["native_province"].map(normalize_province)
    persons["banner_group"] = persons["banner_std"]
    PROCESSED_V02_DIR.mkdir(parents=True, exist_ok=True)
    persons.to_parquet(CGEDQ_PERSONS, index=False)
    return persons


def build_cbdb_persons(*, force: bool = False) -> pd.DataFrame:
    """CBDB Qing persons with name, aliases, merged ids, province, banner, degree."""
    if CBDB_PERSONS.exists() and not force:
        return pd.read_parquet(CBDB_PERSONS)
    lookup = province_lookup()
    conn = sqlite3.connect(CBDB_SQLITE)
    try:
        frame = pd.read_sql_query(
            """
            SELECT c_personid, c_name_chn, c_index_year, c_birthyear, c_deathyear,
                   c_fl_earliest_year, c_fl_latest_year, c_index_addr_id, c_dy,
                   c_ethnicity_code, c_surname_chn, c_mingzi_chn
            FROM BIOG_MAIN WHERE c_dy = 20
            """,
            conn,
        )
        banners = pd.read_sql_query(
            """
            SELECT DISTINCT a.c_personid, ac.c_name_chn AS banner_addr
            FROM BIOG_ADDR_DATA a JOIN ADDR_CODES ac ON ac.c_addr_id = a.c_addr_id
            WHERE a.c_addr_type = 13
            """,
            conn,
        )
        aliases = pd.read_sql_query("SELECT c_personid, c_alt_name_chn FROM ALTNAME_DATA", conn)
        merged = pd.read_sql_query(
            "SELECT c_personid, c_merged_from_personid FROM MERGED_PERSON_DATA", conn
        )
        entries = pd.read_sql_query(
            """
            SELECT e.c_personid, c.c_entry_desc_chn
            FROM ENTRY_DATA e JOIN ENTRY_CODES c ON c.c_entry_code = e.c_entry_code
            """,
            conn,
        )
    finally:
        conn.close()

    frame = frame.rename(columns={"c_personid": "cbdb_personid"})
    frame["province_norm"] = frame["c_index_addr_id"].map(lookup).map(normalize_province)
    banner_map: dict[int, set[str]] = {}
    for person_id, addr in zip(banners["c_personid"], banners["banner_addr"]):
        label = next(
            (value for keyword, value in BANNER_ETHNICITY.items() if str(addr).startswith(keyword)),
            "banner_unspecified",
        )
        banner_map.setdefault(int(person_id), set()).add(label)
    frame["banner_group"] = frame["cbdb_personid"].map(
        lambda person_id: "|".join(sorted(banner_map.get(int(person_id), set()))) or None
    )
    alias_map: dict[int, set[str]] = {}
    for person_id, alias in zip(aliases["c_personid"], aliases["c_alt_name_chn"]):
        if isinstance(alias, str) and alias.strip():
            alias_map.setdefault(int(person_id), set()).add(alias.strip())
    simplify, variants = _name_folding()
    frame["alt_names"] = frame["cbdb_personid"].map(
        lambda person_id: "|".join(sorted(alias_map.get(int(person_id), set()))) or None
    )
    frame["alt_norms"] = [
        "|".join(sorted({normalize_name(alias, simplify, variants) or "" for alias in str(value).split("|")}))
        if isinstance(value, str)
        else None
        for value in frame["alt_names"]
    ]
    merged_map: dict[int, set[int]] = {}
    for surviving, absorbed in zip(merged["c_personid"], merged["c_merged_from_personid"]):
        merged_map.setdefault(int(surviving), set()).add(int(absorbed))
    frame["merged_from"] = frame["cbdb_personid"].map(
        lambda person_id: "|".join(sorted(str(other) for other in merged_map.get(int(person_id), set())))
        or None
    )
    degree_best: dict[int, tuple[str, int]] = {}
    for person_id, desc in zip(entries["c_personid"], entries["c_entry_desc_chn"]):
        degree, rank = classify_entry_desc(desc)
        current = degree_best.get(int(person_id))
        if current is None or rank > current[1]:
            degree_best[int(person_id)] = (degree, rank)
    frame["degree_cbdb"] = frame["cbdb_personid"].map(
        lambda person_id: degree_best.get(int(person_id), ("unknown", 0))[0]
    )
    frame["degree_rank"] = frame["cbdb_personid"].map(
        lambda person_id: degree_best.get(int(person_id), ("unknown", 0))[1]
    )
    frame["name_norm"] = [normalize_name(value, simplify, variants) for value in frame["c_name_chn"]]
    # a usable career window, honest about what is missing
    frame["year_start"] = frame["c_fl_earliest_year"].fillna(
        frame["c_index_year"].fillna(frame["c_birthyear"])
    )
    frame["year_end"] = frame["c_fl_latest_year"].fillna(
        frame["c_index_year"].fillna(frame["c_deathyear"])
    )
    PROCESSED_V02_DIR.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(CBDB_PERSONS, index=False)
    return frame


def build_cgedq_coobservations(*, force: bool = False) -> pd.DataFrame:
    """Cached table of same-office/same-edition CGED-Q person_id pairs."""
    if COOBSERVATIONS.exists() and not force:
        return pd.read_parquet(COOBSERVATIONS)
    from qing_elite.v02.linkage_pairs import build_coobservations

    cfg = load_offices()
    classifier = CgedqOfficeClassifier(cfg)
    frame = load_d_layer(CGEDQ_TAB, classifier, year_min=CGEDQ_YEAR_MIN, year_max=CGEDQ_YEAR_MAX)
    table = build_coobservations(frame)
    PROCESSED_V02_DIR.mkdir(parents=True, exist_ok=True)
    table.to_parquet(COOBSERVATIONS, index=False)
    return table


__all__ = [
    "build_cgedq_persons",
    "build_cbdb_persons",
    "build_cgedq_coobservations",
    "province_lookup",
    "CGEDQ_PERSONS",
    "CBDB_PERSONS",
    "COOBSERVATIONS",
]
