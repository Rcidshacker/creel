"""Site URL enumeration — a discovery verb, not a fetch, so it does not go
through core.orchestrator's acquisition ladder or its failure classes.

Firecrawl's map() is primary: cheap, credit-metered, purpose-built. Falls
back to sitemap.xml (one request, structured, free) and then to crawling
links off the page itself — capped at `limit`, since an uncapped link crawl
on a sitemap-less site never ends.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlsplit, urlunsplit


@dataclass
class MapResult:
    urls: list[str]
    source: str  # "firecrawl" | "sitemap" | "links"
    truncated: bool = False


async def map_site(url: str, api_key: Optional[str] = None, limit: int = 100) -> MapResult:
    if api_key:
        return await _map_via_firecrawl(url, api_key, limit)
    return await _map_via_fallback(url, limit)


async def _map_via_firecrawl(url: str, api_key: str, limit: int) -> MapResult:
    from creel.engines.firecrawl import client

    async with client(api_key) as c:
        data = await c.map(url, limit=limit)
    urls = [link.url for link in data.links]
    return MapResult(urls=urls, source="firecrawl", truncated=len(urls) >= limit)


async def _map_via_fallback(url: str, limit: int) -> MapResult:
    from creel.engines import scrapling_http

    sitemap_outcome = await scrapling_http.fetch(_sitemap_url(url))
    if sitemap_outcome.status == 200:
        urls = _parse_sitemap_urls(sitemap_outcome.body)[:limit]
        if urls:
            return MapResult(urls=urls, source="sitemap", truncated=len(urls) >= limit)

    page_outcome = await scrapling_http.fetch(url)
    if page_outcome.status == 200:
        urls = _extract_links(page_outcome.body, url)[:limit]
        return MapResult(urls=urls, source="links", truncated=len(urls) >= limit)

    return MapResult(urls=[], source="links", truncated=False)


def _sitemap_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, "/sitemap.xml", "", ""))


def _parse_sitemap_urls(body: bytes) -> list[str]:
    text = body.decode("utf-8", errors="ignore")
    return re.findall(r"<loc>\s*(.*?)\s*</loc>", text)


def _extract_links(body: bytes, base_url: str) -> list[str]:
    from scrapling.parser import Selector

    root = Selector(content=body.decode("utf-8", errors="ignore"), url=base_url)
    links: list[str] = []
    for a in root.css("a"):
        href = a.attrib.get("href")
        if href:
            links.append(root.urljoin(href))
    return links
