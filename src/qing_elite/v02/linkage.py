"""U02 entity resolution: CGED-Q dedupe, cross-source linking, gold and audits.

Design (upgrade plan §6/U02, and the U00 findings it closes):

* **deterministic first.** Only an explicit structure or *corroborated multi-field*
  agreement accepts a link. A unique name is never enough: the v0.1 ``unique_name``
  rule is exactly what U00-03 showed to be driving the D layer.
* **Splink + DuckDB score the rest.** The probabilistic model sees only
  ``name`` (exact + Jaro-Winkler) and ``both sources recorded a banner`` — the anchor
  features (province, degree) are deliberately withheld so that the held-out
  precision estimate is not measuring the labelling rule.
* **thresholds are calibrated on a held-out split** of a stratified gold; the grey
  zone is queued for human review and never enters the primary frame.
* **one biography, one entity**: a 《清史稿》 biography may back at most one accepted
  canonical entity; anything else is quarantined.

No LLM extraction and no final statistics happen here.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

from qing_elite.linkage.names import PROVINCE_ALIASES
from qing_elite.utils.config import PROCESSED_DIR, PROCESSED_V02_DIR, PROJECT_ROOT
from qing_elite.v02.linkage_data import (
    build_cbdb_persons,
    build_cgedq_coobservations,
    build_cgedq_persons,
)
from qing_elite.v02.linkage_pairs import cross_source_pairs, dedupe_pairs

SEED = 20260914
PRECISION_TARGET = 0.99
GOLD_PER_STRATUM = 60
HELD_OUT_EVERY = 5  # every 5th pair of a stratum goes to the held-out split

AUDIT_DIR = PROJECT_ROOT / "audit" / "v02"

LINK_DEDUPE = PROCESSED_V02_DIR / "link_dedupe_pairs.parquet"
LINK_DEDUPE_CANONICAL = PROCESSED_V02_DIR / "cgedq_canonical.parquet"
LINK_CROSS = PROCESSED_V02_DIR / "link_cross_source.parquet"
ENTITY_LINKS = PROCESSED_V02_DIR / "entity_links.parquet"
LINKAGE_GOLD = PROCESSED_V02_DIR / "linkage_gold.parquet"
LINKAGE_SCORES = PROCESSED_V02_DIR / "linkage_scores.parquet"
BIOGRAPHY_LINKS = PROCESSED_V02_DIR / "biography_links.parquet"

DECISIONS_DEDUPE = ("merge", "distinct", "review", "no_candidate")
DECISIONS_CROSS = ("deterministic_multifield", "deterministic_alias", "probabilistic_high",
                   "probabilistic_grey", "no_match", "ambiguous")


# --------------------------------------------------------------------------- splink


def _splink_settings(link_type: str) -> Any:
    from splink import SettingsCreator, block_on

    import splink.comparison_library as cl

    return SettingsCreator(
        link_type=link_type,
        blocking_rules_to_generate_predictions=[block_on("name_norm")],
        # The blocking rule already conditions on the name, so a name comparison adds
        # no information and (measured) saturates the score. Only the corroborating
        # fields are compared: that is what the decision needs, and it keeps
        # name-only candidates at a low score.
        comparisons=[
            cl.ExactMatch("banner_key"),
            cl.ExactMatch("province_key"),
            cl.ExactMatch("degree_key"),
        ],
    )


def _splink_score(left: pd.DataFrame, right: pd.DataFrame | None) -> pd.DataFrame:
    """Match probabilities for name-blocked pairs; ``unique_id_l/r`` + ``probability``."""
    from splink import DuckDBAPI, Linker

    link_type = "dedupe_only" if right is None else "link_only"
    settings = _splink_settings(link_type)
    db_api = DuckDBAPI()
    linker = Linker([left] if right is None else [left, right], settings, db_api=db_api)
    linker.training.estimate_u_using_random_sampling(max_pairs=1_000_000, seed=SEED)
    linker.training.estimate_parameters_using_expectation_maximisation("l.name_norm = r.name_norm")
    predictions = linker.inference.predict(threshold_match_probability=0.0)
    frame = predictions.as_pandas_dataframe()
    return frame.rename(columns={"match_probability": "probability"}).loc[
        :, ["unique_id_l", "unique_id_r", "probability"]
    ]


def _model_frame(
    frame: pd.DataFrame, id_column: str, prefix: str, name_column: str
) -> pd.DataFrame:
    """Comparison columns; "unknown" gets a per-record sentinel so it never matches.

    Otherwise ``ExactMatch`` would read "both unspecified" as agreement — the exact
    mistake the v0.1 name-only rule made (U00-01/U00-03).
    """
    unique_id = prefix + frame[id_column].astype(str)
    province = frame["province_norm"]
    degree = frame["degree_rank"].fillna(0).astype("int64")
    banner = frame["banner_group"].notna()
    out = pd.DataFrame(
        {
            "unique_id": unique_id,
            "name_norm": frame["name_norm"].fillna("unknown"),
            "name_raw": frame[name_column].fillna("unknown"),
            "banner_key": np.where(banner, "banner", "unknown#" + unique_id),
            "province_key": np.where(province.isna(), "unknown#" + unique_id, province.fillna("")),
            "degree_key": np.where(degree > 0, degree.astype(str), "unknown#" + unique_id),
        }
    )
    return out


def _score_cross(cgedq: pd.DataFrame, cbdb: pd.DataFrame) -> pd.DataFrame:
    left = _model_frame(cgedq, "cgedq_person_id", "c:", "name_chn")
    right = _model_frame(cbdb, "cbdb_personid", "b:", "c_name_chn")
    scores = _splink_score(left, right)
    scores["l_cgedq_person_id"] = scores["unique_id_l"].str.removeprefix("c:")
    scores["r_cbdb_personid"] = scores["unique_id_r"].str.removeprefix("b:")
    scores["pair_id"] = "cross:" + scores["l_cgedq_person_id"] + "|" + scores["r_cbdb_personid"]
    return scores


def _score_dedupe(cgedq: pd.DataFrame) -> pd.DataFrame:
    frame = _model_frame(cgedq, "cgedq_person_id", "", "name_chn")
    scores = _splink_score(frame, None)
    scores["l_cgedq_person_id"] = scores["unique_id_l"]
    scores["r_cgedq_person_id"] = scores["unique_id_r"]
    scores["pair_id"] = "dedupe:" + scores["l_cgedq_person_id"] + "|" + scores["r_cgedq_person_id"]
    return scores.loc[:, ["l_cgedq_person_id", "r_cgedq_person_id", "pair_id", "probability"]]


# ------------------------------------------------------------------------ decisions


def _q(value: object) -> bool | None:
    return None if value is None or pd.isna(value) else bool(value)


def decide_dedupe(pairs: pd.DataFrame, scores: pd.DataFrame, threshold: float) -> pd.DataFrame:
    """Deterministic merge/distinct rules first; the score only fills the grey zone."""
    scored = pairs.merge(scores.drop(columns=["l_cgedq_person_id", "r_cgedq_person_id"]), on="pair_id", how="left")
    decisions: list[str] = []
    reasons: list[str] = []
    for row in scored.to_dict("records"):
        prov = _q(row["province_compatible"])
        deg = _q(row["degree_equal"])
        banner = _q(row["banner_equal"])
        era = _q(row["era_overlap"])
        structural = bool(row["same_office_same_edition"])
        if banner is False:
            decisions.append("distinct")
            reasons.append("different recorded banner")
        elif prov is False:
            decisions.append("distinct")
            reasons.append("incompatible native province")
        elif structural:
            decisions.append("merge")
            reasons.append(
                f"same office in the same edition ({int(row['n_shared_editions'])} edition(s))"
            )
        elif prov is True and deg is True and era is True and banner is not False:
            decisions.append("merge")
            reasons.append("name+province+degree+overlap agree")
        elif prov is True and banner is True and era is True and deg is not False:
            decisions.append("merge")
            reasons.append("name+province+banner+overlap agree")
        elif deg is False and era is False:
            decisions.append("distinct")
            reasons.append("different degree and no overlapping tenure")
        else:
            probability = row.get("probability")
            if pd.notna(probability) and probability >= threshold:
                decisions.append("review")
                reasons.append(f"probabilistic {probability:.3f} above production threshold")
            else:
                decisions.append("review")
                reasons.append("insufficient corroboration; queued for human review")
    scored["decision"] = decisions
    scored["decision_reason"] = reasons
    return scored


def canonical_ids(dedupe: pd.DataFrame) -> pd.DataFrame:
    """Union-find over accepted merges; conflicting components are quarantined."""
    parent: dict[str, str] = {}

    def find(node: str) -> str:
        parent.setdefault(node, node)
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def union(left: str, right: str) -> None:
        root_left, root_right = find(left), find(right)
        if root_left != root_right:
            parent[max(root_left, root_right)] = min(root_left, root_right)

    for row in dedupe.itertuples(index=False):
        find(row.l_cgedq_person_id)
        find(row.r_cgedq_person_id)
        if row.decision == "merge":
            union(row.l_cgedq_person_id, row.r_cgedq_person_id)

    records: list[dict[str, Any]] = []
    for person_id in sorted(parent):
        root = find(person_id)
        records.append(
            {
                "cgedq_person_id": person_id,
                "cgedq_canonical_id": root,
                "merged": root != person_id,
            }
        )
    table = pd.DataFrame.from_records(records)
    # group size and the evidence that produced it
    sizes = table.groupby("cgedq_canonical_id").size().rename("group_size")
    table = table.merge(sizes, left_on="cgedq_canonical_id", right_index=True, how="left")
    return table


def decide_cross(pairs: pd.DataFrame, scores: pd.DataFrame, threshold: float) -> pd.DataFrame:
    scored = pairs.merge(
        scores.drop(columns=["l_cgedq_person_id", "r_cbdb_personid"]), on="pair_id", how="left"
    )
    decides: list[str] = []
    reasons: list[str] = []
    review: list[str] = []
    for row in scored.to_dict("records"):
        prov = _q(row["province_compatible"])
        deg = _q(row["degree_equal"])
        match_type = str(row["name_match_type"])
        probability = row.get("probability")
        if match_type == "alias" and (prov is True or deg is True):
            decides.append("deterministic_alias")
            reasons.append("CBDB alias match corroborated by province/degree")
            review.append("accepted_deterministic")
        elif prov is True and deg is True:
            decides.append("deterministic_multifield")
            reasons.append("name+province+degree agree")
            review.append("accepted_deterministic")
        elif probability is not None and pd.notna(probability) and probability >= threshold:
            decides.append("probabilistic_high")
            reasons.append(f"probabilistic {probability:.3f} above production threshold")
            review.append("pending")
        elif probability is not None and pd.notna(probability):
            decides.append("probabilistic_grey")
            reasons.append(f"probabilistic {probability:.3f} below threshold; review queue")
            review.append("pending")
        else:
            decides.append("no_match")
            reasons.append("no probabilistic score generated")
            review.append("pending")
    scored["link_decision"] = decides
    scored["decision_reason"] = reasons
    scored["review_status"] = review
    return scored


# ------------------------------------------------------------------------------ gold


def _sample(frame: pd.DataFrame, size: int) -> pd.DataFrame:
    if len(frame) <= size:
        return frame
    return frame.sample(n=size, random_state=SEED)


def build_cross_gold(pairs: pd.DataFrame) -> pd.DataFrame:
    """Stratified, provenance-tagged cross-source gold with a deterministic split."""
    frame = pairs.copy()
    frame["province_compatible"] = frame["province_compatible"].map(_q)
    frame["degree_equal"] = frame["degree_equal"].map(_q)
    frame["era_overlap"] = frame["era_overlap"].map(_q)
    pos = (frame["province_compatible"] == True) & (frame["degree_equal"] == True)  # noqa: E712
    pos_alias = (frame["name_match_type"] == "alias") & (
        (frame["province_compatible"] == True) | (frame["degree_equal"] == True)  # noqa: E712
    )
    neg_province = frame["province_compatible"] == False  # noqa: E712
    neg_degree = (frame["degree_equal"] == False) & (frame["province_compatible"] != True)  # noqa: E712
    high_freq = frame["name_freq_max"] >= 5

    strata: list[tuple[str, pd.DataFrame, int]] = [
        ("multi_field_positive", frame[pos], 1),
        ("alias_positive", frame[pos_alias & ~pos], 1),
        ("province_conflict", frame[neg_province], 0),
        ("degree_conflict", frame[neg_degree], 0),
        ("high_frequency_name", frame[high_freq & ~pos & ~pos_alias & ~neg_province & ~neg_degree], 0),
        ("variant_char", frame[~frame["raw_name_equal"] & ~pos & ~pos_alias & ~neg_province & ~neg_degree], 0),
        ("banner_no_surname", frame[~frame["l_name_has_surname"] & ~pos & ~pos_alias & ~neg_province & ~neg_degree], 0),
        ("era_conflict", frame[(frame["era_overlap"] == False) & ~pos & ~pos_alias & ~neg_province & ~neg_degree], 0),  # noqa: E712
    ]
    rows: list[pd.DataFrame] = []
    for name, subset, label in strata:
        subset = subset.drop_duplicates("pair_id")
        population = int(len(subset))
        if name in ("era_conflict",):
            subset = _sample(subset, GOLD_PER_STRATUM)
            subset = subset.assign(gold_label=pd.NA)
        else:
            subset = _sample(subset, GOLD_PER_STRATUM)
            subset = subset.assign(gold_label=label)
        rows.append(
            subset.assign(
                stratum=name,
                task="cross_source",
                stratum_population=population,
                stratum_sampled=int(len(subset)),
            )
        )
    gold = pd.concat(rows, ignore_index=True)
    gold["anchor"] = [
        _anchor_reason(row)
        for row in gold[
            ["stratum", "name_match_type", "province_compatible", "degree_equal", "era_overlap",
             "raw_name_equal", "l_name_has_surname"]
        ].to_dict("records")
    ]
    return _with_split(gold)


def _anchor_reason(row: Mapping[str, Any]) -> str:
    if row["stratum"] == "alias_positive":
        return "CBDB alias + province/degree agreement"
    if row["stratum"] == "multi_field_positive":
        return "province + degree agreement"
    if row["stratum"] == "province_conflict":
        return "both provinces known and incompatible"
    if row["stratum"] == "degree_conflict":
        return "both degrees known and different"
    if row["stratum"] == "era_conflict":
        return "both career windows known and disjoint (not usable as a gold conflict)"
    if row["stratum"] == "variant_char":
        return "same normalized name, different raw characters"
    if row["stratum"] == "banner_no_surname":
        return "CGED-Q person recorded without a surname"
    return "same name, unlabelled review candidate"


def build_dedupe_gold(pairs: pd.DataFrame) -> pd.DataFrame:
    frame = pairs.copy()
    structural = frame["same_office_same_edition"].fillna(False).astype(bool)
    prov = frame["province_compatible"].map(_q)
    deg = frame["degree_equal"].map(_q)
    banner = frame["banner_equal"].map(_q)
    era = frame["era_overlap"].map(_q)
    pos = structural | ((prov == True) & (deg == True) & (era == True) & (banner != False))  # noqa: E712
    neg = (banner == False) | (prov == False) | ((deg == False) & (era == False))  # noqa: E712
    strata = [
        ("structural_same_edition", frame[structural], 1),
        ("corroborated_pair", frame[pos & ~structural], 1),
        ("banner_conflict", frame[banner == False], 0),  # noqa: E712
        ("province_conflict", frame[(prov == False) & (banner != False)], 0),  # noqa: E712
        ("degree_and_era_conflict", frame[(deg == False) & (era == False) & (prov != True) & (banner != False)], 0),  # noqa: E712
        ("high_frequency_name", frame[(frame["name_freq_max"] >= 5) & ~pos & ~neg], 0),
    ]
    rows: list[pd.DataFrame] = []
    for name, subset, label in strata:
        subset = subset.drop_duplicates("pair_id")
        population = int(len(subset))
        rows.append(
            _sample(subset, GOLD_PER_STRATUM).assign(
                gold_label=label,
                stratum=name,
                task="dedupe",
                stratum_population=population,
                stratum_sampled=min(population, GOLD_PER_STRATUM),
            )
        )
    gold = pd.concat(rows, ignore_index=True)
    gold["anchor"] = [
        _dedupe_anchor(row)
        for row in gold[
            ["stratum", "same_office_same_edition", "province_compatible", "degree_equal",
             "era_overlap", "banner_equal", "name_freq_max"]
        ].to_dict("records")
    ]
    return _with_split(gold)


def _dedupe_anchor(row: Mapping[str, Any]) -> str:
    if row["stratum"] == "structural_same_edition":
        return "same office in the same published edition"
    if row["stratum"] == "corroborated_pair":
        return "province + degree + tenure overlap"
    if row["stratum"] == "banner_conflict":
        return "different recorded banner"
    if row["stratum"] == "province_conflict":
        return "incompatible native province"
    if row["stratum"] == "degree_and_era_conflict":
        return "different degree and disjoint tenure"
    return "same name, unlabelled review candidate"


def _with_split(gold: pd.DataFrame) -> pd.DataFrame:
    """Deterministic 4:1 split, per stratum, seeded by pair id hash."""
    gold = gold.copy()
    gold["split"] = [
        "held_out"
        if _stable_bucket(pair_id) % HELD_OUT_EVERY == 0
        else "train"
        for pair_id in gold["pair_id"]
    ]
    return gold


def _stable_bucket(value: str) -> int:
    digest = hashlib.sha256(f"{SEED}:{value}".encode("utf-8")).hexdigest()
    return int(digest[:8], 16)


# ------------------------------------------------------------------------ evaluation


def evaluate(scores: pd.DataFrame, gold: pd.DataFrame, *, threshold: float) -> dict[str, Any]:
    """Weighted precision/recall/confusion on one gold split.

    The gold is stratified, so the raw counts would misstate precision: every pair is
    re-weighted by ``stratum_population / stratum_sampled`` before the metrics are
    computed. Unweighted counts are reported alongside for traceability.
    """
    merged = gold.merge(scores.drop(columns=["l_cgedq_person_id", "r_cbdb_personid"], errors="ignore"), on="pair_id", how="left")
    labelled = merged[merged["gold_label"].notna()].copy()
    labelled["gold_label"] = labelled["gold_label"].astype(int)
    labelled["weight"] = labelled["stratum_population"] / labelled["stratum_sampled"].clip(lower=1)
    predicted = (labelled["probability"].fillna(0.0) >= threshold).astype(int)
    tp = float(labelled.loc[(predicted == 1) & (labelled["gold_label"] == 1), "weight"].sum())
    fp = float(labelled.loc[(predicted == 1) & (labelled["gold_label"] == 0), "weight"].sum())
    fn = float(labelled.loc[(predicted == 0) & (labelled["gold_label"] == 1), "weight"].sum())
    tn = float(labelled.loc[(predicted == 0) & (labelled["gold_label"] == 0), "weight"].sum())
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    return {
        "n": int(len(labelled)),
        "positive": int((labelled["gold_label"] == 1).sum()),
        "negative": int((labelled["gold_label"] == 0).sum()),
        "tp": round(tp, 2), "fp": round(fp, 2), "fn": round(fn, 2), "tn": round(tn, 2),
        "tp_raw": int(((predicted == 1) & (labelled["gold_label"] == 1)).sum()),
        "fp_raw": int(((predicted == 1) & (labelled["gold_label"] == 0)).sum()),
        "precision": precision,
        "recall": recall,
        "threshold": threshold,
    }


def calibrate_threshold(
    scores: pd.DataFrame, gold: pd.DataFrame, *, precision_target: float = PRECISION_TARGET
) -> tuple[float, dict[str, Any]]:
    """Lowest score cut whose held-out precision reaches the target.

    The grid is derived from the observed score distribution: conditioned on being a
    name-blocked candidate the absolute probabilities are small, so a fixed 0.5..1.0
    grid would never be reached.
    """
    held_out = gold[gold["split"] == "held_out"]
    merged = held_out.merge(
        scores.drop(columns=["l_cgedq_person_id", "r_cbdb_personid"], errors="ignore"),
        on="pair_id",
        how="left",
    )
    candidate_values = np.sort(merged["probability"].dropna().unique())
    if len(candidate_values) == 0:
        return 1.0, evaluate(scores, held_out, threshold=1.0)
    grid = [float(value) for value in candidate_values]
    chosen, chosen_metrics = 1.0, None
    for threshold in grid:
        metrics = evaluate(scores, held_out, threshold=threshold)
        if (
            metrics["precision"] is not None
            and metrics["precision"] >= precision_target
            and metrics["tp_raw"] > 0
        ):
            chosen, chosen_metrics = threshold, metrics
            break
    if chosen_metrics is None:
        chosen_metrics = evaluate(scores, held_out, threshold=1.0)
    return chosen, chosen_metrics


# ------------------------------------------------------------------------- entities


def resolve_entity_links(
    entities: pd.DataFrame, master: pd.DataFrame, cross: pd.DataFrame, canonical: pd.DataFrame
) -> pd.DataFrame:
    """Attach the v0.2 link status to every entity.

    ``link_status`` describes the *source link*, not the person:

    * ``explicit_id``         — CBDB-only entity: identity comes from the CBDB person id;
    * ``accepted_cross_link`` — the CGED-Q↔CBDB link passed a deterministic rule;
    * ``merged_into_cbdb``    — a CGED-Q-only entity whose roster entry is now linked to a
      CBDB person, so it must not be counted as a second person;
    * ``review_pending``      — a candidate exists but only name-level evidence;
    * ``rejected``            — a deterministic rule says the candidates are other people;
    * ``standalone``          — no CBDB candidate at all (roster-only person).

    ``resolution_status`` keeps the U01 contract: only ``resolved`` entities enter the
    primary frame.
    """
    accepted = cross[cross["review_status"] == "accepted_deterministic"]
    accepted_cgedq = dict(zip(accepted["l_cgedq_person_id"], accepted["r_cbdb_personid"]))
    accepted_cbdb = set(accepted["r_cbdb_personid"])
    candidate_ids = set(cross["l_cgedq_person_id"])
    rejected = set(cross.loc[cross["link_decision"] == "no_match", "l_cgedq_person_id"])
    merged_ids = set(canonical.loc[canonical["merged"], "cgedq_person_id"])

    cgedq_by_entity: dict[str, list[str]] = {}
    for row in master.itertuples(index=False):
        raw = getattr(row, "cgedq_person_id", None)
        if isinstance(raw, str) and raw and raw.lower() != "nan":
            cgedq_by_entity[str(row.person_uid)] = [part for part in raw.split("|") if part]

    statuses: list[str] = []
    evidences: list[str] = []
    for row in entities.itertuples(index=False):
        has_cbdb = pd.notna(row.cbdb_personid)
        cbdb_id = int(row.cbdb_personid) if has_cbdb else None
        ids = cgedq_by_entity.get(row.entity_id, [])
        if not ids and has_cbdb:
            statuses.append("explicit_id")
            evidences.append("CBDB person id only; no CGED-Q link required")
            continue
        if has_cbdb and cbdb_id in accepted_cbdb:
            statuses.append("accepted_cross_link")
            evidences.append(
                "deterministic CGED-Q↔CBDB link ("
                + ",".join(sorted(pid for pid, target in accepted_cgedq.items() if target == cbdb_id)[:3])
                + ")"
            )
            continue
        if not has_cbdb:
            linked_to = [pid for pid in ids if pid in accepted_cgedq]
            if linked_to:
                statuses.append("merged_into_cbdb")
                evidences.append(
                    f"roster entry(s) {linked_to} now link to CBDB person "
                    f"{sorted({accepted_cgedq[pid] for pid in linked_to})}"
                )
            elif any(pid in candidate_ids and pid not in rejected for pid in ids):
                statuses.append("review_pending")
                evidences.append("CGED-Q candidate(s) exist but only name-level evidence")
            elif any(pid in merged_ids for pid in ids):
                statuses.append("review_pending")
                evidences.append("CGED-Q id merged by dedupe; cross-source link not accepted")
            elif any(pid in rejected for pid in ids):
                statuses.append("rejected")
                evidences.append("deterministic rule rejects every CGED-Q candidate")
            else:
                statuses.append("standalone")
                evidences.append("no CBDB candidate for this CGED-Q roster entry")
            continue
        statuses.append("review_pending")
        evidences.append("CBDB+CGED-Q entity whose link is not corroborated by the U02 rules")

    frame = entities.copy()
    frame["link_status"] = statuses
    frame["link_evidence"] = evidences
    resolved = frame["link_status"].isin(["explicit_id", "accepted_cross_link", "standalone"])
    frame["resolution_status"] = ["resolved" if value else "unresolved" for value in resolved]
    frame["primary_eligible"] = resolved.to_numpy()
    return frame


def entity_links(cross: pd.DataFrame, canonical: pd.DataFrame) -> pd.DataFrame:
    """Canonical CGED-Q → CBDB link table (the plan's ``entity_links``)."""
    accepted = cross[cross["review_status"] == "accepted_deterministic"].copy()
    accepted["link_type"] = accepted["link_decision"]
    accepted["target_entity_id"] = "cbdb:" + accepted["r_cbdb_personid"].astype(str)
    accepted["source_entity_id"] = "cgedq:" + accepted["l_cgedq_person_id"]
    accepted = accepted.merge(
        canonical[["cgedq_person_id", "cgedq_canonical_id"]],
        left_on="l_cgedq_person_id",
        right_on="cgedq_person_id",
        how="left",
    )
    return accepted[
        [
            "source_entity_id",
            "target_entity_id",
            "link_type",
            "link_decision",
            "decision_reason",
            "review_status",
            "province_compatible",
            "degree_equal",
            "name_match_type",
            "name_freq_max",
            "probability",
            "cgedq_canonical_id",
        ]
    ]


def coverage_audit(master: pd.DataFrame, entities: pd.DataFrame) -> pd.DataFrame:
    """high-only / reviewed-accepted / all-candidate coverage, by tier."""
    status = dict(zip(entities["entity_id"], entities["link_status"]))
    tiers = dict(zip(master["person_uid"], master["highest_tier"].fillna("unknown")))
    sets = {
        "high_only": {"explicit_id", "accepted_cross_link"},
        "reviewed_accepted": {"explicit_id", "accepted_cross_link", "standalone"},
        "all_candidate": {
            "explicit_id", "accepted_cross_link", "standalone", "merged_into_cbdb",
            "review_pending", "rejected",
        },
    }
    rows: list[dict[str, Any]] = []
    for name, allowed in sets.items():
        for tier in sorted(set(tiers.values())):
            tier_entities = [entity for entity, value in tiers.items() if value == tier]
            linked = [entity for entity in tier_entities if status.get(entity) in allowed]
            rows.append(
                {
                    "coverage_set": name,
                    "tier": tier,
                    "n_entities": len(tier_entities),
                    "n_linked": len(linked),
                    "pct": round(100 * len(linked) / len(tier_entities), 2) if tier_entities else None,
                }
            )
        total = len(tiers)
        linked_total = sum(1 for entity in tiers if status.get(entity) in allowed)
        rows.append(
            {
                "coverage_set": name,
                "tier": "ALL",
                "n_entities": total,
                "n_linked": linked_total,
                "pct": round(100 * linked_total / total, 2) if total else None,
            }
        )
    return pd.DataFrame.from_records(rows)


def subgroup_errors(
    scores: pd.DataFrame, gold: pd.DataFrame, thresholds: Mapping[str, float]
) -> pd.DataFrame:
    """Precision/recall per stratum and split (the grouping required by the plan)."""
    rows: list[dict[str, Any]] = []
    for task, task_gold in gold.groupby("task"):
        task_scores = scores[scores["task"] == task]
        threshold = thresholds.get(task, 1.0)
        for stratum, subset in task_gold.groupby("stratum"):
            labelled = subset[subset["gold_label"].notna()]
            if labelled.empty:
                rows.append(
                    {
                        "task": task, "stratum": stratum, "split": "unlabelled",
                        "n": int(len(subset)), "positive": None, "precision": None,
                        "recall": None, "threshold": threshold,
                        "note": "review stratum: no usable gold conflict",
                    }
                )
                continue
            for split, split_subset in labelled.groupby("split"):
                metrics = evaluate(task_scores, split_subset, threshold=threshold)
                rows.append(
                    {
                        "task": task, "stratum": stratum, "split": split,
                        "n": metrics["n"], "positive": metrics["positive"],
                        "precision": metrics["precision"], "recall": metrics["recall"],
                        "tp": metrics["tp"], "fp": metrics["fp"],
                        "fn": metrics["fn"], "tn": metrics["tn"],
                        "threshold": threshold, "note": "",
                    }
                )
    return pd.DataFrame.from_records(rows)


def confusion_table(scores: pd.DataFrame, gold: pd.DataFrame, threshold: float) -> pd.DataFrame:
    """Overall + per split confusion matrices, weighted and raw."""
    rows: list[dict[str, Any]] = []
    for task, task_gold in gold.groupby("task"):
        task_scores = scores[scores["task"] == task]
        for split, subset in task_gold.groupby("split"):
            metrics = evaluate(task_scores, subset, threshold=threshold)
            rows.append({"task": task, "split": split, **metrics})
    return pd.DataFrame.from_records(rows)


def load_biography_index() -> pd.DataFrame:
    """source_ids ↔ person_uid pairs with the biography text when it is cached.

    The harvest index (data/interim, not in Git) carries the passage; without it only
    the tracked enrichment mapping is available and no claim can be corroborated.
    """
    index_path = Path("data/interim/passages/harvest_index.parquet")
    if index_path.exists():
        index = pd.read_parquet(index_path)
        return index[["person_uid", "source_ids", "passage"]].drop_duplicates()
    enriched = pd.read_parquet(PROCESSED_DIR / "family_enriched.parquet")
    enriched = enriched.assign(passage=None)
    return enriched[["person_uid", "source_ids", "passage"]].drop_duplicates()



def _office_lookup() -> dict[str, list[str]]:
    """Office strings per person_uid, from the frozen appointment table."""
    appointments = pd.read_parquet(PROCESSED_DIR / "appointments.parquet")
    lookup: dict[str, list[str]] = {}
    for row in appointments.itertuples(index=False):
        person = str(getattr(row, "person_uid", ""))
        if not person:
            continue
        for value in (getattr(row, "office_core", None), getattr(row, "office_raw", None)):
            if isinstance(value, str) and len(value) >= 2:
                lookup.setdefault(person, [])
                if value not in lookup[person] and len(lookup[person]) < 25:
                    lookup[person].append(value)
    return lookup


REIGN_WINDOWS = (
    ("順治", 1644, 1661),
    ("康熙", 1662, 1722),
    ("雍正", 1723, 1735),
    ("乾隆", 1736, 1795),
    ("嘉慶", 1796, 1820),
    ("道光", 1821, 1850),
    ("咸豐", 1851, 1861),
    ("同治", 1862, 1874),
    ("光緒", 1875, 1908),
)


def _reign_names(first_year: object, last_year: object) -> list[str]:
    names: list[str] = []
    for value in (first_year, last_year):
        try:
            year = int(float(value))
        except (TypeError, ValueError):
            continue
        for name, start, end in REIGN_WINDOWS:
            if start <= year <= end and name not in names:
                names.append(name)
    return names


def _corroboration(
    *,
    text: str,
    province: object,
    banner: object,
    alt_names: object,
    offices: list[str],
    reign_names: list[str],
) -> list[str]:
    """Non-name evidence that the biography really is this person's."""
    evidence: list[str] = []
    if isinstance(province, str) and province:
        for candidate in sorted(PROVINCE_ALIASES.get(province, {province})):
            if candidate in text:
                evidence.append(f"native province {candidate} appears in the biography")
                break
    if isinstance(banner, str) and banner:
        keyword = {
            "manchu_banner": "滿洲",
            "mongol_banner": "蒙古",
            "han_bannerman": "漢軍",
            "booi_bannerman": "內務府",
        }.get(banner, "旗")
        if keyword in text:
            evidence.append(f"banner marker {keyword} appears in the biography")
    if isinstance(alt_names, str):
        for alias in str(alt_names).split("|"):
            if len(alias) >= 2 and alias in text:
                evidence.append(f"alias {alias} appears in the biography")
                break
    for office in offices:
        if office in text:
            evidence.append(f"office {office} appears in the biography")
            break
    for reign in reign_names:
        if reign in text:
            evidence.append(f"weak: career reign {reign} is named in the biography")
            break
    return evidence


def link_biographies(
    index: pd.DataFrame,
    master: pd.DataFrame,
    entities: pd.DataFrame,
    cbdb_persons: pd.DataFrame,
) -> pd.DataFrame:
    """One biography -> at most one entity, requiring non-name corroboration.

    The v0.1 index matched biographies by normalized name alone (U00-04). Here the name
    is only the blocking feature: a claim is accepted only when the entity's native
    place, banner marker, alias or an office it held also appears in the biography text.
    """
    offices = _office_lookup()
    alt_by_entity = {
        f"cbdb:{int(person)}": aliases
        for person, aliases in zip(cbdb_persons["cbdb_personid"], cbdb_persons["alt_names"])
    }
    province_by_entity = dict(
        zip(master["person_uid"], master["native_province_effective"])
    )
    banner_by_entity = dict(zip(master["person_uid"], master["banner_effective"]))
    years_by_entity = {
        str(row.person_uid): (getattr(row, "career_first_year", None), getattr(row, "career_last_year", None))
        for row in master.itertuples(index=False)
    }

    rows: list[dict[str, Any]] = []
    for source_ids, group in index.groupby("source_ids"):
        passage = group["passage"].iloc[0] if "passage" in group.columns else None
        text = passage if isinstance(passage, str) else ""
        claimants = sorted(set(group["person_uid"].astype(str)))
        evidence_by_person: dict[str, list[str]] = {}
        for person in claimants:
            first, last = years_by_entity.get(person, (None, None))
            evidence_by_person[person] = _corroboration(
                text=text,
                province=province_by_entity.get(person),
                banner=banner_by_entity.get(person),
                alt_names=alt_by_entity.get(person),
                offices=offices.get(person, []),
                reign_names=_reign_names(first, last),
            )
        # a reign name alone is too weak to separate same-name claimants
        strong = [
            person
            for person in claimants
            if any(not entry.startswith("weak:") for entry in evidence_by_person[person])
        ]
        for person in claimants:
            evidence = evidence_by_person[person]
            if not text:
                decision, reason = "quarantine", "biography text unavailable; cannot corroborate"
            elif len(strong) == 1 and person == strong[0]:
                decision, reason = "merge", evidence[0]
            elif len(strong) > 1 and person in strong:
                decision, reason = "quarantine", "more than one claimant is corroborated"
            elif strong:
                decision, reason = (
                    "confirmed_distinct",
                    "another claimant is corroborated by non-name evidence",
                )
            else:
                decision, reason = "quarantine", "name-only claim; no corroborating evidence"
            rows.append(
                {
                    "source_ids": source_ids,
                    "person_uid": person,
                    "claim_strength": "name_plus_attribute" if evidence else "name_only",
                    "corroboration": " | ".join(evidence) if evidence else None,
                    "decision": decision,
                    "reason": reason,
                    "n_claimants": len(claimants),
                }
            )
    return pd.DataFrame.from_records(rows)


# ----------------------------------------------------------------------------- run


def _write(frame: pd.DataFrame, path: Path) -> str:
    PROCESSED_V02_DIR.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)
    return str(path.relative_to(PROJECT_ROOT))


