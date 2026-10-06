"""LLM fallback for rule-parser abstentions (U05R).

Rules run first; only the entries the rules could not resolve reach a model, and only in a
bounded number. The call goes to the official DeepSeek API through the v0.1 client
(``deepseek-flash``, thinking explicitly disabled, JSON output), the answer is accepted
**only if its evidence quote appears verbatim in the window that was sent**, and every call
is appended to ``audit/llm_calls/calls.jsonl`` with token and cost accounting — no key, no
auth header, no full prompt.

Cost is planned before the first request (see ``plan_calls``) and the run aborts if the
estimate exceeds the configured cap.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from qing_elite.utils.config import PROJECT_ROOT
from qing_elite.v03.design import load_relation_ontology, relation_index

EXTRACTION_YAML = PROJECT_ROOT / "config" / "v03" / "llm_extraction.yaml"
AUDIT_PATH = PROJECT_ROOT / "audit" / "llm_calls" / "calls.jsonl"

#: RMB per 1M tokens for the official channel (off-peak), from config/pricing.yaml + v0.1.
PRICE_CNY_PER_MTOK = {"input": 1.0, "output": 2.0}


class BudgetExceeded(RuntimeError):
    """Raised when the planned spend exceeds the configured cap."""


def load_config(path: Path | None = None) -> dict[str, Any]:
    with (path or EXTRACTION_YAML).open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def window_around(text: str, offsets: list[int] | tuple[int, int], *, radius: int) -> str:
    start, end = int(offsets[0]), int(offsets[1])
    return text[max(0, start - radius) : min(len(text), end + radius)]


def plan_calls(abstained: list[dict[str, Any]], page_texts: dict[int, str], *, config: dict[str, Any]) -> dict[str, Any]:
    """Estimate tokens and RMB before spending anything."""
    radius = int(config["window_radius"])
    calls = []
    for item in abstained:
        text = page_texts.get(item.get("page_number"), "")
        window = window_around(text, item.get("offsets", [0, 0]), radius=radius)
        calls.append({"item": item, "window": window, "chars": len(window)})
    chars = sum(call["chars"] for call in calls)
    # crude but honest: Chinese text ≈ 1 token per 1.6 characters, plus the fixed prompt
    prompt_tokens = int(chars / 1.6) + int(config["prompt_overhead_tokens"]) * len(calls)
    output_tokens = int(config["expected_output_tokens"]) * len(calls)
    cost = (
        prompt_tokens * PRICE_CNY_PER_MTOK["input"] + output_tokens * PRICE_CNY_PER_MTOK["output"]
    ) / 1_000_000
    return {
        "calls": len(calls),
        "window_chars": chars,
        "estimated_input_tokens": prompt_tokens,
        "estimated_output_tokens": output_tokens,
        "estimated_cost_rmb": round(cost, 6),
        "cap_rmb": float(config["max_cost_rmb"]),
        "items": calls,
    }


def _prompt(window: str, relation_hint: str, config: dict[str, Any]) -> str:
    ontology = sorted(relation_index(load_relation_ontology()))
    return (
        f"{config['instruction']}\n\n"
        f"允許的關係代碼（relation_type）：{', '.join(ontology)}\n"
        f"規則 parser 已判定此段落的關係很可能是：{relation_hint}\n\n"
        "只輸出 JSON，鍵為：found(bool), relation_type, kin_name, degree_raw, office_raw, evidence_quote。\n"
        "evidence_quote 必須是下面原文中的**逐字連續子串**；無法判定時 found=false 且其餘留空。\n\n"
        f"原文：\n{window}"
    )


def _audit(record: dict[str, Any]) -> None:
    AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with AUDIT_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def resolve_abstentions(
    abstained: list[dict[str, Any]],
    page_texts: dict[int, str],
    *,
    config: dict[str, Any] | None = None,
    max_calls: int | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Ask the model about abstained entries; keep only verbatim-quotable answers."""
    config = config or load_config()
    plan = plan_calls(abstained, page_texts, config=config)
    cap = min(float(config["max_cost_rmb"]), float(plan["cap_rmb"]))
    if plan["estimated_cost_rmb"] > cap:
        raise BudgetExceeded(
            f"planned {plan['estimated_cost_rmb']} RMB exceeds cap {cap} RMB ({plan['calls']} calls)"
        )
    limit = max_calls if max_calls is not None else int(config["max_calls"])
    items = plan["items"][:limit]

    result: dict[str, Any] = {"plan": plan, "attempted": 0, "recovered": 0, "rows": [], "rejected": []}
    if dry_run or not items:
        return result

    from qing_elite.llm.client import chat_json

    for call in items:
        item = call["item"]
        window = call["window"]
        result["attempted"] += 1
        prompt = _prompt(window, item.get("relation_type", ""), config)
        digest = hashlib.sha256(prompt.encode()).hexdigest()
        record = {
            "call_id": f"u05r-abstain-{result['attempted']:04d}",
            "task_type": "u05r_abstention_resolution",
            "model": config["model"],
            "thinking_mode": config["thinking_mode"],
            "prompt_version": config["prompt_version"],
            "source_ids": item.get("locator"),
            "input_sha256": digest,
            "status": "error",
            "retry_count": 0,
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        try:
            completion = chat_json(prompt, model=config["model"], max_tokens=int(config["max_tokens"]))
            payload = completion.data
            record.update(
                input_tokens=completion.input_tokens,
                output_tokens=completion.output_tokens,
                latency_ms=completion.latency_ms,
                estimated_cost_rmb=round(
                    (
                        completion.input_tokens * PRICE_CNY_PER_MTOK["input"]
                        + completion.output_tokens * PRICE_CNY_PER_MTOK["output"]
                    )
                    / 1_000_000,
                    6,
                ),
            )
            quote = str(payload.get("evidence_quote") or "")
            relation = str(payload.get("relation_type") or "")
            if not payload.get("found") or not quote or quote not in window:
                record["status"] = "rejected_quote"
                result["rejected"].append({"item": item, "payload": payload, "reason": "quote_not_verbatim"})
            elif relation not in relation_index(load_relation_ontology()):
                record["status"] = "rejected_relation"
                result["rejected"].append({"item": item, "payload": payload, "reason": "relation_not_in_ontology"})
            else:
                record["status"] = "ok"
                result["recovered"] += 1
                result["rows"].append(
                    {
                        **item,
                        "relation_type": relation,
                        "kin_name": payload.get("kin_name"),
                        "degree_raw": payload.get("degree_raw") or None,
                        "office_raw": payload.get("office_raw") or None,
                        "quote": quote,
                        "offsets": [window.index(quote), window.index(quote) + len(quote)],
                        "confidence": "llm_fallback",
                        "resolution": "llm",
                    }
                )
        except Exception as error:
            record["status"] = "error"
            record["error"] = f"{type(error).__name__}: {error}"[:200]
        _audit(record)

    return result


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - thin CLI
    parser = argparse.ArgumentParser(description="plan or run the LLM fallback")
    parser.add_argument("--plan", action="store_true", help="print the cost estimate only")
    args = parser.parse_args(argv)
    config = load_config()

    from qing_elite.v03.pilot.ocr import load_pages, page_text_map, run_ocr_range

    pages: list[dict[str, Any]] = []
    for start, end in ((1, 8), (9, 16)):
        result = run_ocr_range(start=start, end=end, reuse=True)
        pages.extend(load_pages(result["path"]))
    texts = page_text_map(pages)

    from qing_elite.v03.pilot.rules import parse_pages
    from qing_elite.v03.pilot.run import OCR_SOURCE_ID

    outcome = parse_pages(pages, focal_person="(page-entry)", source_id=OCR_SOURCE_ID)
    plan = plan_calls(outcome.abstained, texts, config=config)
    print(json.dumps({k: v for k, v in plan.items() if k != "items"}, ensure_ascii=False, indent=2))
    if args.plan:
        return 0
    if not os.environ.get("DEEPSEEK_API_KEY"):
        print("DEEPSEEK_API_KEY is not set; nothing to run")
        return 0
    result = resolve_abstentions(outcome.abstained, texts, config=config)
    print(json.dumps({k: v for k, v in result.items() if k not in ("plan", "rows")}, ensure_ascii=False)[:1200])
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
