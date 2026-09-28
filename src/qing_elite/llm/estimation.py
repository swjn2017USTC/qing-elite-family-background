"""Cost estimation for the LLM enrichment stages (P03).

No LLM is called here. The estimate is built from *real* source passages: a
stratified sample of the persons that CBDB cannot cover is looked up in the
public-domain 《清史稿》 text on Chinese Wikisource, and the family window that a
P04/P05 call would actually send is measured on that text.

Prices and the character/token ratio come from DeepSeek's published documentation
(retrieved 2026-09-14):

* ``deepseek-flash`` input (cache miss) 1 元 / 1M tokens off-peak, 2 元 peak;
* output 4 元 / 1M off-peak, 8 元 peak;
* 1 Chinese character ≈ 0.6 token.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import httpx
import pandas as pd

WIKISOURCE_API = "https://zh.wikisource.org/w/api.php"
USER_AGENT = "qing-elite-family-background/0.1 (P03 cost estimation; contact: repo owner)"
PASSAGE_CACHE = Path("data/interim/passages")

TOKENS_PER_CJK_CHAR = 0.6
PROMPT_OVERHEAD_TOKENS = 420  # system prompt + instructions + JSON schema skeleton

# 元 per 1M tokens, deepseek-flash
PRICES_CNY_PER_MTOK: dict[str, dict[str, float]] = {
    "off_peak": {"input_cache_miss": 1.0, "input_cache_hit": 0.02, "output": 4.0},
    "peak": {"input_cache_miss": 2.0, "input_cache_hit": 0.04, "output": 8.0},
}

# Keywords from OMP_PLAN.md section 15: used only to *locate* the window.
FAMILY_KEYWORDS: tuple[str, ...] = (
    "父",
    "祖",
    "曾祖",
    "世",
    "家",
    "蔭",
    "廕",
    "貢",
    "舉人",
    "進士",
    "官",
    "任",
    "授",
)

# The single-call output schema from OMP_PLAN.md section 16, with all fields empty.
OUTPUT_TEMPLATE = json.dumps(
    {
        "person_id": "",
        "father": {"name": None, "degree": None, "office": None, "evidence": None},
        "grandfather": {"name": None, "degree": None, "office": None, "evidence": None},
        "great_grandfather": {"name": None, "degree": None, "office": None, "evidence": None},
        "ambiguities": [],
        "confidence": "high",
        "insufficient_evidence": False,
    },
    ensure_ascii=False,
    indent=2,
)


@dataclass(frozen=True, slots=True)
class Passage:
    """One 《清史稿》 lookup result."""

    name: str
    found: bool
    volume_title: str | None
    url: str | None
    text: str
    error: str | None = None


def fetch_passage(name: str, *, cache_dir: Path = PASSAGE_CACHE, sleep_seconds: float = 1.5) -> Passage:
    """Fetch the 《清史稿》 biography text for ``name`` (cached on disk)."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{name}.json"
    if cache_file.exists():
        payload = json.loads(cache_file.read_text(encoding="utf-8"))
        return Passage(**payload)

    passage = _fetch_passage_uncached(name, sleep_seconds=sleep_seconds)
    cache_file.write_text(
        json.dumps(asdict(passage), ensure_ascii=False), encoding="utf-8"
    )
    return passage


