"""CGED-Q JSL office-string classification (P02, tier D).

``官职一`` is a transcription, not a controlled vocabulary: 15,669 distinct
strings in 1760-1798 alone. This module turns one raw string into
``(office_core, category, appointment markers)`` using the rules declared in
``config/offices.yaml``, and returns ``unknown`` rather than guessing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from qing_elite.utils.text import (
    cut_at_left_connector,
    cut_at_right_connector,
    first_keyword_match,
    is_junk_office,
    longest_vocabulary_match,
    normalize_office_chars,
    strip_prefixes,
    strip_suffixes,
)


@dataclass(frozen=True, slots=True)
class OfficeClassification:
    """Classification result for one raw CGED-Q office string."""

    raw: str
    normalized: str
    core: str | None
    category: str  # tier-D category, or an exclusion category, or "unknown"
    tier: str | None  # "D" when the core office belongs to the D categories
    is_acting: bool
    is_concurrent: bool


class CgedqOfficeClassifier:
    """Classify CGED-Q office strings against config/offices.yaml."""

    def __init__(self, cfg: Mapping[str, Any]) -> None:
        section = cfg["cgedq"]
        self._simplify: Mapping[str, str] = section.get("simplify_map") or {}
        self._variants: Mapping[str, str] = section.get("char_variants") or {}
        self._prefixes: list[str] = section["strip_prefixes"]
        self._suffixes: list[str] = section.get("strip_suffixes") or []
        self._left: list[str] = section["keep_left_connectors"]
        self._right: list[str] = section["keep_right_connectors"]
        self._keyword_categories: dict[str, list[str]] = section["institution_keyword_categories"]
        self._junk: list[str] = self._keyword_categories.get("未填寫", [])
        self._acting_markers: list[str] = section["acting_markers"]
        self._concurrent_markers: list[str] = section["concurrent_markers"]
        self._categories: dict[str, list[str]] = section["categories"]
        self._core_to_category: dict[str, str] = {
            office: category
            for category, offices in self._categories.items()
            for office in offices
        }
        # D-tier vocabulary is the union of the declared categories: one source of truth.
        self._core_offices: list[str] = sorted(self._core_to_category)
        # Longest-first so 復設訓導 wins over 訓導.
        self._core_sorted = sorted(self._core_offices, key=len, reverse=True)
        self._known_other: dict[str, list[str]] = section.get("known_other_offices") or {}
        self._other_sorted: list[tuple[str, str]] = sorted(
            (
                (office, category)
                for category, offices in self._known_other.items()
                for office in offices
            ),
            key=lambda pair: len(pair[0]),
            reverse=True,
        )

    def classify(self, raw: object) -> OfficeClassification:
        text = "" if raw is None else str(raw)
        if is_junk_office(text, self._junk):
            return OfficeClassification(
                raw=text, normalized="", core=None, category="未填寫", tier=None,
                is_acting=False, is_concurrent=False,
            )
        normalized = normalize_office_chars(text, self._simplify, self._variants)
        is_acting = any(marker in normalized for marker in self._acting_markers)
        is_concurrent = any(marker in normalized for marker in self._concurrent_markers)

        institution = first_keyword_match(normalized, self._keyword_categories)
        if institution and institution != "未填寫":
            return OfficeClassification(
                raw=text, normalized=normalized, core=None, category=institution,
                tier=None, is_acting=is_acting, is_concurrent=is_concurrent,
            )

        body = cut_at_right_connector(normalized, self._right)
        # "A 管/兼 B 事" (e.g. 吏目管典史事) names the substantive post first; the
        # trailing duty clause only appears in this form, so the cut is gated on it.
        # Without the gate, functional prefixes like 管河 in 邳州管河州同 would be
        # mistaken for the connector and the real office would be discarded.
        if body.endswith("事"):
            body = cut_at_left_connector(body, self._left)
        body = strip_prefixes(body, self._prefixes)
        body = strip_suffixes(body, self._suffixes)

        core = longest_vocabulary_match(body, self._core_sorted)
        if core is None:
            core = self._match_by_trailing_body(body, self._core_sorted)
        if core is None:
            other = self._match_other(body)
            if other is not None:
                office, category = other
                return OfficeClassification(
                    raw=text, normalized=normalized, core=office, category=category,
                    tier=None, is_acting=is_acting, is_concurrent=is_concurrent,
                )
            category = first_keyword_match(normalized, self._keyword_categories) or "unknown"
            return OfficeClassification(
                raw=text, normalized=normalized, core=None, category=category, tier=None,
                is_acting=is_acting, is_concurrent=is_concurrent,
            )
        return OfficeClassification(
            raw=text,
            normalized=normalized,
            core=core,
            category=self._core_to_category[core],
            tier="D",
            is_acting=is_acting,
            is_concurrent=is_concurrent,
        )

    def _match_other(self, body: str) -> tuple[str, str] | None:
        """Match a known non-D office name; returns ``(office, category)``."""
        for office, category in self._other_sorted:
            if office in body:
                return office, category
        return None

    def _match_by_trailing_body(self, body: str, vocabulary: list[str]) -> str | None:
        """Catch prefixed forms such as ``某某縣知縣`` that survive stripping."""
        for core in vocabulary:
            if body.endswith(core):
                return core
        return None
