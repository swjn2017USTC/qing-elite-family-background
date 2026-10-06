"""Linkage validators: chronology, geography, career transition (U06R).

These are the checks that catch a link which looks fine field-by-field and is still
impossible. They run *before* a link is auto-accepted and their output feeds the risk score,
so a violation routes the pair to review instead of silently entering the entity table.

* **chronology** — a pair whose life/activity windows cannot overlap (e.g. one died before
  the other was born, or one's latest observation precedes the other's earliest by a decade);
* **geography** — two records claiming the same person but different declared native
  provinces, where at least one side is not "unknown" (unknown never counts as a conflict);
* **career transition** — a person observed in two different core offices in the *same*
  season and edition, which the roster would not record for one person.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

UNKNOWN_TOKENS = {"", "unknown", "nan", "none", "null", "不詳", "未詳"}


def _known(value: Any) -> bool:
    return isinstance(value, str) and value.strip().lower() not in UNKNOWN_TOKENS and bool(value.strip())


def chronology_flag(left_start: Any, left_end: Any, right_start: Any, right_end: Any, *, tolerance: int = 5) -> int:
    """1 when the two windows cannot belong to one career."""
    values = [left_start, left_end, right_start, right_end]
    if any(pd.isna(value) for value in values):
        return 0
    left_start, left_end, right_start, right_end = (int(value) for value in values)
    if left_end + tolerance < right_start or right_end + tolerance < left_start:
        return 1
    return 0


def geography_flag(left_province: Any, right_province: Any) -> int:
    """1 when both sides declare a province and the provinces differ."""
    if not _known(left_province) or not _known(right_province):
        return 0
    return int(str(left_province).strip() != str(right_province).strip())


def career_transition_flag(
    left_office: Any, right_office: Any, *, same_edition_observed: bool
) -> int:
    """1 when one person appears in two different core offices in the same edition."""
    if not same_edition_observed:
        return 0
    if not _known(left_office) or not _known(right_office):
        return 0
    return int(str(left_office).strip() != str(right_office).strip())


def validate_pairs(pairs: pd.DataFrame) -> pd.DataFrame:
    """Attach the three validator flags to a pair table.

    Column names are the v0.2 comparison-table names so the function works on both the
    recovered gold and freshly generated candidate pairs.
    """
    frame = pd.DataFrame(index=pairs.index)
    frame["chronology_violation"] = [
        chronology_flag(a, b, c, d)
        for a, b, c, d in zip(
            pairs.get("l_first_year", pd.Series(index=pairs.index, dtype=float)),
            pairs.get("l_last_year", pd.Series(index=pairs.index, dtype=float)),
            pairs.get("r_c_index_year", pd.Series(index=pairs.index, dtype=float)),
            pairs.get("r_c_index_year", pd.Series(index=pairs.index, dtype=float)),
        )
    ]
    frame["geography_conflict"] = [
        geography_flag(a, b)
        for a, b in zip(
            pairs.get("l_native_province", pd.Series(index=pairs.index, dtype=object)),
            pairs.get("r_province_norm", pd.Series(index=pairs.index, dtype=object)),
        )
    ]
    frame["career_transition_conflict"] = [
        career_transition_flag(a, b, same_edition_observed=bool(same))
        for a, b, same in zip(
            pairs.get("l_primary_office_core", pd.Series(index=pairs.index, dtype=object)),
            pairs.get("r_primary_office_core", pd.Series(index=pairs.index, dtype=object)),
            pairs.get("same_office_same_edition", pd.Series(index=pairs.index, dtype=bool)).fillna(False),
        )
    ]
    return frame


def summary(validated: pd.DataFrame) -> dict[str, Any]:
    return {
        "chronology_violations": int(validated["chronology_violation"].sum()),
        "geography_conflicts": int(validated["geography_conflict"].sum()),
        "career_transition_conflicts": int(validated["career_transition_conflict"].sum()),
    }