def _fetch_passage_uncached(name: str, *, sleep_seconds: float) -> Passage:
    """Search 《清史稿》, then fetch candidate volumes until a biography is found.

    A volume that merely mentions the name (e.g. 藝文志 listing his works) must not
    be mistaken for the biography, so a volume counts only when it carries the
    person's own section or a paragraph that opens with the name.
    """
    headers = {"User-Agent": USER_AGENT}
    tried: list[str] = []
    try:
        with _client() as client:
            search = client.get(
                WIKISOURCE_API,
                params={
                    "action": "query",
                    "list": "search",
                    "srsearch": f"{name} 清史稿",
                    "srlimit": 8,
                    "format": "json",
                    "formatversion": 2,
                },
            )
            search.raise_for_status()
            hits = [
                hit["title"]
                for hit in search.json().get("query", {}).get("search", [])
                if str(hit.get("title", "")).startswith("清史稿/")
            ]
            if not hits:
                return Passage(name=name, found=False, volume_title=None, url=None, text="")
            # 列傳 volumes (roughly 卷250+) are where biographies live; try those first.
            hits.sort(key=_volume_preference)
            for title in hits[:3]:
                tried.append(title)
                time.sleep(sleep_seconds)
                revision = client.get(
                    WIKISOURCE_API,
                    params={
                        "action": "query",
                        "prop": "revisions",
                        "titles": title,
                        "rvprop": "content",
                        "rvslots": "main",
                        "format": "json",
                        "formatversion": 2,
                    },
                )
                revision.raise_for_status()
                pages = revision.json().get("query", {}).get("pages", [])
                content = ""
                if pages and pages[0].get("revisions"):
                    content = pages[0]["revisions"][0]["slots"]["main"]["content"]
                section = extract_person_section(content, name)
                if section:
                    url = f"https://zh.wikisource.org/wiki/{title.replace(' ', '_')}"
                    return Passage(
                        name=name, found=True, volume_title=title, url=url, text=section
                    )
            return Passage(
                name=name,
                found=False,
                volume_title=None,
                url=None,
                text="",
                error="no biography section in tried volumes: " + ", ".join(tried),
            )
    except Exception as error:  # noqa: BLE001 - the estimate reports failures verbatim
        return Passage(
            name=name,
            found=False,
            volume_title=None,
            url=None,
            text="",
            error=f"{type(error).__name__}: {error}",
        )
    finally:
        time.sleep(sleep_seconds)


def _volume_preference(title: str) -> tuple[int, str]:
    """Rank candidate volumes: biography volumes (卷250+) before the rest."""
    match = re.search(r"卷(\d+)", title)
    number = int(match.group(1)) if match else 0
    return (0 if number >= 250 else 1, title)


# This Wikisource edition mixes heading levels: some volumes use "=名=" (h1),
# others "==名==" (h2).
_SECTION = re.compile(r"^=+\s*([^=\n]+?)\s*=+\s*$", re.MULTILINE)
# Volumes without headings run the biographies together; a biography then starts
# with "名，字…" or "名，…" at the beginning of a line.
_OPENING = re.compile(r"^([^\s，。；：]{2,4})[，,]", re.MULTILINE)
MIN_BIO_CHARS = 150


def extract_person_section(content: str, name: str, *, max_chars: int = 6000) -> str:
    """Extract the biography for ``name``, or ``""`` when this volume has none.

    Two fingerprints count as a biography: a ``== name ==`` section heading, or a
    paragraph that opens with ``name，`` (the standard opening of a 傳). A bare
    mention elsewhere in the volume (bibliographies, cross-references) is rejected.
    """
    if not content:
        return ""
    matches = list(_SECTION.finditer(content))
    for index, match in enumerate(matches):
        heading = match.group(1).strip()
        if name in heading:
            start = match.end()
            end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
            return content[start:end].strip()[:max_chars]
    paragraph = re.search(rf"^{re.escape(name)}[，,]", content, re.MULTILINE)
    if paragraph:
        start = paragraph.start()
        following = content.find("\n\n", start)
        end = following if following > start else start + max_chars
        return content[start:end].strip()[:max_chars]
    return ""


def family_window(
    text: str,
    *,
    keywords: Sequence[str] = FAMILY_KEYWORDS,
    radius: int = 1000,
    max_chars: int = 3000,
) -> str:
    """Merge ±``radius`` windows around family keywords, capped at ``max_chars``.

    Mirrors OMP_PLAN.md section 15: keywords are a recall device, not the answer;
    only the local window is ever sent to the model.
    """
    if not text:
        return ""
    spans: list[tuple[int, int]] = []
    for keyword in keywords:
        start = 0
        while True:
            index = text.find(keyword, start)
            if index < 0:
                break
            spans.append((max(0, index - radius), min(len(text), index + radius)))
            start = index + len(keyword)
    if not spans:
        return text[:max_chars]
    spans.sort()
    merged: list[list[int]] = [list(spans[0])]
    for start, end in spans[1:]:
        if start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    pieces: list[str] = []
    used = 0
    for start, end in merged:
        if used >= max_chars:
            break
        piece = text[start:end]
        room = max_chars - used
        pieces.append(piece[:room])
        used += min(len(piece), room)
    return "…".join(pieces)


