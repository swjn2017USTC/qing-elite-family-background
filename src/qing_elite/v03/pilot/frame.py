"""Pilot frame: reproducible stratified sampling of focal persons (U05R).

The frame is built only from sources U04R confirmed as machine-accessible for this project
(`cbdb` structured records; the public-domain 同官录 scan is the OCR arm and its focal
persons come from the scan itself). Stratification follows the V0.3 sampling rule
``source × cohort × region × credential`` — **never the outcome**.

Sampling is systematic inside each stratum after sorting by ``person_id``, with a per-cell
offset derived from the stage seed, so the same seed reproduces the same people.
"""

from __future__ import annotations

import functools
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
import yaml

from qing_elite.utils.config import PROJECT_ROOT

FRAME_YAML = PROJECT_ROOT / "config" / "v03" / "pilot_frame.yaml"
CBDB_SQLITE = PROJECT_ROOT / "data" / "raw" / "cbdb" / "cbdb_20260926.sqlite3"


class FrameError(ValueError):
    """Raised when the frame configuration or the CBDB release cannot be used."""


@dataclass(frozen=True)
class Strata:
    """The stratification vocabulary (kept in config so it is reviewable, not implicit)."""

    cohorts: tuple[tuple[str, int, int], ...]
    regions: dict[str, tuple[str, ...]]
    credentials: dict[str, tuple[str, ...]]


