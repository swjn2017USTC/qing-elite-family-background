"""U02 candidate generation: CGED-Q internal dedupe pairs and cross-source pairs.

Blocking is deliberately narrow (normalized name, plus CBDB aliases): it proposes
candidates only. Nothing here accepts a link; acceptance lives in
:mod:`qing_elite.v02.linkage` and always requires corroborating fields.
"""

from __future__ import annotations

import pandas as pd

from qing_elite.linkage.names import provinces_compatible
from qing_elite.utils.config import PROCESSED_V02_DIR

COOBSERVATIONS = PROCESSED_V02_DIR / "cgedq_coobservations.parquet"


def _compatible(left: object, right: object) -> bool | None:
    """Province compatibility; ``None`` when either side is unknown."""
    if not isinstance(left, str) or not isinstance(right, str):
        return None
    return bool(provinces_compatible(left, right))


def _same_known(left: object, right: object) -> bool | None:
    if pd.isna(left) or pd.isna(right):
        return None
    return bool(left == right)


def _overlap(left_start, left_end, right_start, right_end) -> bool | None:
    if any(pd.isna(value) for value in (left_start, left_end, right_start, right_end)):
        return None
    return min(left_end, right_end) - max(left_start, right_start) >= 0


def build_coobservations(frame: pd.DataFrame) -> pd.DataFrame:
    """Pairs of CGED-Q person_ids holding the same office in the same edition.

    The release's roster lists one holder per post, so two ids with the same
    normalized name on the same post in the same edition are the release splitting
    one person, not two officials.
    """
    working = frame.loc[:, ["person_id", "office_core", "edition_pos", "surname", "given_name"]].copy()
    working["given"] = working["given_name"].astype(str).str.strip()
    working = working[working["given"].ne("") & working["office_core"].notna()]
    grouped = working.groupby(["edition_pos", "office_core", "given"], sort=False)["person_id"]
    rows: list[dict[str, object]] = []
    for (edition, office, given), ids in grouped:
        unique = sorted({str(value) for value in ids})
        if len(unique) < 2:
            continue
        for index, left in enumerate(unique):
            for right in unique[index + 1 :]:
                rows.append(
                    {
                        "left": left,
                        "right": right,
                        "same_office_same_edition": True,
                        "edition_pos": int(edition),
                        "office_core": office,
                    }
                )
    table = pd.DataFrame.from_records(rows)
    if table.empty:
        return pd.DataFrame(
            columns=["left", "right", "same_office_same_edition", "n_shared_editions", "offices"]
        )
    table = (
        table.groupby(["left", "right"], as_index=False)
        .agg(
            same_office_same_edition=("same_office_same_edition", "max"),
            n_shared_editions=("edition_pos", "nunique"),
            offices=("office_core", lambda values: "|".join(sorted(set(values)))),
        )
    )
    return table


def dedupe_pairs(cgedq: pd.DataFrame, coobservations: pd.DataFrame | None = None) -> pd.DataFrame:
    """All within-CGED-Q candidate pairs: same normalized name, plus co-observed ids."""
    frame = cgedq.copy()
    frame["name_freq"] = frame.groupby("name_norm")["cgedq_person_id"].transform("size")

    named = frame.loc[frame["name_norm"].notna(), ["cgedq_person_id", "name_norm"]]
    blocked = named.merge(named, on="name_norm", suffixes=("_l", "_r"))
    blocked = blocked[blocked["cgedq_person_id_l"] < blocked["cgedq_person_id_r"]]
    keys = blocked.loc[:, ["cgedq_person_id_l", "cgedq_person_id_r"]].rename(
        columns={"cgedq_person_id_l": "l_cgedq_person_id", "cgedq_person_id_r": "r_cgedq_person_id"}
    )

    if coobservations is not None and len(coobservations):
        co = coobservations.loc[:, ["left", "right"]].copy()
        low = co[["left", "right"]].min(axis=1)
        high = co[["left", "right"]].max(axis=1)
        co = pd.DataFrame({"l_cgedq_person_id": low, "r_cgedq_person_id": high})
        keys = pd.concat([keys, co], ignore_index=True)
    keys = keys.drop_duplicates().reset_index(drop=True)

    left = frame.rename(columns=lambda column: f"l_{column}")
    right = frame.rename(columns=lambda column: f"r_{column}")
    pairs = keys.merge(left, on="l_cgedq_person_id", how="left").merge(
        right, on="r_cgedq_person_id", how="left"
    )
    pairs["province_compatible"] = [
        _compatible(a, b) for a, b in zip(pairs["l_province_norm"], pairs["r_province_norm"])
    ]
    pairs["degree_equal"] = [
        _same_known(a, b) for a, b in zip(pairs["l_degree_rank"], pairs["r_degree_rank"])
    ]
    pairs["banner_equal"] = [
        _same_known(a, b) for a, b in zip(pairs["l_banner_group"], pairs["r_banner_group"])
    ]
    pairs["era_overlap"] = [
        _overlap(a, b, c, d)
        for a, b, c, d in zip(
            pairs["l_first_year"], pairs["l_last_year"], pairs["r_first_year"], pairs["r_last_year"]
        )
    ]
    pairs["name_freq_max"] = pairs[["l_name_freq", "r_name_freq"]].max(axis=1)
    if coobservations is not None and len(coobservations):
        pairs = pairs.merge(
            coobservations,
            left_on=["l_cgedq_person_id", "r_cgedq_person_id"],
            right_on=["left", "right"],
            how="left",
        ).drop(columns=["left", "right"])
    for column in ("same_office_same_edition", "n_shared_editions", "offices"):
        if column not in pairs:
            pairs[column] = None
    pairs["same_office_same_edition"] = pairs["same_office_same_edition"].fillna(False).astype(bool)
    pairs["n_shared_editions"] = pairs["n_shared_editions"].fillna(0).astype(int)
    pairs["task"] = "dedupe"
    pairs["pair_id"] = "dedupe:" + pairs["l_cgedq_person_id"] + "|" + pairs["r_cgedq_person_id"]
    return pairs.reset_index(drop=True)


