"""The Extractor shape both llm_sgai and llm_direct satisfy. Not a strict
typing.Protocol with a fixed positional signature — the two implementations
take different provider-config shapes (SGAIConfig vs ProviderConfig), so the
contract is duck-typed: an async `extract(html, prompt, schema=None,
config=...)` returning ExtractOutcome. extract/pipeline.py is the only
caller and treats both uniformly via a partial-applied callable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ExtractOutcome:
    data: Optional[dict]
    prompt_tokens: int = 0
    completion_tokens: int = 0
    exact: bool = False  # False = estimated via tiktoken, not measured from the API response
    error: Optional[str] = None  # gotcha 3: SGAI writes {"error": ...} into `answer` instead of raising
    learned_selectors: dict[str, str] = field(default_factory=dict)  # field name -> candidate CSS selector
