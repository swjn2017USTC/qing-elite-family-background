"""Minimal health check for the official DeepSeek research channel.

Runs exactly one request against ``deepseek-flash`` with thinking explicitly
disabled and a JSON-only response, then prints a JSON summary to stdout.

    uv run python -m qing_elite.llm.health_check

Exit code 0 on success, 1 on any failure. The API key is never printed, and this
probe is not research data: it writes no audit record.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from qing_elite.llm.client import (
    API_KEY_ENV,
    EXTRACTION_MODEL,
    MissingApiKeyError,
    api_key_from_env,
    chat_json,
)

PROMPT = 'Return a JSON object with exactly one field: {"ok": true}'


def main() -> int:
    summary: dict[str, Any] = {
        "check": "deepseek_official_api_health",
        "api_key_env": API_KEY_ENV,
        "requested_model": EXTRACTION_MODEL,
        "thinking": "disabled",
        "response_format": "json_object",
    }
    try:
        api_key_from_env()
    except MissingApiKeyError as exc:
        summary.update(status="error", api_key_present=False, message=str(exc))
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 1

    summary["api_key_present"] = True
    try:
        completion = chat_json(PROMPT, max_tokens=32)
    except Exception as exc:  # noqa: BLE001 - the probe must report any failure verbatim
        summary.update(status="error", message=f"{type(exc).__name__}: {exc}")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 1

    summary.update(
        status="ok",
        returned_model=completion.model,
        latency_ms=completion.latency_ms,
        usage={
            "input_tokens": completion.input_tokens,
            "output_tokens": completion.output_tokens,
        },
        reply=completion.data,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
