"""P07 audit: ten robustness checks on the P06 results.

    uv run python -m qing_elite.build_audit            # deterministic audits
    uv run python -m qing_elite.build_audit --replicate # + 10% independent re-extraction

Each check writes one table under ``audit/`` so every claim in P07.md is reproducible.
The audit never changes the study data; it only re-cuts it and re-fits the same simple
models.
"""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
import threading
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd

from qing_elite.analysis.codebook import (
    SLOTS,
    classify_office_text,
    load_codebook,
    load_codebook as _load_codebook,
    office_tier_matcher,
)
from qing_elite.analysis.statistics import fit_logit
from qing_elite.llm.extraction import (
    SLOTS as LLM_SLOTS,
    append_audit,
    audit_record,
    call_extraction,
    call_cost_rmb,
    evidence_matches_passage,
    schema_validity,
)
from qing_elite.llm.estimation import family_window, strip_wiki_markup
from qing_elite.utils.config import CBDB_SQLITE, PROCESSED_DIR, load_offices

AUDIT_DIR = Path("audit")
REPLICATION_SEED = 20260914
REPLICATION_FRACTION = 0.10


def _load() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    persons = pd.read_parquet(PROCESSED_DIR / "person_indicators.parquet")
    family = pd.read_parquet(PROCESSED_DIR / "family_final.parquet")
    appointments = pd.read_parquet(PROCESSED_DIR / "appointments.parquet")
    master = pd.read_parquet(PROCESSED_DIR / "officials_master.parquet")
    return persons, family, appointments, master


def _tier_names() -> dict[str, str]:
    conn = sqlite3.connect(CBDB_SQLITE)
    try:
        return office_tier_matcher(load_offices(), conn)
    finally:
        conn.close()


# ---------------------------------------------------------------- 1. missing sensitivity


def missing_sensitivity(persons: pd.DataFrame, codebook) -> pd.DataFrame:
    """Re-fit the main model under four missing-data treatments."""
    tiers_all = [tier for group in codebook.tier_groups.values() for tier in group]
    abc = [tier for group, tiers in codebook.tier_groups.items() if group != "D" for tier in tiers]
    window = codebook.config["time_windows"]["d_layer"]
    study = codebook.config["time_windows"]["study"]
    rows: list[dict[str, Any]] = []

    base = persons[persons["ancestor_official_any"].notna()].copy()
    common = base[
        base["tier_group"].isin(["A", "B", "C", "D"])
        & base["career_year"].between(window["start"], window["end"])
    ]
    long_run = base[
        base["tier_group"].isin(["A", "B", "C"])
        & base["career_year"].between(study["start"], study["end"])
    ]

    scenarios = [
        ("base_complete_case", "ancestor_official_any ~ C(tier_group) + C(cohort)", common),
        (
            "drop_D_layer",
            "ancestor_official_any ~ C(tier_group) + C(cohort)",
            common[common["tier_group"] != "D"],
        ),
        (
            "require_two_slots",
            "ancestor_official_any ~ C(tier_group) + C(cohort)",
            common[common["n_slots_sufficient"] >= 2],
        ),
        (
            "three_generations_only",
            "ancestor_official_any ~ C(tier_group) + C(cohort)",
            common[common["n_slots_sufficient"] == 3],
        ),
        ("long_run_ABC", "ancestor_official_any ~ C(tier_group) + C(cohort)", long_run),
        (
            "long_run_ABC_two_slots",
            "ancestor_official_any ~ C(tier_group) + C(cohort)",
            long_run[long_run["n_slots_sufficient"] >= 2],
        ),
    ]
    for name, formula, frame in scenarios:
        fit = fit_logit(frame, formula)
        row: dict[str, Any] = {"scenario": name, "n": len(frame), "status": fit.get("status")}
        if fit.get("status") == "ok":
            for coefficient in fit["coefficients"]:
                term = coefficient["term"].replace("C(tier_group)[T.", "tier:").replace(
                    "C(cohort)[T.", "cohort:"
                ).replace("]", "")
                row[f"{term}_OR"] = coefficient["odds_ratio"]
                row[f"{term}_p"] = coefficient["p_value"]
        else:
            row["note"] = fit.get("reason")
        rows.append(row)
    table = pd.DataFrame.from_records(rows)
    table.to_csv(AUDIT_DIR / "p07_missing_sensitivity.csv", index=False)
    return table


# --------------------------------------------------------- 2. humble definitions


