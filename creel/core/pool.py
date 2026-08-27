"""Concurrency bounds. Split semaphores because HTTP fetchers are
high-concurrency/low-memory and browser fetchers are the opposite — routing
both through one limit either starves HTTP or lets a browser-tier burst OOM
the machine (50 concurrent JS_REQUIRED classifications naively spawning 50
Chromium instances is exactly the failure mode this exists to prevent).

Per-domain limiting is a politeness floor, not throughput tuning: parallel
requests to one domain are what manufacture the blocks the ladder then pays
to defeat.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict


class ConcurrencyPool:
    def __init__(self, http: int = 20, browser: int = 3, per_domain: int = 2) -> None:
        self._http_sem = asyncio.Semaphore(http)
        self._browser_sem = asyncio.Semaphore(browser)
        self._per_domain = per_domain
        self._domain_sems: dict[str, asyncio.Semaphore] = defaultdict(lambda: asyncio.Semaphore(per_domain))

    def acquire(self, domain: str, needs_browser: bool) -> "_Guard":
        return _Guard(self, domain, needs_browser)


class _Guard:
    def __init__(self, pool: ConcurrencyPool, domain: str, needs_browser: bool) -> None:
        self._domain_sem = pool._domain_sems[domain]
        self._tier_sem = pool._browser_sem if needs_browser else pool._http_sem

    async def __aenter__(self) -> "_Guard":
        # Per-domain slot first, then the tier slot — a domain waiting on its
        # own politeness limit should never be holding a scarce browser slot
        # while it waits.
        await self._domain_sem.acquire()
        await self._tier_sem.acquire()
        return self

    async def __aexit__(self, *exc) -> None:
        self._tier_sem.release()
        self._domain_sem.release()
