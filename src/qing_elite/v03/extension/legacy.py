"""Early/mid-Qing extension: recover v0.1 variables and map them onto V0.3 (U10R).

Two rules shape this module:

* **the frozen v0.1 artifacts are recovered, not recomputed** — they live in git history
  (``bd64fba:data/processed/*.parquet``), and re-running the v0.1 pipeline would produce numbers
  that are neither the published ones nor a new study;
* **every legacy variable is mapped explicitly, and the ones that cannot be mapped are listed as
  ``not_comparable`` with the reason** — most importantly ``strict_commoner_3g``, which v0.1
  derived from absence of record and which V0.3 forbids outright.

The early/mid-Qing frame is A/B/C only. The D layer is **not** brought back as a family-capital
control group: U09R's coverage numbers already showed that its family material is effectively
absent, and the stage prompt forbids reviving it.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

import pandas as pd

from qing_elite.utils.config import PROJECT_ROOT

LEGACY_V01_DIR = PROJECT_ROOT / "data" / "interim_v03" / "legacy_v01"
LEGACY_ARTIFACTS = ("person_indicators", "family_final", "officials_master")

#: Early/mid-Qing elite tiers kept as an extension (D deliberately excluded).
ELITE_TIERS = ("A1", "A2", "A3", "B", "C")

#: Variables deliberately excluded from any family-capital comparison, with the reason.
NOT_COMPARABLE: dict[str, str] = {
    "strict_commoner_3g": "v0.1 derived it from absence of record; V0.3 forbids turning missing evidence into a negative fact",
    "ancestor_high_official": "built on the v0.2 tier ladder; tier is a legacy/extension outcome in V0.3, not an exposure",
    "ancestor_high_official_sensitivity": "same tier dependence as ancestor_high_official",
    "ancestor_office_tiers": "raw tier strings, not a family-capital count",
    "n_slots_sufficient": "a count of documented slots, not of capital; comparable only as coverage",
    "highest_tier": "the legacy outcome itself",
}

#: Variables mapped onto the V0.3 ontology, with the caveat that travels with them.
MAPPING: dict[str, dict[str, str]] = {
    "n_slots_degree": {
        "v03_variable": "direct_3g_degree_count",
        "status": "comparable_with_caveat",
        "caveat": "v0.1 counts slots holding a degree record among the three direct slots; the V0.3 count is the same shape but the slot definitions come from the CBDB kin map",
    },
    "n_slots_office": {
        "v03_variable": "direct_3g_office_count",
        "status": "comparable_with_caveat",
        "caveat": "same as n_slots_degree",
    },
    "elite_generations_count": {
        "v03_variable": "direct_elite_generations",
        "status": "comparable_with_caveat",
        "caveat": "both count generations with a degree or an office; v0.1 treats an unrecorded slot as a non-elite generation, V0.3 excludes it from the denominator",
    },
    "ancestor_official_any": {
        "v03_variable": "documented_direct_office_any",
        "status": "comparable_after_recomputation",
        "caveat": "v0.1 coded 0 when records were simply absent (U00-01); the extension frame recomputes the flag only where a slot is documented",
    },
    "banner_effective": {
        "v03_variable": "banner_status",
        "status": "comparable_with_caveat",
        "caveat": "unknown stays unknown and is never read as non-banner",
    },
    "native_province": {
        "v03_variable": "native_province",
        "status": "comparable_with_caveat",
        "caveat": "CBDB address folding differs from the JSL 籍貫 field used by the late-Qing cohort",
    },
}


@dataclass
class LegacyFrame:
    persons: pd.DataFrame
    slots: pd.DataFrame
    master: pd.DataFrame
    recovered: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "recovered": self.recovered,
            "persons": int(len(self.persons)),
            "slots": int(len(self.slots)),
            "master": int(len(self.master)),
        }


def recover_legacy() -> LegacyFrame:
    """Load the recovered v0.1 artifacts (the recovery script writes them into the interim dir)."""
    paths = {name: LEGACY_V01_DIR / f"{name}.parquet" for name in LEGACY_ARTIFACTS}
    missing = [name for name, path in paths.items() if not path.exists()]
    if missing:
        raise FileNotFoundError(
            f"v0.1 artifacts missing: {missing}; run scripts/recover_v02_artifacts.sh with the "
            "v0.1 commit or extend it to data/processed"
        )
    return LegacyFrame(
        persons=pd.read_parquet(paths["person_indicators"]),
        slots=pd.read_parquet(paths["family_final"]),
        master=pd.read_parquet(paths["officials_master"]),
        recovered=True,
    )


def mapping_table() -> pd.DataFrame:
    rows: list[dict[str, str]] = []
    for variable, spec in MAPPING.items():
        rows.append(
            {
                "legacy_variable": variable,
                "v03_variable": spec["v03_variable"],
                "status": spec["status"],
                "note": spec["caveat"],
            }
        )
    for variable, reason in NOT_COMPARABLE.items():
        rows.append(
            {
                "legacy_variable": variable,
                "v03_variable": "—",
                "status": "not_comparable",
                "note": reason,
            }
        )
    return pd.DataFrame(rows)


def early_qing_frame(legacy: LegacyFrame) -> pd.DataFrame:
    """A/B/C persons with recomputed documented family capital and career basics."""
    persons = legacy.persons.loc[legacy.persons["highest_tier"].isin(ELITE_TIERS)].copy()
    slots = legacy.slots.loc[legacy.slots["personal_uid" if "personal_uid" in legacy.slots else "person_uid"].isin(persons["person_uid"])].copy()

    # recompute documented capital from slot records: a slot counts only when it is documented
    slots["slot_documented"] = slots["structured_known"].fillna(False) | slots["final_name"].notna()
    slots["slot_degree"] = slots["final_degree"].notna()
    slots["slot_office"] = slots["final_office"].notna()
    grouped = slots.groupby("person_uid").agg(
        n_slots_documented=("slot_documented", "sum"),
        direct_3g_degree_count=("slot_degree", "sum"),
        direct_3g_office_count=("slot_office", "sum"),
    )
    frame = persons.merge(grouped, on="person_uid", how="left")
    frame["n_slots_documented"] = frame["n_slots_documented"].fillna(0)
    frame[["direct_3g_degree_count", "direct_3g_office_count"]] = frame[
        ["direct_3g_degree_count", "direct_3g_office_count"]
    ].fillna(0)
    # the V0.3 rule: capital is only defined where at least one slot is documented
    frame["family_observable"] = frame["n_slots_documented"] > 0
    frame["direct_elite_generations"] = frame.apply(
        lambda row: float(row["direct_3g_degree_count"] + row["direct_3g_office_count"])
        if row["family_observable"]
        else float("nan"),
        axis=1,
    )
    frame["documented_direct_office_any"] = frame.apply(
        lambda row: (row["direct_3g_office_count"] > 0) if row["family_observable"] else None,
        axis=1,
    )
    wanted = ["person_uid", "native_province", "banner_effective", "career_first_year", "career_last_year"]
    available = [column for column in wanted if column in legacy.master.columns]
    master = legacy.master.loc[:, available]
    frame = frame.merge(master, on="person_uid", how="left", suffixes=("", "_master"))
    frame["native_province"] = frame.get("native_province")
    frame["banner_effective"] = frame.get("banner_effective")
    frame["career_length_years"] = (
        frame["career_last_year"].fillna(0) - frame["career_first_year"].fillna(0)
        if {"career_last_year", "career_first_year"}.issubset(frame.columns)
        else None
    )
    frame["period"] = "early_mid_qing_1644_1820"
    return frame


def summary(frame: pd.DataFrame) -> dict[str, Any]:
    return {
        "persons": int(len(frame)),
        "family_observable": int(frame["family_observable"].sum()),
        "family_observable_share": round(float(frame["family_observable"].mean()), 4),
        "tier_counts": frame["highest_tier"].value_counts().to_dict(),
        "d_layer_included": False,
        "d_layer_reason": "D is not revived as a family-capital control: its family material is effectively absent (U09R coverage) and the stage prompt forbids it",
    }
