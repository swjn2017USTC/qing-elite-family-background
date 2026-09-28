"""CGED-Q JSL D-layer build: quarterly de-duplication and person aggregation (P02).

The public JSL release is a quarterly roster: the same holder of the same post is
re-listed every season it appears in, and one person can hold several posts in the
same season. Nothing here treats a repeated observation as a new appointment;
spells are collapsed and the raw observation count is kept for audit.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from qing_elite.cgedq.offices import CgedqOfficeClassifier

# Columns copied verbatim from the release into the D-layer frame.
CGEDQ_COLUMNS = {
    "阳历年份": "year_decimal",
    "季节号": "season",
    "序号": "sequence_no",
    "record_number": "record_number",
    "person_id": "person_id",
    "官职一": "office_raw",
    "官职二": "office_raw_2",
    "地区": "region",
    "地名_题头": "place_alias",
    "官缺等级": "post_class",
    "缺分": "post_grade",
    "机构一": "institution_1",
    "机构二": "institution_2",
    "姓": "surname",
    "名": "given_name",
    "籍贯省": "native_province_raw",
    "籍贯县": "native_county_raw",
    "原籍省": "origin_province_raw",
    "旗分": "banner_raw",
    "出身一": "degree_raw",
    "出身二": "degree_raw_2",
    "身份一": "status_1",
    "身份二": "status_2",
    "科年一": "exam_year_1",
    "選任方式": "selection_mode",
    "铨选方式": "appointment_route",
    "书名": "book_title",
    "版本年代": "edition_reign",
    "版本季节": "edition_season",
}

VACANT_NAME_MARKERS = ("空白", "塗黑", "涂黑")

# Banner strings in the source mix 正/廂 with 黃/黄; fold them to one spelling.
BANNER_NORMALIZATION = {"廂": "鑲", "黄": "黃"}


def load_d_layer(
    tab_path: Path | str,
    classifier: CgedqOfficeClassifier,
    year_min: int = 1760,
    year_max: int = 1798,
) -> pd.DataFrame:
    """Load the 1760-1798 slice with non-vacant, identified posts and classify offices."""
    columns = ", ".join(f'"{src}"' for src in CGEDQ_COLUMNS)
    markers = ",".join(f"'{marker}'" for marker in VACANT_NAME_MARKERS)
    query = f"""
        SELECT {columns}
        FROM read_csv('{tab_path}', delim='\\t', header=true, quote='', escape='',
                      strict_mode=false, ignore_errors=true, all_varchar=true)
        WHERE CAST(FLOOR(CAST("阳历年份" AS DOUBLE)) AS INT) BETWEEN {year_min} AND {year_max}
          AND "名" NOT IN ({markers})
          AND person_id IS NOT NULL AND person_id <> ''
    """
    con = duckdb.connect()
    try:
        frame = con.sql(query).df()
    finally:
        con.close()
    frame = frame.rename(columns=CGEDQ_COLUMNS)
    frame["year_decimal"] = pd.to_numeric(frame["year_decimal"], errors="coerce")
    frame["year"] = frame["year_decimal"].astype("float64").floordiv(1).astype("Int64")
    # person_id is an opaque team-assigned string ("N…" / "S…"), not a number.
    frame["person_id"] = frame["person_id"].astype(str).str.strip()
    frame = frame[frame["person_id"].notna() & frame["person_id"].ne("")].copy()

    classifications = [classifier.classify(value) for value in frame["office_raw"]]
    frame["office_core"] = [c.core if c.tier == "D" else None for c in classifications]
    frame["office_category"] = [c.category for c in classifications]
    frame["office_tier"] = [c.tier for c in classifications]
    frame["is_acting"] = [c.is_acting for c in classifications]
    frame["is_concurrent"] = [c.is_concurrent for c in classifications]
    frame["banner_std"] = frame["banner_raw"].map(_normalize_banner)
    # Edition positions come from the whole slice, never from a filtered subset:
    # otherwise filtering rows first would silently reshape appointment spells.
    frame["edition_pos"] = frame["year_decimal"].map(edition_positions(frame["year_decimal"]))
    return frame


def edition_positions(year_decimals: pd.Series) -> dict[float, int]:
    """Position of each published edition in the release's own edition sequence."""
    values = sorted({float(value) for value in year_decimals.dropna().unique()})
    return {value: index for index, value in enumerate(values)}