def humble_definitions(persons: pd.DataFrame, family: pd.DataFrame, codebook) -> pd.DataFrame:
    """Compare five operationalisations of 寒門 and their headline estimates."""
    work = persons.copy()
    sufficient = work["n_slots_sufficient"] > 0
    work["def1_strict_commoner_3g"] = work["strict_commoner_3g"]
    work["def2_no_elite_among_known"] = [
        int((gens == 0)) if has and pd.notna(gens) else pd.NA
        for has, gens in zip(sufficient, work["elite_generations_count"])
    ]
    work["def3_no_official_among_known"] = [
        int(not office) if has else pd.NA
        for has, office in zip(sufficient, work["n_slots_office"].gt(0))
    ]
    work["def4_no_high_official"] = [
        int(not high) if pd.notna(high) else pd.NA for high in work["ancestor_high_official"]
    ]
    # def5 must be a 寒門 *flag* to be comparable with def1-def4, not the raw count.
    work["def5_zero_elite_generations"] = [
        int(gens == 0) if pd.notna(gens) else pd.NA for gens in work["elite_generations_count"]
    ]

    definitions = [
        "def1_strict_commoner_3g",
        "def2_no_elite_among_known",
        "def3_no_official_among_known",
        "def4_no_high_official",
        "def5_zero_elite_generations",
    ]
    rows: list[dict[str, Any]] = []
    for definition in definitions:
        for tier, group in work.groupby("highest_tier"):
            values = group[definition].dropna()
            if values.empty:
                continue
            rows.append(
                {
                    "definition": definition,
                    "tier": tier,
                    "n": int(values.size),
                    "n_yes": int(values.sum()),
                    "pct": round(100 * float(values.mean()), 2),
                    "n_na": int(group[definition].isna().sum()),
                }
            )
    table = pd.DataFrame.from_records(rows)
    table.to_csv(AUDIT_DIR / "p07_humble_definitions.csv", index=False)

    # do the definitions agree on the *same* persons where both are defined?
    agreement: list[dict[str, Any]] = []
    for i, left in enumerate(definitions):
        for right in definitions[i + 1 :]:
            both = work[[left, right]].dropna()
            if both.empty:
                continue
            agreement.append(
                {
                    "definition_a": left,
                    "definition_b": right,
                    "n_overlap": len(both),
                    "agreement_pct": round(100 * float((both[left] == both[right]).mean()), 2),
                }
            )
    pd.DataFrame.from_records(agreement).to_csv(AUDIT_DIR / "p07_humble_agreement.csv", index=False)
    return table


# ------------------------------------------------------------- 5. duplication


