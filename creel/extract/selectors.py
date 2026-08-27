"""Deterministic extraction from a selector map — tier 1 of the extraction
ladder, free. Two modes:

- Plain CSS match (the common case).
- Adaptive fallback: if the literal selector misses, retry with Scrapling's
  adaptive relocation (needs a prior auto_save'd baseline — see
  extract/learn.py). Empirically verified: Scrapling's adaptive matching
  finds a previously-tagged element across two DIFFERENT HTML documents
  purely by similarity, even when the class name and DOM position both
  changed. A successful adaptive match means the page drifted since the
  selector was learned, so the caller must mark this "partial" — a
  semantically wrong match could pass silently otherwise.

A partial hit (some fields match, others don't) is not returned as-is — ANY
missing field means the whole call returns None, so the caller falls
through to the next extraction rung rather than caching an incomplete
answer as if it were the whole record.

Empirically discovered corollary: adaptive relocation is scoped by
`identifier`, not by the literal CSS string. Once a baseline exists for a
field name (established the first time that field was learned), even a
WRONG/corrupted selector string for that SAME field name will often still
resolve correctly, because the adaptive retry ignores the literal string
and relocates via similarity to the saved baseline element. This makes the
free tier more resilient than a naive string-keyed cache — but it also
means a genuinely broken cache entry only shows up as a real miss when the
field name itself has no prior baseline (e.g. the whole selector map was
corrupted to different field names), not merely when its CSS string changes.

Storage location: Scrapling's own default writes its adaptive SQLite DB
inside site-packages/scrapling/ (confirmed by reading its source) — the
wrong place for a library consumer's persistent state; it doesn't survive a
Scrapling reinstall/upgrade cleanly and isn't scoped to this project. Every
call here takes an explicit `storage_file`, which pipeline.py derives as a
sibling of Creel's own store.db. `url` is also threaded through — Scrapling
uses it to scope adaptive matches per-site, so two different sites reusing
the same field name (e.g. "title") don't collide in one shared store.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class SelectorExtractResult:
    data: Optional[dict]
    healed: bool = False  # True if ANY field required adaptive relocation to match


def _storage_args(storage_file: Optional[str], url: str) -> Optional[dict]:
    return {"storage_file": storage_file, "url": url} if storage_file else None


def extract_with_selectors(
    html: str,
    url: str,
    selectors: dict[str, str],
    allow_adaptive: bool = True,
    storage_file: Optional[str] = None,
) -> SelectorExtractResult:
    from scrapling.parser import Selector

    root = Selector(
        content=html, url=url, adaptive=allow_adaptive, storage_args=_storage_args(storage_file, url)
    )
    result: dict[str, str] = {}
    healed = False

    for field_name, css in selectors.items():
        matches = root.css(css)
        first = matches.first if matches else None

        if first is None and allow_adaptive:
            adaptive_matches = root.css(css, identifier=field_name, adaptive=True)
            first = adaptive_matches.first if adaptive_matches else None
            if first is not None:
                healed = True

        if first is None:
            return SelectorExtractResult(data=None, healed=False)

        result[field_name] = str(first.text).strip()

    return SelectorExtractResult(data=result, healed=healed)


def establish_adaptive_baseline(
    html: str, url: str, selectors: dict[str, str], storage_file: Optional[str] = None
) -> None:
    """Call once, on the page where a selector was just learned/validated —
    saves a baseline snapshot per field so a later page can adaptively
    relocate the element if the DOM drifts."""
    from scrapling.parser import Selector

    root = Selector(content=html, url=url, adaptive=True, storage_args=_storage_args(storage_file, url))
    for field_name, css in selectors.items():
        root.css(css, identifier=field_name, auto_save=True)
