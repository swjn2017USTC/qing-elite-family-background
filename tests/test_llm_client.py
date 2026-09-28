"""Contract tests for the official DeepSeek research client.

No network access: the OpenAI SDK is pointed at an in-process httpx transport
that records the outgoing request.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from openai import OpenAI

from qing_elite.llm.client import (
    API_KEY_ENV,
    BASE_URL,
    EXTRACTION_MODEL,
    MissingApiKeyError,
    api_key_from_env,
    chat_json,
)

TEST_KEY = "test-key-not-a-real-credential"


def _client_capturing(captured: dict[str, Any]) -> OpenAI:
    response_body = {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 0,
        "model": EXTRACTION_MODEL,
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": '{"ok": true}'},
            }
        ],
        "usage": {"prompt_tokens": 11, "completion_tokens": 4, "total_tokens": 15},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("authorization")
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=response_body)

    return OpenAI(
        api_key=TEST_KEY,
        base_url=BASE_URL,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_api_key_from_env_missing_raises() -> None:
    with pytest.raises(MissingApiKeyError):
        api_key_from_env({})


def test_chat_json_targets_official_api_with_thinking_disabled() -> None:
    captured: dict[str, Any] = {}
    completion = chat_json('Return {"ok": true}', client=_client_capturing(captured))

    assert captured["url"] == f"{BASE_URL}/chat/completions"
    assert captured["authorization"] == f"Bearer {TEST_KEY}"
    body = captured["body"]
    assert body["model"] == EXTRACTION_MODEL
    assert body["response_format"] == {"type": "json_object"}
    assert body["thinking"] == {"type": "disabled"}
    assert completion.data == {"ok": True}
    assert (completion.input_tokens, completion.output_tokens) == (11, 4)


def test_client_reads_key_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(API_KEY_ENV, TEST_KEY)
    assert api_key_from_env() == TEST_KEY
