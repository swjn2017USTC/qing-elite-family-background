"""DeepSeek official-API family extraction (P04).

The only sanctioned research channel: local Python -> ``$DEEPSEEK_API_KEY`` ->
``https://api.deepseek.com``. The OMP ``deepseek`` provider is never used.

Two call shapes:

* ordinary extraction — ``deepseek-flash``, thinking **disabled**, JSON output;
* conflict resolution — the same model with thinking **enabled** and
  ``reasoning_effort=low``, only when the rule-based pass cannot settle the case.

Every call is recorded in ``audit/llm_calls/calls.jsonl`` with the fields required
by OMP_PLAN.md section 14, and never with the API key.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping

from openai import OpenAI

from qing_elite.llm.client import API_KEY_ENV, EXTRACTION_MODEL, create_client
from qing_elite.llm.estimation import PRICES_CNY_PER_MTOK

PROMPT_VERSION = "p04-family-v3"
AUDIT_PATH = Path("audit/llm_calls/calls.jsonl")

SYSTEM_PROMPT = """你是清代史料结构化抽取器。只做一件事：从给定的一段《清史稿》原文中，抽取该人物的父、祖父、曾祖父信息。

输出必须是一个 JSON 对象，字段名与结构**一字不差**如下（不得增删字段、不得改名、不得嵌套其它键）：

{
  "person_id": "",
  "father": {"name": null, "degree": null, "office": null, "evidence": null},
  "grandfather": {"name": null, "degree": null, "office": null, "evidence": null},
  "great_grandfather": {"name": null, "degree": null, "office": null, "evidence": null},
  "ambiguities": [],
  "confidence": "high",
  "insufficient_evidence": false
}

