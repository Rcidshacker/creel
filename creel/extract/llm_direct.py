"""Thin structured-output LLM client — the documented escape hatch if
Scrapegraph-ai's LangChain dependency tree ever destabilizes (Phase 0's
contingency, built here as a genuine second Extractor rather than kept
unimplemented). No LangGraph, no framework — one HTTP call, OpenAI-
compatible `response_format: json_object`, which OpenAI, OpenRouter, and
most OpenAI-compatible endpoints all support identically.

This is also the ONLY implementation that asks the model for candidate CSS
selectors alongside the extracted values, in the SAME call — no extra
request, no extra cost. extract/learn.py validates and promotes those
selectors into the free tier-1 cache.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Optional, Type

from pydantic import BaseModel

from creel.core.prune import count_tokens
from creel.extract.base import ExtractOutcome

NAME = "llm_direct"

# Confirmed live against the real NVIDIA API (Aug 2026): this exact model id
# is served at this base_url, and a response_format=json_object call returns
# valid structured JSON with real (non-estimated) usage counts. Context
# window is documented up to 1M tokens; NVIDIA_MODEL_TOKENS default below is
# a conservative working budget, not the model's real ceiling.
_NVIDIA_DEFAULT_MODEL = "nvidia/nemotron-3-super-120b-a12b"
_NVIDIA_DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"
_NVIDIA_DEFAULT_MODEL_TOKENS = 128_000

_SYSTEM_PROMPT = (
    "You extract structured data from web page content. Always respond with a "
    'single JSON object with exactly two top-level keys: "data" (the requested '
    'fields) and "selectors" (your best-guess CSS selector for where each '
    "field's value appears in the HTML, one per field present in `data`). If "
    "you cannot find a field, omit it from both objects rather than guessing."
)


@dataclass
class ProviderConfig:
    model: str
    api_key: str
    base_url: str = "https://api.openai.com/v1"
    model_tokens: int = 8192

    @classmethod
    def from_env(cls) -> Optional["ProviderConfig"]:
        """NVIDIA-hosted default, since that's the provider actually wired
        up. Returns None (not a config with an empty key) when
        NVIDIA_API_KEY is unset, so `available()` degrades cleanly."""
        api_key = os.environ.get("NVIDIA_API_KEY")
        if not api_key:
            return None
        return cls(
            model=os.environ.get("NVIDIA_MODEL", _NVIDIA_DEFAULT_MODEL),
            api_key=api_key,
            base_url=os.environ.get("NVIDIA_BASE_URL", _NVIDIA_DEFAULT_BASE_URL),
            model_tokens=int(os.environ.get("NVIDIA_MODEL_TOKENS", _NVIDIA_DEFAULT_MODEL_TOKENS)),
        )


def available(config: Optional[ProviderConfig]) -> bool:
    return config is not None and bool(config.api_key)


async def extract(
    html: str,
    prompt: str,
    schema: Optional[Type[BaseModel]] = None,
    config: Optional[ProviderConfig] = None,
) -> ExtractOutcome:
    import httpx

    if not available(config):
        return ExtractOutcome(data=None, error="no provider configured")

    user_content = f"{prompt}\n\n---PAGE CONTENT---\n{html}"
    payload = {
        "model": config.model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0,
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{config.base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {config.api_key}"},
                json=payload,
            )
            response.raise_for_status()
            body = response.json()
    except Exception as e:
        return ExtractOutcome(data=None, error=f"request failed: {type(e).__name__}: {e}")

    try:
        content = body["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        data = parsed.get("data")
        selectors = parsed.get("selectors", {})
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as e:
        return ExtractOutcome(data=None, error=f"malformed response: {e}")

    usage = body.get("usage")
    if usage:
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        exact = True
    else:
        prompt_tokens = count_tokens(user_content, config.model)
        completion_tokens = count_tokens(content, config.model)
        exact = False

    return ExtractOutcome(
        data=data,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        exact=exact,
        learned_selectors=selectors if isinstance(selectors, dict) else {},
    )
