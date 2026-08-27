"""The extraction ladder: prune -> learned selectors -> LLM, with the LLM
promoting validated selectors back down to tier 1 on success. This is
Phase 2's actual deliverable — the module that turns "500 structurally
identical product pages" from 500 LLM calls into roughly one.

    0 prune              (free)
    1 learned selectors  (cached, free)   <- learn.try_learn writes here
    2 LLM rung           (paid)     ──► on success, learn.try_learn re-learns

An extraction cache (separate from the fetch cache) sits in front of
everything: identical url+prompt+model+schema, keyed by html_hash, costs
zero LLM work on a repeat call regardless of which rung produced the
original answer.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional, Type

from pydantic import BaseModel

from creel.core.prune import enforce_budget, prune_html
from creel.core.store import Store
from creel.core.urlnorm import registrable_domain
from creel.extract.base import ExtractOutcome
from creel.extract.learn import load_learned_selectors, try_learn
from creel.extract.schema import retry_prompt, validate
from creel.extract.selectors import extract_with_selectors

LlmExtractFn = Callable[[str, str, Optional[Type[BaseModel]]], Awaitable[ExtractOutcome]]


@dataclass
class ExtractResult:
    data: Optional[dict]
    status: str  # "ok" | "partial" | "failed"
    source: str  # "cache" | "learned_selectors" | "llm" | "none"
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_exact: bool = True
    error: Optional[str] = None
    attempts: list[str] = field(default_factory=list)


def _extract_cache_key(html: str, prompt: str, model: str, schema_name: str) -> str:
    html_hash = hashlib.sha256(html.encode("utf-8", errors="ignore")).hexdigest()
    raw = f"{html_hash}|{prompt}|{model}|{schema_name}"
    return "extract::" + hashlib.sha256(raw.encode()).hexdigest()


async def extract(
    url: str,
    html: str,
    prompt: str,
    schema: Optional[Type[BaseModel]] = None,
    store: Optional[Store] = None,
    llm_extract: Optional[LlmExtractFn] = None,
    model: str = "unset",
    model_tokens: int = 8192,
    selector_storage_file: Optional[str] = None,
) -> ExtractResult:
    domain = registrable_domain(url)
    schema_name = schema.__name__ if schema is not None else "none"
    attempts: list[str] = []

    cache_key = None
    if store is not None:
        cache_key = _extract_cache_key(html, prompt, model, schema_name)
        cached = store.get_extract(cache_key)
        if cached is not None:
            return ExtractResult(data=cached, status="ok", source="cache", attempts=["cache"])

    healed = False
    if store is not None:
        learned = load_learned_selectors(store, domain, url)
        if learned:
            attempts.append("learned_selectors")
            replayed = extract_with_selectors(
                html, url, learned, allow_adaptive=True, storage_file=selector_storage_file
            )
            if replayed.data is not None:
                validated, err = validate(replayed.data, schema)
                if err is None:
                    if cache_key:
                        store.put_extract(cache_key, validated)
                    status = "partial" if replayed.healed else "ok"
                    return ExtractResult(
                        data=validated, status=status, source="learned_selectors",
                        cost_exact=True, attempts=attempts,
                    )
                # matched but failed schema validation -> treat as a miss,
                # fall through to the LLM tier, which will re-learn.
            healed = replayed.healed  # surfaced even on a miss, informational only

    if llm_extract is None:
        return ExtractResult(data=None, status="failed", source="none", error="no LLM extractor configured", attempts=attempts)

    attempts.append("llm")
    pruned = prune_html(html, main_content_only=True)
    budgeted, was_truncated = enforce_budget(pruned, model_tokens, model)

    outcome = await llm_extract(budgeted, prompt, schema)
    validated, err = validate(outcome.data, schema)

    if err is not None and outcome.data is not None:
        attempts.append("llm_retry")
        retry_outcome = await llm_extract(budgeted, retry_prompt(prompt, err), schema)
        outcome = ExtractOutcome(
            data=retry_outcome.data,
            prompt_tokens=outcome.prompt_tokens + retry_outcome.prompt_tokens,
            completion_tokens=outcome.completion_tokens + retry_outcome.completion_tokens,
            exact=outcome.exact and retry_outcome.exact,
            learned_selectors=retry_outcome.learned_selectors or outcome.learned_selectors,
        )
        validated, err = validate(outcome.data, schema)

    if outcome.data is None or err is not None:
        return ExtractResult(
            data=None, status="failed", source="llm",
            prompt_tokens=outcome.prompt_tokens, completion_tokens=outcome.completion_tokens,
            cost_exact=outcome.exact, error=outcome.error or err, attempts=attempts,
        )

    if store is not None:
        try_learn(store, domain, url, html, validated, outcome.learned_selectors, storage_file=selector_storage_file)
        if cache_key:
            store.put_extract(cache_key, validated)

    status = "partial" if (was_truncated or healed) else "ok"
    return ExtractResult(
        data=validated, status=status, source="llm",
        prompt_tokens=outcome.prompt_tokens, completion_tokens=outcome.completion_tokens,
        cost_exact=outcome.exact, attempts=attempts,
    )
