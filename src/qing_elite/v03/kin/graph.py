"""Canonical kinship tables, the NetworkX compute view, and structural validators (U07R).

Division of labour, stated once because it is easy to get wrong:

* **Parquet is the source of record.** ``persons`` / ``kin_edges`` / ``credentials`` /
  ``offices`` / ``evidence_assertions`` hold the facts; every fact row cites an assertion,
  and every assertion cites a document.
* **NetworkX is a calculator.** The graph is rebuilt from ``kin_edges`` on demand to answer
  structural questions (cycles, generation paradoxes, reachable kin). No ``.gpickle``, no
  adjacency blob, nothing that could become a second, silently divergent truth.
* **machine-inferred != source-explicit.** ``edge_origin`` separates an edge the source states
  from one the pipeline inferred (e.g. a symmetric relation added for the graph view); the
  inferred ones are never counted as evidence.

Structural validators implemented here: relation cycles, generation paradox, chronology
contradiction, duplicated kin, conflicting single-valued relation.
"""

from __future__ import annotations

from typing import Any

import networkx as nx
import pandas as pd

from qing_elite.v03.design import load_relation_ontology, relation_index

#: Relations a person can only have one of; two different values = a real conflict.
SINGLE_VALUED = frozenset({"father", "mother", "grandfather", "grandmother", "great_grandfather"})

#: Office keywords → administrative level, in the order they are tried. Deliberately coarse:
#: the CBDB office table in this release carries no rank, so inventing 品级 would be fiction.
OFFICE_LEVEL_RULES: tuple[tuple[str, str], ...] = (
    ("大學士", "central"),
    ("尚書", "central"),
    ("侍郎", "central"),
    ("郎中", "central"),
    ("員外郎", "central"),
    ("主事", "central"),
    ("御史", "central"),
    ("給事中", "central"),
    ("編修", "central"),
    ("檢討", "central"),
    ("庶吉士", "central"),
    ("總督", "provincial"),
    ("巡撫", "provincial"),
    ("布政使", "provincial"),
    ("按察使", "provincial"),
    ("鹽運使", "provincial"),
    ("道員", "provincial"),
    ("知府", "prefectural"),
    ("同知", "prefectural"),
    ("通判", "prefectural"),
    ("知州", "prefectural"),
    ("知縣", "county"),
    ("縣丞", "county"),
    ("主簿", "county"),
    ("典史", "county"),
    ("巡檢", "county"),
    ("教授", "county"),
    ("學正", "county"),
    ("教諭", "county"),
    ("訓導", "county"),
)

LEVEL_ORDER = {"county": 1, "prefectural": 2, "provincial": 3, "central": 4}


def build_offices(career_events: pd.DataFrame, *, ontology_version: str) -> pd.DataFrame:
    """One row per distinct office string appearing in the career events.

    ``mapping_status='matched'`` means the administrative level was recoverable from the
    title; everything else carries an explicit ``unmapped_reason`` — an unmatched office is
    never silently treated as low-level.
    """
    titles = sorted({str(value) for value in career_events["office_raw"].dropna()})
    rows: list[dict[str, Any]] = []
    for title in titles:
        level = next((value for keyword, value in OFFICE_LEVEL_RULES if keyword in title), None)
        central_local = {"central": "central"}.get(level, "local") if level else "unknown"
        rows.append(
            {
                "office_id": f"office-{abs(hash(title)) % 10**8:08d}",
                "office_title": title,
                "office_title_variants": None,
                "rank_label": None,
                "rank_class": None,
                "rank_side": None,
                "administrative_level": level or "unknown",
                "central_local": central_local,
                "authority_type": "civil",
                "institutional_body": None,
                "substantive_default": None,
                "valid_from_year": None,
                "valid_to_year": None,
                "mapping_status": "matched" if level else "unmatched",
                "unmapped_reason": None
                if level
                else "title not in the level keyword table; CBDB OFFICE_CODES carries no rank",
                "ontology_version": ontology_version,
            }
        )
    columns = [
        "office_id", "office_title", "office_title_variants", "rank_label", "rank_class",
        "rank_side", "administrative_level", "central_local", "authority_type",
        "institutional_body", "substantive_default", "valid_from_year", "valid_to_year",
        "mapping_status", "unmapped_reason", "ontology_version",
    ]
    frame = pd.DataFrame(rows, columns=columns)
    for column in ("rank_class", "valid_from_year", "valid_to_year"):
        frame[column] = pd.array(frame[column], dtype="Int64")
    frame["substantive_default"] = pd.array(frame["substantive_default"], dtype="boolean")
    frame["rank_label"] = frame["rank_label"].astype(object)
    frame["rank_side"] = frame["rank_side"].astype(object)
    frame["institutional_body"] = frame["institutional_body"].astype(object)
    return frame


def attach_office_levels(career_events: pd.DataFrame, offices: pd.DataFrame) -> pd.DataFrame:
    """Fill ``office_id`` / level columns on career events from the office table."""
    lookup = offices.set_index("office_title")
    frame = career_events.copy()
    frame["office_id"] = [
        lookup.loc[title, "office_id"] if title in lookup.index else None
        for title in frame["office_raw"]
    ]
    frame["administrative_level"] = [
        lookup.loc[title, "administrative_level"] if title in lookup.index else "unknown"
        for title in frame["office_raw"]
    ]
    frame["central_local"] = [
        lookup.loc[title, "central_local"] if title in lookup.index else "unknown"
        for title in frame["office_raw"]
    ]
    return frame


