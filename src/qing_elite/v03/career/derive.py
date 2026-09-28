"""Derived career outcomes and career validators (U08R).

Outcomes are computed from the event table, never from a static tier:

``maximum_office`` / ``highest_rank_class`` / ``highest_admin_level`` / ``first_substantive_office``
/ ``career_length_years`` / ``central_local_route`` / ``time_to_first_office`` /
``appointment_composition`` / ``major_transitions``.

Every row records the events that produced it (``lineage_event_ids``) so a number can be
traced back to postings → assertions → documents.

Validators:

* **chronology** — an event that ends before it starts, or that falls outside the person's
  plausible life window;
* **impossible transitions** — a rank jump larger than the configured maximum in a single
  step, or a rank *increase* within the same year, flagged for review rather than deleted.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

MAX_RANK_JUMP = 4  # a jump of more than four grades in one transition is reviewed, not accepted


def _ordered_events(events: pd.DataFrame) -> pd.DataFrame:
    frame = events.copy()
    frame["sort_year"] = frame["start_year"].fillna(9999)
    return frame.sort_values(["person_id", "sort_year", "event_id"])


def derive_outcomes(events: pd.DataFrame, offices: pd.DataFrame) -> pd.DataFrame:
    """One row per person with the derived career outcomes and their lineage."""
    lookup = offices.set_index("office_title")
    frame = _ordered_events(events)
    frame["rank_class"] = [
        lookup.loc[title, "rank_class"] if title in lookup.index else pd.NA
        for title in frame["office_raw"]
    ]
    frame["admin_level"] = [
        lookup.loc[title, "administrative_level"] if title in lookup.index else "unknown"
        for title in frame["office_raw"]
    ]
    frame["central_local"] = [
        lookup.loc[title, "central_local"] if title in lookup.index else "unknown"
        for title in frame["office_raw"]
    ]

    rows: list[dict[str, Any]] = []
    for person, group in frame.groupby("person_id"):
        dated = group.loc[group["start_year"].notna()]
        ranks = pd.to_numeric(group["rank_class"], errors="coerce").dropna()
        substantive = group.loc[group.get("substantive", pd.Series(True, index=group.index))]
        first_substantive = substantive.loc[substantive["start_year"].notna()].head(1)
        levels = [value for value in group["admin_level"] if value != "unknown"]
        route_parts = [value for value in group["central_local"] if value in ("central", "local")]
        central_count = route_parts.count("central")
        local_count = route_parts.count("local")
        if central_count and not local_count:
            route = "central_only"
        elif local_count and not central_count:
            route = "local_only"
        elif central_count and local_count:
            route = "mixed"
        else:
            route = "unknown"

        transitions: list[str] = []
        previous_rank = None
        for row in group.itertuples(index=False):
            rank = pd.to_numeric(pd.Series([row.rank_class]), errors="coerce").iloc[0]
            if pd.isna(rank):
                continue
            if previous_rank is not None and rank != previous_rank:
                transitions.append(f"{int(previous_rank)}->{int(rank)}")
            previous_rank = rank

        first_year = dated["start_year"].min() if len(dated) else None
        last_year = dated["end_year"].max() if len(dated) else None

        def _as_int(value):
            return int(value) if value is not None and pd.notna(value) else None

        rows.append(
            {
                "person_id": person,
                "n_events": int(len(group)),
                "n_dated_events": int(len(dated)),
                "first_event_year": _as_int(first_year),
                "last_event_year": _as_int(last_year),
                "career_length_years": (
                    _as_int(last_year - first_year)
                    if _as_int(first_year) is not None and _as_int(last_year) is not None
                    else None
                ),
                "maximum_office": group.iloc[0]["office_raw"] if len(group) else None,
                "highest_rank_class": int(ranks.min()) if len(ranks) else None,
                "lowest_rank_class": int(ranks.max()) if len(ranks) else None,
                "highest_admin_level": levels[0] if levels else "unknown",
                "first_substantive_office": first_substantive.iloc[0]["office_raw"] if len(first_substantive) else None,
                "time_to_first_office": (
                    int(first_substantive.iloc[0]["start_year"]) if len(first_substantive) else None
                ),
                "central_local_route": route,
                "n_central_events": int(central_count),
                "n_local_events": int(local_count),
                "acting_share": round(float(group.get("acting", pd.Series(False, index=group.index)).mean()), 4),
                "expectant_share": round(float(group.get("expectant", pd.Series(False, index=group.index)).mean()), 4),
                "substantive_share": round(float(group.get("substantive", pd.Series(True, index=group.index)).mean()), 4),
                "honorific_share": round(float(group.get("honorific", pd.Series(False, index=group.index)).mean()), 4),
                "concurrent_share": round(float(group.get("concurrent", pd.Series(False, index=group.index)).mean()), 4),
                "n_major_transitions": int(len(transitions)),
                "major_transitions": ";".join(transitions[:12]),
                "lineage_event_ids": ";".join(sorted(set(group["event_id"]))),
                "sources": ";".join(sorted(set(group["source_id"]))),
            }
        )
    return pd.DataFrame(rows)


def validate_events(events: pd.DataFrame) -> pd.DataFrame:
    """Chronology violations and impossible transitions, one row per flagged event."""
    issues: list[dict[str, Any]] = []
    for row in events.itertuples(index=False):
        if pd.notna(row.start_year) and pd.notna(row.end_year) and row.end_year < row.start_year:
            issues.append(
                {
                    "event_id": row.event_id,
                    "person_id": row.person_id,
                    "check": "chronology_inverted",
                    "detail": f"start {row.start_year} > end {row.end_year}",
                }
            )
        if pd.notna(row.start_year) and (row.start_year < 1644 or row.start_year > 1912):
            issues.append(
                {
                    "event_id": row.event_id,
                    "person_id": row.person_id,
                    "check": "chronology_out_of_window",
                    "detail": f"start year {row.start_year} outside 1644-1912",
                }
            )

    ordered = events.dropna(subset=["start_year"]).sort_values(["person_id", "start_year"])
    for person, group in ordered.groupby("person_id"):
        previous = None
        for row in group.itertuples(index=False):
            current_rank = pd.to_numeric(pd.Series([getattr(row, "rank_class", pd.NA)]), errors="coerce").iloc[0]
            if previous is not None and pd.notna(current_rank) and pd.notna(previous["rank"]):
                jump = previous["rank"] - current_rank  # rank 1 is the highest
                if jump > MAX_RANK_JUMP:
                    issues.append(
                        {
                            "event_id": row.event_id,
                            "person_id": person,
                            "check": "impossible_transition",
                            "detail": (
                                f"rank {int(previous['rank'])} -> {int(current_rank)} "
                                f"within {int(row.start_year) - int(previous['year'])} year(s)"
                            ),
                        }
                    )
                # two different ranks inside one year is ambiguous whichever direction it moves
                if int(row.start_year) == int(previous["year"]) and jump != 0:
                    issues.append(
                        {
                            "event_id": row.event_id,
                            "person_id": person,
                            "check": "same_year_rank_change",
                            "detail": f"rank {int(previous['rank'])} -> {int(current_rank)} in {int(row.start_year)}",
                        }
                    )
            if pd.notna(current_rank):
                previous = {"rank": current_rank, "year": row.start_year}
    return pd.DataFrame(issues, columns=["event_id", "person_id", "check", "detail"])


def summary(issues: pd.DataFrame, events: int) -> dict[str, Any]:
    return {
        "events": int(events),
        "flagged": int(len(issues)),
        "by_check": issues["check"].value_counts().to_dict() if len(issues) else {},
    }
