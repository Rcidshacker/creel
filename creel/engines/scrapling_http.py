"""Tier 1: async HTTP via Scrapling's curl_cffi-backed AsyncFetcher.

Never raises on non-2xx (gotcha 6) — status is inspected explicitly by
core.classify, never trusted implicitly. Timeout unit is SECONDS here
(gotcha 5); browser tiers use milliseconds — do not copy this constant
into those modules.

The vendor import is lazy: importing scrapling itself is cheap (confirmed
0.01s in the Phase 0 spike), but this keeps the boundary consistent with
scrapegraphai's engine, which pays 16s if imported eagerly.
"""
from __future__ import annotations

import time
from typing import Optional

from creel.core.guard import GuardConfig, preflight
from creel.core.models import ExecutionModel, FetchOutcome

NAME = "scrapling_http"
TIER = 1
NEEDS_BROWSER = False
EXECUTION_MODEL = ExecutionModel.ASYNC


async def fetch(
    url: str,
    timeout_s: float = 30.0,
    guard_config: Optional[GuardConfig] = None,
    impersonate: str = "chrome",
) -> FetchOutcome:
    from scrapling.fetchers import AsyncFetcher

    preflight(url, guard_config)  # raises SSRFError — must not be swallowed below
    start = time.monotonic()
    try:
        response = await AsyncFetcher.get(
            url, timeout=timeout_s, impersonate=impersonate, follow_redirects="safe"
        )
    except Exception as e:
        return FetchOutcome(
            status=None,
            headers={},
            body=b"",
            final_url=url,
            signals=[f"exception:{type(e).__name__}"],
            elapsed_ms=int((time.monotonic() - start) * 1000),
        )
    return FetchOutcome(
        status=response.status,
        headers=dict(response.headers),
        body=bytes(response.body),
        final_url=response.url,
        redirect_chain=[str(h) for h in (response.history or [])],
        elapsed_ms=int((time.monotonic() - start) * 1000),
    )
