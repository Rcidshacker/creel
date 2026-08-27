"""MCP adapter. One server, two tools:

  fetch(url, escalate=False) -- fast path, bypasses the orchestrator
      entirely and hits tier 1 directly. For "just get me this page" calls
      where the full fallback ladder is more machinery than the request
      needs. escalate=True runs the full ladder instead.
  scrape(url, prompt=None, cost_mode="frugal") -- always runs the full
      ladder, and optionally extracts structured data when `prompt` is
      given and an LLM extractor is configured.

Both return DERIVED data — markdown, or extracted `data` — never raw HTML
by default, size-capped with an explicit truncation flag. An agent's
context window is a scarcer budget than any MCP transport limit. Full HTML
is available on request via a `creel://html/{resource_id}` resource.

Note: `scrapling mcp` (Scrapling's own bundled MCP server) already exists
for anyone who wants raw single-engine access without any of this — this
server is specifically for the fallback-aware, multi-engine path.
"""
from __future__ import annotations

import time
import uuid
from typing import Callable, Optional

from mcp.server.mcpserver import MCPServer

from creel.core.orchestrator import CooldownActive, Orchestrator
from creel.core.prune import prune_html
from creel.extract import pipeline as extract_pipeline

_MAX_MARKDOWN_CHARS = 20_000
_HTML_TTL_S = 3600.0
_MAX_HTML_ENTRIES = 200


class HtmlStore:
    """In-memory, TTL'd, size-bounded. A convenience cache for "give me the
    full page" follow-ups within one session — not a persistent store;
    core.store.Store already owns that job."""

    def __init__(self, ttl_s: float = _HTML_TTL_S, max_entries: int = _MAX_HTML_ENTRIES) -> None:
        self._ttl_s = ttl_s
        self._max_entries = max_entries
        self._entries: dict[str, tuple[float, str]] = {}

    def put(self, html: str) -> str:
        self._evict_oldest_if_full()
        resource_id = uuid.uuid4().hex
        self._entries[resource_id] = (time.time(), html)
        return resource_id

    def get(self, resource_id: str) -> Optional[str]:
        entry = self._entries.get(resource_id)
        if entry is None:
            return None
        stored_at, html = entry
        if time.time() - stored_at > self._ttl_s:
            del self._entries[resource_id]
            return None
        return html

    def _evict_oldest_if_full(self) -> None:
        if len(self._entries) < self._max_entries:
            return
        overflow = len(self._entries) - self._max_entries + 1
        oldest_keys = sorted(self._entries, key=lambda k: self._entries[k][0])[:overflow]
        for k in oldest_keys:
            del self._entries[k]


def _truncate(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[:limit], True


def _result_to_dict(result, html_store: HtmlStore) -> dict:
    markdown = prune_html(result.html) if result.html else ""
    truncated_md, was_truncated = _truncate(markdown, _MAX_MARKDOWN_CHARS)
    resource_id = html_store.put(result.html) if result.html else None
    return {
        "status": result.status,
        "engine_path": result.engine_path,
        "final_url": result.final_url,
        "from_cache": result.from_cache,
        "markdown": truncated_md,
        "truncated": was_truncated,
        "html_resource_id": resource_id,
    }


def create_server(
    orchestrator: Optional[Orchestrator] = None,
    llm_extract: Optional[Callable] = None,
    model: str = "unset",
    model_tokens: int = 8192,
) -> MCPServer:
    orch = orchestrator or Orchestrator()
    html_store = HtmlStore()
    mcp = MCPServer("creel", description="Unified scraping interface — acquisition ladder + extraction")

    @mcp.tool(
        description=(
            "Fetch a single URL. By default (escalate=False) uses tier-1 HTTP "
            "only, bypassing the fallback ladder — fast path for pages that "
            "don't need escalation. escalate=True runs the full ladder."
        )
    )
    async def fetch(url: str, escalate: bool = False) -> dict:
        if not escalate:
            from creel.engines import scrapling_http

            outcome = await scrapling_http.fetch(url)
            html = outcome.body.decode("utf-8", errors="ignore")
            markdown = prune_html(html) if html else ""
            truncated_md, was_truncated = _truncate(markdown, _MAX_MARKDOWN_CHARS)
            resource_id = html_store.put(html) if html else None
            ok = outcome.status is not None and 200 <= outcome.status < 300
            return {
                "status": "ok" if ok else "failed",
                "engine_path": ["scrapling_http"],
                "markdown": truncated_md,
                "truncated": was_truncated,
                "html_resource_id": resource_id,
            }

        try:
            result = await orch.fetch(url)
        except CooldownActive as e:
            return {"status": "failed", "error": str(e)}
        return _result_to_dict(result, html_store)

    @mcp.tool(
        description=(
            "Run the full acquisition ladder against a URL, with optional "
            "structured extraction via `prompt`."
        )
    )
    async def scrape(url: str, prompt: Optional[str] = None, cost_mode: str = "frugal") -> dict:
        try:
            result = await orch.fetch(url, cost_mode=cost_mode)
        except CooldownActive as e:
            return {"status": "failed", "error": str(e)}

        data = None
        if prompt and result.html and llm_extract is not None:
            extract_result = await extract_pipeline.extract(
                url,
                result.html,
                prompt,
                store=orch.store,
                llm_extract=llm_extract,
                model=model,
                model_tokens=model_tokens,
            )
            data = extract_result.data

        payload = _result_to_dict(result, html_store)
        payload["data"] = data
        return payload

    @mcp.resource("creel://html/{resource_id}", mime_type="text/html")
    async def read_html(resource_id: str) -> str:
        return html_store.get(resource_id) or ""

    return mcp
