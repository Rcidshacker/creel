"""Firecrawl remote-egress engine, plus native PDF handling.

`firecrawl.v2.AsyncFirecrawlClient` is genuinely async under the hood —
verified directly by reading its HTTP client source, which wraps
`httpx.AsyncClient` — so no thread-wrapping needed here, unlike
Scrapegraph-ai.

IMPORTANT, verified by reading `firecrawl.v2.methods.scrape.scrape`'s
source: `scrape()` RAISES on failure rather than returning an error-shaped
Document. The exception is `FirecrawlError`, which carries the real HTTP
`status_code` and the raw response — we translate that into an accurate
FetchOutcome instead of collapsing everything into a generic NETWORK
failure the way a bare `except Exception` would.

The top-level `firecrawl.Firecrawl` / `firecrawl.AsyncFirecrawl` classes in
the installed package version (4.40.0) are NOT the scrape/map/search
client — they're an unrelated academic-paper/GitHub-search surface. The
real client lives at `firecrawl.v2.AsyncFirecrawlClient`. Confirmed by
inspecting both directly; the public README (fetched from the `main`
branch) describes a newer surface than what's on PyPI.
"""
from __future__ import annotations

import time
from typing import Optional

from creel.core.models import ExecutionModel, FetchOutcome

NAME = "firecrawl"
TIER = 5  # remote egress, alongside jina — relative order is a cost_mode choice
NEEDS_BROWSER = False
EXECUTION_MODEL = ExecutionModel.ASYNC


def available(api_key: Optional[str]) -> bool:
    return bool(api_key)


async def fetch(
    url: str,
    api_key: Optional[str] = None,
    timeout_s: float = 60.0,
    formats: tuple = ("markdown", "html"),
    only_main_content: bool = False,
    guard_config=None,  # accepted for call-signature parity with local engines; unused — Firecrawl fetches from its own network, not ours
) -> FetchOutcome:
    if not available(api_key):
        return FetchOutcome(status=None, headers={}, body=b"", final_url=url, signals=["exception:NoAPIKey"])

    from firecrawl.v2 import AsyncFirecrawlClient
    from firecrawl.v2.utils.error_handler import FirecrawlError

    start = time.monotonic()
    client = AsyncFirecrawlClient(api_key=api_key, timeout=timeout_s)
    try:
        doc = await client.scrape(
            url, formats=list(formats), only_main_content=only_main_content, timeout=int(timeout_s * 1000)
        )
    except FirecrawlError as e:
        headers = dict(e.response.headers) if e.response is not None else {}
        return FetchOutcome(
            status=e.status_code,
            headers=headers,
            body=str(e).encode("utf-8", errors="ignore"),
            final_url=url,
            signals=[f"firecrawl_error:{type(e).__name__}"],
            elapsed_ms=int((time.monotonic() - start) * 1000),
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
    finally:
        await client.close()

    meta = doc.metadata
    status = (meta.status_code if meta else None) or 200
    html = doc.html or doc.raw_html or ""
    markdown = doc.markdown or ""
    body = (html or markdown).encode("utf-8", errors="ignore")
    signals = [f"firecrawl_meta_error:{meta.error}"] if meta and meta.error else []

    return FetchOutcome(
        status=status,
        headers={},
        body=body,
        final_url=(meta.url if meta and meta.url else url),
        signals=signals,
        elapsed_ms=int((time.monotonic() - start) * 1000),
    )
