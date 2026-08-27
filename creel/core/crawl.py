"""Bulk crawl, seeded by discover.map_site(). Uses Scrapling's OWN Spider
framework directly, not core.orchestrator's ladder: a Spider owns its own
concurrency, throttle, robots compliance, and blocked-request retry — all
already built into the base class (robots_txt_obey, autothrottle_enabled,
concurrent_requests, is_blocked/retry_blocked_request — confirmed directly
by inspection and a live smoke test against the fixture server). Re-deriving
equivalent politeness logic against core.orchestrator's different
concurrency model would just be a worse copy of what Scrapling already does.

Per-page structured extraction reuses extract/pipeline.py — the same
self-teaching selector-learning ladder Phase 2 built, so a bulk crawl of N
structurally-identical pages costs roughly one LLM call, same as a loop of
individual Orchestrator.fetch() calls would.

Verified directly (not assumed) before writing this module:
  - Spider.start() internally calls anyio.run() and OWNS the event loop —
    it cannot be awaited from inside an already-running asyncio loop
    (raises "Already running asyncio in this thread"). Spider.stream() is
    the API for use from within existing async code — confirmed working
    live against the fixture server.
  - A request that hits Scrapling's own BLOCKED_CODES exhausts its retries
    internally and never reaches parse() at all — it simply never appears
    in stream()'s output. We reconcile the seed list against what actually
    streamed back so a dropped URL still shows up as "failed" in the
    result, rather than silently vanishing.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Type

from pydantic import BaseModel

from creel.core.store import Store
from creel.discover.map import map_site
from creel.extract import pipeline as extract_pipeline


@dataclass
class CrawlItemResult:
    url: str
    status: str  # "ok" | "failed"
    data: Optional[dict] = None


async def crawl_site(
    url: str,
    prompt: Optional[str] = None,
    schema: Optional[Type[BaseModel]] = None,
    store: Optional[Store] = None,
    llm_extract: Optional[Callable] = None,
    model: str = "unset",
    model_tokens: int = 8192,
    map_limit: int = 100,
    firecrawl_api_key: Optional[str] = None,
    robots_txt_obey: bool = True,
    concurrent_requests: int = 8,
) -> list[CrawlItemResult]:
    from scrapling.spiders import Spider
    from scrapling.spiders.request import Request

    mapped = await map_site(url, api_key=firecrawl_api_key, limit=map_limit)
    seed_urls = mapped.urls
    if not seed_urls:
        return []

    class _CreelCrawlSpider(Spider):
        name = "creel_crawl"

        async def start_requests(self):
            for seed_url in seed_urls:
                yield Request(seed_url, callback=self.parse)

        async def parse(self, response):
            html = response.body.decode("utf-8", errors="ignore")
            ok = response.status is not None and 200 <= response.status < 300
            data = None
            if ok and prompt and llm_extract is not None:
                extract_result = await extract_pipeline.extract(
                    response.url,
                    html,
                    prompt,
                    schema=schema,
                    store=store,
                    llm_extract=llm_extract,
                    model=model,
                    model_tokens=model_tokens,
                )
                data = extract_result.data
            yield {"url": response.url, "status": "ok" if ok else "failed", "data": data}

    spider = _CreelCrawlSpider()
    spider.robots_txt_obey = robots_txt_obey
    spider.concurrent_requests = concurrent_requests

    scraped: dict[str, CrawlItemResult] = {}
    async for item in spider.stream():
        scraped[item["url"]] = CrawlItemResult(url=item["url"], status=item["status"], data=item["data"])

    results: list[CrawlItemResult] = []
    for seed_url in seed_urls:
        results.append(scraped.get(seed_url, CrawlItemResult(url=seed_url, status="failed", data=None)))
    return results