@dataclass(frozen=True, slots=True)
class TokenEstimate:
    persons: int
    mean_window_chars: float
    mean_input_tokens: float
    total_input_tokens: float
    mean_output_tokens: float
    total_output_tokens: float
    rmb_off_peak: float
    rmb_peak: float


def estimate_tokens(
    persons: int,
    window_chars: Iterable[int],
    *,
    output_template: str = OUTPUT_TEMPLATE,
    tokens_per_char: float = TOKENS_PER_CJK_CHAR,
    prompt_overhead_tokens: int = PROMPT_OVERHEAD_TOKENS,
    prices: Mapping[str, Mapping[str, float]] = PRICES_CNY_PER_MTOK,
) -> TokenEstimate:
    """Turn measured window lengths into token and RMB estimates."""
    lengths = [length for length in window_chars]
    mean_window = sum(lengths) / len(lengths) if lengths else 0.0
    mean_input = mean_window * tokens_per_char + prompt_overhead_tokens
    mean_output = len(output_template) * tokens_per_char
    total_input = mean_input * persons
    total_output = mean_output * persons
    off_peak = prices["off_peak"]
    peak = prices["peak"]
    rmb_off = total_input / 1e6 * off_peak["input_cache_miss"] + total_output / 1e6 * off_peak["output"]
    rmb_peak = total_input / 1e6 * peak["input_cache_miss"] + total_output / 1e6 * peak["output"]
    return TokenEstimate(
        persons=persons,
        mean_window_chars=round(mean_window, 1),
        mean_input_tokens=round(mean_input, 1),
        total_input_tokens=round(total_input, 1),
        mean_output_tokens=round(mean_output, 1),
        total_output_tokens=round(total_output, 1),
        rmb_off_peak=round(rmb_off, 2),
        rmb_peak=round(rmb_peak, 2),
    )


# --------------------------------------------------------------------- sampling
VOLUME_CACHE = PASSAGE_CACHE / "volumes"


def _client() -> httpx.Client:
    return httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30.0, follow_redirects=True)


def _get(client: httpx.Client, params: Mapping[str, object], *, attempts: int = 4) -> httpx.Response:
    """GET with backoff: Wikimedia answers 429 when a script is too eager."""
    delay = 5.0
    response = client.get(WIKISOURCE_API, params=params)
    for _ in range(attempts - 1):
        if response.status_code != 429:
            break
        time.sleep(delay)
        delay *= 2
        response = client.get(WIKISOURCE_API, params=params)
    response.raise_for_status()
    return response


