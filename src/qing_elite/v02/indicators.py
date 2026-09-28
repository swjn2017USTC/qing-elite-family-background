"""v0.2 indicators: documented evidence only, legacy metrics kept separate (U01).

The v0.1 indicators treated "no office field in any known ancestor slot" as a definite
``0``. The v0.2 indicators instead report *documented* facts: an indicator is ``1`` only
when a ``positive`` assertion exists, ``0`` only when the corresponding
``explicit_negative`` evidence exists for every known slot, and ``NA`` otherwise
(``unknown != 0``). The old numbers survive untouched behind the ``legacy_`` prefix and
must never be merged into the primary frame.
"""

from __future__ import annotations

import pandas as pd

from qing_elite.v02.contracts import (
    ANCILLARY_ATTRIBUTES,
    LEGACY_PREFIX,
    PRIMARY_COUNT_COLUMNS,
    PRIMARY_METRIC_COLUMNS,
    PRIMARY_REVIEW_STATUSES,
    validate_table,
)

__all__ = [
    "select_primary_assertions",
    "build_primary_indicators",
    "build_legacy_indicators",
    "assert_no_legacy_columns",
]


def select_primary_assertions(
    assertions: pd.DataFrame, entities: pd.DataFrame
) -> tuple[pd.DataFrame, dict[str, int]]:
    """Assertions allowed into the primary indicators, plus the exclusion counts."""
    resolved = set(entities.loc[entities["resolution_status"] == "resolved", "entity_id"])
    in_scope = assertions["entity_id"].isin(resolved)
    reviewed = assertions["review_status"].isin(PRIMARY_REVIEW_STATUSES)
    no_conflict = assertions["assertion_state"] != "conflict"
    keep = in_scope & reviewed & no_conflict
    exclusions = {
        "unresolved_entity": int((~in_scope).sum()),
        "pending_or_rejected_review": int((in_scope & ~reviewed).sum()),
        "conflict_state": int((in_scope & reviewed & ~no_conflict).sum()),
    }
    return assertions.loc[keep].copy(), exclusions


def _counts(frame: pd.DataFrame, attribute: str, state: str) -> pd.Series:
    rows = frame[(frame["attribute"] == attribute) & (frame["assertion_state"] == state)]
    return rows.groupby("entity_id").size()


def build_primary_indicators(
    assertions: pd.DataFrame, entities: pd.DataFrame
) -> pd.DataFrame:
    """Person-level v0.2 indicators over the primary-eligible, reviewable assertions.

    Raises if an assertion that must never reach the primary frame is handed in
    (pending review, unresolved entity, or unresolved conflict) — defence in depth
    behind :func:`select_primary_assertions`.
    """
    resolved = set(entities.loc[entities["resolution_status"] == "resolved", "entity_id"])
    forbidden = assertions[
        (~assertions["entity_id"].isin(resolved))
        | (~assertions["review_status"].isin(PRIMARY_REVIEW_STATUSES))
        | (assertions["assertion_state"] == "conflict")
    ]
    if len(forbidden):
        sample = forbidden["entity_id"].head(3).tolist()
        raise ValueError(
            "assertions that may not enter the primary frame were passed in "
            f"(n={len(forbidden)}, e.g. {sample}); filter with select_primary_assertions()"
        )
    unknown_attributes = set(assertions["attribute"]) - set(ANCILLARY_ATTRIBUTES)
    if unknown_attributes:
        raise ValueError(f"unknown attributes in the ledger: {sorted(unknown_attributes)}")

    eligible = entities.loc[entities["primary_eligible"], ["entity_id"]].rename(
        columns={"entity_id": "person_uid"}
    )
    frame = eligible.copy()
    for attribute in ANCILLARY_ATTRIBUTES:
        for state in ("positive", "explicit_negative", "unknown"):
            column = f"n_{state}_{attribute}"
            counts = _counts(assertions, attribute, state).rename(column)
            frame = frame.merge(counts, left_on="person_uid", right_index=True, how="left")
            frame[column] = frame[column].fillna(0).astype("Int64")

    frame["n_known_slots"] = frame["n_positive_name"]
    frame["ancestor_identity_coverage_1g"] = (frame["n_known_slots"] >= 1).astype("Int64")
    frame["ancestor_identity_coverage_2g"] = (frame["n_known_slots"] >= 2).astype("Int64")
    frame["ancestor_identity_coverage_3g"] = (frame["n_known_slots"] >= 3).astype("Int64")

    known = frame["n_known_slots"].astype("Int64")
    for attribute in ("office", "degree"):
        definite = frame[f"n_positive_{attribute}"] + frame[f"n_explicit_negative_{attribute}"]
        ratio = definite.astype("Float64") / known.where(known > 0)
        frame[f"attribute_ascertainment_{attribute}"] = ratio.astype(float)

    frame["documented_ancestor_official_any"] = _documented_any(frame, "office")
    frame["documented_ancestor_degree_any"] = _documented_any(frame, "degree")
    frame["documented_family_capital_any"] = _documented_capital(frame)
    frame["documented_commoner_explicit"] = _documented_commoner(frame)
    frame["source_protocol_complete"] = pd.array([pd.NA] * len(frame), dtype="boolean")

    primary = frame[
        ["person_uid", *PRIMARY_COUNT_COLUMNS, *PRIMARY_METRIC_COLUMNS]
    ].reset_index(drop=True)
    return validate_table("person_indicators_v02", primary)


def _documented_any(frame: pd.DataFrame, attribute: str) -> pd.Series:
    positive = frame[f"n_positive_{attribute}"]
    negative = frame[f"n_explicit_negative_{attribute}"]
    known = frame["n_known_slots"]
    complete_negative = (known > 0) & (negative == known) & (positive == 0)
    result = pd.Series(pd.NA, index=frame.index, dtype="Int64")
    result[(positive > 0)] = 1
    result[complete_negative] = 0
    return result


def _documented_capital(frame: pd.DataFrame) -> pd.Series:
    positive = frame["n_positive_office"] + frame["n_positive_degree"]
    negative = frame["n_explicit_negative_office"] + frame["n_explicit_negative_degree"]
    known = frame["n_known_slots"]
    complete_negative = (known > 0) & (negative == 2 * known) & (positive == 0)
    result = pd.Series(pd.NA, index=frame.index, dtype="Int64")
    result[positive > 0] = 1
    result[complete_negative] = 0
    return result


def _documented_commoner(frame: pd.DataFrame) -> pd.Series:
    """1 only for a fully documented, explicitly non-official three-generation line."""
    positive = frame["n_positive_office"] + frame["n_positive_degree"]
    all_negative = (
        (frame["n_known_slots"] == 3)
        & (frame["n_explicit_negative_office"] == 3)
        & (frame["n_explicit_negative_degree"] == 3)
        & (positive == 0)
    )
    result = pd.Series(pd.NA, index=frame.index, dtype="Int64")
    result[positive > 0] = 0
    result[all_negative] = 1
    return result


def build_legacy_indicators(v01_indicators: pd.DataFrame) -> pd.DataFrame:
    """Every v0.1 indicator, renamed with the ``legacy_`` prefix."""
    rename = {
        column: f"{LEGACY_PREFIX}{column}"
        for column in v01_indicators.columns
        if column != "person_uid"
    }
    return v01_indicators.rename(columns=rename)


def assert_no_legacy_columns(frame: pd.DataFrame) -> None:
    """Primary tables must not carry legacy metrics."""
    offending = [column for column in frame.columns if column.startswith(LEGACY_PREFIX)]
    if offending:
        raise ValueError(f"legacy columns are not allowed in a primary table: {offending}")