def _normalize_banner(value: object) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return None
    for source, target in BANNER_NORMALIZATION.items():
        text = text.replace(source, target)
    return text


def d_layer_appointments(frame: pd.DataFrame) -> pd.DataFrame:
    """Collapse quarterly repetition into appointment spells.

    A spell is a maximal run of **consecutive published editions** in which the
    same person holds the same core office. The public release publishes only 16
    season-years for 1760-1798, so "consecutive" is defined on the release's own
    edition sequence (``edition_pos``, attached by :func:`load_d_layer`), not on the
    calendar: a person observed in 1761.5 and next in 1765.0 is treated as two
    tenures, not one continuous one. 兼任 (a second post in the same edition) stays
    as a separate spell.
    """
    tier_d = frame[frame["office_tier"] == "D"].copy()
    if tier_d.empty:
        raise RuntimeError("no tier-D rows were classified in the CGED-Q slice")
    if "edition_pos" not in tier_d.columns:
        raise ValueError("edition_pos is missing; load the slice through load_d_layer()")
    tier_d = tier_d.sort_values(["person_id", "office_core", "edition_pos"])
    gap = tier_d.groupby(["person_id", "office_core"])["edition_pos"].diff()
    tier_d["spell_break"] = gap.gt(1).fillna(False).astype(int).cumsum()

    grouped = tier_d.groupby(["person_id", "office_core", "spell_break"], sort=False)
    spells = grouped.agg(
        first_year_decimal=("year_decimal", "min"),
        last_year_decimal=("year_decimal", "max"),
        first_year=("year", "min"),
        last_year=("year", "max"),
        first_edition_pos=("edition_pos", "min"),
        last_edition_pos=("edition_pos", "max"),
        seasons=("year_decimal", "nunique"),
        n_observations=("record_number", "size"),
        first_record_number=("record_number", "first"),
        last_record_number=("record_number", "last"),
        is_acting=("is_acting", "any"),
        is_concurrent=("is_concurrent", "any"),
        office_category=("office_category", "first"),
        region=("region", "first"),
        post_class=("post_class", "first"),
        post_grade=("post_grade", "first"),
        institution_1=("institution_1", "first"),
        office_raw=("office_raw", "first"),
    ).reset_index()

    # 兼任 also occurs when one person holds two different posts in the same edition
    # even without a 兼 marker in the text.
    same_edition = (
        tier_d.dropna(subset=["edition_pos"])
        .groupby(["person_id", "edition_pos"])["office_core"]
        .nunique()
    )
    multi_post = set(same_edition[same_edition > 1].index.get_level_values("person_id"))
    spells["is_concurrent"] = spells["is_concurrent"] | spells["person_id"].isin(multi_post)
    spells["spell_gaps"] = (
        spells["last_edition_pos"] - spells["first_edition_pos"] + 1 - spells["seasons"]
    ).astype("int64")
    spells["tenure_ordinal"] = spells.groupby(["person_id", "office_core"]).cumcount() + 1
    return spells


def _gaps_table(tier_d: pd.DataFrame) -> pd.DataFrame:
    """Missing quarterly observations inside each (person, office) span."""
    ordered = tier_d.assign(season_pos=_season_index(tier_d["year_decimal"]))
    return (
        ordered.groupby(["person_id", "office_core"])["season_pos"]
        .agg(span=lambda values: int(values.max() - values.min() + 1), seen="nunique")
        .reset_index()
        .assign(spell_gaps=lambda frame: frame["span"] - frame["seen"])
        .loc[:, ["person_id", "office_core", "spell_gaps"]]
    )


