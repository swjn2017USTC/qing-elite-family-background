"""Codebook: turn final family slot values into the study's indicators (P06).

Every classification here is deterministic Python driven by ``config/research.yaml``.
No LLM decides whether a person is 寒門 or 官宦; the model output is only ever a
*source of facts*, and only when its quoted evidence matched the passage verbatim
(that gate is applied in P05, not relaxed here).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

import pandas as pd

from qing_elite.cbdb.offices import included_office_ids, resolve_cbdb_offices
from qing_elite.utils.config import load_yaml

SLOTS = ("father", "grandfather", "great_grandfather")


@dataclass(frozen=True, slots=True)
class Codebook:
    """Pre-registered indicator definitions."""

    config: Mapping[str, Any]

    @property
    def cohorts(self) -> list[dict[str, Any]]:
        return list(self.config["cohorts"])

    @property
    def jiangnan_core(self) -> set[str]:
        return set(self.config["groups"]["jiangnan_core"])

    @property
    def primary_high_tiers(self) -> set[str]:
        return set(self.config["indicators"]["ancestor_high_official"]["primary_tiers"])

    @property
    def sensitivity_high_tiers(self) -> set[str]:
        return set(self.config["indicators"]["ancestor_high_official"]["sensitivity_tiers"])

    @property
    def tier_groups(self) -> dict[str, list[str]]:
        return {name: list(tiers) for name, tiers in self.config["models"]["tier_grouping"].items()}

    def cohort_of(self, year: float | int | None) -> str | None:
        if year is None or pd.isna(year):
            return None
        for cohort in self.cohorts:
            if cohort["start"] <= int(year) <= cohort["end"]:
                return str(cohort["label"])
        return None


def load_codebook(path: Any = None) -> Codebook:
    from qing_elite.utils.config import CONFIG_DIR

    return Codebook(config=load_yaml(path or CONFIG_DIR / "research.yaml"))


def office_tier_matcher(cfg_offices: Mapping[str, Any], conn: Any) -> dict[str, str]:
    """Map every tier office name (A1..C) to its tier, for matching free-text offices."""
    positions = conn.execute("SELECT c_office_id, c_office_chn, c_dy FROM OFFICE_CODES").fetchall()
    resolved = resolve_cbdb_offices(positions, cfg_offices)
    mapping: dict[str, str] = {}
    for row in resolved:
        if row.source == "include":
            mapping[row.office_chn] = row.tier
            if row.canonical != row.office_chn:
                mapping.setdefault(row.canonical, row.tier)
    return mapping


def classify_office_text(text: object, tier_names: Mapping[str, str]) -> str | None:
    """Highest tier mentioned in a free-text office string (longest name wins)."""
    if not isinstance(text, str) or not text.strip():
        return None
    best: tuple[int, str] | None = None
    for name, tier in tier_names.items():
        if name and name in text:
            if best is None or len(name) > best[0]:
                best = (len(name), tier)
    return best[1] if best else None


def build_family_final(
    structured: pd.DataFrame, enriched: pd.DataFrame | None
) -> pd.DataFrame:
    """Per (person, slot) final values with provenance, preferring CBDB structured data."""
    frame = structured[
        [
            "person_uid",
            "ancestor_slot",
            "tiers_present",
            "highest_tier",
            "ancestor_known",
            "ancestor_name",
            "ancestor_degree",
            "ancestor_office_sample",
            "ancestor_highest_tier",
            "ancestor_native_addr",
        ]
    ].copy()
    frame = frame.rename(
        columns={
            "ancestor_known": "structured_known",
            "ancestor_name": "structured_name",
            "ancestor_degree": "structured_degree",
            "ancestor_office_sample": "structured_office",
            "ancestor_highest_tier": "structured_office_tier",
            "ancestor_native_addr": "structured_native_addr",
        }
    )
    if enriched is not None and not enriched.empty:
        llm = enriched[
            [
                "person_uid",
                "ancestor_slot",
                "final_name",
                "final_degree",
                "final_office",
                "final_source",
                "llm_office",
                "llm_evidence_verbatim",
            ]
        ]
        frame = frame.merge(llm, on=["person_uid", "ancestor_slot"], how="left")
    else:
        frame["final_name"] = None
        frame["final_degree"] = None
        frame["final_office"] = None
        frame["final_source"] = None

    def pick(row: pd.Series, value_column: str, structured_column: str) -> object:
        if row["structured_known"]:
            return row[structured_column]
        if row["final_source"] == "llm_extraction":
            return row[value_column]
        return None

    frame["final_name"] = [pick(row, "final_name", "structured_name") for _, row in frame.iterrows()]
    frame["final_degree"] = [pick(row, "final_degree", "structured_degree") for _, row in frame.iterrows()]
    frame["final_office"] = [pick(row, "final_office", "structured_office") for _, row in frame.iterrows()]
    frame["provenance"] = [
        "cbdb_structured"
        if row["structured_known"]
        else ("llm_extraction" if row["final_source"] == "llm_extraction" else "none")
        for _, row in frame.iterrows()
    ]
    # A slot is "sufficient" only when we know who the ancestor was (plan §九).
    frame["slot_sufficient"] = frame["final_name"].notna()
    frame["slot_has_degree"] = frame["final_degree"].notna()
    frame["slot_has_office"] = frame["final_office"].notna()
    return frame


def person_indicators(
    family_final: pd.DataFrame,
    codebook: Codebook,
    tier_names: Mapping[str, str],
) -> pd.DataFrame:
    """Aggregate the three slots into the pre-registered person-level indicators."""
    rows: list[dict[str, Any]] = []
    for person_uid, group in family_final.groupby("person_uid"):
        by_slot = {row["ancestor_slot"]: row for _, row in group.iterrows()}
        slots = [by_slot.get(slot) for slot in SLOTS]
        sufficient = [bool(slot is not None and slot["slot_sufficient"]) for slot in slots]
        degrees = [bool(slot is not None and slot["slot_has_degree"]) for slot in slots]
        offices = [bool(slot is not None and slot["slot_has_office"]) for slot in slots]

        office_tiers: list[str] = []
        for slot in slots:
            if slot is None or not slot["slot_has_office"]:
                continue
            tier = slot.get("structured_office_tier")
            if not isinstance(tier, str) or tier in ("nan", ""):
                tier = classify_office_text(slot.get("final_office"), tier_names)
            if isinstance(tier, str) and tier and tier != "nan":
                office_tiers.append(tier)

        elite_generations = sum(
            1 for degree, office in zip(degrees, offices) if degree or office
        )
        any_sufficient = any(sufficient)
        indicators: dict[str, Any] = {
            "person_uid": person_uid,
            "n_slots_sufficient": sum(sufficient),
            "n_slots_degree": sum(degrees),
            "n_slots_office": sum(offices),
            "ancestor_generations_known": sum(sufficient),
            "elite_generations_count": elite_generations if any_sufficient else pd.NA,
            "ancestor_official_any": int(any(offices)) if any_sufficient else pd.NA,
            "ancestor_degree_any": int(any(degrees)) if any_sufficient else pd.NA,
            "ancestor_high_official": (
                int(any(tier in codebook.primary_high_tiers for tier in office_tiers))
                if any(offices)
                else pd.NA
            ),
            "ancestor_high_official_sensitivity": (
                int(any(tier in codebook.sensitivity_high_tiers for tier in office_tiers))
                if any(offices)
                else pd.NA
            ),
            "ancestor_office_tiers": "|".join(sorted(set(office_tiers))),
        }
        # strict_commoner_3g: all three slots identified, none with degree or office.
        if all(sufficient):
            indicators["strict_commoner_3g"] = int(not any(degrees) and not any(offices))
        else:
            indicators["strict_commoner_3g"] = pd.NA
        rows.append(indicators)
    return pd.DataFrame.from_records(rows)


def attach_groups(
    persons: pd.DataFrame,
    master: pd.DataFrame,
    codebook: Codebook,
    year_column: str = "career_first_year",
) -> pd.DataFrame:
    """Attach tier, cohort, banner group, province and jiangnan_core to each person."""
    frame = persons.merge(
        master[
            [
                "person_uid",
                "highest_tier",
                "tiers_present",
                "source",
                "banner_effective",
                "native_province_effective",
                "career_first_year",
                "career_last_year",
                "n_appointments",
            ]
        ],
        on="person_uid",
        how="left",
    )
    tier_lookup = {tier: group for group, tiers in codebook.tier_groups.items() for tier in tiers}
    frame["tier_group"] = frame["highest_tier"].map(tier_lookup).fillna(
        frame["highest_tier"]
    )
    frame["banner_group"] = frame["banner_effective"].fillna("unknown")
    frame["province"] = frame["native_province_effective"]
    frame["jiangnan_core"] = frame["province"].isin(codebook.jiangnan_core)
    year = frame[year_column] if year_column in frame else frame["career_first_year"]
    frame["cohort"] = [codebook.cohort_of(value) for value in year]
    return frame


def coverage_table(family_final: pd.DataFrame, master: pd.DataFrame) -> pd.DataFrame:
    """The first formal result: how much family information exists per tier (plan §十一)."""
    persons = master[["person_uid", "highest_tier"]].copy()
    wide = (
        family_final.pivot_table(
            index="person_uid",
            columns="ancestor_slot",
            values="slot_sufficient",
            aggfunc="max",
        )
        .reindex(columns=list(SLOTS))
        .fillna(False)
    )
    wide["n_generations"] = wide.sum(axis=1)
    merged = persons.merge(wide, left_on="person_uid", right_index=True, how="left")
    for column in SLOTS:
        merged[column] = merged[column].fillna(False).astype(bool)
    merged["n_generations"] = merged["n_generations"].fillna(0).astype(int)

    rows: list[dict[str, Any]] = []
    for tier, group in merged.groupby("highest_tier"):
        total = len(group)
        rows.append(
            {
                "tier": tier,
                "N_total": total,
                "N_father_observed": int(group["father"].sum()),
                "N_2gen_observed": int((group["father"] & group["grandfather"]).sum()),
                "N_3gen_observed": int(
                    (group["father"] & group["grandfather"] & group["great_grandfather"]).sum()
                ),
                "N_any_generation": int((group["n_generations"] > 0).sum()),
                "coverage_rate_any": round(100 * (group["n_generations"] > 0).mean(), 2),
                "coverage_rate_3gen": round(100 * (group["n_generations"] == 3).mean(), 2),
            }
        )
    return pd.DataFrame.from_records(rows).sort_values("tier").reset_index(drop=True)
