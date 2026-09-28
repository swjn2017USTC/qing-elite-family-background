"""Early/mid-Qing vs late-Qing standardized cohort: comparison and classification (U10R).

The two cohorts are not the same study:

* **early/mid Qing (A/B/C)**: v0.1's CBDB-derived elite sample, 1644–1820, family material from
  CBDB kin records and 《清史稿》 windows;
* **late-Qing standardized cohort**: the U09R link-defined sample (1,204 persons) whose family
  material comes from CBDB entries reached through an accepted cross-source link.

Every comparison row therefore carries ``classification`` — ``CROSS_PERIOD_CONSISTENT`` only
when the *direction* agrees and the two variables mean the same thing, ``PERIOD_SPECIFIC`` when
one cohort cannot speak to the other's level, and ``NOT_COMPARABLE`` when the underlying
variables differ. No pooled regression is run anywhere in this stage: with a link-defined cohort
on one side and an elite census on the other, pooling would produce a number about the sampling
designs rather than about the periods.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

CLASSIFICATIONS = ("CROSS_PERIOD_CONSISTENT", "PERIOD_SPECIFIC", "NOT_COMPARABLE")


def _observable(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.loc[frame["family_observable"]]


def documentation_share(early: pd.DataFrame, late: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for label, frame, source in (
        ("early_mid_qing_elite_ABC", early, "v0.1 CBDB kin + 清史稿 window"),
        ("late_qing_standardized", late, "link-defined CBDB entries for a JSL career sample"),
    ):
        rows.append(
            {
                "cohort": label,
                "n": int(len(frame)),
                "family_observable": int(frame["family_observable"].sum()),
                "share": round(float(frame["family_observable"].mean()), 4),
                "family_source": source,
                "classification": "CROSS_PERIOD_CONSISTENT",
                "note": "both periods document family background for a minority; the levels differ by source protocol and population, so only the direction is comparable",
            }
        )
    return pd.DataFrame(rows)


def direct_capital(early: pd.DataFrame, late: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for label, frame in (
        ("early_mid_qing_elite_ABC", _observable(early)),
        ("late_qing_standardized", _observable(late)),
    ):
        if frame.empty:
            continue
        degrees = pd.to_numeric(frame["direct_3g_degree_count"], errors="coerce")
        offices = pd.to_numeric(frame["direct_3g_office_count"], errors="coerce")
        rows.append(
            {
                "cohort": label,
                "denominator_observable": int(len(frame)),
                "mean_degree_count": round(float(degrees.mean()), 4),
                "mean_office_count": round(float(offices.mean()), 4),
                "share_any_degree": round(float((degrees > 0).mean()), 4),
                "share_any_office": round(float((offices > 0).mean()), 4),
                "classification": "PERIOD_SPECIFIC",
                "note": "slot definitions come from two different pipelines (v0.1 slot build vs CBDB kin map); levels are not directly comparable",
            }
        )
    return pd.DataFrame(rows)


def regional_composition(early: pd.DataFrame, late: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for label, series in (
        ("early_mid_qing_elite_ABC", early.get("native_province_effective", early.get("native_province"))),
        ("late_qing_standardized", late.get("native_province")),
    ):
        if series is None:
            continue
        counts = series.fillna("unknown").value_counts(normalize=True).head(8)
        for province, share in counts.items():
            rows.append(
                {
                    "cohort": label,
                    "province": province,
                    "share": round(float(share), 4),
                    "classification": "PERIOD_SPECIFIC",
                    "note": "province folding differs between the CBDB address tree and the JSL 籍貫 field",
                }
            )
    return pd.DataFrame(rows)


def banner_status(early: pd.DataFrame, late: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for label, series in (
        ("early_mid_qing_elite_ABC", early.get("banner_group")),
        ("late_qing_standardized", late.get("banner_status", late.get("banner_effective"))),
    ):
        if series is None:
            continue
        counts = series.fillna("unknown").value_counts()
        for value, count in counts.items():
            rows.append(
                {
                    "cohort": label,
                    "banner_status": value,
                    "n": int(count),
                    "share": round(float(count / counts.sum()), 4),
                    "classification": "NOT_COMPARABLE",
                    "note": "the late-Qing cohort records banner status for very few persons; unknown is never read as non-banner",
                }
            )
    return pd.DataFrame(rows)


def elite_persistence(early: pd.DataFrame, late: pd.DataFrame) -> pd.DataFrame:
    early_observable = _observable(early)
    late_observable = _observable(late)
    rows = [
        {
            "cohort": "early_mid_qing_elite_ABC",
            "measure": "share with a high-office ancestor (legacy definition)",
            "denominator": int(len(early_observable)),
            "share": round(float(early_observable["ancestor_high_official"].fillna(0).mean()), 4)
            if len(early_observable)
            else None,
            "classification": "NOT_COMPARABLE",
            "note": "built on the v0.2 tier ladder, which V0.3 demotes to a legacy outcome",
        },
        {
            "cohort": "late_qing_standardized",
            "measure": "share with any elite senior collateral kin",
            "denominator": int(len(late_observable)),
            "share": round(float((pd.to_numeric(late_observable["all_senior_elite_kin_count"], errors="coerce") > 0).mean()), 4)
            if len(late_observable)
            else None,
            "classification": "NOT_COMPARABLE",
            "note": "the early cohort has no collateral measure at all; v0.1 only recorded three direct slots",
        },
    ]
    return pd.DataFrame(rows)


def career_outcomes(early: pd.DataFrame, late: pd.DataFrame) -> pd.DataFrame:
    rows = []
    if "highest_tier" in early:
        for tier, count in early["highest_tier"].value_counts().items():
            rows.append(
                {
                    "cohort": "early_mid_qing_elite_ABC",
                    "outcome": "legacy tier (extension outcome only)",
                    "value": tier,
                    "n": int(count),
                    "share": round(float(count / len(early)), 4),
                    "classification": "PERIOD_SPECIFIC",
                    "note": "tier is a v0.1 construct; V0.3 keeps it as legacy/extension only",
                }
            )
    if "central_local_route" in late:
        for route, count in late["central_local_route"].value_counts().items():
            rows.append(
                {
                    "cohort": "late_qing_standardized",
                    "outcome": "central/local route (V0.3)",
                    "value": route,
                    "n": int(count),
                    "share": round(float(count / len(late)), 4),
                    "classification": "NOT_COMPARABLE",
                    "note": "the early cohort has no route variable; comparing route shares with a tier census would compare constructs, not periods",
                }
            )
    return pd.DataFrame(rows)


def evaluate(early: pd.DataFrame, late: pd.DataFrame) -> dict[str, Any]:
    tables = {
        "documentation_share": documentation_share(early, late),
        "direct_capital": direct_capital(early, late),
        "regional_composition": regional_composition(early, late),
        "banner_status": banner_status(early, late),
        "elite_persistence": elite_persistence(early, late),
        "career_outcomes": career_outcomes(early, late),
    }
    combined = pd.concat(tables.values(), ignore_index=True)
    return {
        "tables": tables,
        "classification_counts": combined["classification"].value_counts().to_dict(),
        "pooled_regression": {
            "run": False,
            "reason": "cohorts differ in sampling design (elite census vs link-defined roster sample) and in variable definitions; pooling would estimate the design difference",
        },
    }