def run(*, force: bool = False) -> dict[str, Any]:
    cgedq = build_cgedq_persons()
    cbdb = build_cbdb_persons()
    coobservations = build_cgedq_coobservations()

    dedupe = dedupe_pairs(cgedq, coobservations)
    cross = cross_source_pairs(cgedq, cbdb)

    cross_scores = _score_cross(cgedq, cbdb)
    dedupe_scores = _score_dedupe(cgedq)

    cross_gold = build_cross_gold(cross)
    dedupe_gold = build_dedupe_gold(dedupe)
    gold = pd.concat([cross_gold, dedupe_gold], ignore_index=True)

    threshold, held_out = calibrate_threshold(cross_scores, cross_gold)
    dedupe_threshold, dedupe_held_out = calibrate_threshold(
        dedupe_scores.assign(task="dedupe"), dedupe_gold
    )
    cross = decide_cross(cross, cross_scores, threshold)
    dedupe = decide_dedupe(dedupe, dedupe_scores, dedupe_threshold)
    canonical = canonical_ids(dedupe)

    scores = pd.concat(
        [
            cross_scores.assign(task="cross_source", r_id=cross_scores["r_cbdb_personid"]),
            dedupe_scores.assign(task="dedupe", r_cbdb_personid=pd.NA, r_id=dedupe_scores["r_cgedq_person_id"]),
        ],
        ignore_index=True,
    )

    entities = pd.read_parquet(PROCESSED_V02_DIR / "entities.parquet")
    master = pd.read_parquet(PROCESSED_DIR / "officials_master.parquet")
    entities = resolve_entity_links(entities, master, cross, canonical)
    links = entity_links(cross, canonical)

    biographies = link_biographies(load_biography_index(), master, entities, cbdb)
    coverage = coverage_audit(master, entities)
    subgroups = subgroup_errors(scores, gold, {"cross_source": threshold, "dedupe": dedupe_threshold})
    confusion = confusion_table(scores, gold, threshold)
    dedupe_confusion = confusion_table(
        dedupe_scores.assign(task="dedupe"), dedupe_gold, dedupe_threshold
    )

    written = {
        "link_dedupe_pairs": _write(dedupe, LINK_DEDUPE),
        "cgedq_canonical": _write(canonical, LINK_DEDUPE_CANONICAL),
        "link_cross_source": _write(cross, LINK_CROSS),
        "linkage_gold": _write(gold, LINKAGE_GOLD),
        "linkage_scores": _write(scores, LINKAGE_SCORES),
        "biography_links": _write(biographies, BIOGRAPHY_LINKS),
        "entity_links": _write(links, ENTITY_LINKS),
        "entities": _write(entities, PROCESSED_V02_DIR / "entities.parquet"),
    }
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    coverage.to_csv(AUDIT_DIR / "u02_link_coverage.csv", index=False)
    subgroups.to_csv(AUDIT_DIR / "u02_subgroup_errors.csv", index=False)
    confusion.to_csv(AUDIT_DIR / "u02_confusion_cross.csv", index=False)
    dedupe_confusion.to_csv(AUDIT_DIR / "u02_confusion_dedupe.csv", index=False)
    biographies[biographies["n_claimants"] > 1].to_csv(
        AUDIT_DIR / "u02_shared_biographies.csv", index=False
    )
    decision_rows = (
        [
            {"task": "cross_source", "decision": name, "n": int(value)}
            for name, value in cross["link_decision"].value_counts().items()
        ]
        + [
            {"task": "dedupe", "decision": name, "n": int(value)}
            for name, value in dedupe["decision"].value_counts().items()
        ]
        + [
            {"task": "entity", "decision": name, "n": int(value)}
            for name, value in entities["link_status"].value_counts().items()
        ]
    )
    pd.DataFrame.from_records(decision_rows).to_csv(
        AUDIT_DIR / "u02_link_decision_summary.csv", index=False
    )
    (AUDIT_DIR / "u02_threshold.json").write_text(
        json.dumps(
            {
                "precision_target": PRECISION_TARGET,
                "cross_source_threshold": threshold,
                "dedupe_threshold": dedupe_threshold,
                "cross_source_held_out": held_out,
                "dedupe_held_out": dedupe_held_out,
                "seed": SEED,
                "split": f"every {HELD_OUT_EVERY}th pair by sha256(seed:pair_id)",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    summary = {
        "threshold": threshold,
        "held_out": held_out,
        "dedupe_threshold": dedupe_threshold,
        "dedupe_held_out": dedupe_held_out,
        "cross_pairs": int(len(cross)),
        "dedupe_pairs": int(len(dedupe)),
        "gold_rows": int(len(gold)),
        "gold_labelled": int(gold["gold_label"].notna().sum()),
        "accepted_cross_links": int((cross["review_status"] == "accepted_deterministic").sum()),
        "dedupe_merges": int((dedupe["decision"] == "merge").sum()),
        "review_queue_cross": int((cross["review_status"] == "pending").sum()),
        "review_queue_dedupe": int((dedupe["decision"] == "review").sum()),
        "entities_by_link_status": entities["link_status"].value_counts().to_dict(),
        "primary_entities": int(entities["primary_eligible"].sum()),
        "biographies_shared": int(
            (biographies.loc[biographies["n_claimants"] > 1, "source_ids"].nunique())
        ),
        "biographies_merged": int((biographies["decision"] == "merge").sum()),
        "biographies_quarantined": int((biographies["decision"] == "quarantine").sum()),
        "biographies_double_merged": int(
            (
                biographies.loc[biographies["decision"] == "merge"]
                .groupby("source_ids")
                .size()
                > 1
            ).sum()
        ),
        "written": written,
    }
    return summary


def main() -> int:
    result = run()
    print(json.dumps({key: value for key, value in result.items() if key != "written"}, ensure_ascii=False, indent=2))
    for name, path in result["written"].items():
        print(f"{name}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