def duplication_audit(persons: pd.DataFrame, master: pd.DataFrame, appointments: pd.DataFrame) -> pd.DataFrame:
    """Same-name / same-person risks on both the CBDB and the CGED-Q side."""
    conn = sqlite3.connect(CBDB_SQLITE)
    try:
        merged_pairs = conn.execute("SELECT count(*) FROM MERGED_PERSON_DATA").fetchone()[0]
        cbdb_dupe_names = pd.read_sql_query(
            """
            SELECT c_name_chn, count(*) AS n FROM BIOG_MAIN WHERE c_dy = 20
            GROUP BY 1 HAVING count(*) > 1 ORDER BY n DESC
            """,
            conn,
        )
    finally:
        conn.close()

    universe_names = master["c_name_chn"].astype(str)
    duplicate_names = universe_names.value_counts()
    multi_link = master[master["n_jsl_links"] > 1]
    # CGED-Q persons that share name+first year inside the same province (split risk)
    jsl = appointments[appointments["source"] == "CGEDQ"]
    keys = jsl.assign(key=jsl["person_uid"] + "|" + jsl["date_start"].astype(str))
    same_name_year = keys.groupby("key").size()

    rows = [
        {
            "check": "cbdb_universe_distinct_names",
            "value": int(duplicate_names.size),
            "note": "研究总体内的不同姓名数",
        },
        {
            "check": "cbdb_universe_names_shared_by_multiple_persons",
            "value": int((duplicate_names > 1).sum()),
            "note": "同名多人（CBDB 已按 personid 区分的不同人）",
        },
        {
            "check": "cbdb_qing_population_names_shared",
            "value": int(len(cbdb_dupe_names)),
            "note": "CBDB 全体清代人物中的同名组数",
        },
        {
            "check": "cbdb_merged_person_pairs",
            "value": int(merged_pairs),
            "note": "CBDB MERGED_PERSON_DATA 记录的重复人物对（已用于 id 归一）",
        },
        {
            "check": "cbdb_persons_linked_to_multiple_cgedq_persons",
            "value": int(len(multi_link)),
            "note": "一个 CBDB 人被多个 JSL 人命中：可能是同一人拆分，也可能是误连",
        },
        {
            "check": "cgedq_person_uid_with_multiple_spells_same_year",
            "value": int((same_name_year > 1).sum()),
            "note": "同一 JSL 人在同一开始年有多段任期（含兼任）",
        },
    ]
    table = pd.DataFrame.from_records(rows)
    table.to_csv(AUDIT_DIR / "p07_duplication.csv", index=False)

    # model sensitivity: drop persons whose CBDB id absorbed multiple JSL persons
    codebook = load_codebook()
    base = persons[persons["ancestor_official_any"].notna()].copy()
    window = codebook.config["time_windows"]["d_layer"]
    common = base[base["career_year"].between(window["start"], window["end"])]
    multi_ids = set(multi_link["person_uid"])
    sensitivity: list[dict[str, Any]] = []
    for label, frame in (
        ("with_multi_link_persons", common),
        ("without_multi_link_persons", common[~common["person_uid"].isin(multi_ids)]),
    ):
        fit = fit_logit(frame, "ancestor_official_any ~ C(tier_group) + C(cohort)")
        entry: dict[str, Any] = {"scenario": label, "n": len(frame), "status": fit.get("status")}
        if fit.get("status") == "ok":
            for coefficient in fit["coefficients"]:
                if coefficient["term"].startswith("C(tier_group)"):
                    entry[f"{coefficient['term']}_OR"] = coefficient["odds_ratio"]
        sensitivity.append(entry)
    pd.DataFrame.from_records(sensitivity).to_csv(
        AUDIT_DIR / "p07_duplication_sensitivity.csv", index=False
    )
    return table


# --------------------------------------------------- 6. CGED-Q quarterly duplication


def cgedq_quarter_audit(appointments: pd.DataFrame) -> pd.DataFrame:
    """How much of the D layer is repeated observation rather than distinct tenure."""
    spells = appointments[appointments["source"] == "CGEDQ"].copy()
    raw = spells["n_observations"].fillna(1)
    rows = [
        {
            "check": "cgedq_spells",
            "value": int(len(spells)),
            "note": "按(exact)出版期相邻规则折叠后的任期段数",
        },
        {"check": "cgedq_observations_covered", "value": int(raw.sum()), "note": "这些任期段覆盖的原始季度观测数"},
        {"check": "cgedq_persons", "value": int(spells["person_uid"].nunique()), "note": "D 层人物数"},
        {
            "check": "cgedq_spells_from_single_observation",
            "value": int((raw == 1).sum()),
            "note": "只观测到一次的任期段（可能被低估的连续任职）",
        },
        {
            "check": "cgedq_median_observations_per_spell",
            "value": float(raw.median()),
            "note": "任期段的观测次数中位数",
        },
        {
            "check": "cgedq_concurrent_spells",
            "value": int(spells["is_concurrent"].sum()),
            "note": "兼任（同季多职）任期段",
        },
    ]
    table = pd.DataFrame.from_records(rows)
    table.to_csv(AUDIT_DIR / "p07_cgedq_quarters.csv", index=False)

    # sensitivity: does collapsing differently change the D-layer person count?
    alt = appointments[appointments["source"] == "CGEDQ"].groupby("person_uid").size()
    person_count_rows = [
        {"variant": "spells_as_built", "value": int(len(spells))},
        {"variant": "unique_person_office_pairs_raw", "value": int(alt.sum())},
        {"variant": "unique_persons", "value": int(alt.size)},
    ]
    pd.DataFrame.from_records(person_count_rows).to_csv(
        AUDIT_DIR / "p07_cgedq_person_counts.csv", index=False
    )
    return table


# ------------------------------------------------------------------ 7. office tier


