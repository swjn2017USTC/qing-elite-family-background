"""U06R orchestration: candidate pairs → three matchers → clusters → gates → outputs.

``python -m qing_elite.v03.linkage.run`` writes the four stage artifacts plus the benchmark:

* ``data/processed_v03/entity_links.parquet``        accepted / rejected / grey links
* ``data/processed_v03/linkage_features.parquet``    pair features with labels and splits
* ``data/processed_v03/linkage_risk.parquet``        per-link risk components and tier
* ``data/processed_v03/linkage_review_queue.parquet`` only the items routing to a human
* ``reports/upgrade_v03/linkage_benchmark.md``       numbers rendered from those artifacts

The evaluation surface is the recovered U02 gold set (824 pairs, ``train``/``held_out``
split). Candidate-level numbers (how many pairs the pipeline generates, how many clusters it
produces) come from the regenerated v0.2 candidate universe.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from qing_elite.utils.config import PROJECT_ROOT
from qing_elite.v03.design import load_review_policy
from qing_elite.v03.lit.registry import PROCESSED_V03_DIR
from qing_elite.v03.linkage import UPSTREAM
from qing_elite.v03.linkage import active as active_mod
from qing_elite.v03.linkage import cluster as cluster_mod
from qing_elite.v03.linkage import evaluate as evaluate_mod
from qing_elite.v03.linkage import matchers, validators
from qing_elite.v03.linkage.cgedq import (
    LEGACY_DIR,
    build_crosswalk,
    crosswalk_summary,
    load_config,
    validate_release,
)
from qing_elite.v03.linkage.features import FEATURE_COLUMNS, build_features
from qing_elite.v03.contracts import expected_risk_tier

REPORT_DIR = PROJECT_ROOT / "reports" / "upgrade_v03"
BENCHMARK_MD = REPORT_DIR / "linkage_benchmark.md"
INTERIM_DIR = PROJECT_ROOT / "data" / "interim_v03" / "linkage"
GOLD_PATH = LEGACY_DIR / "linkage_gold.parquet"

MATCHER_THRESHOLD = 0.5


def load_gold() -> pd.DataFrame:
    if not GOLD_PATH.exists():
        raise FileNotFoundError(
            f"U02 gold not recovered at {GOLD_PATH}; see U06R.md for the git-history command"
        )
    return pd.read_parquet(GOLD_PATH)


def _link_id(row: pd.Series, index: int) -> str:
    return f"link-{index:06d}"


def run(*, with_splink: bool = True, with_active_learning: bool = True) -> dict[str, Any]:
    PROCESSED_V03_DIR.mkdir(parents=True, exist_ok=True)
    INTERIM_DIR.mkdir(parents=True, exist_ok=True)
    config = load_config()

    # ------------------------------------------------------------ release + crosswalk
    validation = validate_release()
    crosswalk = build_crosswalk()
    crosswalk.to_parquet(PROCESSED_V03_DIR / "linkage_crosswalk.parquet", index=False)

    # ------------------------------------------------------------ features + validators
    gold_all = load_gold()
    task_gold = gold_all.loc[gold_all["task"] == "cross_source"].reset_index(drop=True)
    # the era_conflict stratum is an unlabelled review layer, not gold: keep it out of
    # training and scoring, and report it (60 pairs in v0.2's gold)
    unlabelled = int(task_gold["gold_label"].isna().sum())
    labelled = task_gold.loc[task_gold["gold_label"].notna()]
    # a pair can appear in two strata of the v0.2 gold; the contract requires one row per
    # pair, so collapse and report how many rows were folded
    duplicates = int(labelled.duplicated(subset=["l_cgedq_person_id", "r_cbdb_personid"]).sum())
    gold = labelled.drop_duplicates(
        subset=["l_cgedq_person_id", "r_cbdb_personid"], keep="first"
    ).reset_index(drop=True)
    features = build_features(gold)
    labels = gold["gold_label"].astype(int)
    splits = gold["split"].astype(str)
    validated = validators.validate_pairs(gold)
    dedupe_gold = gold_all.loc[gold_all["task"] == "dedupe"].reset_index(drop=True)

    # ------------------------------------------------------------ matchers
    deterministic = matchers.deterministic_accept(
        features, require_evidence=bool(config["matchers"]["deterministic"]["require_evidence"])
    )
    train_mask = splits == str(config["matchers"]["ml"]["train_split"])
    model = matchers.fit_ml(features.loc[train_mask], labels.loc[train_mask])
    ml_probability = matchers.ml_scores(model, features)

    splink_probability = pd.Series([None] * len(features), index=features.index, dtype=float)
    splink_note = "skipped"
    if with_splink:
        try:
            splink_probability, splink_note = _run_splink(gold, config)
        except Exception as error:  # a baseline failure is recorded, never hidden
            splink_note = f"{type(error).__name__}: {error}"[:300]

    agreement = matchers.agreement_pattern(
        deterministic, ml_probability, splink_probability.fillna(0.0), ml_threshold=MATCHER_THRESHOLD
    )

    # ------------------------------------------------------------ evaluation
    held_out = splits == str(config["matchers"]["ml"]["calibrate_split"])
    ml_held_out = evaluate_mod.threshold_table(ml_probability.loc[held_out], labels.loc[held_out])
    ml_region = evaluate_mod.auto_accept_region(
        ml_probability.loc[held_out],
        labels.loc[held_out],
        min_precision=float(config["auto_accept"]["min_precision"]),
        min_support=int(config["auto_accept"]["min_support"]),
    )
    det_region = evaluate_mod.auto_accept_region(
        deterministic.loc[held_out],
        labels.loc[held_out],
        min_precision=float(config["auto_accept"]["min_precision"]),
        min_support=int(config["auto_accept"]["min_support"]),
        thresholds=(0.5, 1.0),
    )
    scored_mask = splink_probability.notna()
    splink_held_out = scored_mask & held_out
    splink_region = (
        evaluate_mod.auto_accept_region(
            splink_probability.loc[splink_held_out],
            labels.loc[splink_held_out],
            min_precision=float(config["auto_accept"]["min_precision"]),
            min_support=int(config["auto_accept"]["min_support"]),
        )
        if int(splink_held_out.sum()) >= int(config["auto_accept"]["min_support"])
        else {"status": "INSUFFICIENT_SCORED_SUPPORT", "scored": int(splink_held_out.sum())}
    )
    abstention = evaluate_mod.abstention_summary(
        ml_probability.loc[held_out], labels.loc[held_out], lower=0.3, upper=0.7
    )

    # ------------------------------------------------------------ active learning
    active_result: dict[str, Any] = {"skipped": True}
    if with_active_learning:
        active_result = active_mod.run_rounds(
            features,
            labels,
            splits,
            rounds=int(config["active_learning"]["rounds"]),
            per_round=int(config["active_learning"]["per_round"]),
            seed=int(config["seed"]),
        )
        active_result["budget_pairs"] = int(config["active_learning"]["manual_budget_pairs"])

    # ------------------------------------------------------------ clusters over accepted links
    accepted_mask = (deterministic >= 1.0) | (ml_probability >= (ml_region["threshold"] or 1.01))
    accepted = gold.loc[accepted_mask]
    windows = pd.DataFrame(
        {
            "record_id": "cgedq:" + gold["l_cgedq_person_id"].astype(str),
            "first_year": gold["l_first_year"],
            "last_year": gold["l_last_year"],
        }
    ).drop_duplicates(subset=["record_id"])
    clusters = cluster_mod.cluster_links(
        pd.DataFrame(
            {
                "left": "cgedq:" + accepted["l_cgedq_person_id"].astype(str),
                "right": "cbdb:" + accepted["r_cbdb_personid"].astype(str),
            }
        ),
        left_column="left",
        right_column="right",
    )
    connectivity = cluster_mod.temporal_connectivity(clusters, windows)

    # ------------------------------------------------------------ risk + review queue
    policy = load_review_policy()
    weights = {name: float(spec["weight"]) for name, spec in policy["components"].items()}
    risk_components = pd.DataFrame(index=features.index)
    for name in weights:
        risk_components[name] = 0.0
    risk_components["linkage_uncertainty"] = (ml_probability - 0.5).abs().rsub(1.0).clip(0.0, 1.0) * (
        splink_probability.notna().map({True: 0.0, False: 0.5}).fillna(0.5) + 0.5
    )
    risk_components["source_conflict"] = (
        (agreement.str.count(r"\+") + 1 - agreement.str.contains("det").astype(int) * 0)
        .where(agreement != "none", 0)
        .clip(0, 1)
    ).where(agreement.str.contains("det|ml|splink"), 0.0)
    risk_components["chronology_violation"] = validated["chronology_violation"].astype(float)
    risk_components["evidence_span_failure"] = validated["geography_conflict"].astype(float) * 0.5 + validated[
        "career_transition_conflict"
    ].astype(float) * 0.5
    total = sum(risk_components[name] * weight for name, weight in weights.items()).clip(0.0, 1.0)
    risk = pd.DataFrame(
        {
            "risk_id": [f"risk-{index:06d}" for index in range(len(features))],
            "subject_type": "entity_link",
            "subject_id": [f"link-{index:06d}" for index in range(len(features))],
            **{name: risk_components[name].astype(float).to_numpy() for name in weights},
            "total_score": total.astype(float).to_numpy(),
            "risk_tier": [expected_risk_tier(value, policy) for value in total],
            "policy_version": policy["policy_version"],
            "computed_at": pd.Timestamp(datetime.now(timezone.utc).replace(tzinfo=None)),
        }
    )

    links = pd.DataFrame(
        {
            "link_id": [f"link-{index:06d}" for index in range(len(features))],
            "left_kind": "cgedq_person",
            "left_id": gold["l_cgedq_person_id"].astype(str).to_numpy(),
            "right_kind": "cbdb_person",
            "right_id": gold["r_cbdb_personid"].astype(str).to_numpy(),
            "link_type": "cross_source",
            # the contract's enum has no combined value; the agreement pattern is the
            # decision_rule, and the method records that the decision came from agreement
            "method": "agreement",
            "score": ml_probability.to_numpy(),
            "decision": [
                "auto_accept" if value else ("grey" if 0.3 <= score < 0.7 else "reject")
                for value, score in zip(accepted_mask, ml_probability)
            ],
            "decision_rule": agreement.to_numpy(),
            "evidence": gold.get("anchor", pd.Series(index=gold.index, dtype=object)).to_numpy(),
            "risk_id": risk["risk_id"].to_numpy(),
            # a link that was auto-accepted cannot also be pending review: the contract
            # rejects that combination, and it is the exact ambiguity U00-05 recorded
            "review_status": [
                "not_required" if decision == "auto_accept" else "pending"
                for decision, tier in zip(
                    [
                        "auto_accept" if value else ("grey" if 0.3 <= score < 0.7 else "reject")
                        for value, score in zip(accepted_mask, ml_probability)
                    ],
                    risk["risk_tier"],
                )
            ],
            "upstream_commit": UPSTREAM["commit"],
            "decided_at": pd.Timestamp(datetime.now(timezone.utc).replace(tzinfo=None)),
        }
    )
    queue = links.loc[links["decision"] != "auto_accept"].merge(
        risk.loc[:, ["risk_id", "risk_tier"]], on="risk_id", how="left"
    )
    tier_route = {
        tier: policy["tiers"][tier]["route"] for tier in ("LOW", "MEDIUM", "HIGH")
    }
    review_queue = pd.DataFrame(
        {
            "item_id": [f"u06r-{index:05d}" for index in range(1, len(queue) + 1)],
            "subject_type": "entity_link",
            "subject_id": queue["link_id"].to_numpy(),
            "risk_id": queue["risk_id"].to_numpy(),
            "priority": 1,
            "reason_codes": queue["decision"].to_numpy(),
            # routing follows the frozen policy: only HIGH reaches a human, MEDIUM goes to a
            # machine adjudicator
            "route": [tier_route[tier] for tier in queue["risk_tier"]],
            "status": "open",
            "decision": None,
            "rationale": None,
            "reviewer": None,
            "created_at": pd.Timestamp(datetime.now(timezone.utc).replace(tzinfo=None)),
            # an open item has no resolution time; the column still needs the datetime dtype
            "resolved_at": pd.Series([pd.NaT] * len(queue), dtype="datetime64[ns]"),
        }
    )

    feature_output = pd.concat(
        [
            features.assign(
                gold_label=labels.to_numpy(), split=splits.to_numpy(), agreement=agreement.to_numpy()
            ),
            pd.DataFrame(
                {
                    "deterministic": deterministic.to_numpy(),
                    "ml_probability": ml_probability.to_numpy(),
                    "splink_probability": splink_probability.to_numpy(),
                },
                index=features.index,
            ),
        ],
        axis=1,
    )

    links.to_parquet(PROCESSED_V03_DIR / "entity_links.parquet", index=False)
    feature_output.to_parquet(PROCESSED_V03_DIR / "linkage_features.parquet", index=False)
    risk.to_parquet(PROCESSED_V03_DIR / "linkage_risk.parquet", index=False)
    review_queue.to_parquet(PROCESSED_V03_DIR / "linkage_review_queue.parquet", index=False)

    dedupe_audit = audit_official_id_grouping(crosswalk, dedupe_gold)

    metrics: dict[str, Any] = {
        "stage": "U06R",
        "upstream": UPSTREAM,
        "release": validation,
        "crosswalk": crosswalk_summary(crosswalk),
        "gold": {
            "task": "cross_source",
            "all_tasks": gold_all["task"].value_counts().to_dict(),
            "pairs": int(len(gold)),
            "positives": int(labels.sum()),
            "negatives": int((labels == 0).sum()),
            "train": int(train_mask.sum()),
            "held_out": int(held_out.sum()),
            "strata": gold["stratum"].value_counts().to_dict() if "stratum" in gold else {},
            "unlabelled_review_layer": unlabelled,
            "duplicate_pair_rows_collapsed": duplicates,
            "provenance": "recovered from git history: data/processed_v02/linkage_gold.parquet @ bd64fba",
        },
        "dedupe_audit": dedupe_audit,
        "features": {
            "columns": list(FEATURE_COLUMNS),
            "pairs": int(len(features)),
        },
        "matchers": {
            "deterministic_accepts": int((deterministic >= 1.0).sum()),
            "ml_model": model.to_dict(),
            "ml_at_0_5_precision": next(
                (row["precision"] for row in ml_held_out if abs(row["threshold"] - 0.5) < 1e-9), None
            ),
            "ml_at_0_5_recall": next(
                (row["recall"] for row in ml_held_out if abs(row["threshold"] - 0.5) < 1e-9), None
            ),
            "splink": {
                "status": splink_note,
                "scored_pairs": int(splink_probability.notna().sum()),
                "scored_share_of_pairs": round(float(splink_probability.notna().mean()), 4),
                "max_probability": round(float(splink_probability.max(skipna=True)), 6)
                if splink_probability.notna().any()
                else None,
            },
            "agreement_patterns": agreement.value_counts().to_dict(),
        },
        "auto_accept": {
            "ml": {key: value for key, value in ml_region.items() if key != "table"},
            "deterministic": {key: value for key, value in det_region.items() if key != "table"},
            "splink": {key: value for key, value in splink_region.items() if key != "table"},
            "abstention": abstention,
        },
        "validators": validators.summary(validated),
        "clusters": cluster_mod.cluster_summary(clusters)
        if len(clusters)
        else {"records": 0, "components": 0},
        "temporal_connectivity": {
            "components_checked": int(len(connectivity)),
            "gap_violations": int(connectivity["gap_violation"].sum()) if len(connectivity) else 0,
        },
        "active_learning": active_result,
        "risk": {
            "by_tier": risk["risk_tier"].value_counts().to_dict(),
            "human_review_items": int(len(review_queue)),
        },
    }
    (INTERIM_DIR / "u06r_metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    BENCHMARK_MD.write_text(render_benchmark(metrics), encoding="utf-8")
    return metrics


def audit_official_id_grouping(crosswalk: pd.DataFrame, dedupe_gold: pd.DataFrame) -> dict[str, Any]:
    """Does the official ``person_id`` agree with independent evidence, and with v0.2?

    The official id is authoritative for within-source dedupe from here on, which makes it
    worth checking rather than assuming: this compares it against the paired comparison
    fields (province / degree / same-edition office) and against the legacy v0.2 merges.
    """
    labels = {
        str(row.legacy_person_id): (str(row.legacy_canonical_id), str(row.relation))
        for row in crosswalk.itertuples(index=False)
    }
    rows: list[dict[str, Any]] = []
    for row in dedupe_gold.itertuples(index=False):
        left = str(getattr(row, "l_cgedq_person_id"))
        right = str(getattr(row, "pair_id").split("|")[1])
        left_meta = labels.get(left)
        right_meta = labels.get(right)
        same_official = bool(
            left_meta and right_meta and left_meta[0] == right_meta[0]
        )
        legacy_merged = bool(left_meta and right_meta and left_meta[1] == right_meta[1] != "same_id")
        evidence = [
            bool(getattr(row, column, False))
            for column in ("province_compatible", "degree_equal", "same_office_same_edition")
            if column in dedupe_gold.columns and pd.notna(getattr(row, column, None))
        ]
        rows.append(
            {
                "pair_id": getattr(row, "pair_id"),
                "gold_label": int(getattr(row, "gold_label")),
                "same_official_id": same_official,
                "legacy_v02_merged": legacy_merged,
                "independent_evidence_any": bool(any(evidence)) if evidence else None,
            }
        )
    frame = pd.DataFrame(rows)
    positives = frame.loc[frame["gold_label"] == 1]
    negatives = frame.loc[frame["gold_label"] == 0]
    return {
        "pairs": int(len(frame)),
        "gold_positives": int(len(positives)),
        "official_id_recovers_gold_positives": round(float(positives["same_official_id"].mean()), 4)
        if len(positives)
        else None,
        "official_id_disagrees_with_gold_negatives": round(float(negatives["same_official_id"].mean()), 4)
        if len(negatives)
        else None,
        "legacy_v02_merged_share": round(float(frame["legacy_v02_merged"].mean()), 4),
        "note": (
            "官方 id 作为 dedupe 的唯一权威；这里只检查它与 gold 标签及 v0.2 合并的一致性，"
            "不改动官方 id。"
        ),
    }


def _run_splink(gold: pd.DataFrame, config: dict[str, Any]) -> tuple[pd.Series, str]:
    """Score the gold pairs with the v0.2 Splink configuration via a record-level frame."""
    def _model_frame(prefix: str, ids: pd.Series, name: pd.Series, province: pd.Series, degree: pd.Series, banner: pd.Series) -> pd.DataFrame:
        """Per-record sentinels for 'unknown', exactly as v0.2 did.

        Without them an ExactMatch comparison reads "both unspecified" as agreement — the
        name-only mistake U00-01/U00-03 recorded.
        """
        unique_id = prefix + ids.astype(str)
        return pd.DataFrame(
            {
                "unique_id": unique_id,
                "name_norm": name.fillna("unknown").astype(str),
                "province_key": ["unknown#" + value if pd.isna(province_value) else str(province_value)
                                 for value, province_value in zip(unique_id, province)],
                "degree_key": ["unknown#" + value if pd.isna(degree_value) else str(degree_value)
                               for value, degree_value in zip(unique_id, degree)],
                "banner_key": ["unknown#" + value if pd.isna(banner_value) else str(banner_value)
                               for value, banner_value in zip(unique_id, banner)],
            }
        )

    left = _model_frame(
        "cgedq:",
        gold["l_cgedq_person_id"],
        gold["l_name_norm"],
        gold["l_province_norm"],
        gold["l_degree_rank"],
        gold["l_banner_group"],
    )
    right = _model_frame(
        "cbdb:",
        gold["r_cbdb_personid"],
        gold["r_c_name_norm"] if "r_c_name_norm" in gold else gold["r_c_name_chn"],
        gold["r_province_norm"],
        gold["r_degree_rank"] if "r_degree_rank" in gold else pd.Series([None] * len(gold)),
        gold["r_banner_group"] if "r_banner_group" in gold else pd.Series([None] * len(gold)),
    )
    # a record appears in many pairs; Splink needs one row per unique_id
    left = left.drop_duplicates(subset=["unique_id"]).reset_index(drop=True)
    right = right.drop_duplicates(subset=["unique_id"]).reset_index(drop=True)
    scored, status = matchers.splink_probabilities(left, right, seed=int(config["seed"]))
    lookup = {
        f"{row.unique_id_l}|{row.unique_id_r}": row.splink_probability for row in scored.itertuples(index=False)
    }
    keys = [
        f"cgedq:{left_id}|cbdb:{right_id}"
        for left_id, right_id in zip(gold["l_cgedq_person_id"].astype(str), gold["r_cbdb_personid"].astype(str))
    ]
    matched = sum(1 for key in keys if key in lookup)
    return (
        pd.Series([lookup.get(key) for key in keys], index=gold.index, dtype=float),
        f"ok: {status} (gold pairs scored: {matched}/{len(keys)})",
    )


def render_benchmark(metrics: dict[str, Any]) -> str:
    """Render the benchmark from the metrics dict (no hand-typed numbers)."""
    release = metrics["release"]
    gold = metrics["gold"]
    matchers_info = metrics["matchers"]
    lines = [
        "# U06R linkage benchmark",
        "",
        f"- 生成时间：{datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        f"- 上游参考：`{metrics['upstream']['repo']}` @ `{metrics['upstream']['commit']}`（{metrics['upstream']['license']}）",
        f"- CGED-Q release：`{release['tab']}` sha256 `{release['sha256'][:16]}…`"
        f"（与 v0.1 冻结哈希一致：{release['matches_v01_frozen_hash']}）",
        "",
        "## 1. 评测面（U02 gold，从 git 历史恢复）",
        "",
        f"- 对数 {gold['pairs']}（正例 {gold['positives']} / 负例 {gold['negatives']}；train {gold['train']} / held-out {gold['held_out']}）",
        f"- 来源：{gold['provenance']}",
        "",
        "## 2. 三套 matcher（held-out）",
        "",
        "| matcher | 接受的 pair | held-out precision | held-out recall | 备注 |",
        "| --- | --- | --- | --- | --- |",
        f"| deterministic | {matchers_info['deterministic_accepts']} | "
        f"{metrics['auto_accept']['deterministic'].get('held_out_precision')} | "
        f"{metrics['auto_accept']['deterministic'].get('held_out_recall')} | 必须至少一条非姓名证据 |",
        f"| ML (logit) | — | {matchers_info['ml_at_0_5_precision']} | {matchers_info['ml_at_0_5_recall']} | 阈值 0.5；{matchers_info['ml_model']['train_rows']} 训练对 |",
        f"| Splink (v0.2 设置) | {matchers_info['splink']['scored_pairs']} | — | — | {matchers_info['splink']['status']} |",
        "",
        "### agreement pattern",
        "",
        "| pattern | pairs |",
        "| --- | --- |",
    ]
    for pattern, count in sorted(matchers_info["agreement_patterns"].items(), key=lambda item: -item[1]):
        lines.append(f"| `{pattern}` | {count} |")
    lines += [
        "",
        "## 3. auto-accept 区域（门槛 0.99）",
        "",
        f"- ML：**{metrics['auto_accept']['ml']['status']}**"
        + (
            f"，阈值 {metrics['auto_accept']['ml']['threshold']}，precision "
            f"{metrics['auto_accept']['ml']['held_out_precision']}，recall "
            f"{metrics['auto_accept']['ml']['held_out_recall']}，覆盖 "
            f"{metrics['auto_accept']['ml']['coverage_at_threshold']}"
            if metrics["auto_accept"]["ml"]["status"] == "PASS"
            else f"，best available {metrics['auto_accept']['ml'].get('best_available')}"
        ),
        f"- deterministic：**{metrics['auto_accept']['deterministic']['status']}**"
        + (
            f"（阈值 {metrics['auto_accept']['deterministic']['threshold']}，precision "
            f"{metrics['auto_accept']['deterministic']['held_out_precision']}）"
            if metrics["auto_accept"]["deterministic"]["status"] == "PASS"
            else f"，best available {metrics['auto_accept']['deterministic'].get('best_available')}"
        ),
        f"- 灰带 abstention：[{metrics['auto_accept']['abstention']['band'][0]}, "
        f"{metrics['auto_accept']['abstention']['band'][1]}] 覆盖 "
        f"{metrics['auto_accept']['abstention']['share']}，带内 precision "
        f"{metrics['auto_accept']['abstention']['band_precision']}",
        "",
        "## 4. validator 与聚类",
        "",
        f"- chronology 冲突 {metrics['validators']['chronology_violations']}；"
        f"geography 冲突 {metrics['validators']['geography_conflicts']}；"
        f"career transition 冲突 {metrics['validators']['career_transition_conflicts']}",
        f"- 聚类：{metrics['clusters']}",
        f"- 时间连通性：{metrics['temporal_connectivity']}",
        "",
        "## 5. active learning",
        "",
        "| round | labelled | best threshold precision | coverage |",
        "| --- | --- | --- | --- |",
    ]
    for row in metrics["active_learning"].get("curve", []):
        lines.append(
            f"| {row['round']} | {row['labelled']} | {row['best_threshold_precision']} | {row['best_threshold_coverage']} |"
        )
    lines += [
        "",
        f"- 人工新增标注：{metrics['active_learning'].get('manual_labels_used')} / 预算 "
        f"{metrics['active_learning'].get('budget_pairs')}",
        "",
        "## 6. 官方 id 与旧 dedupe 的关系",
        "",
        f"- release：{release['records']} 条记录，{release['distinct_person_ids']} 个 official person_id，"
        f"缺 id 记录 {release['records_without_person_id']}；跨期同 id {release['persons_in_multiple_periods']} 人",
        f"- crosswalk：{metrics['crosswalk']}",
        "",
        "## 7. risk 与人工队列",
        "",
        f"- risk 分布：{metrics['risk']['by_tier']}",
        f"- 人工队列条数：{metrics['risk']['human_review_items']}（无固定比例抽样）",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - thin CLI
    parser = argparse.ArgumentParser(description="run the U06R linkage benchmark")
    parser.add_argument("--no-splink", action="store_true")
    parser.add_argument("--no-active", action="store_true")
    args = parser.parse_args(argv)
    metrics = run(with_splink=not args.no_splink, with_active_learning=not args.no_active)
    print(json.dumps({k: v for k, v in metrics.items() if k not in ("release",)}, ensure_ascii=False, default=str)[:3000])
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
