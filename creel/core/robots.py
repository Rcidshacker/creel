"""robots.txt enforcement via protego (already a Scrapling dependency — no
new library for something already installed). Gates the STEALTH tier
specifically: solve_cloudflare=True is an explicit anti-bot bypass, and it
must never fire against a host that explicitly asked automated agents not
to access a path. Cached per origin so a bulk crawl doesn't refetch
robots.txt on every page.

Fails OPEN: an unreachable or unparseable robots.txt does not block
crawling — this matches Scrapling's own robots_txt_obey semantics, and a
broken robots.txt is far more common in practice than a deliberate
disallow.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Optional
from urllib.parse import urlsplit, urlunsplit

_DEFAULT_USER_AGENT = "creel"
_CACHE_TTL_S = 3600.0


@dataclass
class _CacheEntry:
    parser: Optional[object]  # protego.Protego, or None if unreachable/unparseable
    cached_at: float


class RobotsChecker:
    def __init__(self, user_agent: str = _DEFAULT_USER_AGENT, ttl_s: float = _CACHE_TTL_S) -> None:
        self._user_agent = user_agent
        self._ttl_s = ttl_s
        self._cache: dict[str, _CacheEntry] = {}

    async def allowed(self, url: str, fetch_fn: Optional[Callable] = None) -> bool:
        parser = await self._get_parser(url, fetch_fn)
        if parser is None:
            return True
        return parser.can_fetch(url, self._user_agent)

    async def _get_parser(self, url: str, fetch_fn: Optional[Callable]):
        parts = urlsplit(url)
        origin = urlunsplit((parts.scheme, parts.netloc, "", "", ""))
        entry = self._cache.get(origin)
        if entry is not None and time.time() - entry.cached_at < self._ttl_s:
            return entry.parser

        robots_url = urlunsplit((parts.scheme, parts.netloc, "/robots.txt", "", ""))
        if fetch_fn is None:
            from creel.engines import scrapling_http

            fetch_fn = scrapling_http.fetch

        parser = None
        try:
            outcome = await fetch_fn(robots_url)
            if outcome.status == 200:
                from protego import Protego

                parser = Protego.parse(outcome.body.decode("utf-8", errors="ignore"))
        except Exception:
            parser = None

        self._cache[origin] = _CacheEntry(parser=parser, cached_at=time.time())
        return parser
