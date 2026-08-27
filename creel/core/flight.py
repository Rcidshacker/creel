"""In-flight request de-duplication keyed on canonical URL.

The fetch cache prevents repeat fetches over TIME. It does nothing for
concurrency: two simultaneous misses for the same URL would otherwise
double-launch browsers and double-spend Firecrawl credits. This closes that
gap — ten lines, no external dependency.
"""
from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, TypeVar

T = TypeVar("T")


class SingleFlight:
    def __init__(self) -> None:
        self._inflight: dict[str, asyncio.Future] = {}

    async def run(self, key: str, coro_fn: Callable[[], Awaitable[T]]) -> T:
        existing = self._inflight.get(key)
        if existing is not None:
            return await existing

        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._inflight[key] = fut
        try:
            result = await coro_fn()
        except Exception as e:
            fut.set_exception(e)
            fut.exception()  # mark retrieved so asyncio never logs "exception was never retrieved"
            raise
        else:
            fut.set_result(result)
            return result
        finally:
            self._inflight.pop(key, None)
