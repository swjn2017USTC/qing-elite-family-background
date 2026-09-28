"""Full-text acquisition and parsing (U04R).

Only licence-clear open-access locations reported by OpenAlex are fetched, one document
at a time, with the response bytes hashed and the extracted text kept out of Git. A paper
counts as ``fulltext_verified`` only after the text layer parses and reports non-trivial
length; everything else stays at ABSTRACT or METADATA level, and the reason is written to
``sources/literature/logs/parse-LIT-NNNN.json``.

Nothing here bypasses a paywall or a login: institution-licensed items go to the human
acquisition queue instead.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import httpx
import pandas as pd

from qing_elite.v03.lit.harvest import load_plan
from qing_elite.v03.lit.registry import (
    LOG_DIR,
    PRIVATE_INBOX,
    PRIVATE_NORMALIZED,
    PUBLIC_DIR,
    QUEUE_DIR,
    REGISTRY_JSONL,
    TEXT_DIR,
    load_registry,
    validate_registry,
    write_jsonl,
)

MIN_TEXT_CHARS = 2_000


class _TextExtractor(HTMLParser):
    """Minimal HTML → text, good enough for journal landing pages and OA HTML."""

    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: D102
        if tag in ("script", "style"):
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:  # noqa: D102
        if tag in ("script", "style") and self._skip:
            self._skip -= 1
        if tag in ("p", "div", "br", "li", "h1", "h2", "h3"):
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:  # noqa: D102
        if not self._skip:
            self._chunks.append(data)

    @property
    def text(self) -> str:
        raw = "".join(self._chunks)
        lines = [line.strip() for line in raw.splitlines()]
        return "\n".join(line for line in lines if line)


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    return parser.text


def pdf_to_text(payload: bytes) -> tuple[str, int, list[int]]:
    """Return ``(text, pages, empty_pages)`` using PyMuPDF page markers."""
    import fitz  # imported lazily: only needed when a PDF is actually parsed

    document = fitz.open(stream=payload, filetype="pdf")
    chunks: list[str] = []
    empty: list[int] = []
    for number, page in enumerate(document, start=1):
        page_text = page.get_text("text")
        if len(page_text.strip()) < 20:
            empty.append(number)
        chunks.append(f"<<<PAGE {number}>>>\n{page_text}")
    document.close()
    return "\n".join(chunks), len(chunks), empty


def _oa_location(note: str) -> str | None:
    for part in (note or "").split(";"):
        part = part.strip()
        if part.startswith("oa_url=") and part != "oa_url=None":
            return part.split("=", 1)[1]
    return None


def acquire_for_row(row: pd.Series, *, client: httpx.Client) -> dict[str, Any]:
    """Fetch, hash and parse one paper; never raises on network trouble."""
    literature_id = row["literature_id"]
    log: dict[str, Any] = {"literature_id": literature_id, "title": row["title"]}
    url = _oa_location(row.get("notes") or "")
    if not url:
        log.update(action="queue_for_human", reason="no open-access location reported")
        return log

    try:
        response = client.get(url)
        response.raise_for_status()
        payload = response.content
    except Exception as error:  # network / 403 / timeouts are all recorded, not raised
        log.update(action="fetch_failed", url=url, reason=f"{type(error).__name__}: {error}")
        return log

    is_pdf = url.lower().endswith(".pdf") or payload[:4] == b"%PDF"
    suffix = "pdf" if is_pdf else "html"
    target = PUBLIC_DIR / f"{literature_id}.{suffix}"
    target.write_bytes(payload)
    sha256 = hashlib.sha256(payload).hexdigest()

    if is_pdf:
        try:
            text, pages, empty = pdf_to_text(payload)
        except Exception as error:
            log.update(action="parse_failed", url=url, reason=f"{type(error).__name__}: {error}")
            return log
    else:
        text = html_to_text(payload.decode("utf-8", errors="replace"))
        pages, empty = 0, []

    normalized = TEXT_DIR / f"{literature_id}.txt"
    normalized.parent.mkdir(parents=True, exist_ok=True)
    normalized.write_text(text, encoding="utf-8")

    verified = len(text) >= MIN_TEXT_CHARS
    log.update(
        action="parsed" if verified else "parse_too_short",
        url=url,
        format=suffix,
        bytes=len(payload),
        sha256=sha256,
        pages=pages,
        empty_pages=empty,
        chars=len(text),
        fulltext_verified=verified,
    )
    return log


def acquire(*, limit: int | None = None) -> pd.DataFrame:
    """Acquire full text for core rows (and any row with an OA link); update the registry."""
    registry = load_registry()
    if registry.empty:
        raise SystemExit("registry is empty: run harvest first")
    targets = registry[
        (registry["tier"] == "core") & registry["notes"].fillna("").str.contains("oa_url=https")
    ]
    if limit:
        targets = targets.head(limit)

    logs: list[dict[str, Any]] = []
    updates: dict[str, dict[str, Any]] = {}
    with httpx.Client(timeout=90.0, follow_redirects=True) as client:
        for _, row in targets.iterrows():
            log = acquire_for_row(row, client=client)
            logs.append(log)
            if log.get("fulltext_verified"):
                suffix = log.get("format", "pdf")
                updates[row["literature_id"]] = {
                    "access_status": "OPEN_FULLTEXT_PDF" if suffix == "pdf" else "OPEN_FULLTEXT_HTML",
                    "rights_status": "OPEN_ACCESS",
                    "evidence_level": "FULLTEXT",
                    "local_file": f"sources/literature/public/{row['literature_id']}.{suffix}",
                    "sha256": log["sha256"],
                    "normalized_text": f"data/interim_v03/lit/text/{row['literature_id']}.txt",
                    "fulltext_verified": True,
                }

    for literature_id, patch in updates.items():
        for key, value in patch.items():
            registry.loc[registry["literature_id"] == literature_id, key] = value

    registry = validate_registry(registry)
    write_jsonl(REGISTRY_JSONL, registry.to_dict(orient="records"))
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    for log in logs:
        (LOG_DIR / f"parse-{log['literature_id']}.json").write_text(
            json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return registry


def write_queue(registry: pd.DataFrame) -> Path:
    """Human acquisition gate: items without a licence-clear OA copy."""
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    pending = registry[
        (registry["tier"] == "core")
        & (~registry["fulltext_verified"])
        & (registry["access_status"] != "OPEN_FULLTEXT_PDF")
    ]
    jsonl = QUEUE_DIR / "acquisition_queue.jsonl"
    write_jsonl(
        jsonl,
        [
            {
                "literature_id": row.literature_id,
                "title": row.title,
                "doi": row.doi,
                "url": row.url,
                "access_status": row.access_status,
                "action": "find licence-clear copy, place as private/inbox/LIT-NNNN.pdf",
            }
            for row in pending.itertuples(index=False)
        ],
    )
    markdown = QUEUE_DIR / "acquisition_queue.md"
    lines = ["# Literature acquisition queue", ""]
    lines.append(f"items: {len(pending)} (core papers without a verified local fulltext)")
    lines.append("")
    for row in pending.itertuples(index=False):
        lines.append(f"- `{row.literature_id}` {row.title} — {row.access_status} — {row.url or 'no url'}")
    markdown.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return markdown


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - thin CLI
    parser = argparse.ArgumentParser(description="acquire open-access fulltext")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--queue", action="store_true", help="rewrite the acquisition queue only")
    args = parser.parse_args(argv)
    registry = load_registry() if args.queue else acquire(limit=args.limit)
    path = write_queue(registry)
    print(f"acquisition queue: {path}")
    print(f"fulltext verified: {int(registry['fulltext_verified'].sum())} / {len(registry)}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
