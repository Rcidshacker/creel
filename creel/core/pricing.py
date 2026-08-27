"""Local price table for LLM cost computation. We compute USD ourselves,
never trust a framework's internal accounting — Scrapegraph-ai's
get_execution_info() depends on the provider echoing token usage, which
varies by provider and is often missing entirely for small calls.

Unknown models price at $0.00. That is a known, deliberate limitation, not
a claim the call was free — Cost.exact is always False in that case, and
callers must render an inexact $0.00 differently from a measured one.
Extend _PRICES as new models get used; this is a config file, not a
pricing API.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelPrice:
    input_per_1k: float
    output_per_1k: float


# USD per 1K tokens, as of authoring. Update when providers change pricing.
_PRICES: dict[str, ModelPrice] = {
    "openai/gpt-4o-mini": ModelPrice(0.00015, 0.0006),
    "openai/gpt-4o": ModelPrice(0.0025, 0.01),
    "anthropic/claude-3-5-sonnet": ModelPrice(0.003, 0.015),
    "anthropic/claude-3-5-haiku": ModelPrice(0.0008, 0.004),
    "ollama/llama3.2": ModelPrice(0.0, 0.0),  # local inference — genuinely free
}

_UNKNOWN = ModelPrice(0.0, 0.0)


def price_for(model: str) -> ModelPrice:
    return _PRICES.get(model, _UNKNOWN)


def compute_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    price = price_for(model)
    return (prompt_tokens / 1000) * price.input_per_1k + (completion_tokens / 1000) * price.output_per_1k


def is_known_model(model: str) -> bool:
    return model in _PRICES
