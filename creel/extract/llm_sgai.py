"""Scrapegraph-ai-backed extraction (default Extractor). SmartScraperGraph
is synchronous — the ONE thread-wrapped engine in the whole design (Phase 0
concurrency audit) — so it runs via asyncio.to_thread, never directly on
the event loop. Its import costs ~16s (Phase 0 spike, LangChain tree),
imported lazily here, never at module load.

Threads cannot be cancelled: a timed-out attempt is abandoned, not stopped,
and may complete detached (gotcha 16). Keep `timeout` short enough that
orphans exit on their own rather than piling up.

Encodes gotchas 1-4 directly:
  1. source must be raw HTML, never a path — handled by construction, we
     never pass anything but the html string.
  2. empty/whitespace source raises ValueError inside SGAI — checked BEFORE
     crossing into the thread, so the error is ours, not a stack trace from
     inside someone else's framework.
  3. SGAI doesn't raise on extraction failure — it writes
     {"error": ..., "raw_response": ...} into `answer` instead of raising.
  4. an unrecognized model silently drops the token window to 8192 and sets
     model_tokens_defaulted=True — treated here as a hard failure, since it
     means core.prune's budget enforcement upstream was computed against an
     assumption SGAI just contradicted.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Optional, Type

from pydantic import BaseModel

from creel.extract.base import ExtractOutcome

NAME = "llm_sgai"


@dataclass
class SGAIConfig:
    model: str  # "<provider>/<model>", e.g. "openai/gpt-4o-mini"
    api_key: str
    model_tokens: int = 8192
    base_url: Optional[str] = None
    timeout: float = 60.0
    html_mode: bool = True  # skip ParseNode; the caller (extract/pipeline.py) already pruned+budgeted


def available(config: Optional[SGAIConfig]) -> bool:
    return config is not None and bool(config.api_key)


async def extract(
    html: str,
    prompt: str,
    schema: Optional[Type[BaseModel]] = None,
    config: Optional[SGAIConfig] = None,
) -> ExtractOutcome:
    if not available(config):
        return ExtractOutcome(data=None, error="no provider configured")
    if not html.strip():
        return ExtractOutcome(data=None, error="empty source")  # gotcha 2, checked before the thread hop

    return await asyncio.to_thread(_run_sync, html, prompt, schema, config)


def _run_sync(html: str, prompt: str, schema: Optional[Type[BaseModel]], config: SGAIConfig) -> ExtractOutcome:
    from scrapegraphai.graphs import SmartScraperGraph  # lazy: ~16s import, Phase 0 spike

    llm_config: dict = {
        "model": config.model,
        "api_key": config.api_key,
        "model_tokens": config.model_tokens,
        "temperature": 0,
    }
    if config.base_url:
        llm_config["base_url"] = config.base_url

    graph = SmartScraperGraph(
        prompt=prompt,
        source=html,  # gotcha 1: raw HTML, NEVER a path
        config={
            "llm": llm_config,
            "html_mode": config.html_mode,
            "verbose": False,
            "timeout": config.timeout,
        },
        schema=schema,
    )
    try:
        answer = graph.run()
    except Exception as e:
        return ExtractOutcome(data=None, error=f"{type(e).__name__}: {e}")

    if isinstance(answer, dict) and "error" in answer and "raw_response" in answer:
        return ExtractOutcome(data=None, error=str(answer["error"]))  # gotcha 3

    if getattr(graph, "model_tokens_defaulted", False):
        return ExtractOutcome(
            data=None, error="model_tokens_defaulted: unrecognized model, our budget assumption was invalid"
        )  # gotcha 4

    try:
        info = graph.get_execution_info() or {}
    except Exception:
        info = {}

    prompt_tokens = info.get("prompt_tokens", 0)
    completion_tokens = info.get("completion_tokens", 0)
    exact = bool(prompt_tokens or completion_tokens)  # SGAI's usage reporting is provider-dependent and often absent

    data = answer if isinstance(answer, dict) else {"value": answer}
    return ExtractOutcome(data=data, prompt_tokens=prompt_tokens, completion_tokens=completion_tokens, exact=exact)
