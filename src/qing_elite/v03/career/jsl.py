"""Longitudinal career events from CGED-Q (JSL) plus CBDB postings (U08R).

Two sources, one event table, and the provenance rules that keep them apart:

* **JSL (CGED-Q)** is the primary source: quarterly rosters give an event's start/end as the
  span of consecutive seasons in which the person is recorded under the same core office, so
  acting/expectant status and selection method come from the roster itself.
* **CBDB** supplies postings for people the rosters do not cover (measured: only 2 of the 300
  pilot persons have an accepted JSL link). Appendix-style, with its own document id.
* **Biographies** are not used here. The U05R OCR arm was demoted to identity-only, so there is
  no attributed resume text to fold in; that is a documented gap, not an omission.

A CBDB posting never silently overrides a JSL posting: overlapping events from different
sources are both kept and flagged as a conflict (requirement 4).
"""

from __future__ import annotations

import hashlib
from typing import Any

import duckdb
import pandas as pd

from qing_elite.v03.career.offices import appointment_flags
from qing_elite.v03.linkage.cgedq import CGEDQ_TAB

#: JSL columns we carry through, and their canonical names.
JSL_COLUMNS = {
    "person_id": "person_id",
    "阳历年份": "year",
    "季节号": "season",
    "官职一": "office_raw",
    "官职二": "office_secondary",
    "机构一": "institution",
    "選任方式": "selection_method",
    "缺分": "post_importance",
    "官缺等级": "post_characteristics",
    "籍贯省": "province",
    "版本年号": "edition_reign",
    "版本季节": "edition_season",
    "record_number": "record_number",
}


def load_jsl_events(*, start_year: int, end_year: int, path: str | None = None) -> pd.DataFrame:
    """Quarterly JSL observations in the window, one row per record."""
    con = duckdb.connect()
    tab = path or str(CGEDQ_TAB)
    con.execute(
        f"""
        CREATE OR REPLACE VIEW jsl AS
        SELECT * FROM read_csv_auto('{tab}', delim='\t', header=true, all_varchar=true,
                                    strict_mode=false, quote='', null_padding=true)
        """
    )
    frame = con.execute(
        f"""
        SELECT {", ".join(f'"{source}" AS {target}' for source, target in JSL_COLUMNS.items())}
        FROM jsl
        WHERE person_id IS NOT NULL AND person_id <> ''
          AND TRY_CAST(阳历年份 AS INT) BETWEEN {int(start_year)} AND {int(end_year)}
        """
    ).fetchdf()
    con.close()
    # 阳历年份 in the release is fractional (1760.75 = 1760 season 4); keep it as a sort key
    # and derive the integer year separately, never by rounding the fraction away silently
    frame["year_float"] = pd.to_numeric(frame["year"], errors="coerce")
    frame["year"] = frame["year_float"].apply(
        lambda value: int(value) if pd.notna(value) else pd.NA
    )
    frame["year"] = pd.array(frame["year"], dtype="Int64")
    frame["season"] = pd.array(pd.to_numeric(frame["season"], errors="coerce"), dtype="Int64")
    return frame


def collapse_spells(observations: pd.DataFrame) -> pd.DataFrame:
    """One event per (person, office) run of consecutive seasons.

    A person recorded in the same core office in 1765-spring, 1765-summer and 1766-spring is
    one posting, not three. A gap re-starts the spell — re-appointment is a career event.
    """
    if observations.empty:
        return pd.DataFrame(
            columns=[
                "event_id", "person_id", "office_raw", "start_year", "end_year",
                "n_observations", "selection_method", "province", "source_id",
            ]
        )
    frame = observations.sort_values(["person_id", "office_raw", "year_float"]).copy()
    frame["quarter_index"] = frame["year_float"].fillna(0) * 4
    previous = frame.groupby(["person_id", "office_raw"])["quarter_index"].shift()
    frame["new_spell"] = (previous.isna()) | ((frame["quarter_index"] - previous) > 1)
    frame["spell_id"] = frame.groupby(["person_id", "office_raw"])["new_spell"].cumsum()
    grouped = frame.groupby(["person_id", "office_raw", "spell_id"])
    spells = grouped.agg(
        start_year=("year", "min"),
        end_year=("year", "max"),
        n_observations=("record_number", "size"),
        selection_method=("selection_method", lambda values: values.dropna().mode().iloc[0] if values.notna().any() else None),
        province=("province", lambda values: values.dropna().iloc[0] if values.notna().any() else None),
        post_importance=("post_importance", lambda values: values.dropna().iloc[0] if values.notna().any() else None),
    ).reset_index()
    spells["source_id"] = "cgedq_jsl"
    spells["event_id"] = [
        "jsl-"
        + hashlib.sha256(
            f"{row.person_id}|{row.office_raw}|{row.spell_id}|{row.start_year}".encode()
        ).hexdigest()[:12]
        for row in spells.itertuples(index=False)
    ]
    return spells


def cbdb_events(career_events: pd.DataFrame) -> pd.DataFrame:
    """CBDB postings reshaped into the same event schema (they already carry a document id)."""
    frame = career_events.copy()
    frame["source_id"] = "cbdb"
    frame["n_observations"] = 1
    frame["post_importance"] = None
    return frame.rename(
        columns={
            "office_raw": "office_raw",
            "start_year": "start_year",
            "end_year": "end_year",
        }
    ).loc[
        :,
        [
            "event_id",
            "person_id",
            "office_raw",
            "start_year",
            "end_year",
            "n_observations",
            "selection_method",
            "province",
            "post_importance",
            "source_id",
            "assertion_state",
            "evidence_assertion_id",
            "source_document_id",
            "review_status",
        ],
    ]


def flag_source_conflicts(events: pd.DataFrame) -> pd.DataFrame:
    """Flag events from different sources that claim the same person in the same year.

    The JSL roster and a CBDB posting can both describe 1765 for one person; when they name
    different offices that is a conflict to review, not a fact to merge.
    """
    frame = events.copy()
    frame["conflict_with_other_source"] = False
    if frame["source_id"].nunique() < 2:
        return frame
    key = ["person_id"]
    grouped = frame.groupby(key)
    for _, group in grouped:
        if group["source_id"].nunique() < 2:
            continue
        for row in group.itertuples(index=False):
            if pd.isna(row.start_year):
                continue
            others = group.loc[
                (group["source_id"] != row.source_id)
                & (group["start_year"].notna())
                & (group["start_year"] <= (row.end_year if pd.notna(row.end_year) else row.start_year))
                & (group["end_year"].fillna(group["start_year"]) >= row.start_year)
            ]
            different_office = others.loc[others["office_raw"].astype(str) != str(row.office_raw)]
            if len(different_office):
                frame.loc[frame["event_id"] == row.event_id, "conflict_with_other_source"] = True
    return frame


def summary(events: pd.DataFrame) -> dict[str, Any]:
    return {
        "events": int(len(events)),
        "persons": int(events["person_id"].nunique()),
        "by_source": events["source_id"].value_counts().to_dict(),
        "with_start_year": int(events["start_year"].notna().sum()),
        "conflicts_across_sources": int(events["conflict_with_other_source"].sum())
        if "conflict_with_other_source" in events
        else 0,
    }
