from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

# Rough USD / 1M tokens for cost reporting (eval). Override with env if needed.
_DEFAULT_RATES = {
    "gpt-4.1": (2.0, 8.0),
    "gpt-4o": (2.5, 10.0),
    "llama-3.3-70b-versatile": (0.59, 0.79),
}


@dataclass
class LLMResult:
    content: str
    parsed: dict[str, Any] | None
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    model: str


def _client() -> OpenAI:
    api_key = os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "Set LLM_API_KEY (or OPENAI_API_KEY) in the environment / .env"
        )
    kwargs: dict[str, Any] = {"api_key": api_key}
    base = os.environ.get("LLM_BASE_URL") or os.environ.get("OPENAI_BASE_URL")
    if base:
        kwargs["base_url"] = base
    return OpenAI(**kwargs)


def model_name() -> str:
    return (
        os.environ.get("LLM_MODEL")
        or os.environ.get("OPENAI_MODEL")
        or "llama-3.3-70b-versatile"
    )


def _estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    rates = _DEFAULT_RATES.get(model, (1.0, 1.0))
    return (prompt_tokens * rates[0] + completion_tokens * rates[1]) / 1_000_000


def _extract_json(text: str) -> dict[str, Any] | None:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def chat_json(messages: list[dict[str, str]], *, temperature: float = 0.0) -> LLMResult:
    client = _client()
    model = model_name()
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    # Prefer JSON mode when the backend supports it; fall back without it.
    try:
        resp = client.chat.completions.create(
            **kwargs, response_format={"type": "json_object"}
        )
    except Exception:
        resp = client.chat.completions.create(**kwargs)

    content = resp.choices[0].message.content or ""
    usage = resp.usage
    pt = int(usage.prompt_tokens) if usage else 0
    ct = int(usage.completion_tokens) if usage else 0
    return LLMResult(
        content=content,
        parsed=_extract_json(content),
        prompt_tokens=pt,
        completion_tokens=ct,
        cost_usd=_estimate_cost(model, pt, ct),
        model=model,
    )
