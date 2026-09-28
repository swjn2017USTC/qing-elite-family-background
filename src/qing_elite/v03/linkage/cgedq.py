"""CGED-Q release access, ``person_id`` validation and the legacy↔official crosswalk (U06R).

Three things happen here, in this order:

1. **load** the frozen public release with the ragged-row handling the file needs (the
   upstream ``.tab`` is not strictly rectangular: ~1% of rows are short and must be padded
   rather than dropped);
2. **validate** the official ``person_id`` — uniqueness of records, id-space shape, coverage
   per period, cross-edition consistency — and record what the user guide does and does not
   say about it;
3. **crosswalk** the v0.2 legacy ids to the official ones, so the old dedupe survives as an
   audit comparison instead of being silently replaced.

Nothing here writes to the release; it is read-only.
"""

from __future__ import annotations

import functools
import hashlib
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
import yaml

from qing_elite.utils.config import PROJECT_ROOT

LINKAGE_YAML = PROJECT_ROOT / "config" / "v03" / "linkage.yaml"
CGEDQ_TAB = PROJECT_ROOT / "data" / "raw" / "cgeq" / "cgedq_jsl_public_1760-1912_personid_2026-08-28.tab"
LEGACY_DIR = PROJECT_ROOT / "data" / "interim_v03" / "legacy_v02"

#: The v0.1/v0.2 frozen release hash, so a download can be proven identical.
FROZEN_TAB_SHA256 = "92d74b4dbca2de3ad67a502f2cd2e14815d4ba0cc27776b1c06063873928d476"


class ReleaseError(ValueError):
    """Raised when the CGED-Q release is missing, altered or unusable."""


