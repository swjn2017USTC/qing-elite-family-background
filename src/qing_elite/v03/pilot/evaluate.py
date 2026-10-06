"""Field-level evaluation against a small gold set (U05R).

The gold set is deliberately small and hard: two scanned roster pages read visually from the
page image (not from the OCR text, which would make the comparison circular), covering
direct-line kin, collateral kin and junior kin, with and without degrees/offices.

Metrics are field-level, and *abstention is reported next to them* — a parser that guesses
instead of abstaining is not better, it is just harder to audit. Every mismatch is sorted
into an error class by deterministic rules so the taxonomy can be aggregated across sources.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from qing_elite.utils.config import PROJECT_ROOT

GOLD_PATH = PROJECT_ROOT / "reports" / "upgrade_v03" / "pilot" / "gold_ocr.jsonl"

ERROR_CLASSES = (
    "name_error",
    "relation_error",
    "missed_entry",
    "ocr_induced_relation_loss",
    "spurious_row",
    "degree_conflict",
    "office_conflict",
    "office_priority_confusion",
)

#: Offices that CBDB/同官录 treat as a promotion of the previous post; a mismatch between
#: them is recorded as an ordering difference, not as a wrong extraction.
PROMOTION_PAIRS: tuple[tuple[str, str], ...] = (
    ("縣丞", "知縣"),
    ("知縣", "同知"),
    ("同知", "知府"),
    ("教諭", "教授"),
)


def load_gold(path: Path | None = None) -> pd.DataFrame:
    path = path or GOLD_PATH
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return pd.DataFrame(rows)


def _present(value: Any) -> bool:
    """True only for a real value: NaN is truthy in Python and must not count as a field."""
    if value is None:
        return False
    if isinstance(value, float) and value != value:  # NaN
        return False
    text = str(value).strip()
    return bool(text) and text.lower() not in ("nan", "none", "null")


def _key(row: pd.Series | dict) -> tuple:
    get = row.get if isinstance(row, dict) else row.get
    return (int(get("page_number")), str(get("relation_type")), str(get("kin_name")))


def _page_key(row) -> tuple:
    get = row.get
    return (int(get("page_number")), str(get("kin_name")))


def classify_mismatch(predicted: dict | None, gold: dict | None, page_text: str) -> dict[str, Any]:
    """Name the error class for one unmatched item (deterministic, auditable)."""
    if predicted is not None and gold is None:
        same_page_and_name = False
        return {
            "kind": "predicted_not_in_gold",
            "error_class": "spurious_row" if not same_page_and_name else "relation_error",
            "detail": {"relation_type": predicted.get("relation_type"), "kin_name": predicted.get("kin_name")},
        }
    if gold is not None and predicted is None:
        term_present = str(gold.get("relation_type")) in page_text or True  # filled below
        return {
            "kind": "gold_not_predicted",
            "error_class": "missed_entry",
            "detail": {"relation_type": gold.get("relation_type"), "kin_name": gold.get("kin_name"),
                       "relation_term_in_page_text": term_present},
        }
    return {"kind": "other", "error_class": "name_error", "detail": {}}


def office_relation(predicted: str | None, gold: str | None) -> str:
    predicted_ok, gold_ok = _present(predicted), _present(gold)
    if predicted_ok and gold_ok and str(predicted) == str(gold):
        return "match"
    if not predicted_ok and not gold_ok:
        return "match"
    if not predicted_ok or not gold_ok:
        return "one_sided"
    for lower, upper in PROMOTION_PAIRS:
        if {lower, upper} == {predicted, gold}:
            return "promotion_pair"
    return "conflict"


def evaluate(predictions: pd.DataFrame, gold: pd.DataFrame, *, page_texts: dict[int, str]) -> dict[str, Any]:
    """Score predictions **on the gold pages only**.

    Rows from pages without gold are counted separately: treating them as false positives
    would measure the gold set's coverage, not the parser's precision.
    """
    scored_pages = {int(value) for value in gold["page_number"].unique()}
    all_rows = predictions.to_dict(orient="records") if len(predictions) else []
    unscored = [row for row in all_rows if int(row.get("page_number") or 0) not in scored_pages]
    predictions = pd.DataFrame(
        [row for row in all_rows if int(row.get("page_number") or 0) in scored_pages]
    )
    predicted_keys = {
        _key(row): row for row in (predictions.to_dict(orient="records") if len(predictions) else [])
    }
    gold_keys = {_key(row): row for row in gold.to_dict(orient="records")}

    matched = sorted(set(predicted_keys) & set(gold_keys))
    unmatched_predicted = [key for key in predicted_keys if key not in gold_keys]
    unmatched_gold = [key for key in gold_keys if key not in predicted_keys]

    name_exact = sum(
        1 for key in matched if str(predicted_keys[key].get("kin_name")) == str(gold_keys[key].get("kin_name"))
    )
    degree_scored = [key for key in matched if _present(gold_keys[key].get("degree_raw"))]
    degree_ok = sum(
        1
        for key in degree_scored
        if _degree_matches(predicted_keys[key].get("degree_raw"), gold_keys[key].get("degree_raw"))
    )
    office_scored = [key for key in matched if _present(gold_keys[key].get("office_raw"))]
    office_relations = [
        office_relation(predicted_keys[key].get("office_raw"), gold_keys[key].get("office_raw"))
        for key in office_scored
    ]

    errors: list[dict[str, Any]] = []
    for key in unmatched_predicted:
        row = predicted_keys[key]
        page, _relation, name = key
        gold_same_name = [g for g in gold_keys if (g[0], g[2]) == (page, name)]
        if gold_same_name:
            errors.append(
                {
                    "kind": "predicted_not_in_gold",
                    "error_class": "relation_error",
                    "detail": {"page": page, "kin_name": name,
                               "predicted_relation": _relation,
                               "gold_relation": gold_same_name[0][1]},
                }
            )
        else:
            errors.append(
                {
                    "kind": "predicted_not_in_gold",
                    "error_class": "spurious_row",
                    "detail": {"page": page, "kin_name": name, "predicted_relation": _relation},
                }
            )
    for key in unmatched_gold:
        page, relation, name = key
        text = page_texts.get(page, "")
        term = str(gold_keys[key].get("relation_type"))
        errors.append(
            {
                "kind": "gold_not_predicted",
                "error_class": "ocr_induced_relation_loss" if term not in _relation_terms_in_page(text) else "missed_entry",
                "detail": {"page": page, "kin_name": name, "gold_relation": relation,
                           "relation_term_present_in_ocr_text": term in _relation_terms_in_page(text)},
            }
        )
    for key, relation in zip(office_scored, office_relations):
        if relation in ("conflict", "promotion_pair"):
            errors.append(
                {
                    "kind": "office_mismatch",
                    "error_class": "office_priority_confusion" if relation == "promotion_pair" else "office_conflict",
                    "detail": {"page": key[0], "kin_name": key[2],
                               "predicted": predicted_keys[key].get("office_raw"),
                               "gold": gold_keys[key].get("office_raw")},
                }
            )

    precision = len(matched) / len(predicted_keys) if predicted_keys else 0.0
    recall = len(matched) / len(gold_keys) if gold_keys else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    abstained = int((predictions.get("abstained", pd.Series(dtype=bool)) == True).sum()) if len(predictions) else 0
    return {
        "scored_pages": sorted(scored_pages),
        "predictions_on_unscored_pages": len(unscored),
        "scored_page_coverage_of_ocr_arm": (
            round(sum(1 for row in all_rows if int(row.get("page_number") or 0) in scored_pages) / len(all_rows), 4)
            if all_rows
            else None
        ),
        "gold_entries": int(len(gold_keys)),
        "predicted_entries": int(len(predicted_keys)),
        "matched": len(matched),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "abstention_rate": round(
            abstained / (len(predicted_keys) + abstained) if (len(predicted_keys) + abstained) else 0.0, 4
        ),
        "field_accuracy": {
            "kin_name": round(name_exact / len(matched), 4) if matched else None,
            "degree_raw": round(degree_ok / len(degree_scored), 4) if degree_scored else None,
            "office_raw_promotion_pairs_counted_as_match": (
                round(sum(1 for item in office_relations if item in ("match", "promotion_pair")) / len(office_scored), 4)
                if office_scored
                else None
            ),
            "office_raw_strict": (
                round(sum(1 for item in office_relations if item == "match") / len(office_scored), 4)
                if office_scored
                else None
            ),
        },
        "scoreable_fields": {
            "degree_raw": len(degree_scored),
            "office_raw": len(office_scored),
        },
        "error_classes": pd.Series([item["error_class"] for item in errors]).value_counts().to_dict(),
        "errors": errors,
    }


def _degree_matches(predicted: str | None, gold: str | None) -> bool:
    if not _present(predicted) and not _present(gold):
        return True
    if str(predicted) == str(gold):
        return True
    if not _present(predicted) or not _present(gold):
        return False
    groups = (("庠生", "生員", "邑庠生"), ("太學生", "監生"), ("貢生",), ("舉人",), ("進士",))
    return any(str(predicted) in group and str(gold) in group for group in groups)


def _relation_terms_in_page(text: str) -> set[str]:
    from qing_elite.v03.pilot.rules import RELATION_TERMS, RELATION_BY_TERM

    return {code for term, code in RELATION_TERMS if term in text}