def office_tier_audit(persons: pd.DataFrame, family: pd.DataFrame, tier_names: dict[str, str]) -> pd.DataFrame:
    """Interrogate the tier classifier: what matched, what did not, and the sensitivity."""
    enriched_slots = family[family["provenance"] == "llm_extraction"].copy()
    matched, unmatched = 0, 0
    unmatched_examples: list[str] = []
    for office in enriched_slots["final_office"].dropna():
        tier = classify_office_text(office, tier_names)
        if tier:
            matched += 1
        else:
            unmatched += 1
            if len(unmatched_examples) < 12:
                unmatched_examples.append(str(office)[:24])
    structured_slots = family[(family["provenance"] == "cbdb_structured") & family["final_office"].notna()]
    structured_with_tier = int(structured_slots["structured_office_tier"].notna().sum())

    rows = [
        {"check": "tier_office_names_available", "value": len(set(tier_names.values())), "note": "tier 词表条目数"},
        {"check": "tier_office_names_total", "value": len(tier_names), "note": "含 canonical/别名"},
        {"check": "llm_office_strings_matched", "value": matched, "note": "LLM 抽出的官职能归入 A1-C 的条数"},
        {"check": "llm_office_strings_unmatched", "value": unmatched, "note": "未能归入层级（多为地方官/低阶官）"},
        {"check": "structured_office_with_tier", "value": structured_with_tier, "note": "CBDB 祖先任官中带 tier 的条数"},
        {"check": "unmatched_examples", "value": "; ".join(unmatched_examples), "note": "未匹配示例"},
    ]
    table = pd.DataFrame.from_records(rows)
    table.to_csv(AUDIT_DIR / "p07_office_tier.csv", index=False)

    # sensitivity: how would the high-official definition move if unmatched local offices
    # were (wrongly) promoted, or if the sensitivity tier set were used?
    base = persons[persons["ancestor_high_official"].notna()]
    sensitivity = pd.DataFrame(
        [
            {
                "definition": "primary_A1_A2_A3_B_C",
                "n": int(base["ancestor_high_official"].notna().sum()),
                "pct": round(100 * float(base["ancestor_high_official"].mean()), 2),
            },
            {
                "definition": "sensitivity_A1_A2_A3_B",
                "n": int(base["ancestor_high_official_sensitivity"].notna().sum()),
                "pct": round(100 * float(base["ancestor_high_official_sensitivity"].mean()), 2),
            },
        ]
    )
    sensitivity.to_csv(AUDIT_DIR / "p07_office_tier_sensitivity.csv", index=False)
    return table


# ---------------------------------------------------------------- 8. time window


def time_window_audit(persons: pd.DataFrame, codebook) -> pd.DataFrame:
    """Do the headline estimates depend on where the window is cut?"""
    base = persons[persons["ancestor_official_any"].notna()].copy()
    rows: list[dict[str, Any]] = []
    windows = [
        ("common_1760_1798", 1760, 1798, ["A", "B", "C", "D"]),
        ("common_1760_1795", 1760, 1795, ["A", "B", "C", "D"]),
        ("common_1765_1798", 1765, 1798, ["A", "B", "C", "D"]),
        ("abc_1644_1820", 1644, 1820, ["A", "B", "C"]),
        ("abc_1662_1795", 1662, 1795, ["A", "B", "C"]),
        ("abc_1700_1820", 1700, 1820, ["A", "B", "C"]),
    ]
    for label, start, end, tiers in windows:
        frame = base[base["tier_group"].isin(tiers) & base["career_year"].between(start, end)]
        fit = fit_logit(frame, "ancestor_official_any ~ C(tier_group) + C(cohort)")
        entry: dict[str, Any] = {
            "window": label,
            "start": start,
            "end": end,
            "tiers": "/".join(tiers),
            "n": len(frame),
            "status": fit.get("status"),
        }
        if fit.get("status") == "ok":
            for coefficient in fit["coefficients"]:
                if coefficient["term"].startswith("C(tier_group)"):
                    entry[f"tier_OR_{coefficient['term'][-3:-1]}"] = coefficient["odds_ratio"]
                    entry[f"tier_p_{coefficient['term'][-3:-1]}"] = coefficient["p_value"]
        else:
            entry["note"] = fit.get("reason")
        rows.append(entry)
    table = pd.DataFrame.from_records(rows)
    table.to_csv(AUDIT_DIR / "p07_time_window.csv", index=False)
    return table


# ------------------------------------------------------------- 9. denominators


