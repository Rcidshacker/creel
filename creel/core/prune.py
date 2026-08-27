"""Boilerplate stripping + hard token-budget enforcement between acquisition
and extraction. Narrowing to main/article HTML by hand-written CSS is
brittle; one miss feeds the LLM 100k tokens of navbar and trips the silent
8192-token truncation (gotcha 4). This module makes that truncation
IMPOSSIBLE to happen silently: enforce_budget() always runs before html
reaches an Extractor, and it always returns whether it had to cut anything.
"""
from __future__ import annotations

import tiktoken

_MIN_USEFUL_MARKDOWN_LEN = 40


def prune_html(html: str, main_content_only: bool = True) -> str:
    """Scrapling's own readability pass (`.markdown()`), already a
    dependency — no new library for something already installed.

    `.markdown()` lives on `Response`, not the base `Selector` (verified
    directly — a bare Selector raises AttributeError). We don't have a real
    fetch response here, just raw HTML, so a minimal Response is
    constructed purely as a vehicle for the method; the placeholder
    url/status/headers are never inspected by `.markdown()` itself.

    ponytail: trafilatura is the documented drop-in upgrade if this
    measurably underperforms on real targets (occasionally drops legitimate
    sidebar content) — same signature, one module, plus a --raw escape
    hatch. Not adopted speculatively.
    """
    from scrapling.engines.toolbelt.custom import Response

    try:
        response = Response(
            url="about:blank", content=html, status=200, reason="OK", cookies={}, headers={}, request_headers={}
        )
        text = response.markdown(main_content_only=main_content_only)
    except Exception:
        text = ""
    if len(text.strip()) < _MIN_USEFUL_MARKDOWN_LEN:
        return html  # readability pass yielded suspiciously little -> fall back to raw
    return text


def _encoding_for(model: str):
    try:
        return tiktoken.encoding_for_model(model)
    except KeyError:
        return tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str, model: str = "gpt-4") -> int:
    return len(_encoding_for(model).encode(text))


def enforce_budget(text: str, model_tokens: int, model: str = "gpt-4") -> tuple[str, bool]:
    """Returns (text, was_truncated). Truncates at a token boundary rather
    than chunking into multiple LLM calls — chunking is the natural upgrade
    if real usage shows information loss matters, but it multiplies calls
    (and cost-accounting complexity) for a case this design hasn't hit yet.
    Never silently exceeds model_tokens; was_truncated tells the caller to
    mark the eventual extraction "partial"."""
    enc = _encoding_for(model)
    tokens = enc.encode(text)
    if len(tokens) <= model_tokens:
        return text, False
    return enc.decode(tokens[:model_tokens]), True
