"""Harvest 《清史稿》 biographies and match them to the study universe (P05).

P04 established that looking a person up by name finds a biography only ~10% of the
time, while fetching a 列傳 volume yields dozens of biographies per request. P05
therefore harvests by volume, caches everything on disk, and resumes across runs.

    uv run python -m qing_elite.llm.harvest --volumes 250-529
    uv run python -m qing_elite.llm.harvest --build-index
"""

from __future__ import annotations

import argparse
import sqlite3
import time
from pathlib import Path
from typing import Iterable, Sequence

import pandas as pd

from qing_elite.llm.estimation import biography_spans, fetch_volume
from qing_elite.utils.config import PROCESSED_DIR, load_offices
from qing_elite.linkage.names import normalize_name

HARVEST_DIR = Path("data/interim/passages")
BIOGRAPHIES_PARQUET = HARVEST_DIR / "biographies.parquet"
HARVEST_INDEX = HARVEST_DIR / "harvest_index.parquet"
STATE_FILE = HARVEST_DIR / "harvest_state.json"

# 清史稿 列傳 volumes run from roughly 卷250 to 卷529; earlier volumes are 本紀/志/表.
DEFAULT_VOLUME_RANGE = (250, 529)


def parse_volume_range(spec: str) -> list[str]:
    if "-" in spec:
        start, end = spec.split("-", 1)
        return [f"清史稿/卷{number}" for number in range(int(start), int(end) + 1)]
    return [f"清史稿/卷{int(spec)}"]


def harvest_volumes(
    titles: Sequence[str],
    *,
    sleep_seconds: float = 2.0,
    max_seconds: float = 3600.0,
    flush_every: int = 10,
) -> pd.DataFrame:
    """Fetch volumes, extract biographies, and keep a resumable local table."""
    HARVEST_DIR.mkdir(parents=True, exist_ok=True)
    done: set[str] = set()
    frames: list[pd.DataFrame] = []
    if BIOGRAPHIES_PARQUET.exists():
        existing = pd.read_parquet(BIOGRAPHIES_PARQUET)
        frames.append(existing)
        done = set(existing["volume"].unique())
    started = time.time()
    pending: list[dict[str, object]] = []
    failures: list[dict[str, str]] = []

    for title in titles:
        if title in done:
            continue
        if time.time() - started > max_seconds:
            failures.append({"volume": title, "error": "skipped: harvest time budget reached"})
            continue
        try:
            content = fetch_volume(title, sleep_seconds=sleep_seconds)
            spans = biography_spans(content)
        except Exception as error:  # noqa: BLE001 - rate limits must not abort the harvest
            failures.append({"volume": title, "error": f"{type(error).__name__}: {error}"})
            continue
        for name, body in spans:
            pending.append({"volume": title, "name": name, "passage": body, "chars": len(body)})
        done.add(title)
        if len(done) % flush_every == 0:
            frames.append(pd.DataFrame.from_records(pending))
            pd.concat(frames, ignore_index=True).to_parquet(BIOGRAPHIES_PARQUET, index=False)
            pending = []

    if pending:
        frames.append(pd.DataFrame.from_records(pending))
    table = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if not table.empty:
        table = table.drop_duplicates(subset=["volume", "name"]).reset_index(drop=True)
        table.to_parquet(BIOGRAPHIES_PARQUET, index=False)
    if failures:
        pd.DataFrame.from_records(failures).to_csv(HARVEST_DIR / "harvest_failures.csv", index=False)
    return table


def build_index(master: pd.DataFrame | None = None, table: pd.DataFrame | None = None) -> pd.DataFrame:
    """Match harvested biographies to universe persons (exact normalized name)."""
    cfg = load_offices()
    simplify = cfg["cgedq"]["simplify_map"]
    variants = cfg["cgedq"]["char_variants"]
    if master is None:
        master = pd.read_parquet(PROCESSED_DIR / "officials_master.parquet")
    if table is None:
        table = pd.read_parquet(BIOGRAPHIES_PARQUET)

    master = master.copy()
    master["name_norm"] = [
        normalize_name(value, simplify, variants) for value in master["c_name_chn"]
    ]
    table = table.copy()
    table["name_norm"] = [normalize_name(value, simplify, variants) for value in table["name"]]
    merged = master.merge(
        table[["volume", "name", "name_norm", "passage", "chars"]],
        on="name_norm",
        how="inner",
    )
    merged = merged.rename(columns={"name": "biography_name", "chars": "passage_chars"})
    merged["source_ids"] = merged["volume"] + "#" + merged["biography_name"]
    merged = merged.sort_values(["person_uid", "passage_chars"], ascending=[True, False])
    merged = merged.drop_duplicates("person_uid", keep="first")
    merged.to_parquet(HARVEST_INDEX, index=False)
    return merged


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="harvest 清史稿 biographies")
    parser.add_argument("--volumes", default=f"{DEFAULT_VOLUME_RANGE[0]}-{DEFAULT_VOLUME_RANGE[1]}")
    parser.add_argument("--max-seconds", type=float, default=3600.0)
    parser.add_argument("--sleep", type=float, default=2.0)
    parser.add_argument("--build-index", action="store_true")
    args = parser.parse_args(argv)

    if args.build_index:
        index = build_index()
        print(f"harvest index: {len(index)} universe persons with a biography")
        return 0

    titles = parse_volume_range(args.volumes)
    print(f"harvesting {len(titles)} volumes (sleep={args.sleep}s, budget={args.max_seconds}s)")
    table = harvest_volumes(titles, sleep_seconds=args.sleep, max_seconds=args.max_seconds)
    print(f"biographies on disk: {len(table):,} across {table['volume'].nunique()} volumes")
    index = build_index(table=table)
    print(f"matched to universe: {len(index)} persons")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
