"""Fuzzy candidate recall for entity linkage (P03).

Policy (OMP_PLAN.md P03 linkage order, .omp/RULES.md):

1. explicit ids first — nothing fuzzy is ever promoted automatically;
2. RapidFuzz only *proposes* candidates for later rule/LLM/human verification;
3. every proposal keeps ``match_method`` / ``match_score`` / ``match_features`` /
   ``match_status`` so a reviewer can audit or reject it.

``match_status`` is therefore never ``accepted``: accepted links only come from the
explicit-id paths (CBDB person id, CBDB kin id, P02 rule-based linkage).
"""

from __future__ import annotations

from dataclasses import dataclass
import pandas as pd
from rapidfuzz import fuzz, process

CANDIDATE_STATUS = "candidate_needs_review"
REJECTED_STATUS = "rejected_low_score"
ALREADY_LINKED_STATUS = "already_linked_by_id"


@dataclass(frozen=True, slots=True)
class RecallConfig:
    """Blocking + scoring knobs for candidate recall."""

    limit: int = 3
    score_cutoff: float = 82.0
    block_by_initial: bool = True


def recall_candidates(
    subjects: pd.DataFrame,
    candidates: pd.DataFrame,
    *,
    config: RecallConfig | None = None,
    subject_id_column: str = "cgedq_person_id",
    subject_name_column: str = "name_norm",
) -> pd.DataFrame:
    """Propose CBDB candidates for subjects that have no explicit-id link.

    ``subjects`` needs ``name_norm`` and, when available, ``native_province`` /
    ``degree_effective``; ``candidates`` needs ``cbdb_personid``, ``name_norm`` and
    optionally ``cbdb_province`` / ``degree_cbdb``. Blocking by the first character
    keeps the fuzzy pass linear in practice without dropping plausible matches.
    """
    config = config or RecallConfig()
    blocks: dict[str, list[tuple[int, str]]] = {}
    for person_id, name, province, degree in zip(
        candidates["cbdb_personid"],
        candidates["name_norm"],
        candidates.get("cbdb_province", pd.Series([None] * len(candidates))),
        candidates.get("degree_cbdb", pd.Series([None] * len(candidates))),
    ):
        if not isinstance(name, str) or not name:
            continue
        key = name[0] if config.block_by_initial else "*"
        blocks.setdefault(key, []).append((int(person_id), name, province, degree))

    rows: list[dict[str, object]] = []
    for subject in subjects.itertuples(index=False):
        name = getattr(subject, subject_name_column, None)
        subject_id = getattr(subject, subject_id_column)
        if not isinstance(name, str) or not name:
            continue
        pool = blocks.get(name[0] if config.block_by_initial else "*", [])
        if not pool:
            continue
        # A list (not a dict) keeps candidates that share a name distinct; the
        # returned index maps straight back to the pool.
        pool_names = [candidate_name for _pid, candidate_name, _province, _degree in pool]
        matches = process.extract(
            name,
            pool_names,
            scorer=fuzz.ratio,
            limit=config.limit,
            score_cutoff=config.score_cutoff,
        )
        for matched_name, score, index in matches:
            person_id, label, candidate_province, candidate_degree = pool[index]
            features = _features(subject, name, candidate_province, candidate_degree, score)
            rows.append(
                {
                    "subject_type": "cgedq_person",
                    "subject_id": subject_id,
                    "subject_name": name,
                    "candidate_cbdb_personid": person_id,
                    "candidate_name": matched_name,
                    "candidate_province": candidate_province,
                    "candidate_degree": candidate_degree,
                    "match_method": "rapidfuzz_ratio_blocked_by_initial"
                    if config.block_by_initial
                    else "rapidfuzz_ratio",
                    "match_score": float(score),
                    "match_features": features,
                    "match_status": CANDIDATE_STATUS,
                }
            )
    frame = pd.DataFrame.from_records(rows)
    if not frame.empty:
        frame = frame.sort_values(
            ["subject_id", "match_score"], ascending=[True, False]
        ).reset_index(drop=True)
    return frame


def _features(
    subject: object,
    subject_name: str,
    candidate_province: object,
    candidate_degree: object,
    score: float,
) -> str:
    """Human-readable evidence string; only observed agreements are listed."""
    parts = [f"name_ratio={score:.1f}", f"len={len(subject_name)}"]
    if getattr(subject, "subject_has_surname", True) is False:
        parts.append("subject_has_no_surname")
    province = getattr(subject, "native_province", None)
    if isinstance(province, str) and isinstance(candidate_province, str):
        parts.append(
            "province=" + ("match" if province == candidate_province else "mismatch")
        )
    elif isinstance(province, str):
        parts.append("province=unknown_on_candidate")
    degree = getattr(subject, "degree_effective", None)
    if isinstance(degree, str) and isinstance(candidate_degree, str):
        parts.append("degree=" + ("match" if degree == candidate_degree else "mismatch"))
    elif isinstance(degree, str):
        parts.append("degree=unknown_on_candidate")
    return "; ".join(parts)


def merge_with_explicit_links(
    candidates: pd.DataFrame,
    explicit: pd.DataFrame,
    *,
    explicit_status: str = ALREADY_LINKED_STATUS,
) -> pd.DataFrame:
    """Annotate fuzzy rows that duplicate an explicit-id link.

    Rows for subjects already linked by id are marked so the candidate table never
    looks like it is re-deciding an accepted link.
    """
    if candidates.empty or explicit.empty:
        return candidates
    if "cbdb_personid" not in explicit.columns or "cgedq_person_id" not in explicit.columns:
        return candidates
    accepted = {
        str(subject): person
        for subject, person in zip(explicit["cgedq_person_id"], explicit["cbdb_personid"])
        if pd.notna(person)
    }
    frame = candidates.copy()
    frame["explicit_link_conflicts_with_candidate"] = [
        accepted.get(str(subject)) is not None and int(accepted[str(subject)]) != int(candidate)
        for subject, candidate in zip(frame["subject_id"], frame["candidate_cbdb_personid"])
    ]
    frame.loc[
        frame["explicit_link_conflicts_with_candidate"], "match_status"
    ] = explicit_status
    return frame


def status_summary(candidates: pd.DataFrame, subjects_total: int) -> pd.DataFrame:
    """Recall coverage: how many subjects got at least one candidate, by status."""
    if candidates.empty:
        return pd.DataFrame(
            [{"subjects_with_candidates": 0, "subjects_total": subjects_total, "coverage_pct": 0.0}]
        )
    per_subject = candidates.groupby("subject_id")["match_status"].agg(
        lambda values: sorted(set(values))[0]
    )
    return pd.DataFrame(
        [
            {
                "subjects_with_candidates": int(per_subject.size),
                "subjects_total": int(subjects_total),
                "coverage_pct": round(100 * per_subject.size / max(subjects_total, 1), 2),
                "candidate_rows": int(len(candidates)),
                "note": "RapidFuzz 只召回候选；match_status 永不自动置为 accepted",
            }
        ]
    )


def top_candidates_per_subject(candidates: pd.DataFrame, limit: int = 1) -> pd.DataFrame:
    """Keep the strongest N proposals per subject (for reporting)."""
    if candidates.empty:
        return candidates
    return (
        candidates.sort_values(["subject_id", "match_score"], ascending=[True, False])
        .groupby("subject_id")
        .head(limit)
        .reset_index(drop=True)
    )
