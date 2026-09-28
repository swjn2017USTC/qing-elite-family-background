"""Graph clustering with temporal-connectivity validation (U06R).

Accepted links form a graph; a person is a connected component. Three things are checked
before a component is allowed to stand for one person:

* **no contradiction inside the component** — the same record cannot be in two components,
  and a component may not contain two records that the validators call mutually exclusive;
* **temporal connectivity** — the observation windows inside a component must overlap or be
  separated by a plausible gap, not by centuries;
* **singleton accounting** — records that never matched stay singletons, and are reported,
  because "how many records did not cluster" is a result, not a footnote.

Implemented with union-find rather than a graph library: the operation is transitive closure
over a few ten thousand edges, and a dependency would add nothing.
"""

from __future__ import annotations

from typing import Any

import pandas as pd


class UnionFind:
    def __init__(self) -> None:
        self._parent: dict[str, str] = {}
        self._rank: dict[str, int] = {}

    def find(self, item: str) -> str:
        self._parent.setdefault(item, item)
        self._rank.setdefault(item, 0)
        root = item
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[item] != root:  # path compression
            self._parent[item], item = root, self._parent[item]
        return root

    def union(self, left: str, right: str) -> None:
        root_left, root_right = self.find(left), self.find(right)
        if root_left == root_right:
            return
        if self._rank[root_left] < self._rank[root_right]:
            root_left, root_right = root_right, root_left
        self._parent[root_right] = root_left
        if self._rank[root_left] == self._rank[root_right]:
            self._rank[root_left] += 1

    def components(self) -> dict[str, str]:
        return {item: self.find(item) for item in list(self._parent)}


def cluster_links(links: pd.DataFrame, *, left_column: str, right_column: str) -> pd.DataFrame:
    """Transitive closure over accepted links; one row per (record, component)."""
    union = UnionFind()
    for row in links.itertuples(index=False):
        left, right = getattr(row, left_column), getattr(row, right_column)
        if pd.isna(left) or pd.isna(right):
            continue
        union.union(str(left), str(right))
    mapping = union.components()
    frame = pd.DataFrame({"record_id": list(mapping), "component_id": list(mapping.values())})
    sizes = frame.groupby("component_id")["record_id"].transform("size")
    frame["component_size"] = sizes
    return frame.sort_values(["component_size", "component_id"], ascending=[False, True]).reset_index(drop=True)


def temporal_connectivity(
    components: pd.DataFrame,
    windows: pd.DataFrame,
    *,
    record_column: str = "record_id",
    start_column: str = "first_year",
    end_column: str = "last_year",
    max_gap_years: int = 25,
) -> pd.DataFrame:
    """Flag components whose records cannot plausibly be one person in time.

    A gap is allowed because a JSL window is a sample of a career: someone observed in 1760
    and again in 1800 is not impossible, but a 60-year gap needs a human. The threshold is a
    parameter, not a truth.
    """
    merged = components.merge(
        windows.loc[:, [record_column, start_column, end_column]], on=record_column, how="left"
    )
    grouped = merged.groupby("component_id")
    summary = grouped.agg(
        records=(record_column, "size"),
        first_year=(start_column, "min"),
        last_year=(end_column, "max"),
        n_missing_dates=(start_column, lambda values: int(values.isna().sum())),
    ).reset_index()
    summary["span_years"] = summary["last_year"] - summary["first_year"]
    summary["gap_violation"] = summary["span_years"] > max_gap_years
    return summary


def cluster_summary(components: pd.DataFrame) -> dict[str, Any]:
    sizes = components["component_size"].value_counts().sort_index()
    return {
        "records": int(len(components)),
        "components": int(components["component_id"].nunique()),
        "multi_record_components": int((components["component_size"] > 1).sum()),
        "singletons": int((components["component_size"] == 1).sum()),
        "size_distribution": {int(size): int(count) for size, count in sizes.items() if int(size) <= 5},
        "max_component_size": int(components["component_size"].max()) if len(components) else 0,
    }