def cross_source_pairs(cgedq: pd.DataFrame, cbdb: pd.DataFrame) -> pd.DataFrame:
    """CGED-Q x CBDB candidate pairs (normalized name, or CBDB alias)."""
    c = cgedq[cgedq["name_norm"].notna()].copy()
    c["name_freq_cgedq"] = c.groupby("name_norm")["cgedq_person_id"].transform("size")
    b = cbdb[cbdb["name_norm"].notna()].copy()
    b["name_freq_cbdb"] = b.groupby("name_norm")["cbdb_personid"].transform("size")

    direct = c.rename(columns=lambda column: f"l_{column}").merge(
        b.rename(columns=lambda column: f"r_{column}"),
        left_on="l_name_norm",
        right_on="r_name_norm",
    )
    direct["name_match_type"] = "direct"

    alias_rows: list[pd.DataFrame] = []
    exploded = b.assign(alias=b["alt_norms"].fillna("").str.split("|")).explode("alias")
    exploded = exploded[exploded["alias"].ne("") & exploded["alias"].notna()]
    if len(exploded):
        alias_rows.append(
            c.rename(columns=lambda column: f"l_{column}").merge(
                exploded.rename(columns=lambda column: f"r_{column}"),
                left_on="l_name_norm",
                right_on="r_alias",
            ).assign(name_match_type="alias")
        )
    pairs = pd.concat([direct, *alias_rows], ignore_index=True) if alias_rows else direct
    pairs = pairs.drop_duplicates(subset=["l_cgedq_person_id", "r_cbdb_personid", "name_match_type"])
    pairs["province_compatible"] = [
        _compatible(a, b_) for a, b_ in zip(pairs["l_province_norm"], pairs["r_province_norm"])
    ]
    pairs["degree_equal"] = [
        _same_known(a, b_) for a, b_ in zip(pairs["l_degree_rank"], pairs["r_degree_rank"])
    ]
    pairs["banner_equal"] = [
        _same_known(a, b_) for a, b_ in zip(pairs["l_banner_group"], pairs["r_banner_group"])
    ]
    pairs["era_overlap"] = [
        _overlap(a, b_, c_, d)
        for a, b_, c_, d in zip(
            pairs["l_first_year"], pairs["l_last_year"], pairs["r_year_start"], pairs["r_year_end"]
        )
    ]
    pairs["raw_name_equal"] = pairs["l_name_chn"].astype(str) == pairs["r_c_name_chn"].astype(str)
    pairs["name_freq_max"] = pairs[["l_name_freq_cgedq", "r_name_freq_cbdb"]].max(axis=1)
    pairs["task"] = "cross_source"
    pairs["pair_id"] = (
        "cross:" + pairs["l_cgedq_person_id"] + "|" + pairs["r_cbdb_personid"].astype(str)
    )
    return pairs.reset_index(drop=True)


__all__ = ["build_coobservations", "dedupe_pairs", "cross_source_pairs", "COOBSERVATIONS"]
