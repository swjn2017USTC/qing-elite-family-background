"""Pair features for entity resolution (U06R).

The v0.2 pipeline already computed comparison columns for its gold pairs; this module keeps
those and adds the features the upstream ML-record-linkage paper relies on that were missing
here:

* **pinyin** equality and initial equality (catches variant characters that sound the same),
* **character-set Jaccard** and normalised edit distance as a *shape proxy* — the upstream
  approach uses stroke n-gram embeddings, but those ship as a CC BY-NC dictionary plus a
  2.6 GB word2vec binary, so nothing is vendored: the proxy captures typographic overlap
  without redistributing their assets (see ``UPSTREAM`` in the package docstring),
* **surname** equality on the raw Chinese strings,
* name-frequency and career-context encodings already present in the v0.2 gold.

Everything is a pure function of the two records, so the same code scores the gold pairs and
any newly generated candidate pairs.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from pypinyin import Style, lazy_pinyin

NAME_MATCH_SCORES = {
    "direct": 1.0,
    "alias": 0.8,
    "alias_block": 0.8,
    "fuzzy": 0.5,
    "none": 0.0,
}

FEATURE_COLUMNS = (
    "name_match_type_score",
    "province_compatible",
    "degree_equal",
    "banner_equal",
    "era_overlap",
    "raw_name_equal",
    "name_freq_log",
    "same_office_same_edition",
    "n_shared_editions_log",
    "pinyin_equal",
    "pinyin_initial_equal",
    "char_jaccard",
    "edit_similarity",
    "surname_equal",
)


def pinyin_key(name: Any, *, initials: bool = False) -> str:
    """Tone-free pinyin key for a Chinese name (empty when the name has no Han characters)."""
    if not isinstance(name, str) or not name.strip():
        return ""
    style = Style.FIRST_LETTER if initials else Style.NORMAL
    return "".join(lazy_pinyin(name.strip(), style=style, errors="ignore"))


def char_jaccard(left: Any, right: Any) -> float:
    left_set = set(str(left)) if isinstance(left, str) else set()
    right_set = set(str(right)) if isinstance(right, str) else set()
    if not left_set or not right_set:
        return 0.0
    return len(left_set & right_set) / len(left_set | right_set)


def edit_similarity(left: Any, right: Any) -> float:
    """1 - normalised Levenshtein distance (implemented locally; no extra dependency)."""
    a = str(left) if isinstance(left, str) else ""
    b = str(right) if isinstance(right, str) else ""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    previous = list(range(len(b) + 1))
    for i, char_a in enumerate(a, start=1):
        current = [i]
        for j, char_b in enumerate(b, start=1):
            current.append(
                min(
                    previous[j] + 1,
                    current[j - 1] + 1,
                    previous[j - 1] + (char_a != char_b),
                )
            )
        previous = current
    return 1.0 - previous[-1] / max(len(a), len(b))


def _boolean(series: pd.Series) -> pd.Series:
    """Map tri-state comparison columns to 0/1 with a 0.5 prior for 'unknown'."""
    return series.map(lambda value: np.nan if pd.isna(value) else float(bool(value))).fillna(0.5)


def build_features(pairs: pd.DataFrame, *, left_name: str = "l_name_chn", right_name: str = "r_c_name_chn") -> pd.DataFrame:
    """Return the modelling frame: one row per pair, one column per feature."""
    frame = pd.DataFrame(index=pairs.index)
    frame["pair_id"] = pairs.get("pair_id", pd.Series(index=pairs.index, dtype=str))
    frame["name_match_type_score"] = (
        pairs.get("name_match_type", pd.Series(index=pairs.index, dtype=str))
        .map(lambda value: NAME_MATCH_SCORES.get(str(value), 0.0))
        .astype(float)
    )
    frame["province_compatible"] = _boolean(pairs.get("province_compatible", pd.Series(dtype=object)))
    frame["degree_equal"] = _boolean(pairs.get("degree_equal", pd.Series(dtype=object)))
    frame["banner_equal"] = _boolean(pairs.get("banner_equal", pd.Series(dtype=object)))
    frame["era_overlap"] = _boolean(pairs.get("era_overlap", pd.Series(dtype=object)))
    frame["raw_name_equal"] = (
        pairs.get("raw_name_equal", pd.Series(dtype=object)).map(lambda value: float(bool(value)) if pd.notna(value) else 0.5)
    )
    frequency = pd.to_numeric(pairs.get("name_freq_max", pd.Series(dtype=float)), errors="coerce").fillna(1.0)
    frame["name_freq_log"] = np.log1p(frequency)
    frame["same_office_same_edition"] = (
        pairs.get("same_office_same_edition", pd.Series(dtype=object))
        .map(lambda value: float(bool(value)) if pd.notna(value) else 0.0)
    )
    shared = pd.to_numeric(pairs.get("n_shared_editions", pd.Series(dtype=float)), errors="coerce").fillna(0.0)
    frame["n_shared_editions_log"] = np.log1p(shared)

    left = pairs.get(left_name, pd.Series(index=pairs.index, dtype=str))
    right = pairs.get(right_name, pd.Series(index=pairs.index, dtype=str))
    frame["pinyin_equal"] = [
        float(bool(pinyin_key(a)) and pinyin_key(a) == pinyin_key(b))
        for a, b in zip(left, right)
    ]
    frame["pinyin_initial_equal"] = [
        float(bool(pinyin_key(a, initials=True)) and pinyin_key(a, initials=True) == pinyin_key(b, initials=True))
        for a, b in zip(left, right)
    ]
    frame["char_jaccard"] = [char_jaccard(a, b) for a, b in zip(left, right)]
    frame["edit_similarity"] = [edit_similarity(a, b) for a, b in zip(left, right)]
    left_surname = pairs.get("l_surname", pd.Series(index=pairs.index, dtype=str))
    right_surname = pairs.get("r_c_surname_chn", pd.Series(index=pairs.index, dtype=str))
    frame["surname_equal"] = [
        float(bool(str(a).strip()) and str(a).strip() == str(b).strip())
        for a, b in zip(left_surname, right_surname)
    ]
    return frame


def feature_matrix(features: pd.DataFrame) -> pd.DataFrame:
    """Numeric matrix in the declared feature order, NaNs filled with the neutral 0.5."""
    matrix = features.loc[:, [column for column in FEATURE_COLUMNS if column in features.columns]].copy()
    return matrix.fillna(0.5).astype(float)
