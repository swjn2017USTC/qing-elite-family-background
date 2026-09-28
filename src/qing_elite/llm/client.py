"""Official DeepSeek API client for formal research data production.

This module is the only sanctioned channel for producing research data with an
LLM. Rules it enforces (OMP_PLAN.md sections 2.2, 3.2, 14):

* the API key is read from the process environment (``DEEPSEEK_API_KEY``) on
  every call and is never written to disk, YAML, logs, or the OMP providers;
* thinking is explicitly disabled for extraction calls, because newer DeepSeek
  models may default to thinking on;
* responses are requested as JSON objects.

The OMP ``deepseek`` provider is a separate, conversational channel and MUST NOT
be used to produce formal research data.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Final, Mapping

from openai import OpenAI

API_KEY_ENV: Final[str] = "DEEPSEEK_API_KEY"
BASE_URL: Final[str] = "https://api.deepseek.com"
EXTRACTION_MODEL: Final[str] = "deepseek-flash"
THINKING_DISABLED: Final[Mapping[str, str]] = {"type": "disabled"}

DEFAULT_SYSTEM_PROMPT: Final[str] = (
    "You reply with a single compact JSON object and nothing else."
)


class MissingApiKeyError(RuntimeError):
    """Raised when the DeepSeek API key is absent from the environment."""


@dataclass(frozen=True, slots=True)
class JsonCompletion:
    """Result of one JSON-only completion."""

    data: dict[str, Any]
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: int


def api_key_from_env(env: Mapping[str, str] | None = None) -> str:
    """Return the DeepSeek API key from the environment, or raise."""
    source = os.environ if env is None else env
    key = source.get(API_KEY_ENV, "").strip()
    if not key:
        raise MissingApiKeyError(
            f"{API_KEY_ENV} is not set; export it in the shell before running the pipeline"
        )
    return key


def create_client(env: Mapping[str, str] | None = None, **kwargs: Any) -> OpenAI:
    """Build an OpenAI-compatible client pointed at the official DeepSeek API."""
    return OpenAI(api_key=api_key_from_env(env), base_url=BASE_URL, **kwargs)


def chat_json(
    prompt: str,
    *,
    system: str = DEFAULT_SYSTEM_PROMPT,
    model: str = EXTRACTION_MODEL,
    max_tokens: int = 64,
    temperature: float = 0.0,
    client: OpenAI | None = None,
) -> JsonCompletion:
    """Run one JSON-only completion with thinking explicitly disabled."""
    active_client = client or create_client()
    started = time.perf_counter()
    response = active_client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        response_format={"type": "json_object"},
        max_tokens=max_tokens,
        temperature=temperature,
        # DeepSeek thinking must be turned off explicitly, never left to default.
        extra_body={"thinking": dict(THINKING_DISABLED)},
    )
    latency_ms = int((time.perf_counter() - started) * 1000)
    text = response.choices[0].message.content or ""
    usage = response.usage
    return JsonCompletion(
        data=json.loads(text),
        model=response.model,
        input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
        output_tokens=getattr(usage, "completion_tokens", 0) or 0,
        latency_ms=latency_ms,
    )
