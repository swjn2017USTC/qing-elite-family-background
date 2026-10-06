"""Family-capital indicators with explicit denominators and observability (U07R).

Every indicator here follows the same three rules, because they are what v0.1 broke:

1. **numerator and denominator travel together** — an indicator without its denominator is
   not a result;
2. **observability gates the value** — ``*_new_entrant`` is ``True``/``False`` only when the
   relevant kin are actually observable, otherwise it is ``unknown``, never ``False``;
3. **lineage is stored, not implied** — each row carries the edge ids that produced it, so an
   indicator can be traced back to assertions and documents.

Office strength uses the administrative level from the office ontology (county < prefectural <
provincial < central). That is a *proxy*: the release carries no 品级 for these titles, and the
column is named ``*_level`` where that matters.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from qing_elite.v03.design import load_relation_ontology, relation_index
from qing_elite.v03.kin.graph import LEVEL_ORDER

DIRECT_LINE = ("father", "grandfather", "great_grandfather", "great_great_grandfather")
SENIOR_COLLATERAL = ("uncle_paternal", "great_uncle_paternal", "great_great_uncle_paternal")

INDICATOR_COLUMNS = (
    "direct_3g_degree_count",
    "direct_3g_office_count",
    "direct_elite_generations",
    "max_direct_office_rank",
    "senior_collateral_degree_count",
    "senior_collateral_office_count",
    "all_senior_elite_kin_count",
)


def _relation_column(edges: pd.DataFrame) -> str:
    return "relation_code" if "relation_code" in edges.columns else "relation_type"


def compute_indicators(
    focal_person_ids: list[str],
    edges: pd.DataFrame,
    credentials: pd.DataFrame,
    career_events: pd.DataFrame,
) -> pd.DataFrame:
    """One row per focal person, with values, denominators, observability and lineage."""
    ontology = relation_index(load_relation_ontology())
    relation_column = _relation_column(edges)
    degree_by_person: dict[str, int] = (
        credentials.groupby("person_id").size().to_dict() if len(credentials) else {}
    )
    level_by_person: dict[str, int] = {}
    if len(career_events):
        levels = career_events.copy()
        levels["order"] = levels["administrative_level"].map(LEVEL_ORDER).fillna(0)
        level_by_person = levels.groupby("person_id")["order"].max().to_dict()

    edges = edges.copy()
    edges["alter_key"] = edges["alter_person_id"].fillna("")
    grouped = edges.groupby("ego_person_id")

    rows: list[dict[str, Any]] = []
    for focal in focal_person_ids:
        person_edges = grouped.get_group(focal) if focal in grouped.groups else edges.iloc[:0]
        direct = person_edges.loc[person_edges[relation_column].isin(DIRECT_LINE)]
        senior = person_edges.loc[person_edges[relation_column].isin(SENIOR_COLLATERAL)]

        def _with_degree(frame: pd.DataFrame) -> pd.DataFrame:
            return frame.loc[frame["alter_key"].map(lambda key: degree_by_person.get(key, 0) > 0)]

        def _with_office(frame: pd.DataFrame) -> pd.DataFrame:
            return frame.loc[frame["alter_key"].map(lambda key: level_by_person.get(key, 0) > 0)]

        direct_degrees = int(_with_degree(direct).shape[0])
        direct_offices = int(_with_office(direct).shape[0])
        senior_degrees = int(_with_degree(senior).shape[0])
        senior_offices = int(_with_office(senior).shape[0])
        senior_elite = int(
            len(set(_with_degree(senior)["edge_id"]) | set(_with_office(senior)["edge_id"]))
        )

        # elite generations: how many of the three direct-line slots carry a degree or an office
        elite_slots = {
            row.alter_key
            for row in direct.itertuples(index=False)
            if degree_by_person.get(row.alter_key, 0) > 0 or level_by_person.get(row.alter_key, 0) > 0
        }
        max_level = max(
            (
                level_by_person.get(row.alter_key, 0)
                for row in direct.itertuples(index=False)
            ),
            default=0,
        )

        # observability: a generation counts as observable only when the edge exists AND the
        # alter resolved to a person we can measure
        observable_direct = int(direct.loc[direct["alter_key"] != ""].shape[0])
        observable_senior = int(senior.loc[senior["alter_key"] != ""].shape[0])
        direct_complete = all(
            slot in set(direct[relation_column]) for slot in ("father", "grandfather", "great_grandfather")
        )

        direct_new_entrant: Any = None
        if direct_complete and observable_direct >= 3:
            direct_new_entrant = bool(direct_degrees == 0 and direct_offices == 0)
        extended_new_entrant: Any = None
        if direct_complete and observable_direct >= 3 and observable_senior >= 1:
            extended_new_entrant = bool(
                direct_degrees == 0 and direct_offices == 0 and senior_elite == 0
            )

        rows.append(
            {
                "person_id": focal,
                "direct_3g_degree_count": direct_degrees,
                "direct_3g_office_count": direct_offices,
                "direct_elite_generations": len(elite_slots),
                "max_direct_office_rank": max_level or None,
                "senior_collateral_degree_count": senior_degrees,
                "senior_collateral_office_count": senior_offices,
                "all_senior_elite_kin_count": senior_elite,
                "direct_line_new_entrant": direct_new_entrant,
                "extended_family_new_entrant": extended_new_entrant,
                # denominators / observability
                "n_direct_edges": int(direct.shape[0]),
                "n_senior_edges": int(senior.shape[0]),
                "n_all_edges": int(person_edges.shape[0]),
                "observable_direct_kin": observable_direct,
                "observable_senior_kin": observable_senior,
                "direct_line_complete": bool(direct_complete),
                "denominator_direct_kin": 3,
                "denominator_senior_kin": int(senior.shape[0]) or None,
                # lineage: the edges that produced the counts
                "lineage_edge_ids": ";".join(sorted(set(person_edges["edge_id"]))),
                "lineage_direct_edge_ids": ";".join(sorted(set(direct["edge_id"]))),
                "lineage_senior_edge_ids": ";".join(sorted(set(senior["edge_id"]))),
                "ontology_version": ontology and load_relation_ontology()["ontology_version"],
            }
        )
    return pd.DataFrame(rows)


def summary(indicators: pd.DataFrame) -> dict[str, Any]:
    frame = indicators
    return {
        "focal_persons": int(len(frame)),
        "with_any_kin": int((frame["n_all_edges"] > 0).sum()),
        "direct_line_complete": int(frame["direct_line_complete"].sum()),
        "new_entrant_defined": {
            "direct_line": int(frame["direct_line_new_entrant"].notna().sum()),
            "extended_family": int(frame["extended_family_new_entrant"].notna().sum()),
        },
        "new_entrant_true": {
            "direct_line": int((frame["direct_line_new_entrant"] == True).sum()),  # noqa: E712
            "extended_family": int((frame["extended_family_new_entrant"] == True).sum()),  # noqa: E712
        },
        "indicator_means": {
            column: round(float(frame[column].mean()), 4) for column in INDICATOR_COLUMNS
        },
        "numerator_sums": {
            column: int(frame[column].sum()) for column in INDICATOR_COLUMNS
        },
    }