def _season_index(year_decimal: pd.Series) -> pd.Series:
    """Map the release's decimal year encoding onto a monotone quarterly index."""
    numeric = pd.to_numeric(year_decimal, errors="coerce")
    return (numeric * 4).round().astype("Int64")


def person_level(frame: pd.DataFrame, spells: pd.DataFrame) -> pd.DataFrame:
    """Aggregate D-layer persons, keeping the most frequent attribute values."""
    persons = frame.groupby("person_id")
    records: list[dict[str, Any]] = []
    for person_id, rows in persons:
        rows = rows.sort_values(["year_decimal", "season"])
        surname = _mode(rows["surname"])
        given = _mode(rows["given_name"])
        name = f"{surname or ''}{given or ''}" or None
        core_counts = rows.loc[rows["office_tier"] == "D", "office_core"].value_counts()
        records.append(
            {
                "cgedq_person_id": str(person_id),
                "name_chn": name,
                "surname": surname,
                "given_name": given,
                "name_has_surname": bool(surname),
                "native_province": _mode(rows["native_province_raw"]),
                "native_county": _mode(rows["native_county_raw"]),
                "origin_province": _mode(rows["origin_province_raw"]),
                "banner_std": _mode(rows["banner_std"]),
                "status_1": _mode(rows["status_1"]),
                "status_2": _mode(rows["status_2"]),
                "degree_raw": _mode(rows["degree_raw"]),
                "degree_raw_2": _mode(rows["degree_raw_2"]),
                "primary_office_core": core_counts.index[0] if len(core_counts) else None,
                "n_distinct_office_core": int(core_counts.size),
                "n_observations": int(len(rows)),
                "n_seasons": int(rows["year_decimal"].nunique()),
                "first_year_decimal": float(rows["year_decimal"].min()),
                "last_year_decimal": float(rows["year_decimal"].max()),
                "first_year": int(rows["year"].min()) if rows["year"].notna().any() else None,
                "last_year": int(rows["year"].max()) if rows["year"].notna().any() else None,
            }
        )
    result = pd.DataFrame.from_records(records)
    spell_counts = spells.groupby("person_id").size().rename("n_appointment_spells")
    result = result.merge(
        spell_counts, left_on="cgedq_person_id", right_index=True, how="left"
    )
    result["n_appointment_spells"] = result["n_appointment_spells"].fillna(0).astype("int64")
    return result


def _mode(series: pd.Series) -> str | None:
    values = series.dropna().astype(str)
    values = values[values.str.strip() != ""]
    values = values[values.str.lower() != "nan"]
    if values.empty:
        return None
    return str(values.value_counts().idxmax())


def load_chushen_recodes(path: Path) -> pd.DataFrame:
    """Degree recode table shipped with the release (出身 -> category/order)."""
    frame = pd.read_csv(path, sep="\t", dtype=str)
    return frame


def degree_lookup(
    recodes: pd.DataFrame, category_rank: Mapping[str, int]
) -> dict[str, tuple[str, int]]:
    """Map every raw 出身 spelling to ``(category, rank)`` via the official recodes.

    ``chushen_order`` in the released recode table is a *category label*, not a
    number, so the rank comes from ``category_rank`` (config/offices.yaml). Reading
    it as a number used to yield an empty lookup, which silently left every JSL
    degree unknown.
    """
    lookup: dict[str, tuple[str, int]] = {}
    for _, row in recodes.iterrows():
        category = row.get("chushen_category")
        if not isinstance(category, str) or not category.strip():
            continue
        rank = int(category_rank.get(category.strip(), 0))
        for column in ("出身一", "出身二", "chushen_1", "chushen_2", "chushen"):
            raw = row.get(column)
            if isinstance(raw, str) and raw.strip():
                lookup.setdefault(raw.strip(), (category.strip(), rank))
    return lookup
