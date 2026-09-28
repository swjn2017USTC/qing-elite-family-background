"""Source-specific and cohort-specific coverage (U07R).

Coverage is reported before any indicator is interpreted, per the V0.3 contract: a family
capital mean without the share of people it could be computed for is not a result. Two cuts
are produced:

* **by source** — which source supplied the kin edges / credentials / offices, and how much of
  the frame it covers;
* **by cohort** — the same numbers split by the sampling cohort, so a cohort with thin
  kin coverage is visible instead of being averaged into a flat line.
"""

from __future__ import annotations

from typing import Any

import pandas as pd


def coverage_by_source(
    persons: pd.DataFrame,
    kin_edges: pd.DataFrame,
    credentials: pd.DataFrame,
    career_events: pd.DataFrame,
    assertions: pd.DataFrame,
) -> pd.DataFrame:
    """One row per source: what it contributed and to how many focal persons."""
    frame = persons.loc[persons["primary_eligible"], "person_id"]
    focal = set(frame)
    documents = assertions.groupby("source_document_id").size()
    rows: list[dict[str, Any]] = []
    for source, edges in kin_edges.groupby("edge_source", dropna=False) if "edge_source" in kin_edges else [("cbdb", kin_edges)]:
        touched = edges.loc[edges["ego_person_id"].isin(focal), "ego_person_id"].nunique()
        rows.append(
            {
                "source": source,
                "kin_edges": int(len(edges)),
                "focal_persons_covered": int(touched),
                "coverage_share": round(touched / len(focal), 4) if len(focal) else 0.0,
                "credentials": int(len(credentials)),
                "career_events": int(len(career_events)),
                "assertions": int(len(assertions)),
                "documents": int(documents.nunique()) if len(documents) else 0,
            }
        )
    if not rows:
        rows.append(
            {
                "source": "none",
                "kin_edges": 0,
                "focal_persons_covered": 0,
                "coverage_share": 0.0,
                "credentials": 0,
                "career_events": 0,
                "assertions": 0,
                "documents": 0,
            }
        )
    return pd.DataFrame(rows)


def coverage_by_cohort(persons: pd.DataFrame, indicators: pd.DataFrame) -> pd.DataFrame:
    """Kin observability per cohort: the honest denominator for any cohort comparison."""
    merged = persons.merge(indicators, on="person_id", how="left")
    merged["cohort_id"] = merged["cohort_id"].fillna("unknown")
    grouped = merged.groupby("cohort_id")
    frame = grouped.agg(
        focal_persons=("person_id", "size"),
        with_any_kin=("n_all_edges", lambda values: int((values.fillna(0) > 0).sum())),
        direct_line_complete=("direct_line_complete", lambda values: int(values.fillna(False).sum())),
        mean_direct_kin=("observable_direct_kin", lambda values: round(float(values.fillna(0).mean()), 3)),
        mean_senior_kin=("observable_senior_kin", lambda values: round(float(values.fillna(0).mean()), 3)),
        new_entrant_defined=("direct_line_new_entrant", lambda values: int(values.notna().sum())),
    ).reset_index()
    frame["with_any_kin_share"] = (frame["with_any_kin"] / frame["focal_persons"]).round(4)
    return frame


def render_coverage(
    by_source: pd.DataFrame, by_cohort: pd.DataFrame, *, path: str | None = None
) -> str:
    lines = ["# U07R kinship coverage", "", "## by source", ""]
    lines.append("| source | kin_edges | focal covered | share | credentials | career events | assertions |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")
    for row in by_source.itertuples(index=False):
        lines.append(
            f"| {row.source} | {row.kin_edges} | {row.focal_persons_covered} | {row.coverage_share} | "
            f"{row.credentials} | {row.career_events} | {row.assertions} |"
        )
    lines += ["", "## by cohort", ""]
    lines.append(
        "| cohort | focal persons | with any kin | share | direct line complete | mean direct kin | mean senior kin | new-entrant defined |"
    )
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for row in by_cohort.itertuples(index=False):
        lines.append(
            f"| {row.cohort_id} | {row.focal_persons} | {row.with_any_kin} | {row.with_any_kin_share} | "
            f"{row.direct_line_complete} | {row.mean_direct_kin} | {row.mean_senior_kin} | {row.new_entrant_defined} |"
        )
    lines.append("")
    return "\n".join(lines)
