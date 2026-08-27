"""Tier 2: async browser via Scrapling's Playwright-backed DynamicFetcher.

Timeout unit is MILLISECONDS here (gotcha 5) — HTTP tier uses seconds.
One-shot async_fetch() opens and closes a browser per call; core.pool
(Phase 1b) is what bounds concurrent browser launches, not this module.
"""
from __future__ import annotations

import time
from typing import Optional

from creel.core.guard import GuardConfig, preflight
from creel.core.models import ExecutionModel, FetchOutcome

NAME = "scrapling_dynamic"
TIER = 2
NEEDS_BROWSER = True
EXECUTION_MODEL = ExecutionModel.ASYNC


async def fetch(
    url: str,
    timeout_ms: int = 30000,
    guard_config: Optional[GuardConfig] = None,
    network_idle: bool = False,
) -> FetchOutcome:
    from scrapling.fetchers import DynamicFetcher

    preflight(url, guard_config)
    start = time.monotonic()
    try:
        response = await DynamicFetcher.async_fetch(
            url, headless=True, timeout=timeout_ms, network_idle=network_idle
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
        elapsed_ms=int((time.monotonic() - start) * 1000),
    )