def fetch_volume(title: str, *, cache_dir: Path = VOLUME_CACHE, sleep_seconds: float = 1.5) -> str:
    """Fetch one 《清史稿》 volume's wikitext, cached on disk."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    safe = title.replace("/", "_")
    cache_file = cache_dir / f"{safe}.txt"
    if cache_file.exists():
        return cache_file.read_text(encoding="utf-8")
    with _client() as client:
        try:
            response = _get(
                client,
                {
                    "action": "query",
                    "prop": "revisions",
                    "titles": title,
                    "rvprop": "content",
                    "rvslots": "main",
                    "format": "json",
                    "formatversion": 2,
                },
            )
        finally:
            time.sleep(sleep_seconds)
    pages = response.json().get("query", {}).get("pages", [])
    content = ""
    if pages and pages[0].get("revisions"):
        content = pages[0]["revisions"][0]["slots"]["main"]["content"]
    if not content:
        raise RuntimeError(f"empty content for {title}; not cached")
    cache_file.write_text(content, encoding="utf-8")
    return content


def volume_titles(start: int = 250, end: int = 529, step: int = 25) -> list[str]:
    """A spread of 列傳 volumes (roughly 卷250-529 hold the biographies)."""
    return [f"清史稿/卷{number}" for number in range(start, end + 1, step)]


def _looks_like_person(name: str) -> bool:
    """Volume headings also carry 列傳/本紀 titles and editorial notes; keep names."""
    if not name or len(name) > 4:
        return False
    if any(marker in name for marker in "【】〔〕（）()[]《》、·"):
        return False
    if any(structure in name for structure in ("列傳", "本紀", "志", "表", "論", "序")):
        return False
    # Paragraph fallback also catches sentences that merely start with a date
    # ("二十一年，…"), which are not biographies.
    if name[0] in "一二三四五六七八九十百千萬元年月日春夏秋冬":
        return False
    return not any(character.isspace() or character == "\u3000" for character in name)


def biography_spans(content: str) -> list[tuple[str, str]]:
    """Split a volume into ``(name, body)`` biographies.

    Prefers explicit headings; falls back to paragraphs that open with a name for
    volumes shipped without headings on this Wikisource edition.
    """
    spans: list[tuple[str, str]] = []
    matches = list(_SECTION.finditer(content))
    if matches:
        for index, match in enumerate(matches):
            name = match.group(1).strip()
            start = match.end()
            end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
            body = content[start:end].strip()
            if _looks_like_person(name) and len(body) >= MIN_BIO_CHARS:
                spans.append((name, body))
        if spans:
            return spans
    openings = list(_OPENING.finditer(content))
    for index, match in enumerate(openings):
        name = match.group(1).strip()
        start = match.start()
        end = openings[index + 1].start() if index + 1 < len(openings) else len(content)
        body = content[start:end].strip()
        if _looks_like_person(name) and len(body) >= MIN_BIO_CHARS:
            spans.append((name, body))
    return spans


def biography_windows(content: str, *, max_chars: int = 3000) -> list[tuple[str, int, int]]:
    """All ``(name, biography_chars, family_window_chars)`` in one volume."""
    return [
        (name, len(body), len(family_window(body, max_chars=max_chars)))
        for name, body in biography_spans(content)
    ]


def sample_biographies(
    titles: Sequence[str] | None = None,
    *,
    sleep_seconds: float = 1.5,
    max_chars: int = 3000,
) -> pd.DataFrame:
    """Measure biography / window lengths across sampled volumes."""
    titles = list(titles or volume_titles())
    rows: list[dict[str, object]] = []
    failures: list[dict[str, object]] = []
    for title in titles:
        try:
            content = fetch_volume(title, sleep_seconds=sleep_seconds)
        except Exception as error:  # noqa: BLE001 - rate limits must not kill the sample
            failures.append({"volume": title, "error": f"{type(error).__name__}: {error}"})
            continue
        for name, bio_chars, window_chars in biography_windows(content, max_chars=max_chars):
            rows.append(
                {"volume": title, "name": name, "biography_chars": bio_chars, "window_chars": window_chars}
            )
    frame = pd.DataFrame.from_records(rows)
    frame.attrs["failures"] = failures
    return frame


_TEMPLATE = re.compile(r"\{\{[^{}]*\|([^{}|]*)\}\}")
_TEMPLATE_PLAIN = re.compile(r"\{\{[^{}]*\}\}")


def strip_wiki_markup(text: str) -> str:
    """Drop Wikisource templates/links, keeping the human-readable text.

    The pilot found that raw wikitext (``曾祖{{ProperNoun|日燿}}，明末官…``) forces the
    model to quote a *reconstructed* sentence, which then fails verbatim evidence
    checks. Prompts must carry the stripped text.
    """
    text = _TEMPLATE.sub(r"\1", text)
    text = _TEMPLATE_PLAIN.sub("", text)
    text = re.sub(r"\[\[([^\]|]*)\|([^\]]*)\]\]", r"\2", text)
    text = re.sub(r"\[\[([^\]]*)\]\]", r"\1", text)
    return text