def build_graph(kin_edges: pd.DataFrame) -> nx.MultiDiGraph:
    """Rebuild the kinship graph from the canonical edge table.

    Edges point *from the junior to the senior* (``generation_delta > 0`` means the alter is
    older), so a cycle is a contradiction rather than a family shape.
    """
    graph = nx.MultiDiGraph()
    for row in kin_edges.itertuples(index=False):
        ego, alter = str(row.ego_person_id), row.alter_person_id
        graph.add_node(ego)
        if pd.isna(alter):
            continue
        graph.add_edge(
            ego,
            str(alter),
            edge_id=row.edge_id,
            relation=row.relation_type if hasattr(row, "relation_type") else row.relation_code,
            generation_delta=int(row.generation_delta),
            origin=row.edge_origin,
        )
    return graph


def validate_edges(kin_edges: pd.DataFrame) -> pd.DataFrame:
    """Run the five structural checks; return one row per flagged edge."""
    ontology = relation_index(load_relation_ontology())
    graph = build_graph(kin_edges)
    flags: list[dict[str, Any]] = []

    relation_column = "relation_code" if "relation_code" in kin_edges.columns else "relation_type"
    seen_pairs: dict[tuple[str, str], int] = {}
    seen_single: dict[tuple[str, str], str] = {}

    for row in kin_edges.itertuples(index=False):
        ego = str(row.ego_person_id)
        alter = str(row.alter_person_id) if pd.notna(row.alter_person_id) else None
        relation = getattr(row, relation_column)
        delta = int(row.generation_delta)

        # 1. duplicated kin: the same (ego, alter, relation) recorded twice
        key = (ego, f"{alter}|{relation}")
        if key in seen_pairs:
            flags.append(
                {
                    "edge_id": row.edge_id,
                    "check": "duplicated_kin",
                    "detail": f"duplicate of {seen_pairs[key]} for ({ego}, {alter}, {relation})",
                }
            )
        seen_pairs[key] = row.edge_id

        # 2. generation paradox: the relation's declared direction vs the ontology
        expected = ontology.get(relation, {}).get("generation_delta")
        if expected is not None and delta != expected:
            flags.append(
                {
                    "edge_id": row.edge_id,
                    "check": "generation_paradox",
                    "detail": f"{relation} implies delta {expected}, edge says {delta}",
                }
            )

        # 3. conflicting single-valued relation: two different alters for father/mother/...
        if relation in SINGLE_VALUED:
            previous = seen_single.get((ego, relation))
            if previous is not None and previous != str(alter):
                flags.append(
                    {
                        "edge_id": row.edge_id,
                        "check": "conflicting_relationship",
                        "detail": f"{relation} already recorded as {previous}, now {alter}",
                    }
                )
            seen_single[(ego, relation)] = str(alter)

    # 4. relation cycles (junior → senior edges must not come back to the start)
    try:
        simple = nx.DiGraph()
        for source, target, data in graph.edges(data=True):
            if data.get("origin") == "source_explicit":
                simple.add_edge(source, target, edge_id=data.get("edge_id"))
        for cycle in nx.simple_cycles(simple):
            if len(cycle) <= 1:
                continue
            flags.append(
                {
                    "edge_id": ",".join(
                        str(simple.edges[cycle[index], cycle[(index + 1) % len(cycle)]].get("edge_id"))
                        for index in range(len(cycle))
                    ),
                    "check": "relation_cycle",
                    "detail": " → ".join(cycle),
                }
            )
    except Exception as error:  # a validator must not take the run down with it
        flags.append({"edge_id": None, "check": "relation_cycle", "detail": f"{type(error).__name__}: {error}"})

    return pd.DataFrame(flags, columns=["edge_id", "check", "detail"])


def chronology_check(edges: pd.DataFrame, *, tolerance: int = 5) -> pd.DataFrame:
    """Flag kin whose recorded years contradict the relation's direction.

    Needs both sides' years, so it abstains (no flag) when either is unknown rather than
    guessing — the same rule the rest of the project uses for missing data.
    """
    rows: list[dict[str, Any]] = []
    for row in edges.itertuples(index=False):
        ego_year = getattr(row, "ego_index_year", None)
        alter_year = getattr(row, "alter_index_year", None)
        delta = getattr(row, "generation_delta", None)
        if pd.isna(ego_year) or pd.isna(alter_year) or pd.isna(delta):
            continue
        impossible = (int(delta) > 0 and int(alter_year) > int(ego_year) + tolerance) or (
            int(delta) < 0 and int(alter_year) < int(ego_year) - tolerance
        )
        if impossible:
            rows.append(
                {
                    "edge_id": row.edge_id,
                    "check": "chronology_contradiction",
                    "detail": (
                        f"relation delta {int(delta)} but alter index year {int(alter_year)} vs "
                        f"ego {int(ego_year)}"
                    ),
                }
            )
    return pd.DataFrame(rows, columns=["edge_id", "check", "detail"])


def summary(flags: pd.DataFrame, *, edges: int) -> dict[str, Any]:
    counts = flags["check"].value_counts().to_dict() if len(flags) else {}
    return {
        "edges": int(edges),
        "flagged": int(len(flags)),
        "by_check": counts,
    }
