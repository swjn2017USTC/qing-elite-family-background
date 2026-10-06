"""Record-level source frames and candidate generation for U06R.

The v0.2 modules are kept for the audit comparison, but they hard-code the old release
filename (``cbdb_20260912.sqlite3``) and the old CGED-Q path. U06R reads the *current* frozen
releases directly:

* JSL person frame — built by the v0.2 loader (`build_cgedq_persons`), which is a pure
  function of the released ``.tab`` and already reproduces the released ids;
* CBDB person frame — built here with DuckDB over the frozen SQLite, since the v0.2 loader
  cannot be pointed at a different release without editing frozen code;
* candidate pairs — name blocking (v0.2 rule) **plus pinyin blocking**, which the upstream
  ML-linkage paper uses to catch variant characters that a character-exact block misses.

Nothing here decides anything: a candidate pair is a *question*, and the matchers answer it.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

from qing_elite.linkage.names import normalize_name, normalize_province
from qing_elite.utils.config import PROJECT_ROOT, load_offices
from qing_elite.v03.linkage.cgedq import CGEDQ_TAB
from qing_elite.v03.linkage.features import pinyin_key

CBDB_SQLITE = PROJECT_ROOT / "data" / "raw" / "cbdb" / "cbdb_20260926.sqlite3"

#: Degree classes ordered strongest first; used for the ``degree_rank`` comparison column.
DEGREE_RANK_SQL = """
CASE
  WHEN ec.c_entry_desc_chn LIKE '%進士%' THEN 6
  WHEN ec.c_entry_desc_chn LIKE '%舉人%' THEN 5
  WHEN ec.c_entry_desc_chn LIKE '%貢生%' THEN 4
  WHEN ec.c_entry_desc_chn LIKE '%監生%' THEN 3
  WHEN ec.c_entry_desc_chn LIKE '%生員%' OR ec.c_entry_desc_chn LIKE '%庠生%'
       OR ec.c_entry_desc_chn LIKE '%廩生%' OR ec.c_entry_desc_chn LIKE '%增生%'
       OR ec.c_entry_desc_chn LIKE '%附生%' THEN 2
  ELSE 0
