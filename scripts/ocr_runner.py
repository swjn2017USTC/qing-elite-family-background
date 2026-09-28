#!/usr/bin/env python
"""Submit one PDF page range to the PaddleOCR-VL online service (external venv).

Run under the OCR venv declared in ``config/v03/ocr.yaml`` (never the project venv):

    <ocr_venv>/bin/python scripts/ocr_runner.py --pdf book.pdf --range 1-8 --out pages.jsonl

Writes one JSON object per page with the layout-aware markdown, so the caller gets page
markers and reading order instead of line boxes. The access token is read from the
environment by the SDK; it is never passed on the command line or written to disk.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time


def _jsonable(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return str(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--model", default="PaddleOCR-VL-1.6")
    parser.add_argument("--range", required=True, dest="page_range")
    parser.add_argument("--mode", default="document", choices=["document", "ocr"])
    parser.add_argument("--token-env", default="PADDLEOCR_ACCESS_TOKEN")
    args = parser.parse_args(argv)

    if not os.environ.get(args.token_env):
        print(json.dumps({"ok": False, "error": f"missing env {args.token_env}"}), file=sys.stderr)
        return 3

    first, _, last = args.page_range.partition("-")
    first_page = int(first)
    last_page = int(last) if last else first_page

    from paddleocr import PaddleOCRClient

    client = PaddleOCRClient()
    started = time.time()
    try:
        if args.mode == "document":
            result = client.parse_document(
                file_path=args.pdf, model=args.model, page_ranges=args.page_range
            )
        else:
            result = client.ocr(file_path=args.pdf, model=args.model, page_ranges=args.page_range)
    except Exception as error:  # network / quota / service errors are reported, not raised
        print(json.dumps({"ok": False, "error": f"{type(error).__name__}: {error}"}), file=sys.stderr)
        return 1

    pages = getattr(result, "pages", None) or []
    expected = last_page - first_page + 1
    if len(pages) != expected:
        print(
            json.dumps(
                {"ok": False, "error": f"page count mismatch: requested {expected}, got {len(pages)}"}
            ),
            file=sys.stderr,
        )
        return 2

    with open(args.out, "w", encoding="utf-8") as handle:
        for position, page in enumerate(pages):
            markdown = getattr(page, "markdown_text", None)
            if markdown is None:
                markdown = getattr(page, "markdown", "") or ""
            handle.write(
                json.dumps(
                    {
                        "page_number_1based": first_page + position,
                        "markdown_text": markdown,
                        "raw": _jsonable(getattr(page, "raw", None)),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    print(
        json.dumps(
            {
                "ok": True,
                "pages": len(pages),
                "elapsed_s": round(time.time() - started, 1),
                "out": args.out,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
