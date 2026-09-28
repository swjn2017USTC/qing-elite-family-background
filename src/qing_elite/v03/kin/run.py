"""U07R orchestration: canonical tables → graph → indicators → coverage → gate artifacts.

``python -m qing_elite.v03.kin.run`` writes:

* ``data/processed_v03/{persons,kin_edges,credentials,offices,evidence_assertions}.parquet``
  (the five canonical tables, each validated against the V0.3 contract)
* ``data/processed_v03/kin_indicators.parquet`` — indicators with denominators, observability
  and lineage
* ``data/processed_v03/kin_validation.parquet`` — structural flags
* ``data/processed_v03/kin_coverage_by_{source,cohort}.parquet``
* ``reports/upgrade_v03/kin_coverage.md`` — rendered coverage (no hand-typed numbers)

The graph is built from the U05R pilot's CBDB arm (the only evidence-aware kin source this
project can currently read legally and automatically) and the U06R link decisions. No
regression is run here: this stage produces structure and coverage, not effects.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd

from qing_elite.utils.config import PROJECT_ROOT
from qing_elite.v03.contracts import (
    validate_detail_rows_cite_ledger,
    validate_table,
)
from qing_elite.v03.kin import ONTOLOGY_VERSION
from qing_elite.v03.kin import coverage as coverage_mod
from qing_elite.v03.kin import graph as graph_mod
from qing_elite.v03.kin import indicators as indicators_mod
from qing_elite.v03.lit.registry import PROCESSED_V03_DIR

PILOT_DIR = PROJECT_ROOT / "data" / "interim_v03" / "pilot"
REPORT_DIR = PROJECT_ROOT / "reports" / "upgrade_v03"
REPORT_MD = REPORT_DIR / "kin_coverage.md"
METRICS_JSON = PROJECT_ROOT / "data" / "interim_v03" / "kin" / "u07r_metrics.json"


def load_pilot() -> dict[str, pd.DataFrame]:
    """Read the U05R pilot artifacts (CBDB arm + OCR arm where it has identity evidence)."""
    frame = pd.read_parquet(PROCESSED_V03_DIR / "pilot_frame.parquet")
    edges = pd.read_parquet(PROCESSED_V03_DIR / "pilot_kin_edges.parquet")
    credentials = pd.read_parquet(PROCESSED_V03_DIR / "pilot_credentials.parquet")
    career_path = PROCESSED_V03_DIR / "pilot_career_events.parquet"
    if not career_path.exists():  # U05R published the kin/credential tables but not this one
        career_path = PILOT_DIR / "cbdb_career_events.parquet"
    career = pd.read_parquet(career_path)
    assertions = pd.read_parquet(PROCESSED_V03_DIR / "pilot_assertions.parquet")
    return {
        "frame": frame,
        "edges": edges,
        "credentials": credentials,
        "career": career,
        "assertions": assertions,
    }


def _residual_credential_type(label: object) -> str | None:
    """Fallback mapping for CBDB entry labels that are statuses, not degrees."""
    text = str(label or "")
    if any(keyword in text for keyword in ("科舉", "學校", "貢", "監", "生員", "庠生", "生")):
        return "other_exam"
    if any(keyword in text for keyword in ("蔭", "宗室", "官學生", "行伍", "軍", "議敘", "保舉")):
        return "other_privilege"
    return None


def build_persons(frame: pd.DataFrame, edges: pd.DataFrame) -> pd.DataFrame:
    """Focal persons plus every kin person reachable through a resolved edge."""
    focal = pd.DataFrame(
        {
            "person_id": "cbdb:" + frame["person_id"].astype(str),
            "canonical_name": frame["name_chn"].fillna(""),
            "surname": None,
            "given_name": None,
            "name_variants": None,
            "birth_year": None,
            "death_year": None,
            "native_province": frame["province"].replace({"unknown": None}),
            "native_county": None,
            "banner_status": frame.get("banner_status", pd.Series([None] * len(frame))),
            "cohort_id": frame["cohort"],
            "primary_source": "cbdb",
            "source_person_ids": frame["person_id"].astype(str),
            "link_status": "explicit_id",
            "link_evidence": "CBDB c_personid",
            "resolution_status": "resolved",
            "primary_eligible": True,
        }
    )
    kin_ids = sorted({str(value) for value in edges["alter_person_id"].dropna()})
    kin = pd.DataFrame(
        {
            "person_id": kin_ids,
            "canonical_name": [
                str(edges.loc[edges["alter_person_id"] == value, "alter_name_raw"].iloc[0] or "")
                for value in kin_ids
            ],
            "surname": None,
            "given_name": None,
            "name_variants": None,
            "birth_year": None,
            "death_year": None,
            "native_province": None,
            "native_county": None,
            "banner_status": None,
            "cohort_id": None,
            "primary_source": "cbdb",
            "source_person_ids": [value.split(":")[-1] for value in kin_ids],
            "link_status": "explicit_id",
            "link_evidence": "CBDB KIN_DATA c_kin_id",
            "resolution_status": "resolved",
            "primary_eligible": True,
        }
    )
    persons = pd.concat([focal, kin], ignore_index=True).drop_duplicates(subset=["person_id"])
    # nullable integer columns lose their dtype through the concat with an all-NaN frame
    for column in ("birth_year", "death_year"):
        persons[column] = pd.array(persons[column], dtype="Int64")
    persons["native_province"] = persons["native_province"].astype(object)
    return persons


def run() -> dict[str, Any]:
    PROCESSED_V03_DIR.mkdir(parents=True, exist_ok=True)
    METRICS_JSON.parent.mkdir(parents=True, exist_ok=True)
    pilot = load_pilot()
    frame, edges_in = pilot["frame"], pilot["edges"]

    # ---------------------------------------------------------------- canonical tables
    persons = build_persons(frame, edges_in)
    kin_edges = edges_in.drop(columns=[column for column in ("alter_index_year", "ego_index_year") if column in edges_in.columns]).copy()
    credentials = pilot["credentials"].copy()
    # the contract requires a positive credential to carry a type; CBDB carries entry labels
    # that are statuses rather than examination degrees. Map the rest explicitly and count what
    # is left over instead of letting a null type slip through.
    credentials["credential_type"] = credentials["credential_type"].fillna(
        credentials["exam_route"].map(_residual_credential_type)
    )
    unmapped_credentials = int(credentials["credential_type"].isna().sum())
    credentials = credentials.loc[credentials["credential_type"].notna()].copy()
    credentials["credential_year"] = pd.array(credentials["credential_year"], dtype="Int64")
    offices = graph_mod.build_offices(pilot["career"], ontology_version=ONTOLOGY_VERSION)
    career = graph_mod.attach_office_levels(pilot["career"], offices)
    assertions = pilot["assertions"].copy()
    for column in ("quote_start", "quote_end"):
        assertions[column] = pd.array(assertions[column], dtype="Int64")

    # ---------------------------------------------------------------- validation
    for name, table in (
        ("persons", persons),
        ("kin_edges", kin_edges),
        ("credentials", credentials),
        ("offices", offices),
        ("evidence_assertions", assertions),
    ):
        validate_table(name, table)
    validate_detail_rows_cite_ledger(
        {
            "career_events": career,
            "kin_edges": kin_edges,
            "credentials": credentials,
            "evidence_assertions": assertions,
        }
    )

    structural = graph_mod.validate_edges(kin_edges)
    chronology = graph_mod.chronology_check(edges_in)
    flags = pd.concat([structural, chronology], ignore_index=True)

    # ---------------------------------------------------------------- indicators
    # indicators are computed for the *sampled focal persons* only; kin persons exist as
    # graph nodes but are not rows of the study
    focal_ids = ["cbdb:" + str(value) for value in frame["person_id"]]
    indicator_frame = indicators_mod.compute_indicators(
        focal_ids,
        kin_edges,
        credentials,
        career,
    )

    # ---------------------------------------------------------------- coverage
    # the source of an edge is reached through its assertion → document chain, which is the
    # V0.3 contract's own lineage path (kin_edges carries no source column, by design)
    edge_sources = (
        kin_edges.loc[:, ["edge_id", "evidence_assertion_id"]]
        .merge(
            assertions.loc[:, ["assertion_id", "source_document_id"]],
            left_on="evidence_assertion_id",
            right_on="assertion_id",
            how="left",
        )
        .assign(source=lambda frame: frame["source_document_id"].fillna("").str.split("|").str[0])
    )
    kin_edges_for_coverage = kin_edges.merge(
        edge_sources.loc[:, ["edge_id", "source"]], on="edge_id", how="left"
    ).rename(columns={"source": "edge_source"})
    by_source = coverage_mod.coverage_by_source(
        persons, kin_edges_for_coverage, credentials, career, assertions
    )
    focal_persons = persons.loc[persons["person_id"].isin(set(focal_ids))]
    by_cohort = coverage_mod.coverage_by_cohort(focal_persons, indicator_frame)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_MD.write_text(coverage_mod.render_coverage(by_source, by_cohort), encoding="utf-8")

    # ---------------------------------------------------------------- write
    persons.to_parquet(PROCESSED_V03_DIR / "persons.parquet", index=False)
    kin_edges.to_parquet(PROCESSED_V03_DIR / "kin_edges.parquet", index=False)
    credentials.to_parquet(PROCESSED_V03_DIR / "credentials.parquet", index=False)
    offices.to_parquet(PROCESSED_V03_DIR / "offices.parquet", index=False)
    assertions.to_parquet(PROCESSED_V03_DIR / "evidence_assertions.parquet", index=False)
    indicator_frame.to_parquet(PROCESSED_V03_DIR / "kin_indicators.parquet", index=False)
    flags.to_parquet(PROCESSED_V03_DIR / "kin_validation.parquet", index=False)
    by_source.to_parquet(PROCESSED_V03_DIR / "kin_coverage_by_source.parquet", index=False)
    by_cohort.to_parquet(PROCESSED_V03_DIR / "kin_coverage_by_cohort.parquet", index=False)

    import networkx as nx

    graph = graph_mod.build_graph(kin_edges)
    metrics = {
        "stage": "U07R",
        "ontology_version": ONTOLOGY_VERSION,
        "tables": {
            "persons": int(len(persons)),
            "focal_persons": int(len(focal_ids)),
            "kin_persons": int(len(persons) - len(focal_ids)),
            "kin_edges": int(len(kin_edges)),
            "credentials": int(len(credentials)),
            "offices": int(len(offices)),
            "career_events": int(len(career)),
            "evidence_assertions": int(len(assertions)),
        },
        "graph": {
            "engine": f"networkx {nx.__version__} (compute layer only; Parquet is the source of record)",
            "nodes": int(graph.number_of_nodes()),
            "edges": int(graph.number_of_edges()),
            "source_explicit_edges": int((kin_edges["edge_origin"] == "source_explicit").sum()),
            "machine_inferred_edges": int((kin_edges["edge_origin"] == "machine_inferred").sum()),
            "components_undirected": int(
                nx.number_connected_components(graph.to_undirected())
            ),
        },
        "edge_origin": kin_edges["edge_origin"].value_counts().to_dict(),
        "relation_classes": kin_edges["relation_class"].value_counts().to_dict(),
        "validators": graph_mod.summary(flags, edges=len(kin_edges)),
        "indicators": indicators_mod.summary(indicator_frame),
        "coverage": {
            "by_source": by_source.to_dict(orient="records"),
            "by_cohort": by_cohort.to_dict(orient="records"),
        },
        "credentials_unmapped_type_dropped": unmapped_credentials,
        "office_ontology": {
            "distinct_titles": int(len(offices)),
            "matched": int((offices["mapping_status"] == "matched").sum()),
            "unmatched": int((offices["mapping_status"] == "unmatched").sum()),
        },
        "notes": [
            "launched on the U05R pilot CBDB arm; the OCR arm contributes identity evidence only "
            "(its attribute layer was demoted in U05R)",
            "no substantive regression run in this stage",
        ],
    }
    METRICS_JSON.write_text(json.dumps(metrics, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return metrics


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - thin CLI
    parser = argparse.ArgumentParser(description="build the U07R kinship graph and indicators")
    parser.parse_args(argv)
    metrics = run()
    print(json.dumps(metrics, ensure_ascii=False, indent=2, default=str)[:3500])
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
