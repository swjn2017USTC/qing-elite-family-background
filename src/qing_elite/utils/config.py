"""Project paths and YAML configuration loading."""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[3]
CONFIG_DIR = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
INTERIM_DIR = DATA_DIR / "interim"
PROCESSED_DIR = DATA_DIR / "processed"
PROCESSED_V02_DIR = DATA_DIR / "processed_v02"
OUTPUT_DIR = PROJECT_ROOT / "output"

OFFICES_YAML = CONFIG_DIR / "offices.yaml"

CBDB_SQLITE = RAW_DIR / "cbdb" / "cbdb_20260912.sqlite3"
CGEDQ_TAB = RAW_DIR / "cgeq" / "cgedq_jsl_public_1760-1912_personid_2026-08-28.tab"


@functools.lru_cache(maxsize=None)
def load_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def load_offices() -> dict[str, Any]:
    """Return the office tier / standardization configuration (config/offices.yaml)."""
    return load_yaml(OFFICES_YAML)
