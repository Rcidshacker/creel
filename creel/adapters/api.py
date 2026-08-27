"""HTTP API adapter — built on Starlette, not FastAPI: FastAPI isn't
installed in this environment, and Starlette IS, as an existing transitive
dependency of `mcp`. It covers everything this thin adapter needs, so
there's no reason to add a new dependency for something already present.

Two routes:
  GET /scrape         one JSON response, format-negotiated via `include`
  GET /scrape/stream   SSE, emits each Attempt as it happens

Streaming exists because of gotcha 14: pool wait + a stealth escalation +
an LLM call can exceed a client's default 30-60s timeout. Streaming solves
that with no job store, at the cost of the client having to consume SSE.

Format negotiation: ?include=data,markdown,html (default: data,markdown —
html is opt-in, since dumping raw HTML makes 10 MB payloads routine).
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Callable, Optional

from sse_starlette.sse import EventSourceResponse
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

_WEB_INDEX = Path(__file__).parent / "web" / "index.html"

from creel.core.events import AttemptFinished, AttemptStarted, EventBus
from creel.core.orchestrator import CooldownActive, Orchestrator, ScrapeResult
from creel.core.prune import prune_html
from creel.extract import pipeline as extract_pipeline

_DEFAULT_INCLUDE = "data,markdown"


def _parse_include(request: Request) -> set[str]:
    return set(request.query_params.get("include", _DEFAULT_INCLUDE).split(","))


def _result_payload(result: ScrapeResult, include: set[str]) -> dict:
    payload = {
        "status": result.status,
        "engine_path": result.engine_path,
        "final_url": result.final_url,
        "from_cache": result.from_cache,
    }
    if "markdown" in include:
        payload["markdown"] = prune_html(result.html) if result.html else None
    if "html" in include:
        payload["html"] = result.html
    if "data" in include:
        payload["data"] = result.data
    return payload


async def _maybe_extract(
    orch: Orchestrator,
    url: str,
    result: ScrapeResult,
    prompt: Optional[str],
    llm_extract: Optional[Callable],
    model: str,
    model_tokens: int,
) -> ScrapeResult:
    if not (prompt and result.html and llm_extract is not None):
        return result
    extract_result = await extract_pipeline.extract(
        url, result.html, prompt, store=orch.store, llm_extract=llm_extract, model=model, model_tokens=model_tokens
    )
    result.data = extract_result.data
    return result


def create_app(
    orchestrator: Optional[Orchestrator] = None,
    llm_extract: Optional[Callable] = None,
    model: str = "unset",
    model_tokens: int = 8192,
) -> Starlette:
    orch = orchestrator or Orchestrator()

    async def index(request: Request) -> HTMLResponse:
        return HTMLResponse(_WEB_INDEX.read_text(encoding="utf-8"))

    async def scrape(request: Request) -> JSONResponse:
        url = request.query_params.get("url")
        if not url:
            return JSONResponse({"error": "missing url"}, status_code=400)
        cost_mode = request.query_params.get("cost_mode", "frugal")
        prompt = request.query_params.get("prompt")
        include = _parse_include(request)

        try:
            result = await orch.fetch(url, cost_mode=cost_mode)
        except CooldownActive as e:
            return JSONResponse({"status": "failed", "error": str(e)}, status_code=429)

        result = await _maybe_extract(orch, url, result, prompt, llm_extract, model, model_tokens)
        return JSONResponse(_result_payload(result, include))

    async def scrape_stream(request: Request) -> EventSourceResponse:
        url = request.query_params.get("url")
        cost_mode = request.query_params.get("cost_mode", "frugal")
        prompt = request.query_params.get("prompt")
        include = _parse_include(request)

        async def event_generator():
            queue: asyncio.Queue = asyncio.Queue()
            bus = EventBus()

            def on_event(event) -> None:
                if isinstance(event, AttemptStarted):
                    queue.put_nowait(("attempt_started", {"engine": event.engine}))
                elif isinstance(event, AttemptFinished):
                    queue.put_nowait(
                        (
                            "attempt_finished",
                            {
                                "engine": event.engine,
                                "status": event.status,
                                "duration_ms": event.duration_ms,
                                "failure_class": event.failure_class,
                            },
                        )
                    )

            bus.subscribe(on_event)
            child = orch.with_events(bus)  # isolated bus; shared pool/breaker/cooldowns/memory/store

            async def run() -> None:
                try:
                    result = await child.fetch(url, cost_mode=cost_mode)
                    result = await _maybe_extract(child, url, result, prompt, llm_extract, model, model_tokens)
                    queue.put_nowait(("done", _result_payload(result, include)))
                except CooldownActive as e:
                    queue.put_nowait(("failed", {"error": str(e)}))
                finally:
                    queue.put_nowait(None)  # sentinel

            task = asyncio.create_task(run())
            try:
                while True:
                    item = await queue.get()
                    if item is None:
                        break
                    event_name, data = item
                    yield {"event": event_name, "data": json.dumps(data)}
            finally:
                await task

        return EventSourceResponse(event_generator())

    return Starlette(
        routes=[Route("/", index), Route("/scrape", scrape), Route("/scrape/stream", scrape_stream)]
    )


def main(argv: Optional[list[str]] = None) -> int:
    import argparse

    import uvicorn

    from creel.core.env import load_dotenv
    from creel.extract.llm_direct import ProviderConfig, extract as llm_direct_extract

    load_dotenv()

    parser = argparse.ArgumentParser(prog="creel-web")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)

    provider_config = ProviderConfig.from_env()
    llm_extract = None
    model = "unset"
    model_tokens = 8192
    if provider_config is not None:
        async def llm_extract(html, prompt, schema=None, _cfg=provider_config):
            return await llm_direct_extract(html, prompt, schema=schema, config=_cfg)
        model = provider_config.model
        model_tokens = provider_config.model_tokens

    app = create_app(llm_extract=llm_extract, model=model, model_tokens=model_tokens)
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
