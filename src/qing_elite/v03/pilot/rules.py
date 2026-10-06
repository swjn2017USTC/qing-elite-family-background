"""Deterministic roster parser for standardized family-background sources (U05R).

First stage of the pipeline: rules before models. Given the text of a name-roster page
(同官录 / 同年齿录 style, after OCR or from a structured export) the parser emits one record
per kin mention with the ten fields the stage requires:

``focal_person, relation_type, kin_name, generation, degree_raw, office_raw, source_id,
locator, quote, offsets``

Everything it emits is anchored to a character span in the page text, so the verifier can
demand a verbatim quote and downstream code can always go back to the source. When the rules
cannot converge (no name-like token, no relation term, ambiguous segment) the record is
marked ``abstained`` instead of guessed — abstention is a measured outcome, not a failure.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from qing_elite.v03.design import load_relation_ontology, relation_index

#: Relation terms, longest first so 堂伯叔祖 wins over 伯叔祖 and 祖.
RELATION_TERMS: tuple[tuple[str, str], ...] = (
    ("堂伯叔祖", "great_uncle_paternal"),
    ("從堂伯叔祖", "great_uncle_paternal"),
    ("胞伯叔祖", "great_uncle_paternal"),
    ("伯叔曾祖", "great_great_uncle_paternal"),
    ("堂伯叔曾祖", "great_great_uncle_paternal"),
    ("嫡堂伯叔", "uncle_paternal"),
    ("從堂伯叔", "uncle_paternal"),
    ("堂伯叔", "uncle_paternal"),
    ("胞伯叔", "uncle_paternal"),
    ("胞伯", "uncle_paternal"),
    ("胞叔", "uncle_paternal"),
    ("堂伯", "uncle_paternal"),
    ("堂叔", "uncle_paternal"),
    ("伯叔祖", "great_uncle_paternal"),
    ("曾祖", "great_grandfather"),
    ("高祖", "great_great_grandfather"),
    ("祖父", "grandfather"),
    ("祖", "grandfather"),
    ("父", "father"),
    ("母", "mother"),
    ("祖母", "grandmother"),
    ("胞兄", "brother"),
    ("胞弟", "brother"),
    ("胞兄弟", "brother"),
    ("嫡堂兄", "cousin_paternal"),
    ("嫡堂弟", "cousin_paternal"),
    ("從堂兄弟", "cousin_paternal"),
    ("堂兄弟", "cousin_paternal"),
    ("堂兄", "cousin_paternal"),
    ("堂弟", "cousin_paternal"),
    ("胞姪", "nephew_paternal"),
    ("堂姪", "nephew_paternal"),
    ("從堂姪", "nephew_paternal"),
    ("姪", "nephew_paternal"),
    ("子", "son"),
    ("女", "daughter"),
    ("妻", "wife"),
    ("娶", "wife"),
    ("孫", "grandson"),
)

DEGREE_TERMS: tuple[str, ...] = (
    "進士", "舉人", "貢生", "監生", "生員", "庠生", "廩生", "增生", "附生",
    "蔭生", "蔭監", "捐納", "武舉", "武進士", "秀才", "恩貢", "拔貢", "副貢", "歲貢", "優貢",
)

OFFICE_TERMS: tuple[str, ...] = (
    "大學士", "尚書", "侍郎", "郎中", "員外郎", "主事", "御史", "給事中", "編修", "檢討",
    "庶吉士", "總督", "巡撫", "布政使", "按察使", "道員", "知府", "同知", "通判", "知州",
    "知縣", "縣丞", "主簿", "典史", "巡檢", "教授", "學正", "教諭", "訓導", "巡道", "鹽運使",
)

STATUS_TERMS: tuple[str, ...] = ("候選", "候補", "現任", "署理", "署", "分發", "試用", "加捐")

#: Single glyphs that can never be a given name at the position right after a kin term.
#: Kept deliberately short: roster names do use glyphs like 國/太/原, so a longer stoplist
#: silently eats real names (measured: 孫國光 was rejected while 國 was a stopword).
NAME_STOPWORDS: frozenset[str] = frozenset({"諱", "字", "號", "公", "氏", "某", "府", "君"})

#: A name ends where one of these begins. Roster entries put a status token (原任/候選),
#: a degree, an office, a reign year or the next kin term right after the given name, which
#: is what makes a 1–2 glyph name recoverable from unsegmented text.
NAME_END_MARKERS: tuple[str, ...] = (
    "原任", "候選", "候補", "現任", "署理", "分發", "試用", "加捐", "升用", "捐陞", "保舉",
    "投効", "承繼", "就職", "就塾", "業儒", "讀書", "幼", "殤", "早卒", "卒", "氏",
    "諱", "字", "號", "行",
    "乾", "嘉", "道", "咸", "同", "光", "康", "雍", "順", "宣",
    "縣", "府", "州", "道", "司", "廳", "省",
    "邑", "庠", "太", "監", "貢", "舉", "進", "武", "恩", "拔", "副", "歲", "優", "廩", "增", "附",
) + OFFICE_TERMS

#: Status tokens that are never a name: if the "name" candidate starts one of these, the
#: entry simply has no recoverable name and the parser must abstain (胞叔原任縣典史).
STATUS_GUARDS: tuple[str, ...] = ("原任", "候選", "候補", "現任", "署理", "分發", "試用", "加捐")

#: Compounds that contain a relation term but are not kin mentions.
RELATION_FALSE_POSITIVES: tuple[str, ...] = ("祖籍", "父老", "母子", "子女", "子孫", "子孫", "父兄")

CJK_RUN = re.compile(r"[\u3400-\u9fff]{1,8}")
RELATION_BY_TERM = dict(sorted(RELATION_TERMS, key=lambda item: -len(item[0])))
SEPARATORS = "、，；;。：:　 \n\t,|/"

_HTML_TABLE = re.compile(r"<table.*?</table>", re.S | re.I)
_CELL = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S | re.I)
_ANY_TAG = re.compile(r"<[^>]+>")


def normalize_ocr_markdown(markdown: str) -> str:
    """Turn PaddleOCR-VL markdown into the run-on text the roster parser expects.

    The service returns either plain text broken one glyph per line or an HTML table whose
    cells hold the roster text. Both are reduced to one continuous string: cell contents are
    separated by the roster separator, tags are dropped, and inter-line whitespace inside a
    roster run is removed (the original layout is vertical, so newlines carry no meaning).
    """
    if not markdown:
        return ""
    text = markdown
    if "<table" in text.lower():
        parts: list[str] = []
        for table in _HTML_TABLE.findall(text):
            for cell in _CELL.findall(table):
                cleaned = _ANY_TAG.sub("", cell).replace("\n", " ").strip()
                if cleaned:
                    parts.append(cleaned)
            parts.append("\n")
        remainder = _HTML_TABLE.sub(" ", text)
        text = "｜".join(parts) + " " + _ANY_TAG.sub(" ", remainder)
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\u00ad", "")
    # a line that is a single glyph is a layout artefact, not a boundary
    text = re.sub(r"\n+(?=[\u3400-\u9fff])", "", text)
    return text


class RuleParserError(ValueError):
    """Raised for caller mistakes (bad source configuration), never for hard text."""


@dataclass
class ParseOutcome:
    """Rows plus the counts needed to reason about abstention and convergence."""

    rows: list[dict[str, Any]] = field(default_factory=list)
    abstained: list[dict[str, Any]] = field(default_factory=list)
    relation_mentions: int = 0

    @property
    def convergence(self) -> float:
        total = len(self.rows) + len(self.abstained)
        return (len(self.rows) / total) if total else 0.0


def normalize_text(text: str) -> str:
    """NFKC + strip soft hyphens; offsets are computed on this normalised text."""
    text = unicodedata.normalize("NFKC", text)
    return text.replace("\u00ad", "")


def _segment(text: str, start: int, *, lookahead: int = 48) -> tuple[str, int, int]:
    """Return the roster segment starting at ``start`` plus its end offset."""
    end = start
    limit = min(len(text), start + lookahead)
    while end < limit and text[end] not in SEPARATORS:
        end += 1
    return text[start:end], start, end


def _first_name(text: str) -> tuple[str, int, int] | None:
    """Recover a 1–3 glyph given name from unsegmented roster text.

    The scan gives no separators, so the name is delimited by what follows it: a status
    token (原任／候選), a degree, an office, a reign year or the next kin term. The shortest
    prefix whose successor matches one of those markers wins; if nothing matches, a 2-glyph
    prefix is accepted and left to the verifier.
    """
    text = text.lstrip("諱名 ")
    if not text:
        return None
    # two glyphs first: most given names here are 2 glyphs, and the reign-year markers
    # (乾/嘉/道/咸/同/光/康/雍/順) are also common name glyphs, so shortest-first mis-splits
    for length in (2, 1, 3):
        candidate = text[:length]
        if len(candidate) < length:
            continue
        rest = text[length:]
        if not rest:
            continue
        if any(char in NAME_STOPWORDS for char in candidate):
            continue
        if candidate in RELATION_BY_TERM:
            continue
        if any(
            candidate == guard or guard.startswith(candidate) or candidate.startswith(guard)
            for guard in STATUS_GUARDS
        ):
            continue
        if any(rest.startswith(marker) for marker in NAME_END_MARKERS):
            return candidate, 0, length
    fallback = text[:2]
    if (
        len(fallback) == 2
        and not any(char in NAME_STOPWORDS for char in fallback)
        and not any(fallback.startswith(guard) for guard in STATUS_GUARDS)
    ):
        return fallback, 0, 2
    return None


def _find_terms(text: str, terms: tuple[str, ...]) -> list[tuple[str, int]]:
    found: list[tuple[str, int]] = []
    for term in terms:
        for match in re.finditer(re.escape(term), text):
            found.append((term, match.start()))
    return sorted(found, key=lambda item: item[1])


def parse_roster_text(
    text: str,
    *,
    focal_person: str,
    source_id: str,
    locator_prefix: str,
    page_number: int | None = None,
) -> ParseOutcome:
    """Parse one page/roster block into kin records anchored to character spans."""
    ontology = relation_index(load_relation_ontology())
    normalized = normalize_text(text)
    outcome = ParseOutcome()

    mentions: list[tuple[str, int]] = []
    for term in RELATION_BY_TERM:
        for match in re.finditer(re.escape(term), normalized):
            mentions.append((term, match.start()))
    mentions.sort(key=lambda item: item[1])
    outcome.relation_mentions = len(mentions)

    # drop overlapping mentions: the longest term starting earliest wins
    consumed_until = -1
    accepted: list[tuple[str, int]] = []
    for term, start in mentions:
        if start < consumed_until:
            continue
        if any(normalized.startswith(compound, start) for compound in RELATION_FALSE_POSITIVES):
            continue  # 祖籍 / 父子 … are not kin mentions
        accepted.append((term, start))
        consumed_until = start + len(term)

    for position, (term, start) in enumerate(accepted):
        relation_code = RELATION_BY_TERM[term]
        # the entry ends where the next kin mention begins: attributing a degree or office
        # from beyond that boundary would credit another person's career to this kin
        boundary = accepted[position + 1][1] if position + 1 < len(accepted) else len(normalized)
        segment, seg_start, seg_end = _segment(
            normalized, start, lookahead=min(80, max(8, boundary - start))
        )
        tail = segment[len(term) :]
        name = _first_name(tail)
        locator = f"{locator_prefix}#p{page_number or 0}:{seg_start}-{seg_end}"
        base = {
            "focal_person": focal_person,
            "relation_type": relation_code,
            "source_id": source_id,
            "locator": locator,
            "quote": segment,
            "offsets": [seg_start, seg_end],
            "page_number": page_number,
        }
        if name is None:
            outcome.abstained.append({**base, "reason": "no_name_token_after_relation"})
            continue
        kin_name, name_start, _ = name
        degree = next((token for token in DEGREE_TERMS if token in segment), None)
        office = next((token for token in OFFICE_TERMS if token in segment), None)
        status = next((token for token in STATUS_TERMS if token in segment), None)
        outcome.rows.append(
            {
                **base,
                "kin_name": kin_name,
                "kin_name_offsets": [seg_start + len(term) + name_start, seg_start + len(term) + name_start + len(kin_name)],
                "generation": ontology[relation_code]["generation_delta"],
                "relation_class": ontology[relation_code]["class"],
                "degree_raw": degree,
                "office_raw": office,
                "status_raw": status,
                "confidence": "rule",
            }
        )
    return outcome


def parse_pages(pages: list[dict[str, Any]], *, focal_person: str, source_id: str) -> ParseOutcome:
    """Concatenate per-page parses; offsets stay page-local and locators carry the page."""
    combined = ParseOutcome()
    for page in pages:
        outcome = parse_roster_text(
            normalize_ocr_markdown(page.get("markdown_text") or ""),
            focal_person=focal_person,
            source_id=source_id,
            locator_prefix=source_id,
            page_number=page.get("page_number_1based"),
        )
        combined.rows.extend(outcome.rows)
        combined.abstained.extend(outcome.abstained)
        combined.relation_mentions += outcome.relation_mentions
    return combined