END
"""


def build_cbdb_persons(*, db_path: Path | None = None, limit: int | None = None) -> pd.DataFrame:
    """CBDB Qing persons with the comparison columns the matchers need."""
    db = str(db_path or CBDB_SQLITE)
    if not Path(db).exists():
        raise FileNotFoundError(f"CBDB sqlite not present: {db}")
    con = duckdb.connect()
    limit_clause = f"LIMIT {int(limit)}" if limit else ""
    frame = con.execute(
        f"""
        WITH persons AS (
          -- population = every Qing person (c_dy = 20), the same frame v0.2 linked against.
          -- Filtering on a non-null index year would silently drop the local officials whose
          -- index year is unrecorded — the very records this stage has to resolve (measured:
          -- 58,489 of 238,257 persons had an index year in range).
          SELECT b.c_personid, b.c_name_chn, b.c_surname_chn, b.c_mingzi_chn,
                 CAST(b.c_index_year AS INTEGER) AS index_year,
                 CAST(b.c_birthyear AS INTEGER) AS birthyear,
                 CAST(b.c_deathyear AS INTEGER) AS deathyear,
                 CAST(b.c_fl_earliest_year AS INTEGER) AS fl_earliest_year,
                 CAST(b.c_fl_latest_year AS INTEGER) AS fl_latest_year,
                 CAST(b.c_dy AS INTEGER) AS c_dy
          FROM sqlite_scan('{db}','BIOG_MAIN') b
          WHERE CAST(b.c_dy AS INTEGER) = 20
        ),
        aliases AS (
          SELECT a.c_personid, LIST(DISTINCT a.c_alt_name_chn) AS alias_list
          FROM sqlite_scan('{db}','ALTNAME_DATA') a
          WHERE a.c_alt_name_chn IS NOT NULL
          GROUP BY 1
        ),
        province AS (
          SELECT p.c_personid, MIN(a.c_name_chn) AS province_norm
          FROM persons p
          LEFT JOIN sqlite_scan('{db}','BIOG_ADDR_DATA') d
                 ON d.c_personid = p.c_personid AND d.c_addr_type = 1
          LEFT JOIN sqlite_scan('{db}','ADDR_CODES') a ON a.c_addr_id = d.c_addr_id
          WHERE a.c_name_chn LIKE '%省'
          GROUP BY 1
        ),
        degree AS (
          SELECT e.c_personid, MAX({DEGREE_RANK_SQL}) AS degree_rank
          FROM sqlite_scan('{db}','ENTRY_DATA') e
          LEFT JOIN sqlite_scan('{db}','ENTRY_CODES') ec ON ec.c_entry_code = e.c_entry_code
          GROUP BY 1
        )
        SELECT p.*, pr.province_norm, COALESCE(dg.degree_rank, 0) AS degree_rank,
               al.alias_list
        FROM persons p
        LEFT JOIN province pr ON pr.c_personid = p.c_personid
        LEFT JOIN degree dg ON dg.c_personid = p.c_personid
        LEFT JOIN aliases al ON al.c_personid = p.c_personid
        {limit_clause}
        """
    ).fetchdf()
    con.close()
    # the same character folding the v0.2 candidate generator used, so the two candidate
    # sets are comparable and the recovered gold pairs are reachable by blocking
    simplify, variants = _folding()
    frame["name_norm"] = [normalize_name(value, simplify, variants) for value in frame["c_name_chn"]]
    frame["province_norm"] = frame["province_norm"].map(normalize_province)
    frame["pinyin_key"] = frame["name_norm"].fillna("").map(pinyin_key)
    frame["banner_group"] = None  # flag status is not in this release slice; unknown, never "no"
    return frame.rename(columns={"c_personid": "cbdb_personid"})


def _folding() -> tuple[dict[str, str], dict[str, str]]:
    config = load_offices()
    return config["cgedq"]["simplify_map"], config["cgedq"]["char_variants"]


def build_cgedq_persons_reference(tab: Path | None = None) -> pd.DataFrame:
    """JSL person frame from the frozen ``.tab`` (one row per released ``person_id``)."""
    tab = tab or CGEDQ_TAB
    con = duckdb.connect()
    con.execute(
        f"""
        CREATE OR REPLACE VIEW jsl AS
        SELECT * FROM read_csv_auto('{tab}', delim='\t', header=true, all_varchar=true,
                                    strict_mode=false, quote='', null_padding=true)
        """
    )
    frame = con.execute(
        """
        SELECT person_id AS cgedq_person_id,
               MIN(姓) AS surname,
               MIN(名) AS given_name,
               ANY_VALUE(姓 || 名) AS name_chn,
               MIN(籍贯省) AS province_norm,
               COUNT(*) AS n_records,
               MIN(TRY_CAST(阳历年份 AS INT)) AS first_year,
               MAX(TRY_CAST(阳历年份 AS INT)) AS last_year
        FROM jsl
        WHERE person_id IS NOT NULL AND person_id <> ''
        GROUP BY 1
        """
    ).fetchdf()
    con.close()
    simplify, variants = _folding()
    frame["name_norm"] = [normalize_name(value, simplify, variants) for value in frame["name_chn"]]
    frame["province_norm"] = frame["province_norm"].map(normalize_province)
    frame["pinyin_key"] = frame["name_norm"].fillna("").map(pinyin_key)
    return frame


def dedupe_candidates(
    frame: pd.DataFrame,
    *,
    id_column: str = "cgedq_person_id",
    block_on_pinyin: bool = True,
    block_given_name_only: bool = False,
) -> pd.DataFrame:
    """Within-source candidate pairs: same folded name (or same pinyin key), never self-pairs.

    ``block_given_name_only`` is off by default on purpose. The only pairs it would add are
    records sharing nothing but a given name (陸芳 / 李芳, different offices and years); the
    v0.2 rule merged some of them, but "same given name" is exactly the name-only inference
    this project rejected in U00/U02, so the pairs stay unreachable and the resulting
    blocking ceiling is reported instead of being papered over.

    The official ``person_id`` decides these pairs now, so this candidate set exists to
    *validate* the official grouping against independent fields, and to keep the v0.2 dedupe
    comparable as an audit baseline.
    """
    keys = ["name_norm"] + (["pinyin_key"] if block_on_pinyin else [])
    if "pinyin_key" not in frame.columns:
        frame = frame.assign(pinyin_key=frame["name_norm"].fillna("").map(pinyin_key))
    # structural key: the same given name in the same core office in the same year. The v0.2
    # rule merged such pairs even when the surname differed (its ``structural_same_edition``
    # stratum), and without this key those pairs are simply unreachable — which would make the
    # dedupe audit measure candidate generation instead of the matchers.
    if {"given_name", "primary_office_core", "first_year"}.issubset(frame.columns):
        frame = frame.assign(
            structural_key=(
                frame["given_name"].fillna("")
                + "|"
                + frame["primary_office_core"].fillna("").astype(str)
                + "|"
                + frame["first_year"].fillna(0).astype(int).astype(str)
            )
        )
        keys.append("structural_key")
    if block_given_name_only and "given_name" in frame.columns:
        frame = frame.assign(given_name_key=frame["given_name"].fillna(""))
        keys.append("given_name_key")
    frames: list[pd.DataFrame] = []
    for key in keys:
        left = frame.loc[:, [id_column, key]].rename(columns={id_column: "left_id", key: "block_key"})
        right = frame.loc[:, [id_column, key]].rename(columns={id_column: "right_id", key: "block_key"})
        merged = left.merge(right, on="block_key")
        merged = merged.loc[(merged["left_id"] < merged["right_id"]) & (merged["block_key"].astype(str).str.len() > 0)]
        merged["block_type"] = "dedupe_" + key
        frames.append(merged.loc[:, ["left_id", "right_id", "block_key", "block_type"]])
    combined = pd.concat(frames, ignore_index=True).drop_duplicates(subset=["left_id", "right_id"])
    ids = frame.set_index(id_column)
    combined["same_official_id"] = [
        bool(ids.loc[left, "official_id"] == ids.loc[right, "official_id"])
        for left, right in zip(combined["left_id"], combined["right_id"])
    ]
    combined["pair_id"] = "dedupe:" + combined["left_id"].astype(str) + "|" + combined["right_id"].astype(str)
    return combined


def candidate_pairs_by_block(
    left: pd.DataFrame,
    right: pd.DataFrame,
    *,
    left_key: str = "name_norm",
    right_key: str = "name_norm",
    left_id: str = "cgedq_person_id",
    right_id: str = "cbdb_personid",
    block_on_pinyin: bool = True,
) -> pd.DataFrame:
    """Block on the normalised name (v0.2 rule) and optionally on the pinyin key."""
    # a caller may hand us a frame that was built elsewhere (e.g. the v0.2 person frame);
    # derive the pinyin key here rather than requiring it upstream
    for frame, key_column in ((left, left_key), (right, right_key)):
        if "pinyin_key" not in frame.columns:
            frame["pinyin_key"] = frame[key_column].fillna("").map(pinyin_key)
    blocks = [(left_key, right_key, "name")]
    if block_on_pinyin:
        blocks.append(("pinyin_key", "pinyin_key", "pinyin"))
    frames: list[pd.DataFrame] = []
    for left_column, right_column, block_type in blocks:
        merged = left.loc[:, [left_id, left_column]].merge(
            right.loc[:, [right_id, right_column]], left_on=left_column, right_on=right_column
        )
        merged = merged.loc[merged[left_column].astype(str).str.len() > 0]
        merged["block_type"] = block_type
        frames.append(merged.rename(columns={left_column: "block_key"}))

    # alias blocking: CBDB records the same person under 字/號 variants, which name blocking
    # cannot reach (v0.2 had the same third path; here it follows the same folding)
    if "alias_list" in right.columns:
        exploded = right.loc[:, [right_id, "alias_list"]].explode("alias_list").dropna(subset=["alias_list"])
        exploded["alias_norm"] = [normalize_name(value, *_folding()) for value in exploded["alias_list"]]
        merged = left.loc[:, [left_id, left_key]].merge(
            exploded, left_on=left_key, right_on="alias_norm"
        )
        merged = merged.loc[merged[left_key].astype(str).str.len() > 0]
        merged["block_type"] = "alias"
        frames.append(merged.rename(columns={left_key: "block_key"}))
    combined = pd.concat(frames, ignore_index=True)
    combined = combined.drop_duplicates(subset=[left_id, right_id])
    combined["pair_id"] = (
        "cross:" + combined[left_id].astype(str) + "|" + combined[right_id].astype(str)
    )
    return combined