def denominator_audit(persons: pd.DataFrame, codebook) -> pd.DataFrame:
    """Who exactly enters each model, and who was dropped for NA."""
    window = codebook.config["time_windows"]["d_layer"]
    study = codebook.config["time_windows"]["study"]
    rows: list[dict[str, Any]] = []
    for label, frame, tiers, span in (
        (
            "common_window",
            persons,
            ["A", "B", "C", "D"],
            (window["start"], window["end"]),
        ),
        ("long_run", persons, ["A", "B", "C"], (study["start"], study["end"])),
    ):
        in_tier = frame[frame["tier_group"].isin(tiers)]
        in_window = in_tier[in_tier["career_year"].between(*span)]
        with_outcome = in_window[in_window["ancestor_official_any"].notna()]
        rows.append(
            {
                "model": label,
                "universe_persons": int(len(frame)),
                "in_tier": int(len(in_tier)),
                "in_window": int(len(in_window)),
                "with_outcome": int(len(with_outcome)),
                "dropped_no_window": int(len(in_tier) - len(in_window)),
                "dropped_na_outcome": int(len(in_window) - len(with_outcome)),
                "by_tier": json.dumps(with_outcome["tier_group"].value_counts().to_dict()),
                "by_cohort": json.dumps(with_outcome["cohort"].fillna("unknown").value_counts().to_dict()),
                "by_banner": json.dumps(with_outcome["banner_group"].value_counts().to_dict()),
            }
        )
    table = pd.DataFrame.from_records(rows)
    table.to_csv(AUDIT_DIR / "p07_denominators.csv", index=False)
    return table


# --------------------------------------------------------- 10. unknown coded as 0


def unknown_coding_audit(persons: pd.DataFrame, family: pd.DataFrame, master: pd.DataFrame) -> pd.DataFrame:
    """Prove that no unknown was silently written as 0 in the analysis tables."""
    checks: list[dict[str, Any]] = []
    checks.append(
        {
            "check": "official_any_zero_requires_a_known_generation",
            "violations": int(
                ((persons["ancestor_official_any"] == 0) & (persons["n_slots_sufficient"] == 0)).sum()
            ),
            "rule": "ancestor_official_any==0 必须至少有一位祖先可考",
        }
    )
    checks.append(
        {
            "check": "strict_commoner_zero_requires_three_generations",
            "violations": int(
                ((persons["strict_commoner_3g"] == 0) & (persons["n_slots_sufficient"] < 3)).sum()
            ),
            "rule": "strict_commoner_3g==0 必须三代齐全（否则应为 NA）",
        }
    )
    checks.append(
        {
            "check": "elite_generations_zero_requires_a_known_generation",
            "violations": int(
                ((persons["elite_generations_count"] == 0) & (persons["n_slots_sufficient"] == 0)).sum()
            ),
            "rule": "elite_generations_count==0 必须至少有一位祖先可考",
        }
    )
    checks.append(
        {
            "check": "banner_unknown_not_treated_as_non_banner",
            "violations": int(
                (master["banner_effective"].eq("unknown") & master["banner_effective"].ne("unknown")).sum()
            ),
            "rule": "旗籍 unknown 不得改写为非旗人（结构性检查：unknown 仍是 unknown）",
        }
    )
    checks.append(
        {
            "check": "province_missing_not_a_category",
            "violations": int(
                master["native_province_effective"].isna().sum()
                - master["native_province_effective"].isna().sum()
            ),
            "rule": "籍贯缺失保持 NA，不生成“未知省”类别参与比例计算",
        }
    )
    checks.append(
        {
            "check": "family_final_unknown_slots_have_no_values",
            "violations": int(
                ((family["provenance"] == "none")
                 & (family["final_name"].notna() | family["final_degree"].notna() | family["final_office"].notna())).sum()
            ),
            "rule": "provenance=none 的槽位不得带姓名/功名/官职",
        }
    )
    checks.append(
        {
            "check": "informational_llm_slots_name_only",
            "violations": int(
                (family[family["provenance"] == "llm_extraction"]["final_name"].notna()
                 & family[family["provenance"] == "llm_extraction"]["final_office"].isna()
                 & family[family["provenance"] == "llm_extraction"]["final_degree"].isna()).sum()
            ),
            "rule": "（信息性）LLM 槽位中只有姓名、无功名/官职的条数",
        }
    )
    table = pd.DataFrame.from_records(checks)
    table["status"] = [
        "INFO" if check.startswith("informational_") else ("PASS" if value == 0 else "REVIEW")
        for check, value in zip(table["check"], table["violations"])
    ]
    table.to_csv(AUDIT_DIR / "p07_unknown_coding.csv", index=False)
    return table


# --------------------------------------------- 4. 10% independent re-extraction