规则：
1. 只能使用原文中明确写出的信息。原文没写的，对应字段填 null；三代全部无据时三个槽位的 name/degree/office/evidence 均为 null，并把 insufficient_evidence 设为 true。
2. degree 字段填科举功名原文写法（如「進士」「舉人」「諸生」「蔭生」），office 字段填官职原文写法（如「大學士」「知府」「訓導」）。
3. 每个 name 非 null 的槽位，evidence 必须是**原文中连续出现的一段原话**：一字不改地照抄，长度控制在 40 字以内，且必须是原文中**相邻**的文字；若相关信息分散在两处，只取最直接支持该槽位的那一句，绝不用省略号或逗号把不相邻的句子拼起来。
4. 原文常用「某，某某之子」「祖某」「曾祖某」这类说法：照抄即可，但要确认所指是父/祖/曾祖。
5. 只有无法判断某人是父还是祖父时才写入 ambiguities；不要用它代替 null。
6. confidence 只能是 "high"、"medium"、"low" 之一。
7. 只输出这一个 JSON 对象，不要输出解释、不要 Markdown 代码块、不要在对象外附加任何文字。"""

OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "person_id": {"type": "string"},
        "father": {"$ref": "#/definitions/ancestor"},
        "grandfather": {"$ref": "#/definitions/ancestor"},
        "great_grandfather": {"$ref": "#/definitions/ancestor"},
        "ambiguities": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "insufficient_evidence": {"type": "boolean"},
    },
    "required": ["father", "grandfather", "great_grandfather", "ambiguities", "confidence", "insufficient_evidence"],
    "definitions": {
        "ancestor": {
            "type": "object",
            "properties": {
                "name": {"type": ["string", "null"]},
                "degree": {"type": ["string", "null"]},
                "office": {"type": ["string", "null"]},
                "evidence": {"type": ["string", "null"]},
            },
            "required": ["name", "degree", "office", "evidence"],
        }
    },
}

SLOTS = ("father", "grandfather", "great_grandfather")


@dataclass(frozen=True, slots=True)
class ExtractionCall:
    """One API call and its parsed result."""

    call_id: str
    person_uid: str
    person_name: str
    thinking_mode: str  # "disabled" | "enabled"
    reasoning_effort: str | None
    prompt_version: str
    model: str
    input_sha256: str
    passage_chars: int
    output: dict[str, Any] | None
    raw_text: str
    input_tokens: int
    cached_tokens: int
    output_tokens: int
    latency_ms: int
    status: str
    error: str | None = None
    retry_count: int = 0
    escalation_of: str | None = None
    usage_raw: dict[str, Any] = field(default_factory=dict)


def build_user_prompt(person_name: str, passage: str, source_ids: str = "") -> str:
    """Prompt carrying only the name and the local passage window."""
    header = f"人物：{person_name}\n"
    if source_ids:
        header += f"来源定位：{source_ids}\n"
    return (
        f"{header}\n"
        "下面是该人物的原文窗口。请按系统提示的 JSON 结构抽取父/祖父/曾祖父信息；"
        "原文没有依据的字段填 null，并在 evidence 中给出原文连续片段。\n\n"
        f"【原文开始】\n{passage}\n【原文结束】"
    )


def call_extraction(
    *,
    call_id: str,
    person_uid: str,
    person_name: str,
    passage: str,
    source_ids: str = "",
    escalation: bool = False,
    escalation_of: str | None = None,
    max_tokens: int = 900,
    client: OpenAI | None = None,
) -> ExtractionCall:
    """Run one extraction call against the official DeepSeek API."""
    active_client = client or create_client()
    user_prompt = build_user_prompt(person_name, passage, source_ids)
    request: dict[str, Any] = {
        "model": EXTRACTION_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "response_format": {"type": "json_object"},
        "max_tokens": max_tokens,
        "extra_body": {"thinking": {"type": "enabled" if escalation else "disabled"}},
    }
    if escalation:
        request["reasoning_effort"] = "low"
    else:
        request["temperature"] = 0.0

    started = time.perf_counter()
    try:
        response = active_client.chat.completions.create(**request)
        latency_ms = int((time.perf_counter() - started) * 1000)
        usage = response.usage
        usage_raw = usage.model_dump() if hasattr(usage, "model_dump") else {}
        text = response.choices[0].message.content or ""
        try:
            parsed = json.loads(text)
            status = "ok"
            error = None
        except json.JSONDecodeError as error:
            parsed = None
            status = "invalid_json"
            error = str(error)
        return ExtractionCall(
            call_id=call_id,
            person_uid=person_uid,
            person_name=person_name,
            thinking_mode="enabled" if escalation else "disabled",
            reasoning_effort="low" if escalation else None,
            prompt_version=PROMPT_VERSION,
            model=EXTRACTION_MODEL,
            input_sha256=hashlib.sha256(user_prompt.encode("utf-8")).hexdigest(),
            passage_chars=len(passage),
            output=parsed,
            raw_text=text,
            input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
            cached_tokens=int(
                usage_raw.get("prompt_cache_hit_tokens")
                or usage_raw.get("prompt_tokens_details", {}).get("cached_tokens", 0)
                or 0
            ),
            output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
            latency_ms=latency_ms,
            status=status,
            error=error,
            escalation_of=escalation_of,
            usage_raw=usage_raw,
        )
    except Exception as error:  # noqa: BLE001 - the audit must record the failure verbatim
        latency_ms = int((time.perf_counter() - started) * 1000)
        return ExtractionCall(
            call_id=call_id,
            person_uid=person_uid,
            person_name=person_name,
            thinking_mode="enabled" if escalation else "disabled",
            reasoning_effort="low" if escalation else None,
            prompt_version=PROMPT_VERSION,
            model=EXTRACTION_MODEL,
            input_sha256=hashlib.sha256(user_prompt.encode("utf-8")).hexdigest(),
            passage_chars=len(passage),
            output=None,
            raw_text="",
            input_tokens=0,
            cached_tokens=0,
            output_tokens=0,
            latency_ms=latency_ms,
            status="api_error",
            error=f"{type(error).__name__}: {error}",
            escalation_of=escalation_of,
        )


def call_cost_rmb(call: ExtractionCall, *, peak: bool = False) -> float:
    """Cost of one call under the published deepseek-flash prices."""
    prices = PRICES_CNY_PER_MTOK["peak" if peak else "off_peak"]
    cache_miss = max(call.input_tokens - call.cached_tokens, 0)
    return round(
        cache_miss / 1e6 * prices["input_cache_miss"]
        + call.cached_tokens / 1e6 * prices["input_cache_hit"]
        + call.output_tokens / 1e6 * prices["output"],
        6,
    )


REQUIRED_TOP_LEVEL = (
    "father",
    "grandfather",
    "great_grandfather",
    "ambiguities",
    "confidence",
    "insufficient_evidence",
)
REQUIRED_SLOT_FIELDS = ("name", "degree", "office", "evidence")


def schema_validity(output: Mapping[str, Any] | None) -> tuple[bool, str]:
    """Check the response against the declared schema, not merely JSON parseability.

    The v1 prompt never showed the model the schema, so it invented keys (父/祖父,
    "type", "exam"). Parseability alone would have scored those calls as valid.
    """
    if not isinstance(output, Mapping):
        return False, "not_an_object"
    missing = [key for key in REQUIRED_TOP_LEVEL if key not in output]
    if missing:
        return False, "missing_top_level:" + ",".join(missing)
    extra = [key for key in output if key not in REQUIRED_TOP_LEVEL and key != "person_id"]
    for slot in SLOTS:
        block = output.get(slot)
        if not isinstance(block, Mapping):
            return False, f"{slot}:not_an_object"
        slot_missing = [key for key in REQUIRED_SLOT_FIELDS if key not in block]
        if slot_missing:
            return False, f"{slot}:missing:" + ",".join(slot_missing)
        extra_slot = [key for key in block if key not in REQUIRED_SLOT_FIELDS]
        if extra_slot:
            return False, f"{slot}:extra:" + ",".join(extra_slot)
    if not isinstance(output.get("ambiguities"), list):
        return False, "ambiguities:not_a_list"
    if output.get("confidence") not in {"high", "medium", "low"}:
        return False, "confidence:invalid"
    if not isinstance(output.get("insufficient_evidence"), bool):
        return False, "insufficient_evidence:not_bool"
    if extra:
        return False, "extra_top_level:" + ",".join(sorted(extra))
    return True, "ok"


_EVIDENCE_NOISE = str.maketrans("", "", "…\u2026 \n\t「」“”\"'")


def evidence_matches_passage(evidence: str, passage: str) -> bool:
    """Is the quoted evidence really in the text the model was given?

    The window is assembled from merged spans joined by an ellipsis, so a verbatim
    quote may straddle a join. Comparison therefore ignores the join markers and
    whitespace; nothing else is relaxed.
    """
    normalized = evidence.translate(_EVIDENCE_NOISE)
    haystack = passage.translate(_EVIDENCE_NOISE)
    return bool(normalized) and normalized in haystack


def evidence_fidelity(output: Mapping[str, Any] | None, passage: str) -> dict[str, Any]:
    """Check every non-null extraction against the passage text.

    Evidence fidelity is measured objectively: the quoted ``evidence`` must appear
    verbatim in the window that was sent. A non-null name with no verbatim quote is
    an unsupported assertion.
    """
    result: dict[str, Any] = {
        "non_null_slots": 0,
        "with_evidence": 0,
        "evidence_verbatim": 0,
        "unsupported_assertions": 0,
        "slot_detail": {},
    }
    if not output:
        return result
    for slot in SLOTS:
        block = output.get(slot) or {}
        detail = {"name": block.get("name"), "evidence_ok": None, "has_value": False}
        has_value = any(block.get(field_name) for field_name in ("name", "degree", "office"))
        if not has_value:
            result["slot_detail"][slot] = detail
            continue
        result["non_null_slots"] += 1
        detail["has_value"] = True
        evidence = block.get("evidence")
        if isinstance(evidence, str) and evidence.strip():
            result["with_evidence"] += 1
            found = evidence_matches_passage(evidence, passage)
            detail["evidence_ok"] = bool(found)
            if found:
                result["evidence_verbatim"] += 1
            else:
                result["unsupported_assertions"] += 1
        else:
            detail["evidence_ok"] = False
            result["unsupported_assertions"] += 1
        result["slot_detail"][slot] = detail
    return result


def audit_record(call: ExtractionCall, *, person_id: str, source_ids: str, task_type: str) -> dict[str, Any]:
    """Audit line per OMP_PLAN.md section 14 — never contains credentials."""
    record = {
        "call_id": call.call_id,
        "person_id": person_id,
        "person_uid": call.person_uid,
        "task_type": task_type,
        "model": call.model,
        "thinking_mode": call.thinking_mode,
        "reasoning_effort": call.reasoning_effort,
        "prompt_version": call.prompt_version,
        "source_ids": source_ids,
        "input_sha256": call.input_sha256,
        "input_tokens": call.input_tokens,
        "cached_tokens": call.cached_tokens,
        "output_tokens": call.output_tokens,
        "estimated_cost_rmb": call_cost_rmb(call),
        "status": call.status,
        "retry_count": call.retry_count,
        "latency_ms": call.latency_ms,
        "escalation_of": call.escalation_of,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    return record


def append_audit(records: list[Mapping[str, Any]], path: Path = AUDIT_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(dict(record), ensure_ascii=False) + "\n")
    return path


def call_to_row(call: ExtractionCall) -> dict[str, Any]:
    """Flatten a call for the pilot result table."""
    row = asdict(call)
    row.pop("usage_raw", None)
    row["output_json"] = json.dumps(call.output, ensure_ascii=False) if call.output else None
    row.pop("raw_text", None)
    return row
