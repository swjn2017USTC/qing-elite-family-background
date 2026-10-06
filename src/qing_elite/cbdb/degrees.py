"""CBDB entry-code -> degree classification (P02).

CBDB records entry into office through hundreds of ``ENTRY_CODES``. The study
needs a coarse, auditable degree ladder, so the rules below are explicit and are
dumped to ``output/tables/p02_cbdb_degree_map.csv`` for review.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

# Ordered rules: the first matching keyword group wins.
DEGREE_RULES: tuple[tuple[tuple[str, ...], str, int], ...] = (
    (("武進士", "武舉", "武科"), "武科", 7),
    (("進士", "狀元", "榜眼", "探花", "傳臚", "庶吉士"), "進士", 6),
    (("舉人", "鄉貢", "鄉試"), "舉人", 5),
    (("貢生", "貢監", "拔貢", "優貢", "歲貢", "恩貢", "副貢", "副榜", "例貢", "納貢"), "貢生", 4),
    (("監生", "蔭生", "廕生", "官生", "例監", "捐監", "恩監", "優監"), "監生", 3),
    (("生員", "庠生", "秀才", "附生", "廩生", "增生", "縣學", "府學", "州學", "儒學"), "生員", 2),
    (("行伍", "軍功", "議敘", "捐", "吏員", "襲職", "世襲", "世職", "廕襲", "難廕"), "其他入仕", 1),
)

DEGREE_LADDER = ["unknown", "其他入仕", "生員", "監生", "貢生", "舉人", "進士", "武科"]


@dataclass(frozen=True, slots=True)
class DegreeMapping:
    entry_code: int
    entry_desc_chn: str
    degree: str
    rank: int


def classify_entry_desc(desc: str | None) -> tuple[str, int]:
    """Map one CBDB entry description to ``(degree, rank)``."""
    if not desc:
        return "unknown", 0
    for keywords, degree, rank in DEGREE_RULES:
        if any(keyword in desc for keyword in keywords):
            return degree, rank
    return "unknown", 0


def entry_code_mapping(conn: sqlite3.Connection) -> list[DegreeMapping]:
    """Classify every ENTRY_CODES row once, for the audit table."""
    rows = conn.execute(
        "SELECT c_entry_code, c_entry_desc_chn FROM ENTRY_CODES ORDER BY c_entry_code"
    ).fetchall()
    mapped: list[DegreeMapping] = []
    for entry_code, desc in rows:
        degree, rank = classify_entry_desc(desc)
        mapped.append(DegreeMapping(int(entry_code), desc or "", degree, rank))
    return mapped
