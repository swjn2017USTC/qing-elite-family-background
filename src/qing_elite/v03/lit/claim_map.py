"""Claim → variable → source → testability (U04R requirement D).

Joins three machine-checked inputs and writes ``06_claim_source_map.csv``:

* the LC ledger (what the modern literature claims, with locators),
* ``config/v03/variable_source_map.yaml`` (which source capabilities each V0.3 variable
  needs),
* ``data/processed_v03/source_feasibility.parquet`` (what the probes actually found).

``testability`` is therefore derived, never asserted by hand: a claim about a kin-count
exposure is only ``TESTABLE_NOW`` if some candidate source both documents kin broadly and
is machine-readable with bulk/API access.
"""

from __future__ import annotations

import argparse
import functools
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from qing_elite.utils.config import PROJECT_ROOT
from qing_elite.v03.lit.registry import (
    CLAIMS_JSONL,
    CLAIM_MAP_CSV,
    FEASIBILITY_PARQUET,
    read_jsonl,
    validate_claim_map,
    write_jsonl,
)

VARIABLE_MAP_YAML = PROJECT_ROOT / "config" / "v03" / "variable_source_map.yaml"

#: Sources reachable without an institutional agreement and without OCR.
STRUCTURED_ACCESS = "PUBLIC_STRUCTURED"
SCAN_ACCESS = "PUBLIC_SCAN"
GATED_ACCESS = ("PUBLIC_UI_ONLY", "ACCESS_REQUEST_REQUIRED", "UNAVAILABLE")


class ClaimMapError(ValueError):
    """Raised when the variable map or the ledger cannot be joined."""


@functools.lru_cache(maxsize=None)
def load_variable_map(path: Path | None = None) -> dict[str, Any]:
    with (path or VARIABLE_MAP_YAML).open(encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    variables = config.get("variables") or {}
    if not variables:
        raise ClaimMapError("variable_source_map.yaml must declare variables")
    for name, spec in variables.items():
        if spec.get("role") not in ("exposure", "outcome"):
            raise ClaimMapError(f"{name}: role must be exposure or outcome")
        if not spec.get("candidate_sources"):
            raise ClaimMapError(f"{name}: candidate_sources must not be empty")
        if not spec.get("required_source_capability"):
            raise ClaimMapError(f"{name}: required_source_capability is required")
    return config


def testability_for(variable_role: str, source: pd.Series | None) -> str:
    """Derive testability from the source's probed access, not from opinion."""
    if source is None:
        return "NOT_TESTABLE_IN_V03"
    access = str(source["access_type"])
    if access == STRUCTURED_ACCESS and (
        bool(source["bulk_available"]) or bool(source["api_available"])
    ):
        if variable_role == "outcome":
            return "TESTABLE_NOW"
        return (
            "TESTABLE_NOW"
            if str(source["kin_population_coverage"]) in ("HIGH", "MEDIUM")
            else "NOT_TESTABLE_IN_V03"
        )
    if access == SCAN_ACCESS and bool(source["bulk_available"]):
        return "TESTABLE_WITH_OCR"
    if access in GATED_ACCESS:
        return "TESTABLE_WITH_ACCESS_REQUEST"
    return "NOT_TESTABLE_IN_V03"


def build_claim_map(
    claims: pd.DataFrame, feasibility: pd.DataFrame, variable_map: dict[str, Any]
) -> pd.DataFrame:
    """One row per (claim, variable, candidate source)."""
    variables = variable_map["variables"]
    by_source = {row["source_name"]: row for _, row in feasibility.iterrows()}
    rows: list[dict[str, Any]] = []
    counter = 0
    for claim in claims.itertuples(index=False):
        names = [name.strip() for name in str(claim.v03_variables).split(";") if name.strip()]
        for name in names:
            if name == "none":
                continue
            spec = variables.get(name)
            if spec is None:
                raise ClaimMapError(f"{claim.lit_claim_id}: variable {name!r} is not declared")
            for source_name in spec["candidate_sources"]:
                if source_name not in by_source:
                    raise ClaimMapError(
                        f"{claim.lit_claim_id}: candidate source {source_name!r} is not declared"
                    )
                source = by_source[source_name]
                counter += 1
                testability = testability_for(spec["role"], source)
                rows.append(
                    {
                        "claim_id": f"C-{counter:03d}",
                        "lit_claim_id": claim.lit_claim_id,
                        "literature_id": claim.literature_id,
                        "claim": claim.claim,
                        "v03_variable": name,
                        "variable_role": spec["role"],
                        "candidate_source": source_name,
                        "testability": testability,
                        "required_source_capability": spec["required_source_capability"],
                        "reason": (
                            f"{source_name}: access_type={source['access_type']}, "
                            f"bulk={bool(source['bulk_available'])}, "
                            f"api={bool(source['api_available'])}, "
                            f"kin_scope_grade={source['kin_scope_grade']}, "
                            f"kin_population_coverage={source['kin_population_coverage']} → {testability}"
                        ),
                    }
                )
    return pd.DataFrame(rows)


def build_and_write() -> pd.DataFrame:
    claims = pd.DataFrame(read_jsonl(CLAIMS_JSONL))
    if claims.empty:
        raise ClaimMapError(f"empty ledger: {CLAIMS_JSONL}")
    feasibility = pd.read_parquet(FEASIBILITY_PARQUET)
    mapping = build_claim_map(claims, feasibility, load_variable_map())
    mapping = validate_claim_map(mapping)
    CLAIM_MAP_CSV.parent.mkdir(parents=True, exist_ok=True)
    mapping.to_csv(CLAIM_MAP_CSV, index=False)
    return mapping


def summarise(mapping: pd.DataFrame) -> dict[str, Any]:
    return {
        "rows": int(len(mapping)),
        "claims_referenced": int(mapping["lit_claim_id"].nunique()),
        "variables": int(mapping["v03_variable"].nunique()),
        "by_testability": mapping["testability"].value_counts().to_dict(),
        "testable_now_variables": sorted(
            mapping.loc[mapping["testability"] == "TESTABLE_NOW", "v03_variable"].unique()
        ),
        "blocked_variables": sorted(
            mapping.loc[
                mapping["testability"].isin(("TESTABLE_WITH_ACCESS_REQUEST", "NOT_TESTABLE_IN_V03")),
                "v03_variable",
            ].unique()
        ),
    }


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - thin CLI
    parser = argparse.ArgumentParser(description="claim → variable → source → testability")
    parser.add_argument("--csv", action="store_true", help="print the summary only")
    args = parser.parse_args(argv)
    mapping = build_and_write()
    print(f"claim map rows: {len(mapping)} -> {CLAIM_MAP_CSV}")
    import json

    print(json.dumps(summarise(mapping), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