@functools.lru_cache(maxsize=None)
def load_frame_config(path: Path | None = None) -> dict[str, Any]:
    with (path or FRAME_YAML).open(encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    for key in ("seed", "target_size", "min_per_cell", "cohorts", "regions", "credentials", "source"):
        if key not in config:
            raise FrameError(f"pilot_frame.yaml is missing '{key}'")
    if not 200 <= int(config["target_size"]) <= 500:
        raise FrameError("target_size must stay within the 200–500 pilot budget")
    return config


def _credential_class_expr(entry_join: str) -> str:
    """Map a person's entry codes to the credential classes declared in the config.

    CBDB entry descriptions carry both Chinese and English labels; the Chinese keyword is
    the one that survives translation changes, so the mapping runs on it.
    """
    return f"""
    CASE
      WHEN {entry_join} LIKE '%進士%' THEN 'jinshi'
      WHEN {entry_join} LIKE '%舉人%' THEN 'juren'
      WHEN {entry_join} LIKE '%貢生%' THEN 'gongsheng'
      WHEN {entry_join} LIKE '%監生%' THEN 'jiansheng'
      WHEN {entry_join} LIKE '%生員%' OR {entry_join} LIKE '%庠生%'
        OR {entry_join} LIKE '%廩生%' OR {entry_join} LIKE '%增生%' OR {entry_join} LIKE '%附生%'
        THEN 'shengyuan'
      ELSE 'none'
    END
    """


def _person_query(db: str, config: dict[str, Any]) -> str:
    """Population: Qing persons (c_dy=20) with an index year in range and at least one kin."""
    return f"""
    WITH raw AS (
      SELECT
        b.c_personid                       AS person_id,
        b.c_name_chn                       AS name_chn,
        CAST(b.c_index_year AS INTEGER)    AS index_year,
        (SELECT COUNT(*) FROM sqlite_scan('{db}','KIN_DATA') k WHERE k.c_personid = b.c_personid) AS n_kin,
        (SELECT LISTAGG(ec.c_entry_desc_chn, '|')
           FROM sqlite_scan('{db}','ENTRY_DATA') e
           LEFT JOIN sqlite_scan('{db}','ENTRY_CODES') ec ON ec.c_entry_code = e.c_entry_code
          WHERE e.c_personid = b.c_personid) AS entry_labels
      FROM sqlite_scan('{db}','BIOG_MAIN') b
      WHERE CAST(b.c_dy AS INTEGER) = 20
        AND CAST(b.c_index_year AS INTEGER) BETWEEN 1644 AND 1912
    )
    SELECT person_id, name_chn, index_year, n_kin,
           {_credential_class_expr('COALESCE(entry_labels, )')} AS credential_class
    FROM raw
    WHERE n_kin >= 1
    """


def _province_query(db: str) -> str:
    """Native province from the 籍貫 address, walking up to the first 省-level node."""
    return f"""
    WITH RECURSIVE
    native AS (
      SELECT d.c_personid, MIN(d.c_addr_id) AS addr_id
      FROM sqlite_scan('{db}','BIOG_ADDR_DATA') d
      WHERE d.c_addr_type = 1
      GROUP BY 1
    ),
    walk(person_id, addr_id, depth) AS (
      SELECT n.c_personid, n.addr_id, 0 FROM native n
      UNION ALL
      SELECT w.person_id, bl.c_belongs_to, w.depth + 1
      FROM walk w
      JOIN sqlite_scan('{db}','ADDR_BELONGS_DATA') bl ON bl.c_addr_id = w.addr_id
      WHERE w.depth < 4
    )
    SELECT w.person_id,
           COALESCE(
             (SELECT MIN(a.c_name_chn) FROM walk w2
                JOIN sqlite_scan('{db}','ADDR_CODES') a ON a.c_addr_id = w2.addr_id
               WHERE w2.person_id = w.person_id AND a.c_name_chn LIKE '%省'),
             (SELECT MIN(a.c_name_chn) FROM sqlite_scan('{db}','ADDR_CODES') a
               WHERE a.c_addr_id = (SELECT MIN(addr_id) FROM walk w3 WHERE w3.person_id = w.person_id)
                 AND a.c_name_chn LIKE '%省')
           ) AS province
    FROM walk w
    GROUP BY 1
    """


def _region_expr(regions: dict[str, tuple[str, ...]]) -> str:
    clauses = ["WHEN province LIKE '%" + name + "%' THEN '" + group + "'"
               for group, names in regions.items() for name in names]
    body = " ".join(clauses)
    return f"CASE {body} ELSE 'other' END"


def build_frame(
    *, db_path: Path | None = None, config_path: Path | None = None, target: int | None = None
) -> pd.DataFrame:
    """Return the sampled pilot frame with inclusion probabilities and weights."""
    config = load_frame_config(config_path)
    db = str(db_path or CBDB_SQLITE)
    if not Path(db).exists():
        raise FrameError(f"CBDB sqlite not present: {db} (download it first, see U04R)")
    con = duckdb.connect()
    people = con.execute(_person_query(db, config)).fetchdf()
    provinces = con.execute(_province_query(db)).fetchdf()
    con.close()

    frame = people.merge(provinces, on="person_id", how="left")
    frame["province"] = frame["province"].fillna("unknown")

    cohorts = tuple((str(name), int(start), int(end)) for name, start, end in
                    ((item["name"], item["start"], item["end"]) for item in config["cohorts"]))
    frame["cohort"] = "unknown"
    for name, start, end in cohorts:
        mask = frame["index_year"].between(start, end) & (frame["cohort"] == "unknown")
        frame.loc[mask, "cohort"] = name

    regions = {key: tuple(value) for key, value in config["regions"].items()}
    region_map: dict[str, str] = {}
    for group, names in regions.items():
        for name in names:
            region_map[name] = group
    frame["region_group"] = frame["province"].map(
        lambda value: next(
            (group for name, group in region_map.items() if name in str(value)), "other"
        )
    )
    frame["credential_class"] = frame["credential_class"].fillna("none")

    target_size = int(target or config["target_size"])
    min_per_cell = int(config["min_per_cell"])
    seed = int(config["seed"])
    frame["cell"] = frame["cohort"] + "|" + frame["region_group"] + "|" + frame["credential_class"]

    # proportional allocation with a floor per stratum, then trimmed to the target so the
    # sample size stays a decision, not an accident of how many strata exist
    sizes = frame.groupby("cell").size().to_dict()
    quotas = {
        cell: min(max(min_per_cell, round(target_size * size / len(frame))), size)
        for cell, size in sizes.items()
    }
    while sum(quotas.values()) > target_size:
        largest = max(quotas, key=lambda cell: (quotas[cell], cell))
        quotas[largest] -= 1

    sampled: list[pd.DataFrame] = []
    for cell in sorted(quotas):
        if quotas[cell] <= 0:
            continue
        ordered = frame.loc[frame["cell"] == cell].sort_values("person_id").reset_index(drop=True)
        quota = min(quotas[cell], len(ordered))
        offset = int(hashlib.sha256(f"{seed}:{cell}".encode()).hexdigest(), 16) % len(ordered)
        step = len(ordered) / quota
        picked = [ordered.iloc[int((offset + index * step) % len(ordered))] for index in range(quota)]
        taken = pd.DataFrame(picked).drop_duplicates(subset=["person_id"])
        taken["cell_population"] = len(ordered)
        taken["cell_sample"] = len(taken)
        sampled.append(taken)

    result = pd.concat(sampled, ignore_index=True) if sampled else frame.iloc[:0].copy()
    result["inclusion_probability"] = result["cell_sample"] / result["cell_population"]
    result["weight"] = 1.0 / result["inclusion_probability"]
    result["source"] = config["source"]
    result["seed"] = seed
    return result.sort_values(["cell", "person_id"]).reset_index(drop=True)


def frame_summary(frame: pd.DataFrame) -> dict[str, Any]:
    return {
        "focal_persons": int(len(frame)),
        "by_cohort": frame["cohort"].value_counts().to_dict(),
        "by_region_group": frame["region_group"].value_counts().to_dict(),
        "by_credential_class": frame["credential_class"].value_counts().to_dict(),
        "province_unknown": int((frame["province"] == "unknown").sum()),
        "cells": int(frame["cell"].nunique()),
        "kin_records_in_frame": int(frame["n_kin"].sum()),
        "mean_inclusion_probability": float(frame["inclusion_probability"].mean()),
    }


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - thin CLI
    import argparse
    import json

    parser = argparse.ArgumentParser(description="build the U05R pilot frame")
    parser.add_argument("--out", default="data/processed_v03/pilot_frame.parquet")
    parser.add_argument("--target", type=int, default=None)
    args = parser.parse_args(argv)
    frame = build_frame(target=args.target)
    out = PROJECT_ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(out, index=False)
    print(f"pilot frame: {len(frame)} persons -> {out}")
    print(json.dumps(frame_summary(frame), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
