"""PaddleOCR-VL runner wrapper (U05R).

PaddleOCR lives in its own local environment (heavy deps, kept out of the project venv), so
this module shells out to ``scripts/ocr_runner.py`` with the interpreter declared in
``config/v03/ocr.yaml``. Results are cached per (pdf, range, mode) so re-running the pilot
never re-submits pages, and the access token stays in the environment — it is never passed
as an argument nor written anywhere.

The page quality proxy lives here too: the markdown path does not return per-line scores, so
low-confidence detection uses observable text-level signals and is labelled as a proxy.
"""

from __future__ import annotations

import functools
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import yaml

from qing_elite.utils.config import PROJECT_ROOT

OCR_YAML = PROJECT_ROOT / "config" / "v03" / "ocr.yaml"
OCR_CACHE_DIR = PROJECT_ROOT / "data" / "interim_v03" / "pilot" / "ocr"
TONGUANLU_PDF = (
    PROJECT_ROOT / "data" / "raw" / "tongguanlu" / "tongguanlu_jiangnan_lingshu_jinkuiguang.pdf"
)


class OcrError(RuntimeError):
    """Raised when the OCR service or its environment cannot be used."""


@functools.lru_cache(maxsize=None)
def load_ocr_config(path: Path | None = None) -> dict[str, Any]:
    with (path or OCR_YAML).open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def cache_path(pdf: Path, start: int, end: int, mode: str) -> Path:
    digest = hashlib.sha256(f"{pdf.name}:{start}-{end}:{mode}".encode()).hexdigest()[:10]
    return OCR_CACHE_DIR / f"{pdf.stem}_p{start}-{end}_{mode}_{digest}.jsonl"


def run_ocr_range(
    *, start: int, end: int, pdf: Path | None = None, mode: str | None = None, reuse: bool = True
) -> dict[str, Any]:
    """Run (or reuse) one page range; returns ``{path, cached, pages}``."""
    config = load_ocr_config()
    pdf = pdf or TONGUANLU_PDF
    mode = mode or config["modes"]["primary"]
    out = cache_path(pdf, start, end, mode)
    if out.exists() and reuse:
        return {"path": out, "cached": True, "pages": sum(1 for _ in out.open(encoding="utf-8"))}

    runner = PROJECT_ROOT / config["runner"]
    venv_python = Path(config["venv_python"])
    if not venv_python.exists():
        raise OcrError(f"OCR venv interpreter not found: {venv_python}")
    if not pdf.exists():
        raise OcrError(f"PDF not found: {pdf}")
    out.parent.mkdir(parents=True, exist_ok=True)

    result = subprocess.run(
        [
            str(venv_python),
            str(runner),
            "--pdf",
            str(pdf),
            "--range",
            f"{start}-{end}",
            "--out",
            str(out),
            "--model",
            config["engine"],
            "--mode",
            mode,
            "--token-env",
            config["token_env"],
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not out.exists():
        raise OcrError(
            f"OCR failed for pages {start}-{end}: {result.stderr.strip()[-300:]}"
        )
    return {"path": out, "cached": False, "pages": sum(1 for _ in out.open(encoding="utf-8"))}


def load_pages(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def page_text_map(pages: list[dict[str, Any]]) -> dict[int, str]:
    from qing_elite.v03.pilot.rules import normalize_ocr_markdown

    return {
        int(page["page_number_1based"]): normalize_ocr_markdown(page.get("markdown_text") or "")
        for page in pages
    }


def ocr_page_quality(markdown: str) -> float:
    """Proxy uncertainty in [0, 1] for one OCR page, measured on the parser's input text."""
    from qing_elite.v03.pilot.rules import normalize_ocr_markdown
    from qing_elite.v03.pilot.verifier import ocr_confidence_proxy

    config = load_ocr_config()["confidence"]
    return ocr_confidence_proxy(
        normalize_ocr_markdown(markdown or ""),
        min_cjk_ratio=float(config["min_cjk_ratio"]),
        min_chars=int(config["min_chars_per_page"]),
        max_chars=int(config.get("max_chars_per_page", 20000)),
    )