@functools.lru_cache(maxsize=None)
def load_config(path: Path | None = None) -> dict[str, Any]:
    with (path or LINKAGE_YAML).open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def sha256(path: Path, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(chunk):
            digest.update(block)
    return digest.hexdigest()


def release_view(con: duckdb.DuckDBPyConnection, tab: Path | None = None) -> None:
    """Create the ``jsl`` view (padded, all-varchar) used by every query here."""
    tab = tab or CGEDQ_TAB
    if not Path(tab).exists():
        raise ReleaseError(f"CGED-Q release not present: {tab}")
    con.execute(
        f"""
        CREATE OR REPLACE VIEW jsl AS
        SELECT * FROM read_csv_auto('{tab}', delim='\t', header=true, all_varchar=true,
                                    strict_mode=false, quote='', null_padding=true)
        """
    )


PERIOD_CASE = """
CASE WHEN TRY_CAST(阳历年份 AS INT) < 1850 THEN 'period_1760_1798'
     WHEN TRY_CAST(阳历年份 AS INT) < 1900 THEN 'period_1850_1864'
     WHEN TRY_CAST(阳历年份 AS INT) >= 1900 THEN 'period_1900_1912'
     ELSE 'unknown' END
"""


def validate_release(*, tab: Path | None = None, con: duckdb.DuckDBPyConnection | None = None) -> dict[str, Any]:
    """Check the release hash and the official ``person_id`` semantics."""
    tab = tab or CGEDQ_TAB
    if not Path(tab).exists():
        raise ReleaseError(f"CGED-Q release not present: {tab}")
    digest = sha256(Path(tab))
    con = con or duckdb.connect()
    release_view(con, tab)
    q = lambda sql: con.execute(sql).fetchdf()  # noqa: E731

    totals = q(
        f"""
        SELECT COUNT(*) AS records,
               COUNT(DISTINCT record_number) AS distinct_record_numbers,
               COUNT(DISTINCT person_id) AS persons,
               SUM(CASE WHEN person_id IS NULL OR person_id = '' THEN 1 ELSE 0 END) AS records_without_id
        FROM jsl
        """
    ).iloc[0]
    per_period = q(
        f"SELECT {PERIOD_CASE} AS period, COUNT(*) AS records, COUNT(DISTINCT person_id) AS persons,"
        f" SUM(CASE WHEN person_id IS NULL OR person_id = '' THEN 1 ELSE 0 END) AS records_without_id"
        " FROM jsl GROUP BY 1 ORDER BY 1"
    )
    prefixes = q(
        "SELECT LEFT(person_id, 1) AS prefix, COUNT(DISTINCT person_id) AS ids, COUNT(*) AS records"
        " FROM jsl WHERE person_id IS NOT NULL AND person_id <> '' GROUP BY 1 ORDER BY 1"
    )
    multi_period = q(
        f"SELECT COUNT(*) AS persons_in_multiple_periods FROM ("
        f"  SELECT person_id FROM jsl WHERE person_id IS NOT NULL AND person_id <> ''"
        f"  GROUP BY 1 HAVING COUNT(DISTINCT {PERIOD_CASE}) > 1)"
    ).iloc[0, 0]
    return {
        "tab": str(Path(tab).relative_to(PROJECT_ROOT)),
        "sha256": digest,
        "matches_v01_frozen_hash": digest == FROZEN_TAB_SHA256,
        "records": int(totals["records"]),
        "distinct_record_numbers": int(totals["distinct_record_numbers"]),
        "record_number_is_unique": int(totals["records"]) == int(totals["distinct_record_numbers"]),
        "distinct_person_ids": int(totals["persons"]),
        "records_without_person_id": int(totals["records_without_id"]),
        "by_period": per_period.to_dict(orient="records"),
        "by_id_prefix": prefixes.to_dict(orient="records"),
        "persons_in_multiple_periods": int(multi_period),
        "guide_semantics": (
            "用户指南 v4 §5.4.16：PersonID 是团队成员用距离算法生成的内部连接变量，"
            "「其形式类似于个人证件号码，具有唯一性，可用于追踪官员的任职记录」；"
            "指南同时说明模糊匹配阈值会影响分组、中文异体字会影响匹配。"
            "**指南未解释 N*/S* 前缀差异** → 本阶段按经验描述两个 id 空间，不作语义断言。"
        ),
    }


def load_legacy_canonical() -> pd.DataFrame:
    """v0.2 dedupe output, recovered from git history (read-only audit input)."""
    path = LEGACY_DIR / "cgedq_canonical.parquet"
    if not path.exists():
        raise ReleaseError(
            f"legacy canonical table missing: {path} (recover it from git history, see U06R.md)"
        )
    return pd.read_parquet(path)


def build_crosswalk(*, tab: Path | None = None, con: duckdb.DuckDBPyConnection | None = None) -> pd.DataFrame:
    """Map every legacy v0.2 id to the official ``person_id`` and flag how they differ.

    ``relation``:
      ``same_id``        the legacy id string is already the official id (no merge happened)
      ``merged_by_v02``  v0.2 merged this member into a canonical id; the official id is kept
      ``absent``         the legacy id does not occur in the release (id space drift)
    """
    con = con or duckdb.connect()
    release_view(con, tab)
    canonical = load_legacy_canonical()
    con.register("canon", canonical)
    frame = con.execute(
        """
        SELECT c.cgedq_person_id AS legacy_person_id,
               c.cgedq_canonical_id AS legacy_canonical_id,
               c.merged AS merged_by_v02,
               c.group_size AS legacy_group_size,
               j.person_id AS official_person_id,
               CASE
                 WHEN j.person_id IS NULL THEN 'absent'
                 WHEN c.cgedq_person_id = c.cgedq_canonical_id THEN 'same_id'
                 ELSE 'merged_by_v02'
               END AS relation
        FROM canon c
        LEFT JOIN (SELECT DISTINCT person_id FROM jsl WHERE person_id IS NOT NULL) j
               ON j.person_id = c.cgedq_person_id
        """
    ).fetchdf()
    return frame


def crosswalk_summary(crosswalk: pd.DataFrame) -> dict[str, Any]:
    return {
        "legacy_ids": int(len(crosswalk)),
        "relations": crosswalk["relation"].value_counts().to_dict(),
        "official_ids_matched": int(crosswalk["official_person_id"].notna().sum()),
        "merged_groups": int(
            crosswalk.loc[crosswalk["relation"] == "merged_by_v02", "legacy_canonical_id"].nunique()
        ),
        "max_group_size": int(crosswalk["legacy_group_size"].max()),
    }


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - thin CLI
    import argparse
    import json

    parser = argparse.ArgumentParser(description="validate the CGED-Q release and build the crosswalk")
    parser.add_argument("--out", default="data/processed_v03/linkage_crosswalk.parquet")
    args = parser.parse_args(argv)
    con = duckdb.connect()
    validation = validate_release(con=con)
    print(json.dumps(validation, ensure_ascii=False, indent=2)[:2000])
    crosswalk = build_crosswalk(con=con)
    out = PROJECT_ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    crosswalk.to_parquet(out, index=False)
    print(json.dumps(crosswalk_summary(crosswalk), ensure_ascii=False, indent=2))
    print(f"crosswalk -> {out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
