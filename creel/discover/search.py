"""Web search — a discovery verb, not a fetch. Firecrawl's search() is
primary (returns results, optionally with scraped content, in one credit-
metered call). Falls back to `ddgs` (already a Scrapegraph-ai dependency —
no new library for something already installed), run in a thread since its
`DDGS.text()` is synchronous.

`ddgs` is the renamed `duckduckgo_search` package — result dicts use keys
`title`/`href`/`body`, verified directly against its DuckDuckGo backend
source, not assumed from memory.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Optional


@dataclass
class SearchResult:
    url: str
    title: str
    snippet: str


@dataclass
class SearchResponse:
    results: list[SearchResult]
    source: str  # "firecrawl" | "ddgs"


async def search(query: str, api_key: Optional[str] = None, limit: int = 10) -> SearchResponse:
    if api_key:
        return await _search_via_firecrawl(query, api_key, limit)
    return await _search_via_ddgs(query, limit)


async def _search_via_firecrawl(query: str, api_key: str, limit: int) -> SearchResponse:
    from creel.engines.firecrawl import client

    async with client(api_key) as c:
        data = await c.search(query, limit=limit)
    web = data.web or []
    results = [SearchResult(url=r.url, title=r.title or "", snippet=r.description or "") for r in web]
    return SearchResponse(results=results, source="firecrawl")


async def _search_via_ddgs(query: str, limit: int) -> SearchResponse:
    def _run() -> list[dict]:
        from ddgs import DDGS

        with DDGS() as ddgs:
            return list(ddgs.text(query, max_results=limit))

    raw = await asyncio.to_thread(_run)
    results = [SearchResult(url=r.get("href", ""), title=r.get("title", ""), snippet=r.get("body", "")) for r in raw]
    return SearchResponse(results=results, source="ddgs")
