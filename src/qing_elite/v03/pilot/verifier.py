"""Automatic evidence verification and risk scoring (U05R).

The verifier is what replaces a fixed-percentage human review: every extracted fact is
checked against the source text and the ontologies, the failures are turned into the risk
components declared in ``config/v03/review_policy.yaml``, and only the HIGH tier reaches a
human. Nothing here decides whether a fact is *historically* true; it decides whether the
machine can stand behind the evidence.

Checks implemented (each maps to a component of the risk score):

===================  =========================  ==========================================
check                component                  failure condition
===================  =========================  ==========================================
exact quote          evidence_span_failure      quote missing, or != text[offsets]
offsets              evidence_span_failure      offsets absent when the document has text
valid relation       rare_relation              relation absent from the ontology
chronology           chronology_violation       kin dates contradict the relation's direction
ontology lookup      office_unknown             office string not in the office vocabulary
duplication          duplication flag           same (ego, relation, name) twice
conflicts            source_conflict            same (ego, relation) with different alters
linkage              linkage_uncertainty        alter not resolved to a person id
OCR confidence       ocr_uncertainty            page-level OCR proxy below threshold
===================  =========================  ==========================================
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from qing_elite.v03.design import load_review_policy, load_relation_ontology, relation_index
from qing_elite.v03.pilot.rules import OFFICE_TERMS
from qing_elite.v03.contracts import expected_risk_tier

RISK_COMPONENT_NAMES = (
    "linkage_uncertainty",
    "ocr_uncertainty",
    "parser_disagreement",
    "source_conflict",
    "rare_relation",
    "office_unknown",
    "chronology_violation",
    "evidence_span_failure",
)

#: Relations appearing fewer times than this in the sample are "rare" and get a small risk.
RARE_RELATION_THRESHOLD = 5

#: Relations a person can only have one of. Someone with two different fathers is a genuine
#: conflict; someone with two different sons is normal, so multi-valued relations must not be
#: flagged (measured: flagging every (ego, relation) pair marked 306 of 768 CBDB rows).
SINGLE_VALUED_RELATIONS = frozenset(
    {"father", "mother", "grandfather", "grandmother", "great_grandfather"}
)


def _cjk_ratio(text: str) -> float:
    if not text:
        return 0.0
    cjk = sum(1 for char in text if "\u3400" <= char <= "\u9fff")
    return cjk / len(text)


def ocr_confidence_proxy(
    page_text: str, *, min_cjk_ratio: float, min_chars: int, max_chars: int = 20_000
) -> float:
    """Page-level OCR uncertainty in [0, 1] from text-level signals only.

    The service does not return per-line scores in the markdown path, so this uses what is
    observable on the *normalised* page text: whether any text came back, whether it is
    mostly Chinese, and whether the amount is plausible for one page (a runaway table dump is
    as suspicious as an empty page). It is a proxy, and it is labelled as one. It must be
    computed after markup stripping, otherwise markup skews the Chinese-character ratio.
    """
    if not page_text:
        return 1.0
    ratio_gap = max(0.0, (min_cjk_ratio - _cjk_ratio(page_text)) / min_cjk_ratio)
    length_gap = max(0.0, (min_chars - len(page_text)) / min_chars)
    overflow_gap = max(0.0, (len(page_text) - max_chars) / max_chars)
    return min(1.0, max(ratio_gap, length_gap, overflow_gap))


def verify_rows(
    rows: list[dict[str, Any]],
    *,
    page_text: dict[int, str] | None = None,
    ocr_flags: dict[int, float] | None = None,
    person_years: dict[str, int] | None = None,
) -> pd.DataFrame:
    """Verify extracted rows and attach one value per risk component."""
    page_text = page_text or {}
    ocr_flags = ocr_flags or {}
    person_years = person_years or {}
    ontology = relation_index(load_relation_ontology())

    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame.assign(**{name: pd.Series(dtype=float) for name in RISK_COMPONENT_NAMES})

    relation_counts = frame["relation_type"].value_counts().to_dict()
    seen: dict[tuple, set] = {}
    duplicates: set[int] = set()
    for index, row in frame.iterrows():
        key = (row.get("focal_person"), row.get("relation_type"))
        names = seen.setdefault(key, set())
        identity = row.get("kin_name")
        if identity in names:
            duplicates.add(index)
        names.add(identity)

    conflict_keys = {
        key
        for key, names in seen.items()
        if key[1] in SINGLE_VALUED_RELATIONS and len({name for name in names if name}) > 1
    }

    components: dict[str, list[float]] = {name: [] for name in RISK_COMPONENT_NAMES}
    for index, row in frame.iterrows():
        text = page_text.get(row.get("page_number"))
        offsets = row.get("offsets")
        quote = row.get("quote")
        has_offsets = isinstance(offsets, (list, tuple)) and len(offsets) == 2
        span_ok = False
        if isinstance(text, str) and has_offsets:
            start, end = int(offsets[0]), int(offsets[1])
            span_ok = text[start:end] == quote
        elif text is None and quote:
            # structured sources carry no text, so there is no span to check
            span_ok = True
        components["evidence_span_failure"].append(0.0 if span_ok else 1.0)

        relation = row.get("relation_type")
        known = relation in ontology
        rare = known and relation_counts.get(relation, 0) < RARE_RELATION_THRESHOLD
        components["rare_relation"].append(1.0 if not known else (0.5 if rare else 0.0))

        office = row.get("office_raw")
        office_known = (not office) or any(term in str(office) for term in OFFICE_TERMS)
        components["office_unknown"].append(0.0 if office_known else 1.0)

        chronology = 0.0
        if person_years:
            ego_year = person_years.get(str(row.get("focal_person")))
            kin_year = person_years.get(str(row.get("kin_name")))
            delta = row.get("generation")
            if ego_year and kin_year and isinstance(delta, (int, float)):
                if delta > 0 and kin_year > ego_year:  # a senior kin born later is impossible
                    chronology = 1.0
                elif delta < 0 and kin_year < ego_year:  # a junior kin born earlier likewise
                    chronology = 1.0
        components["chronology_violation"].append(chronology)

        components["source_conflict"].append(
            1.0 if (row.get("focal_person"), relation) in conflict_keys else 0.0
        )
        components["linkage_uncertainty"].append(
            0.0 if row.get("alter_person_id") else (0.5 if row.get("kin_name") else 1.0)
        )
        components["ocr_uncertainty"].append(float(ocr_flags.get(row.get("page_number"), 0.0)))
        components["parser_disagreement"].append(float(row.get("parser_disagreement", 0.0)))

    for name, values in components.items():
        frame[name] = pd.Series(values, index=frame.index).clip(0.0, 1.0)
    frame["duplicate_of_previous"] = [index in duplicates for index in frame.index]
    return frame


def score(frame: pd.DataFrame, *, policy: dict[str, Any] | None = None) -> pd.DataFrame:
    """Weighted total → tier → route, using the frozen review policy."""
    policy = policy or load_review_policy()
    weights = {name: float(spec["weight"]) for name, spec in policy["components"].items()}
    scored = frame.copy()
    missing = [name for name in weights if name not in scored.columns]
    if missing:
        raise ValueError(f"risk components missing from the frame: {missing}")
    scored["total_score"] = sum(
        scored[name].fillna(0.0) * weight for name, weight in weights.items()
    ).clip(0.0, 1.0)
    scored["risk_tier"] = [expected_risk_tier(value, policy) for value in scored["total_score"]]
    scored["route"] = [policy["tiers"][tier]["route"] for tier in scored["risk_tier"]]
    scored["policy_version"] = policy["policy_version"]
    return scored


def summary(scored: pd.DataFrame) -> dict[str, Any]:
    return {
        "rows": int(len(scored)),
        "by_tier": scored["risk_tier"].value_counts().to_dict() if len(scored) else {},
        "by_route": scored["route"].value_counts().to_dict() if len(scored) else {},
        "human_review_share": (
            float((scored["risk_tier"] == "HIGH").mean()) if len(scored) else 0.0
        ),
        "top_failure_components": (
            {
                name: int((scored[name] > 0).sum())
                for name in RISK_COMPONENT_NAMES
                if (scored[name] > 0).any()
            }
            if len(scored)
            else {}
        ),
    }
