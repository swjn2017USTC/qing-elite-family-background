"""CBDB office-code -> study tier resolution (P02).

The tier decisions themselves live in ``config/offices.yaml``; this module only
resolves those names against the released ``OFFICE_CODES`` table and records
what it did, so every tier assignment stays auditable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

TIER_ORDER = ["A1", "A2", "A3", "B", "C"]


@dataclass(frozen=True, slots=True)
class OfficeTier:
    """One resolved CBDB office code."""

    office_id: int
    office_chn: str
    office_dy: int | None
    tier: str
    canonical: str
    institution: str
    source: str  # "include" | "exclude:<category>"


def build_office_lookup(rows: Iterable[tuple[int, str, int | None]]) -> dict[str, list[tuple[int, str,int | None]]]:
    """Index ``(office_id, office_chn, c_dy)`` rows by office name."""
    lookup: dict[str, list[tuple[int, str, int | None]]] = {}
    for office_id, office_chn, office_dy in rows:
        lookup.setdefault(office_chn, []).append((office_id, office_chn, office_dy))
    return lookup


def resolve_cbdb_offices(
    rows: Iterable[tuple[int, str, int | None]],
    cfg: Mapping[str, Any],
) -> list[OfficeTier]:
    """Resolve every tier / exclusion rule to concrete CBDB office codes.

    ``rows`` are ``(c_office_id, c_office_chn, c_dy)`` tuples from ``OFFICE_CODES``.
    Raises if a configured office name does not exist in the release: a silent
    mismatch would quietly shrink the study universe.
    """
    lookup = build_office_lookup(rows)
    resolved: list[OfficeTier] = []
    seen: set[tuple[int, str]] = set()
    missing: list[str] = []

    for tier in TIER_ORDER:
        spec = cfg["cbdb"][tier]
        canonical_map = spec.get("canonical") or {}
        for name in spec["office_names"]:
            matches = lookup.get(name)
            if not matches:
                missing.append(f"{tier}:{name}")
                continue
            for office_id, office_chn, office_dy in matches:
                key = (office_id, "include")
                if key in seen:
                    continue
                seen.add(key)
                resolved.append(
                    OfficeTier(
                        office_id=office_id,
                        office_chn=office_chn,
                        office_dy=office_dy,
                        tier=tier,
                        canonical=canonical_map.get(name, name),
                        institution=spec["institution"],
                        source="include",
                    )
                )

    for category, names in (cfg.get("cbdb_excluded") or {}).items():
        for name in names:
            matches = lookup.get(name)
            if not matches:
                missing.append(f"excluded:{category}:{name}")
                continue
            for office_id, office_chn, office_dy in matches:
                key = (office_id, f"exclude:{category}")
                if key in seen:
                    continue
                seen.add(key)
                resolved.append(
                    OfficeTier(
                        office_id=office_id,
                        office_chn=office_chn,
                        office_dy=office_dy,
                        tier="excluded",
                        canonical=office_chn,
                        institution=category,
                        source=f"exclude:{category}",
                    )
                )

    if missing:
        raise ValueError(
            "config/offices.yaml references office names absent from this CBDB release: "
            + ", ".join(missing)
        )
    return resolved


def included_office_ids(resolved: Iterable[OfficeTier]) -> dict[int, OfficeTier]:
    return {row.office_id: row for row in resolved if row.source == "include"}


def excluded_office_ids(resolved: Iterable[OfficeTier]) -> dict[int, OfficeTier]:
    return {row.office_id: row for row in resolved if row.source.startswith("exclude")}