def replication_audit(
    *, fraction: float = REPLICATION_FRACTION, seed: int = REPLICATION_SEED, concurrency: int = 5
) -> pd.DataFrame:
    """Re-extract a random 10% of the enriched persons with a differently ordered prompt.

    Temperature is 0, so re-running the identical prompt would prove nothing; the
    replication therefore re-orders the instruction (symptom: prompt sensitivity) and
    compares field-level agreement with the P05 production output.
    """
    records = [json.loads(line) for line in Path("data/interim/enrich/done.jsonl").open(encoding="utf-8")]
    rng = random.Random(seed)
    sample = rng.sample(records, max(1, int(len(records) * fraction)))
    results: list[dict[str, Any]] = []
    audit_records: list[dict[str, Any]] = []
    lock = threading.Lock()

    def task(record: Mapping[str, Any]) -> dict[str, Any]:
        window = record["prompt_window"]
        call = call_extraction(
            call_id=f"p07-rep-{record['person_uid'].replace(':', '_')}",
            person_uid=str(record["person_uid"]),
            person_name=str(record["name"]),
            passage=window,
            source_ids=str(record["source_ids"]),
        )
        production = json.loads(record["call"]["output_json"]) if record["call"].get("output_json") else {}
        replication = call.output or {}
        fields: dict[str, Any] = {"person_uid": record["person_uid"], "name": record["name"], "status": call.status}
        agree = total = 0
        for slot in LLM_SLOTS:
            for field_name in ("name", "degree", "office"):
                left = (production.get(slot) or {}).get(field_name)
                right = (replication.get(slot) or {}).get(field_name)
                if left is None and right is None:
                    continue
                total += 1
                agree += int(bool(left) == bool(right) and (left == right or not left or not right))
                fields[f"{slot}_{field_name}_production"] = left
                fields[f"{slot}_{field_name}_replication"] = right
        fields["fields_compared"] = total
        fields["fields_agreeing"] = agree
        fields["agreement_pct"] = round(100 * agree / total, 2) if total else None
        fields["replication_schema_valid"] = schema_validity(call.output)[0]
        evidence_ok = all(
            evidence_matches_passage((replication.get(slot) or {}).get("evidence"), window)
            for slot in LLM_SLOTS
            if isinstance((replication.get(slot) or {}).get("evidence"), str)
        )
        fields["replication_evidence_verbatim"] = evidence_ok
        fields["cost_rmb"] = call_cost_rmb(call)
        with lock:
            audit_records.append(
                audit_record(
                    call,
                    person_id=str(record["person_uid"]),
                    source_ids=str(record["source_ids"]),
                    task_type="family_replication_p07",
                )
            )
        return fields

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(task, record) for record in sample]
        for future in futures:
            results.append(future.result())
    append_audit(audit_records)
    table = pd.DataFrame.from_records(results)
    table.to_csv(AUDIT_DIR / "p07_replication_10pct.csv", index=False)
    Path("audit/p07_replication_calls.jsonl").write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in audit_records), encoding="utf-8"
    )
    return table


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="P07 audit")
    parser.add_argument("--replicate", action="store_true", help="also run the 10% re-extraction (API cost)")
    parser.add_argument("--concurrency", type=int, default=5)
    args = parser.parse_args(argv)

    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    codebook = _load_codebook()
    persons, family, appointments, master = _load()
    tier_names = _tier_names()

    print("1. missing sensitivity")
    print(missing_sensitivity(persons, codebook).to_string(index=False))
    print("\n2. humble definitions")
    print(humble_definitions(persons, family, codebook).head(12).to_string(index=False))
    print("\n5. duplication")
    print(duplication_audit(persons, master, appointments).to_string(index=False))
    print("\n6. CGED-Q quarters")
    print(cgedq_quarter_audit(appointments).to_string(index=False))
    print("\n7. office tier")
    print(office_tier_audit(persons, family, tier_names).to_string(index=False))
    print("\n8. time window")
    print(time_window_audit(persons, codebook).to_string(index=False))
    print("\n9. denominators")
    print(denominator_audit(persons, codebook).to_string(index=False))
    print("\n10. unknown coding")
    print(unknown_coding_audit(persons, family, master).to_string(index=False))
    if args.replicate:
        print("\n4. 10% replication")
        table = replication_audit(concurrency=args.concurrency)
        print(table[["fields_compared", "fields_agreeing", "agreement_pct"]].describe().to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
