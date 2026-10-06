"""Office ontology v2: title, rank, level, authority, appointment status (U08R).

What changed from U07R: U07R had only a keyword level table and marked 279 of 417 titles
unmatched, with no rank at all. Here the ontology carries

* **rank** from the curated table in ``config/v03/office_ranks.yaml``, with its
  ``verification_status`` travelling with every row (the CGED-Q release states no 品级, so a
  rank that looks authoritative but is unverified would be the worst outcome);
* **administrative level** and **central/local** from the keyword tables;
* **authority type** (civil / military / imperial household);
* **appointment status** flags derived from the appointment markers (acting / expectant /
  concurrent / honorific / substantive), kept as separate booleans because a posting can be
  two of them at once (署理 + 兼署).

Unmatched titles keep ``mapping_status='unmatched'`` and an explicit reason; they are never
quietly assigned a default rank or level.
"""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from qing_elite.utils.config import PROJECT_ROOT

RANKS_YAML = PROJECT_ROOT / "config" / "v03" / "office_ranks.yaml"

ONTOLOGY_COLUMNS = (
    "office_id",
    "office_title",
    "office_title_variants",
    "rank_label",
    "rank_class",
    "rank_side",
    "administrative_level",
    "central_local",
    "authority_type",
    "institutional_body",
    "substantive_default",
    "valid_from_year",
    "valid_to_year",
    "mapping_status",
    "unmapped_reason",
    "ontology_version",
)

APPOINTMENT_FLAGS = ("acting", "expectant", "concurrent", "honorific", "substantive")


@functools.lru_cache(maxsize=None)
def load_rank_config(path: Path | None = None) -> dict[str, Any]:
    with (path or RANKS_YAML).open(encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    if not config.get("ranks"):
        raise ValueError("office_ranks.yaml must declare ranks")
    return config


def rank_for(title: str, config: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Longest-title-first match so 直隸州知州 is not read as 知州."""
    config = config or load_rank_config()
    for entry in sorted(config["ranks"], key=lambda item: -len(item["title"])):
        if entry["title"] in title:
            return entry
    return None


def level_for(title: str, config: dict[str, Any] | None = None) -> str | None:
    config = config or load_rank_config()
    for level, keywords in config["levels"].items():
        if any(keyword in title for keyword in keywords):
            return level
    return None


def authority_for(title: str, config: dict[str, Any] | None = None) -> str:
    config = config or load_rank_config()
    for authority, keywords in config["authority_types"].items():
        if any(keyword in title for keyword in keywords):
            return authority
    return "unknown"


def appointment_flags(*, office_raw: str = "", selection_method: str = "") -> dict[str, bool]:
    """Status flags from the office string and the JSL ``選任方式`` value."""
    config = load_rank_config()
    text = f"{office_raw}||{selection_method}"
    flags: dict[str, bool] = {}
    for flag in APPOINTMENT_FLAGS:
        markers = config["appointment_markers"].get(flag, [])
        flags[flag] = any(marker in text for marker in markers)
    if not any(flags.values()):
        flags["substantive"] = bool(selection_method.strip())  # a coded appointment is a real one
    return flags


def build_ontology(titles: list[str], *, ontology_version: str) -> pd.DataFrame:
    """One row per distinct office title, with rank / level / authority and reasons."""
    config = load_rank_config()
    rows: list[dict[str, Any]] = []
    for title in sorted({str(value) for value in titles if str(value).strip()}):
        rank = rank_for(title, config)
        level = level_for(title, config)
        authority = authority_for(title, config)
        flags = appointment_flags(office_raw=title)
        reasons: list[str] = []
        if rank is None:
            reasons.append("rank not in the curated table")
        if level is None:
            reasons.append("level not in the keyword table")
        rows.append(
            {
                "office_id": f"office-{abs(hash(title)) % 10**8:08d}",
                "office_title": title,
                "office_title_variants": None,
                "rank_label": (
                    f"{rank['rank_side']}{'一二三四五六七八九'[rank['rank_class'] - 1]}品"
                    if rank and rank.get("rank_class")
                    else (rank["rank_side"] if rank else None)
                ),
                "rank_class": rank["rank_class"] if rank else None,
                "rank_side": rank["rank_side"] if rank else None,
                "administrative_level": level or "unknown",
                "central_local": "central" if level == "central" else ("local" if level else "unknown"),
                "authority_type": authority,
                "institutional_body": None,
                "substantive_default": flags["substantive"],
                "valid_from_year": None,
                "valid_to_year": None,
                "mapping_status": "matched" if (rank or level) else "unmatched",
                "unmapped_reason": None if (rank or level) else "; ".join(reasons),
                "ontology_version": ontology_version,
            }
        )
    frame = pd.DataFrame(rows, columns=ONTOLOGY_COLUMNS)
    for column in ("rank_class", "valid_from_year", "valid_to_year"):
        frame[column] = pd.array(frame[column], dtype="Int64")
    frame["substantive_default"] = pd.array(frame["substantive_default"], dtype="boolean")
    frame.attrs["rank_verification_status"] = config.get("verification_status", "unknown")
    frame.attrs["rank_basis"] = config.get("basis", "")
    return frame


def describe(ontology: pd.DataFrame) -> dict[str, Any]:
    return {
        "titles": int(len(ontology)),
        "with_rank": int(ontology["rank_class"].notna().sum()),
        "with_level": int((ontology["administrative_level"] != "unknown").sum()),
        "unmatched": int((ontology["mapping_status"] == "unmatched").sum()),
        "rank_verification_status": ontology.attrs.get("rank_verification_status", "unknown"),
        "rank_basis": ontology.attrs.get("rank_basis", ""),
    }
