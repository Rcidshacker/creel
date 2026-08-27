"""Tier 4 (remote egress): our own async Jina Reader client via httpx.

Deliberately NOT a wrapper around Agent-Reach's web.py — that implementation
uses blocking urllib (agent_reach/channels/web.py:51), which would stall the
event loop, and Agent-Reach is a solo-maintained, Linux-first dependency with
recent Windows-specific crashes. A 15-line httpx GET removes that dependency
from the hot path entirely; Agent-Reach stays scoped to credentials and
platform-CLI capability (Phase 4).

No SSRF preflight here — Jina fetches from ITS OWN network, which is the
whole point of this rung existing (a different egress IP than ours). Their
5 MiB response cap is mirrored defensively; we do not rely on them enforcing it.
"""
from __future__ import annotations

import time
from typing import Optional

from creel.core.models import ExecutionModel, FetchOutcome

NAME = "jina"
TIER = 4
NEEDS_BROWSER = False
EXECUTION_MODEL = ExecutionModel.ASYNC

_BASE = "https://r.jina.ai/"
_MAX_BYTES = 5 * 1024 * 1024


async def fetch(url: str, timeout_s: float = 30.0, api_key: Optional[str] = None) -> FetchOutcome:
    import httpx

    start = time.monotonic()
    headers = {"Accept": "text/plain"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            response = await client.get(_BASE + url, headers=headers)
    except Exception as e:
        return FetchOutcome(
            status=None,
            headers={},
            body=b"",
            final_url=url,
            signals=[f"exception:{type(e).__name__}"],
            elapsed_ms=int((time.monotonic() - start) * 1000),
        )
    body = response.content[:_MAX_BYTES]
    return FetchOutcome(
        status=response.status_code,
        headers=dict(response.headers),
        body=body,
        final_url=url,  # identity is the ORIGINAL url — Jina is a proxy, not the target
        elapsed_ms=int((time.monotonic() - start) * 1000),
    )
