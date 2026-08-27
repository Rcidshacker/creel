"""Tier 3: async stealth via Scrapling's StealthyFetcher (fingerprint
spoofing, Cloudflare Turnstile/Interstitial bypass).

Sniffs for the "solved challenge but landed on an error page" case: a 2xx
status with Cloudflare-error body markers still means the fetch failed and
must re-escalate, not celebrate a solver that fired but didn't actually get
through. That distinction lives in `signals`, not `status` — classify()
reads both.
"""
from __future__ import annotations

import time
from typing import Optional

from creel.core.guard import GuardConfig, preflight
from creel.core.models import ExecutionModel, FetchOutcome

NAME = "scrapling_stealth"
TIER = 3
NEEDS_BROWSER = True
EXECUTION_MODEL = ExecutionModel.ASYNC

_CF_ERROR_MARKERS = ("checking your browser", "cloudflare ray id", "access denied")


async def fetch(
    url: str,
    timeout_ms: int = 60000,  # solve_cloudflare internally raises Scrapling's own timeout to 60s
    guard_config: Optional[GuardConfig] = None,
    solve_cloudflare: bool = True,
) -> FetchOutcome:
    from scrapling.fetchers import StealthyFetcher

    preflight(url, guard_config)
    start = time.monotonic()
    try:
        response = await StealthyFetcher.async_fetch(
            url, headless=True, timeout=timeout_ms, solve_cloudflare=solve_cloudflare
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

    body = bytes(response.body)
    signals: list[str] = []
    if response.status and response.status < 400:
        text_lower = body.decode("utf-8", errors="ignore").lower()
        if any(marker in text_lower for marker in _CF_ERROR_MARKERS):
            signals.append("cf_error_page")

    return FetchOutcome(
        status=response.status,
        headers=dict(response.headers),
        body=body,
        final_url=response.url,
        solver_engaged=solve_cloudflare,
        signals=signals,
        elapsed_ms=int((time.monotonic() - start) * 1000),
    )
