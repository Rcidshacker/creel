# Creel

A unified scraping interface over four independent tools, wired together by
a **failure-classifying acquisition ladder** instead of a flat
try-everything fallback list. Each failure (rate-limited, blocked, JS
required, auth wall, ...) escalates to a specific next rung — never a
blind retry of everything.

```
1 HTTP fetch → 2 headless browser → 3 stealth browser → 4 remote egress (Jina / Firecrawl)
```

with a self-teaching extraction ladder on top: learned CSS selectors
(free) before an LLM call (paid), promoting validated LLM output back into
the selector cache.

## The building blocks

| Library | Role in Creel |
|---|---|
| **[Scrapling](https://github.com/D4Vinci/Scrapling)** | Acquisition (HTTP / dynamic / stealth tiers) and HTML parsing. Async-native. |
| **[Scrapegraph-ai](https://github.com/ScrapeGraphAI/Scrapegraph-ai)** | LLM structured extraction over HTML already in hand. Never fetches — the one synchronous engine, wrapped via `asyncio.to_thread`. |
| **[Firecrawl](https://github.com/firecrawl/firecrawl)** | Managed egress, `map`, `search`, native PDF→markdown. |
| **[Agent-Reach](https://github.com/Panniantong/Agent-Reach)** | Credential vault + capability check for auth-walled platforms (V2EX, GitHub, ...). |

None of these wraps or replaces another — each covers a gap the others
don't. Design rationale, the full gotcha list, and the phase-by-phase plan
this repo was built from live in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Status

All five phases (0–4) of the original plan are complete. 252 tests
passing, no live third-party host required to run the suite.

| Phase | Delivered |
|---|---|
| 0 | Environment spike, dependency pin, import-cost audit |
| 1a | Core contracts + safety floors (SSRF guard, redaction, URL canonicalization) + hermetic fixture server |
| 1b | Acquisition ladder: local tiers, failure classification, cooldowns, circuit breaker, tier memory, single-flight dedup, concurrency pool, CLI |
| 2 | Extraction ladder: pruning, token budgeting, schema validation, selector learning, cost accounting |
| 3 | Firecrawl engine, `map`/`search` discovery, HTTP API (SSE streaming), MCP server, web UI |
| 4 | Agent-Reach platform channel, robots.txt enforcement, bulk site crawl |

## Install

```powershell
cd "C:\Users\Lenovo\Desktop\Code\2026\Creel"
uv venv --python 3.13
uv pip install "scrapling[all]" scrapegraphai firecrawl-py "git+https://github.com/Panniantong/Agent-Reach"
uv pip install starlette sse_starlette "mcp>=2.0" httpx protego tld tiktoken ddgs
.venv\Scripts\python.exe -m scrapling install   # browser binaries + TLD data
```

Python 3.13 is required — Scrapegraph-ai declares `>=3.12,<4.0`. Never
`pip install agent-reach` from PyPI; that's an unrelated package. See
`docs/ARCHITECTURE.md` § Environment for the full pin rationale.

### Optional configuration

| Variable | Effect if unset |
|---|---|
| `FIRECRAWL_API_KEY` | Firecrawl engine, PDF handling, and `map`/`search` remote-first paths self-disable; local/Scrapling-only fallbacks still work. |
| An LLM provider key (passed via `SGAIConfig`/`ProviderConfig`, not auto-read from env) | The LLM extraction rung is unavailable; the selector/markdown rungs still run. |

Agent-Reach's own per-channel credentials (GitHub token, etc.) are read
from its own config — see its docs. `platform_cli` probes `agent-reach
doctor` lazily and degrades to "unavailable" per channel rather than
failing the whole ladder.

## Usage

### CLI

```powershell
.venv\Scripts\python.exe -m creel.adapters.cli fetch https://example.com --show-trace
```

`--cost-mode {frugal,reliable}` controls remote-egress ordering (Jina vs.
Firecrawl first) once local tiers are exhausted.

### HTTP API + web UI

```powershell
.venv\Scripts\python.exe -m creel.adapters.api --port 8000
```

Then open `http://127.0.0.1:8000/` for the web UI, or use the API directly:

- `GET /scrape?url=...&include=data,markdown` — one JSON response.
- `GET /scrape/stream?url=...` — Server-Sent Events, emits every attempt as
  it happens (a stealth escalation + LLM call can exceed a client's
  default 30–60s timeout; streaming avoids that without a job queue).
- `GET /` — the single-page web UI, driving `/scrape/stream` live.

`--host`/`--port` override the bind address. To wire a configured
`Orchestrator` (LLM extractor, Firecrawl key, custom policy) instead of the
defaults, call `create_app(...)` yourself and run it with `uvicorn`:

```python
from creel.adapters.api import create_app
from creel.core.orchestrator import Orchestrator
import uvicorn

uvicorn.run(create_app(Orchestrator(firecrawl_api_key="...")))
```

### MCP server

```python
from creel.adapters.mcp import create_server
from creel.core.orchestrator import Orchestrator

mcp = create_server(Orchestrator())
mcp.run()
```

Two tools: `fetch(url, escalate=False)` (fast path, tier-1 only) and
`scrape(url, prompt=None, cost_mode="frugal")` (full ladder, optional
structured extraction). Both return derived data — markdown or extracted
JSON — never raw HTML by default; full HTML is available on request via
the `creel://html/{resource_id}` resource.

### Bulk crawl

```python
from creel.core.crawl import crawl_site

results = await crawl_site("https://example.com", prompt="extract the title")
```

Seeded by `discover.map_site()`, crawled via Scrapling's own `Spider`
class (which owns its concurrency, throttling, and robots compliance),
extracting per page through the same self-teaching selector ladder —
so N structurally identical pages cost roughly one LLM call.

## Project layout

```
creel/
  core/        Contracts, safety, and orchestration
    models.py       Dataclasses: FetchOutcome, Attempt, Cost, ScrapeResult, ...
    guard.py         SSRF preflight, byte caps, redaction
    urlnorm.py       Canonical URL / registrable-domain keying
    dispatch.py      Pre-fetch content-class routing (html/pdf/xml/json)
    classify.py      Outcome -> FailureClass
    cooldown.py      Retry-After-aware per-domain cooldowns
    breaker.py       Circuit breaker (closed/open/half-open)
    memory.py        TTL'd per-domain tier hints
    pool.py          Bounded concurrency, per-domain + per-tier
    flight.py        Single-flight in-flight dedup
    policy.py        Domain policy resolution (global < config < learned)
    store.py         SQLite: runs, attempts, fetch/extract cache
    events.py        Attempt event bus (drives CLI trace + SSE)
    prune.py         Readability pruning + hard token budget
    pricing.py       Local LLM price table
    budget.py        Daily/monthly spend ceilings
    robots.py        robots.txt enforcement (gates the stealth tier)
    crawl.py         Bulk-crawl via Scrapling's Spider
    orchestrator.py  Wires everything above into the acquisition ladder
  engines/     One module per fetch engine (scrapling_http/dynamic/stealth, jina, firecrawl, platform_cli)
  extract/     Extraction ladder (selectors -> markdown -> LLM), schema validation, selector learning
  discover/    map() and search()
  adapters/    cli.py, api.py, mcp.py, web/index.html
tests/         252 tests, unittest + a local fixture server (no live network)
```

## Testing

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests
```

The suite is hermetic: `tests/fixtures/server.py` runs a real local HTTP
server emitting one deterministic response per failure class (429 with
`Retry-After`, Cloudflare-style 403, JS-required shell, login wall,
redirect loop, oversized body, PDF, soft-404). LLM calls, Firecrawl, and
subprocess calls are mocked at the SDK boundary; real Scrapling browser
engines and real `gh` CLI calls are exercised live where that's cheap and
already verified to work.

## Design notes worth knowing before extending this

- **429 is not "blocked."** It registers a cooldown honoring
  `Retry-After` and stays on the same tier; escalating would turn a
  wait-for-N-seconds problem into a spend-six-rungs problem.
- **A solved Cloudflare challenge that lands on an error page still
  re-escalates** — `solver_engaged=True` is not success.
- **Selector healing is scoped by field identifier, not literal CSS** —
  Scrapling's adaptive relocation re-finds a named field even if its
  cached selector string no longer matches.
- **Threads can't be cancelled.** The one sync engine (Scrapegraph-ai) is
  wrapped in `asyncio.to_thread`; a timed-out call is abandoned, not
  stopped, and may complete detached.
- **SSRF preflight applies only to local engines** — Jina and Firecrawl
  fetch from their own network, not this host's.

## License

Personal project, no license file yet — treat as all-rights-reserved
until one is added.
