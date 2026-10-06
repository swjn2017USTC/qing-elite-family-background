"""Text normalization helpers shared by the P02 universe build.

CGED-Q JSL is a transcription dataset: office strings mix simplified and
traditional characters, use variant glyphs, and carry qualitative prefixes
("特調", "署理", "兼管…事"). Everything that rewrites such strings lives here so
the behaviour is testable in isolation and identical across callers.
"""

from __future__ import annotations

import re
from typing import Iterable, Mapping, Sequence

_WHITESPACE = re.compile(r"\s+")
# Full-width and half-width question marks are used in the source for
# unreadable characters; they must never be treated as an office name.
_JUNK_CHARS = frozenset("?？")


def normalize_office_chars(
    raw: str,
    simplify_map: Mapping[str, str] | None = None,
    variant_map: Mapping[str, str] | None = None,
) -> str:
    """Collapse whitespace and fold simplified/variant characters to canonical ones."""
    simplify_map = simplify_map or {}
    variant_map = variant_map or {}
    text = _WHITESPACE.sub("", raw)
    return "".join(variant_map.get(ch) or simplify_map.get(ch) or ch for ch in text)


def is_junk_office(raw: str, junk_values: Sequence[str]) -> bool:
    """True when the office cell carries no office information at all."""
    text = _WHITESPACE.sub("", raw)
    if not text or text.lower() == "nan":
        return True
    if text in set(junk_values):
        return True
    return bool(text) and all(ch in _JUNK_CHARS for ch in text)


def strip_prefixes(text: str, prefixes: Iterable[str]) -> str:
    """Strip known modifier prefixes, longest first, repeatedly."""
    ordered = sorted(set(prefixes), key=len, reverse=True)
    changed = True
    while changed and text:
        changed = False
        for prefix in ordered:
            if text.startswith(prefix) and len(text) > len(prefix):
                text = text[len(prefix):]
                changed = True
                break
    return text


def strip_suffixes(text: str, suffixes: Iterable[str]) -> str:
    """Strip trailing qualifiers such as 事務 / 銜."""
    ordered = sorted(set(suffixes), key=len, reverse=True)
    changed = True
    while changed and text:
        changed = False
        for suffix in ordered:
            if text.endswith(suffix) and len(text) > len(suffix):
                text = text[: -len(suffix)]
                changed = True
                break
    return text


def cut_at_left_connector(text: str, connectors: Iterable[str]) -> str:
    """Return the part before the earliest "A 兼 B" style connector.

    ``吏目管典史事`` -> ``吏目``: the substantive post is named first and the
    trailing clause describes an additional duty.
    """
    cut = len(text)
    for connector in connectors:
        index = text.find(connector)
        if index > 0:
            cut = min(cut, index)
    return text[:cut]


def cut_at_right_connector(text: str, connectors: Iterable[str]) -> str:
    """Return the part after the latest "A 改授 B" style connector.

    ``知縣改授教諭`` -> ``教諭``: the appointment actually held is named last.
    """
    cut = 0
    for connector in connectors:
        index = text.rfind(connector)
        if index >= 0:
            cut = max(cut, index + len(connector))
    return text[cut:]


def longest_vocabulary_match(text: str, vocabulary: Iterable[str]) -> str | None:
    """Return the longest vocabulary entry contained in ``text``."""
    best: str | None = None
    for candidate in vocabulary:
        if candidate and candidate in text and (best is None or len(candidate) > len(best)):
            best = candidate
    return best


def first_keyword_match(text: str, keyword_map: Mapping[str, Sequence[str]]) -> str | None:
    """Return the first category whose keyword occurs in ``text``.

    ``keyword_map`` preserves YAML order, so the caller controls precedence.
    """
    for category, keywords in keyword_map.items():
        for keyword in keywords:
            if keyword and keyword in text:
                return category
    return None
