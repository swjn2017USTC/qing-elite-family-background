"""Literature registry: ids, storage, validation and summaries (U04R).

Layout (framework reused from the verified literature-pipeline handoff):

```text
sources/literature/
├── registry/literature_registry.jsonl   tracked — one row per paper (LIT-NNNN)
├── digests/LIT-NNNN.md                  tracked — one digest per core paper
├── queues/acquisition_queue.{md,jsonl}  tracked — human acquisition gate
├── logs/                                tracked — parse QC reports
├── public/                              gitignored bodies — licence-clear OA fulltext
└── private/{inbox,normalized}/          gitignored — institution-licensed copies
```

Project-specific outputs (claim ledger, claim→source map) live under
``reports/upgrade_v03/literature/``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from qing_elite.utils.config import PROJECT_ROOT
from qing_elite.v03.lit.schema import (
    CLAIM_SOURCE_SCHEMA,
    LITERATURE_CLAIMS_SCHEMA,
    LITERATURE_ID_PATTERN,
    LITERATURE_REGISTRY_SCHEMA,
    SOURCE_FEASIBILITY_SCHEMA,
    validate_claim_links,
    validate_core_have_digests,
    validate_digest_sections,
    validate_lit_table,
)

LIT_DIR = PROJECT_ROOT / "sources" / "literature"
REGISTRY_DIR = LIT_DIR / "registry"
REGISTRY_JSONL = REGISTRY_DIR / "literature_registry.jsonl"
DIGEST_DIR = LIT_DIR / "digests"
QUEUE_DIR = LIT_DIR / "queues"
LOG_DIR = LIT_DIR / "logs"
PUBLIC_DIR = LIT_DIR / "public"
PRIVATE_INBOX = LIT_DIR / "private" / "inbox"
PRIVATE_NORMALIZED = LIT_DIR / "private" / "normalized"
CANDIDATE_DIR = LIT_DIR / "candidates"
CANDIDATES_JSONL = CANDIDATE_DIR / "literature_candidates.jsonl"

REPORT_DIR = PROJECT_ROOT / "reports" / "upgrade_v03" / "literature"
CLAIMS_JSONL = REPORT_DIR / "03_literature_claims.jsonl"
CLAIM_MAP_CSV = REPORT_DIR / "06_claim_source_map.csv"
HISTORIOGRAPHY_MD = REPORT_DIR / "04_historiography.md"
CLAIM_DELTA_MD = REPORT_DIR / "05_claim_delta.md"
SEARCH_PLAN_MD = REPORT_DIR / "01_search_plan.md"
COVERAGE_YAML = REPORT_DIR / "02_coverage.yaml"

PROCESSED_V03_DIR = PROJECT_ROOT / "data" / "processed_v03"
FEASIBILITY_PARQUET = PROCESSED_V03_DIR / "source_feasibility.parquet"
SOURCE_PLAN_JSON = PROCESSED_V03_DIR / "source_plan.json"
INTERIM_LIT_DIR = PROJECT_ROOT / "data" / "interim_v03" / "lit"
TEXT_DIR = INTERIM_LIT_DIR / "text"
RAW_DIR = INTERIM_LIT_DIR / "raw"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=False) + "\n")


def load_registry(path: Path | None = None) -> pd.DataFrame:
    """Load the registry as a schema-validated frame (empty frame when absent)."""
    path = path or REGISTRY_JSONL
    rows = read_jsonl(path)
    if not rows:
        return pd.DataFrame(columns=list(LITERATURE_REGISTRY_SCHEMA.columns))
    frame = pd.DataFrame(rows)
    frame["year"] = frame["year"].astype("Int64")
    frame["relevance_score"] = frame["relevance_score"].astype("Int64")
    frame["fulltext_verified"] = frame["fulltext_verified"].fillna(False).astype(bool)
    return frame


def validate_registry(frame: pd.DataFrame) -> pd.DataFrame:
    return validate_lit_table("literature_registry", frame)


def validate_claims(claims: pd.DataFrame) -> pd.DataFrame:
    return validate_lit_table("literature_claims", claims)


def validate_claim_map(mapping: pd.DataFrame) -> pd.DataFrame:
    return validate_lit_table("claim_source_map", mapping)


def validate_feasibility(frame: pd.DataFrame) -> pd.DataFrame:
    return validate_lit_table("source_feasibility", frame)


def next_literature_ids(count: int, existing: pd.DataFrame | None = None) -> list[str]:
    """Allocate ``LIT-NNNN`` ids continuing after the highest existing id."""
    existing = existing if existing is not None else load_registry()
    used = [
        int(value.split("-")[1])
        for value in existing.get("literature_id", pd.Series(dtype=str)).tolist()
    ]
    start = (max(used) + 1) if used else 1
    return [f"LIT-{number:04d}" for number in range(start, start + count)]


def digest_ids() -> set[str]:
    if not DIGEST_DIR.exists():
        return set()
    return {path.stem for path in DIGEST_DIR.glob("LIT-*.md")}


def missing_digests(frame: pd.DataFrame) -> list[str]:
    present = digest_ids()
    return sorted(set(frame.loc[frame["tier"] == "core", "literature_id"]) - present)


def read_digest(literature_id: str) -> str:
    return (DIGEST_DIR / f"{literature_id}.md").read_text(encoding="utf-8")


def check_digest_sections(required: tuple[str, ...]) -> None:
    problems: list[str] = []
    for literature_id in sorted(digest_ids()):
        text = read_digest(literature_id)
        try:
            validate_digest_sections(text, required)
        except ValueError as error:
            problems.append(f"{literature_id}: {error}")
    if problems:
        raise ValueError("digest section violations:\n  " + "\n  ".join(problems))


def registry_summary(frame: pd.DataFrame) -> dict[str, Any]:
    return {
        "rows": int(len(frame)),
        "core": int((frame["tier"] == "core").sum()),
        "candidates": int((frame["tier"] == "candidate").sum()),
        "with_abstract": int(frame["abstract"].notna().sum()),
        "fulltext_verified": int(frame["fulltext_verified"].sum()),
        "by_access_status": frame["access_status"].value_counts().to_dict(),
        "by_query_cluster": frame["query_cluster"].value_counts().to_dict(),
    }


def validate_pipeline(
    *,
    registry: pd.DataFrame,
    claims: pd.DataFrame,
    mapping: pd.DataFrame,
    feasibility: pd.DataFrame,
    required_digest_sections: tuple[str, ...],
) -> None:
    """Validate every literature-pipeline table plus its cross-table rules."""
    validate_registry(registry)
    validate_claims(claims)
    validate_claim_map(mapping)
    validate_feasibility(feasibility)
    validate_claim_links(claims, registry, mapping)
    validate_core_have_digests(registry, digest_ids())
    check_digest_sections(required_digest_sections)


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - thin CLI
    import argparse

    parser = argparse.ArgumentParser(description="literature registry CLI")
    parser.add_argument("action", choices=["summary", "validate"])
    args = parser.parse_args(argv)

    registry = load_registry()
    if args.action == "summary":
        print(json.dumps(registry_summary(registry), ensure_ascii=False, indent=2))
        print(f"missing digests: {missing_digests(registry)}")
        return 0
    validate_registry(registry)
    print(f"registry ok: {len(registry)} rows")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
