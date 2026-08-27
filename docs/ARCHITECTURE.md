# Creel — Unified Scraping Interface

## Context

One application backed by Scrapling, Scrapegraph-ai, Firecrawl, and Agent-Reach, where a failed path escalates to another. Source exploration changed the premise three times:

**1. Agent-Reach is not a scraper.** `agent_reach/core.py:5-8`: *"After installation, agents call the upstream tools directly — no wrapper layer needed."* Its whole public Python API is `doctor()` / `doctor_report()`. Only 4 of 15 channels carry data methods.

**2. Scrapegraph-ai never has to fetch.** `graphs/smart_scraper_graph.py:67` picks its input key with `"url" if source.startswith("http") else "local_dir"`; `nodes/fetch_node.py:223-264` shows `handle_local_source` treats the source string **as the content itself**.

**3. Self-hosting Firecrawl is a downgrade.** `SELF_HOST.md`: self-hosted scraping is *"bundled Playwright with basic fetch fallback"*. Fire-engine, its real anti-bot layer, is cloud-only closed beta — so self-hosted Firecrawl evades blocks **worse than Scrapling already does locally**. Its unique value is `map` and `search`.

| Repo | Real role |
|---|---|
| **Scrapling** | Acquisition + parsing. Async-native, 3 tiers, adaptive selectors, throttle, robots, page pooling. |
| **Scrapegraph-ai** | LLM structured extraction over HTML we hold. Never fetches. The one sync dependency. |
| **Firecrawl** | Managed egress, `map`, `search`, native PDF→markdown. |
| **Agent-Reach** | Credential vault + capability oracle for auth-walled platforms. Nothing else. |

---

## Concurrency model — settled first, because everything depends on it

**Audited, not assumed:**

| Engine | Model | Evidence |
|---|---|---|
| `scrapling_http` | **async** | `AsyncFetcher.get` → `Awaitable[Response]` (`requests.py:52`) |
| `scrapling_dynamic` | **async** | `DynamicFetcher.async_fetch` (`chrome.py:54`), `AsyncDynamicSession` (`_controllers.py:215`) |
| `scrapling_stealth` | **async** | `StealthyFetcher.async_fetch` (`stealth_chrome.py:66`), `AsyncStealthySession` (`_stealth.py:314`) |
| `jina_reader` | **async** | We implement it: a 15-line httpx GET to `r.jina.ai`. Agent-Reach's `web.read` uses blocking `urllib` — do not wrap it, replace it. Also removes a dependency on the SPOF for a hot path. |
| `firecrawl` | **async** | SDK ships an async client |
| `platform_cli` | **async** | `asyncio.create_subprocess_exec` |
| `llm_sgai` | **THREAD** | `SmartScraperGraph.run()` is sync. `run_safe_async()` is just run-in-executor (`abstract_graph.py:344-353`) |

**Exactly one thread-wrapped engine.** No thread-pool subsystem required.

```python
class ExecutionModel(Enum): ASYNC; THREAD
```

The orchestrator wraps `THREAD` engines in `asyncio.to_thread`. **Threads cannot be killed** — a timed-out SGAI attempt is *abandoned*, not cancelled, and may complete detached. Bound SGAI's own `timeout` short enough that orphans exit naturally, and state this in the docstring rather than pretending cancellation works.

**Lazy imports, per module.** `import scrapegraphai` pulls the LangChain tree (seconds). Every engine imports its vendor inside its own module so `creel --help` stays instant and unused engines cost nothing.

**Bounds** (`core/pool.py`) — parallel requests to one domain are what *manufacture* the blocks the ladder then pays to defeat, so this is a floor, not throughput tuning:

```toml
[concurrency]
http            = 20     # low memory
browser         = 3      # each Chromium context is hundreds of MB — tune to RAM
per_domain      = 2      # politeness; keyed on registrable suffix
```

Long-lived pooled sessions with `max_pages`, never one-shot fetchers — `DynamicFetcher.fetch()` opens *and closes* a browser per call (`chrome.py:50-51`). Sessions are single-use-at-a-time; re-entry raises `RuntimeError` (`static.py:204`), so the pool owns instances one-per-slot.

---

## Architecture

```
                   ┌────────────────────────────────────────────┐
   CLI ──┐         │                creel.core                  │
   API ──┼───────► │ guard → dispatch → policy → pool → orch    │
   MCP ──┤         │        ↕ cooldown  ↕ breaker  ↕ events     │
   Web ──┘         └───┬──────────┬──────────┬──────────────────┘
                       │          │          │
                 ACQUISITION   PRUNE +    DISCOVERY
                       │      EXTRACTION      │
        ┌──────────────┴─────┐ ┌────┴──────┐ ┌┴───────────┐
        │1 Fetcher (HTTP)  SC│ │0 prune  SC│ │map()     FC│
        │2 DynamicFetcher  SC│ │1 learned  │ │search()  FC│
        │3 StealthyFetcher SC│ │2 CSS/XPath│ │sitemap   SC│
        │4 Jina Reader    own│ │3 markdown │ │exa       AR│
        │5 Firecrawl       FC│ │4 LLM  SG/direct└──────────┘
        │6 Platform CLI    AR│ └──┬────────┘
        └────────────────────┘    └─► validated selectors → tier 1
```

**Directory** — `C:\Users\Lenovo\Desktop\Code\2026\Creel\`

```
creel/
  core/
    models.py      ScrapeRequest/Result, FetchOutcome, Attempt, Cost, FailureClass
    urlnorm.py     canonicalize; registrable-suffix keying (Scrapling ships `tld`)
    guard.py       SSRF preflight + max_bytes + content-type sniff
    dispatch.py    content class -> route (pdf/xml/json/html/unsupported)
    policy.py      global default <- domain_policy TOML <- learned memory
    classify.py    FetchOutcome|Exception -> FailureClass
    cooldown.py    per-domain Retry-After registry (fetch AND discovery)
    pool.py        split + per-domain semaphores, pooled browser sessions
    flight.py      single-flight dedup by canonical URL
    breaker.py     closed/open/half-open + doubling cooldown
    memory.py      tier hints: TTL'd, IP-aware, probe-down
    events.py      AttemptStarted/Finished emit hook
    store.py       sqlite: runs, attempts, fetch_cache, extract_cache
    prune.py       readability + hard token budget
    budget.py      daily/monthly token + credit ceilings
    pricing.py     local model price table
    orchestrator.py
    config.py
  engines/         scrapling_{http,dynamic,stealth} | jina | firecrawl | platform_cli
  extract/         base | selectors | markdown | llm_sgai | llm_direct | schema | learn
  discover/        map.py | search.py
  adapters/        cli.py | api.py | mcp.py | web/
  doctor.py        creel doctor
  tests/fixtures/  local failure-injection server
```

### Contracts

No vendor type crosses the `engines/` boundary. `firecrawl-py` (two API generations, ongoing renames) and Scrapling (class reorganizations across minors — **pin exactly**) are the churn-prone surfaces this protects. Drift then costs one file, not a grep.

```python
@dataclass
class FetchOutcome:
    status: int | None          # scrapling never raises on 4xx/5xx (gotcha 6)
    headers: Mapping[str, str]  # retry-after, content-type, cf-*
    body: bytes                 # decoded lazily
    final_url: str
    redirect_chain: list[str]   # evidence: meta-refresh / soft-404 interstitials
    solver_engaged: bool
    signals: list[str]          # "cf_challenge" vs "cf_error_page" — the latter RE-escalates
    elapsed_ms: int

class EngineContext:            # availability is temporal, not a boot snapshot
    now; budget_state; breaker_state; cooldowns; channel_snapshot

class FetchEngine(Protocol):
    name: str; tier: int; cost_per_call: float
    needs_browser: bool
    execution_model: ExecutionModel
    def available(self, ctx: EngineContext) -> bool: ...
    async def fetch(self, url: str, opts: FetchOpts) -> FetchOutcome: ...

@dataclass
class Cost:
    prompt_tokens: int; completion_tokens: int; credits: int
    usd: float
    exact: bool                 # False => estimated. Never render an estimate as a measurement.

@dataclass
class ScrapeResult:
    url: str                    # canonical original — redirects must NOT poison identity
    final_url: str
    status: Literal["ok", "partial", "failed"]
    engine_path: list[str]
    html / markdown / data
    attempts: list[Attempt]     # detail scrubbed of keys, URLs truncated
    cost: Cost
    from_cache: bool
```

**`"partial"` is now defined:** *delivered formats ⊂ requested formats*, **or** an adaptive selector self-healed. Silent healing is exactly how a semantically-wrong match goes unnoticed, so healing demotes status and says so in `attempts`.

---

## Safety floors (not features — these ship in Phase 1)

### SSRF + size (`core/guard.py`)

Jina's SSRF guards protect *their* egress. Our own curl and headless Chromium will cheerfully fetch `http://169.254.169.254/` or `http://localhost:8080/admin` the moment anything programmatic supplies a URL — and the web UI is itself that pivot surface.

Preflight on **local rungs only**:
```python
parsed.scheme in {"http", "https"}      # no file:, ftp:
ipaddress.ip_address(resolved).is_global # rejects RFC1918, link-local, loopback
```
Plus `max_bytes` per attempt (mirror Jina's 5 MiB), streamed, and content-type sniff. A hostile 800 MB body pulled into RAM through a headless browser is a self-inflicted DoS. Explicit `allow_private_hosts` opt-out for legitimate intranet work.

### Redaction

`Attempt.detail` persists for weeks in `runs`. SDK exceptions echo configs. Scrub key-shaped strings and truncate URLs **at the emit site**, not the render site.

---

## Fallback: acquisition

### Content dispatch, before the router (`core/dispatch.py`)

PDFs are the common case: SGAI won't parse them, `.markdown()` won't, and walking one through three browser rungs is pure waste — while Firecrawl does PDF→markdown natively. Sniff extension + content-type into `{pdf, xml/rss, json, html, unsupported}`. PDF routes straight to the Firecrawl rung (recorded in `engine_path`) or fails as `UNSUPPORTED_CONTENT` rather than laundering through `NETWORK`/`PARSE_FAILED`.

### Classification (`core/classify.py`)

Start from `scrapling.spiders.spider.BLOCKED_CODES` (`spider.py:16`), then **split it** — using it wholesale is the bug:

| Class | Signal |
|---|---|
| `RATE_LIMITED` | **429**, or 503 **with** `Retry-After` |
| `BLOCKED` | 401/403/407/444, 503 without `Retry-After`, or a `[blocked_markers]` hit |
| `JS_REQUIRED` | 200, low body text, high `<script>` density |
| `AUTH_REQUIRED` | login-wall **marker** (not a hardcoded domain list, which rots) |
| `NETWORK` | timeout, DNS, `RuntimeError("Failed to get response…")` |
| `NOT_FOUND` | 404 / 410 — terminal |
| `UNSUPPORTED_CONTENT` | dispatch rejected it — terminal |
| `PARSE_FAILED` | fetch OK, extraction found nothing (two sub-cases below) |

**`[blocked_markers]` is a TOML registry**, global + per-domain: "Access denied", "enable JavaScript", region-block boilerplate, login-wall fragments. Bot-walls increasingly serve plausible 200s. Users extend it; we don't chase it.

**`solver_engaged=True` + `cf_error_page` signal must re-escalate**, not celebrate. A solved challenge that lands on an error page is still a failure.

### Escalation

| Failure | Next |
|---|---|
| `RATE_LIMITED` | **register a per-domain cooldown honoring `Retry-After`**; same tier only |
| `NETWORK` | Scrapling's own `retries` handles it — do not stack ours on top |
| `JS_REQUIRED` | → dynamic (pooled) |
| `BLOCKED` | → stealth (pooled) → remote-egress rung |
| `AUTH_REQUIRED` | → `platform_cli` if the channel is usable; else terminal |
| `NOT_FOUND` / `UNSUPPORTED_CONTENT` | terminal |
| `PARSE_FAILED` | see extraction diagnostics below |

**The cooldown registry is why 429 must not escalate.** Escalating converts a *cooldown* problem into a *spend* problem and discards `Retry-After`, which is the actual instruction. Requests landing inside an active cooldown **fail fast** — caller chooses wait-or-refuse — instead of burning six rungs. Without this, Phase 3 concurrency turns one 429 into six wasted attempts per request for the whole window. Scrapling already implements the backoff correctly in `spiders/throttle.py:88-90`; reuse it. Discovery rungs feed the same registry: keyless Jina is politely rate-limited and `ddgs` is throttled aggressively, so *everything that talks to a network respects per-peer state*.

### Remote egress is a policy knob

```toml
cost_mode = "frugal"     # … stealth → jina → firecrawl   (default)
# cost_mode = "reliable" # … stealth → firecrawl → jina
```

### "Domain blocks everyone" vs "my IP is banned"

The ladder already runs the experiment. Local stealth `BLOCKED` **and** remote `ok` → the differing variable was our IP → tag `ip_suspect`, **do not** latch the domain hostile. Both blocked → genuine `domain_hostile`. Never write memory from a single observation.

### Deadlines and budgets (`core/budget.py`)

Six rungs × 30 s ≈ 3 minutes, unbudgeted in time and money. A wall-clock `deadline` reaches the router, which **prunes**: if the remaining budget can't plausibly cover `stealth → firecrawl`, skip to Jina. `budget.py` holds daily/monthly token and credit ceilings, checked after each paid rung and surfaced through `EngineContext` — so `available()` means *key present AND budget remaining AND not cooling down AND not tripped*.

### Policy, from three sources, one evaluator (`core/policy.py`)

```toml
[[domain_policy]]
glob = "*.stubborn-site.com"
start_tier = 3
allowed_engines = ["scrapling_stealth", "firecrawl"]
```
Resolution: global `cost_mode` ← domain config ← learned memory. No expression language, no solver.

### Identity and keying (`core/urlnorm.py`)

Scheme/host lowercase, default-port strip, trailing-slash rule, tracking-param denylist (`utm_*` and friends), www canonicalization. Without it, one URL × 50 tracking variants earns 50 cache rows. **Domain memory keys on the registrable suffix** via the public-suffix list — Scrapling's installer already pulls TLD data (`cli.py:110-142`) — never subdomains, because CDNs spawn them endlessly.

### Caches and dedup (`core/store.py`, `core/flight.py`)

- **Single-flight:** an in-flight futures map keyed on canonical URL. Without it, two concurrent misses double-launch browsers and double-spend credits. Ten lines, Phase 1.
- **Fetch cache** key must include everything that changes the response: canonical URL, tier, **`Accept-Language`, cookies, UA-impersonation profile**. "normalized_opts" was aspirational; codify the field list. Per-domain TTL overrides (news vs docs), plus purge/vacuum commands.
- **Extraction cache** — separate table, keyed `sha256(html_hash | prompt | model | params)`. Without it, same URL + same prompt re-pays the LLM every call. Also makes model comparisons reproducible offline.
- **Stale-body rule:** `PARSE_FAILED` on a cached body → invalidate, re-fetch once, retry, *then* escalate. And `learn.py` never derives selectors from a cached body.

### Breaker and memory

Breaker per `(engine, domain)`: closed → open after N consecutive failures → half-open after cooldown → one success closes, one failure reopens with cooldown doubled to a ceiling. Memory entries are **hints with TTL**, and **probe down-tier every K requests / N days**; success demotes. Inert memories calcify into a permanent browser launch long after a site relaxes.

### Events and history (`core/events.py`, `runs` table)

Designed in from Phase 1 — retrofitting an event bus into a finished orchestrator is painful; a callback is trivial. `AttemptStarted` / `AttemptFinished` → CLI prints incrementally, API streams SSE, MCP ignores. Persist `runs(id, url, ts, engine_path, status, cost)` with FK'd attempts in the same SQLite file: powers cost reporting, trace diffing, and "this domain started needing tier 3 last Tuesday".

---

## Prune, then extract

### `core/prune.py`

1. `response.markdown(main_content_only=True)` — Scrapling's own readability pass, already installed.
2. Fallback to raw body if that yields suspiciously little.
3. **Hard budget check, always:** count with `tiktoken` (already an SGAI dep) against declared `model_tokens`; over budget → prune harder, then chunk. Never hand the LLM an input exceeding its declared window — that is what makes gotcha 4 *silent*.

```python
# ponytail: main_content_only is Scrapling's readability pass and ships with the
# dep we already have. trafilatura is the drop-in upgrade — same signature, one
# module, plus a --raw escape hatch for its sidebar-dropping artifacts. Swap only
# if it measurably wins on real targets. Do not add it speculatively.
```

### The ladder teaches itself

500 structurally identical product pages are 500 URLs, so a naive LLM rung is 500 LLM calls — frugal on fetching, ruinous on extraction.

```
0 prune → 1 learned selectors → 2 CSS/XPath → 3 markdown → 4 LLM ──┐
                    ▲                                              │
                    └───────── validated selectors ────────────────┘
```

`extract/learn.py`: the LLM returns a candidate selector per field in the *same* call (one schema field, no extra request). **Validate before trusting** — run them against the same HTML, compare to extracted values, cache only on exact match. That is what stops hallucinated selectors from poisoning tier 1. Store keyed `(registrable_domain, template_hash)` in Scrapling's existing adaptive-selector SQLite store.

```python
# ponytail: template_hash = URL path shape (numeric/slug segments normalized).
# Upgrade to a DOM-skeleton hash only if it misfires. (A structural hash is
# legitimate HERE — computed after the fetch, unlike a cache lookup key.)
```

### `PARSE_FAILED` has two meanings

Extractors return diagnostics (match counts, text density) so `classify()` decides with evidence:

- **Dense page, selectors missed** → heal adaptively, stay local, re-learn.
- **Sparse / ambiguous page** → likely the wrong page state → **re-fetch at a higher tier**.

### Schema-first, one guided retry (`extract/schema.py`)

One authoritative Pydantic schema per job, compiled to each backend's form (SGAI schema; Firecrawl JSON schema) so the two never diverge. Validate output post-hoc — including gotcha 3's `{"error":…, "raw_response":…}` shape sniff. On validation failure, **one** guided retry feeding the validator errors back as corrective context, then `PARSE_FAILED`. Unvalidated LLM JSON is `data: dict` fiction; this is what makes `data` trustworthy.

### Two LLM implementations, behind a worker seam

`llm_sgai` (default) and `llm_direct` (thin structured-output client) both satisfy `Extractor`. The **subprocess seam is built now, not kept as contingency**: flipping in-process ↔ subprocess becomes deployment config rather than a refactor, and it buys crash isolation plus the ability to skip the LangChain import entirely at startup.

### Cost: measured or admitted

`core/pricing.py` holds the local price table; **we compute USD, not the graph.** `llm_direct` → exact counts, `exact=True`. `llm_sgai` → `get_execution_info()` when populated, else `tiktoken` estimate with `exact=False`. CLI, API, and UI render estimates visually distinct. A LangChain `on_llm_end` callback would give exact counts inside SGAI — the right escalation *if* estimates prove materially off, but it couples us to internals `llm_direct` exists to escape. Measure first.

---

## Discovery

| Verb | Primary | Fallback |
|---|---|---|
| `map(url)` | `firecrawl.map()` | Scrapling `SitemapSpider` / `LinkExtractor` |
| `search(q)` | `firecrawl.search()` | Exa via mcporter (keyless), then `ddgs` |

Map-then-crawl is the pattern: enumerate cheaply on credits, fetch in bulk for free. **Cap the crawl fallback** (`max_pages`, `max_depth`) — a link-crawl without a ceiling on a sitemap-less site never ends. `ddgs` is the renamed `duckduckgo_search`; alias defensively in requirements.

---

## Agent-Reach: contained to credentials and capability

Solo-maintained, Linux-first, with recent Windows-specific bugs (GBK console decoding in `doctor`, `.cmd` resolution via `shutil.which()`).

- **Never at boot.** Lazy, first time a platform channel is needed, then cached.
- **try/except every call** → "Agent-Reach unavailable". It must never block local tiers.
- Predicate is `status in ("ok","warn")`, never `active_backend is not None` — `None` is the normal working state for twitter/xiaohongshu/exa (`twitter.py:83-109`).
- Snapshot the dict immediately; channels are mutable singletons (`channels/__init__.py:26-42`).
- Jina is ours now, so no hot path depends on this at all.

---

## Environment

**Present:** Python 3.13 · uv 0.12.1 · Node 24.19 · npm 11.17 · gh 2.97 · ffmpeg 9.0 · Docker 29.5 · git 2.55 · Chrome · Playwright chromium-1228 · 105 GB free.

**Python 3.13 is mandatory** — SGAI declares `>=3.12,<4.0` (`pyproject.toml:66`); your uv-managed 3.11.15 cannot host it.

| Item | Note |
|---|---|
| **LLM API key** | None in env, no `~/.claude/.env`, no Ollama. Extraction rung is dead without one. |
| `scrapling install` | Browser deps + TLD data (which `urlnorm` also uses) |
| `playwright install chromium` | Scrapling wants `>=1.62`; present build is 1228 |
| `scrapling[all]` | `markdownify`, `protego`, `mcp>=2.0`. **Pin exactly** — API renames across minors. |
| Agent-Reach **from git** | ⚠️ PyPI `agent-reach` is a **different project** |
| `FIRECRAWL_API_KEY` | Optional; absent → egress/map/search/PDF rungs self-disable |

**Windows specifics:** spawn CLI subprocesses with `CREATE_NO_WINDOW` (no console flash per call) and a minimal scrubbed child env — generalize `twitter_cli_child_env` into `platform_cli`'s builder. Expect first-run AV friction with Camoufox's Firefox build; `creel doctor` surfaces it. Agent-Reach's `--system` installer is apt-based, checks-only here; OpenCLI on Windows is unverified. Docker Desktop needs WSL2, which you don't run.

**Licensing:** local and single-user, nothing binds. If Creel ever becomes hosted multi-user, **self-hosted Firecrawl is AGPL-3.0** and becomes a review item; Firecrawl *cloud* never does. One more quiet argument for the Phase-0 recommendation.

### `creel doctor`

Agent-Reach gets a doctor; our own environment needs one more. Boot-check browser executables, validate imports, fire one real request per tier at a stable URL, print a channel-style table. ~10 s, catches Playwright build mismatch, a Camoufox update Defender dislikes, a Scrapling minor that renamed classes, a vanished `markdownify` — at boot instead of at first scrape.

### LLM provider config

SGAI's whitelist (`abstract_graph.py:158-178`) covers openai/azure/google/ollama/nvidia/groq/anthropic/bedrock/mistral/deepseek/fireworks/togetherai/xai/minimax and more. Any extra key is forwarded verbatim to `init_chat_model` (`:253`), **including `base_url`** — so OpenRouter and any OpenAI-compatible endpoint ride the openai provider:

```toml
[llm]
provider = "openrouter"; model = "anthropic/claude-3.5-sonnet"
base_url = "https://openrouter.ai/api/v1"; api_key_env = "OPENROUTER_API_KEY"
model_tokens = 200000          # MANDATORY — gotcha 4
```

One registry feeds `llm_sgai`, `llm_direct`, and `pricing.py`.

---

## Gotchas

1. SGAI `source` must be raw HTML, **not a path** — the literal string becomes page content; no `os.path.exists` anywhere in `handle_local_source`.
2. Empty/whitespace source raises `ValueError` (`fetch_node.py:240`).
3. SGAI **does not raise** on extraction failure — writes `{"error":…, "raw_response":…}` into `answer` (`generate_answer_node.py:241`). try/except won't catch it.
4. Unknown model → window silently drops to 8192, `model_tokens_defaulted = True` (`abstract_graph.py:223-229`). Pass `model_tokens`, assert the flag, enforce in `prune.py`.
5. Scrapling timeout units differ: HTTP **seconds** (30), browser **milliseconds** (30000).
6. Scrapling never raises on non-2xx — inspect `status` explicitly.
7. `Selectors.first` returns `None` on empty, not `IndexError`.
8. Agent-Reach channels are mutable singletons — snapshot, never hold, never call concurrently.
9. Never `pip install agent-reach` from PyPI.
10. Browser sessions are single-use-at-a-time (`static.py:204`) — the pool owns instances.
11. Firecrawl: pin **v2**; v1 is feature-frozen; never mix.
12. Firecrawl `product`/`menu` silently no-op without their service URLs — warning, no data, no error.
13. Firecrawl crawls are async jobs; `start_crawl` returns an id.
14. Apply 13 to ourselves: pool wait + stealth + LLM exceeds 30–60 s client defaults.
15. Never return raw HTML through MCP by default.
16. Threads can't be cancelled — a timed-out SGAI attempt is abandoned, not stopped.

---

## Phases

### Phase 0 — Spike + doctor skeleton

```powershell
cd "C:\Users\Lenovo\Desktop\Code\2026\Creel"
uv venv --python 3.13
uv pip install "scrapling[all]" scrapegraphai firecrawl-py "git+https://github.com/Panniantong/Agent-Reach"
```
Watch `playwright` (`>=1.62` vs `>=1.57`), `mcp` (`>=2.0` vs `mcp[cli]`), `pydantic` (`>=2.12.5`).
**Verify:** `creel doctor` passes imports + browser executables.
**If it fails:** the worker seam means SGAI moves to its own venv as deployment config, or `llm_direct` becomes default. Not a refactor.

### Phase 1a — Contracts, safety, and the fixture harness (zero network)

`core/{models,urlnorm,guard,dispatch,events,store}.py` + `tests/fixtures/` — a local server emitting one deterministic response per failure class: CF-marker 403, 200-empty-with-scripts, login-wall, 429-with-`Retry-After`, redirect loop, gzip bomb, 800 MB stream, PDF, soft-404 interstitial.

**This lands before any engine.** Live third-party hosts are not a regression gate — `<cloudflare-site>` changes posture, httpbin hiccups, Jina throttles. Green today ≠ green Thursday. Every ladder assertion becomes hermetic and CI-grade, and a shared contract-test suite (*given fixture X, produce outcome Y*) means each new engine inherits the guarantees.

**Verify:** guard rejects `169.254.169.254`, `localhost`, `file://`; `max_bytes` aborts the 800 MB stream; `urlnorm` collapses 50 utm variants to one key; redaction strips a key-shaped token from `Attempt.detail`.

### Phase 1b — The ladder

`engines/scrapling_{http,dynamic,stealth}.py`, `engines/jina.py`, `core/{classify,cooldown,pool,flight,breaker,memory,policy,orchestrator}.py`, `adapters/cli.py`. Deterministic extraction only.

**Verify — all against fixtures:** each failure class produces its expected `engine_path`; **429 registers a cooldown and never switches engines**; a second request inside the cooldown fails fast; 404 and PDF terminate without escalation; cache hit does zero I/O; two concurrent identical URLs produce **one** fetch; breaker walks open → cooldown → half-open → closed; 50 concurrent browser-tier requests never exceed `concurrency.browser` Chromium processes (the test that catches OOM); per-domain concurrency never exceeds `per_domain`.

### Phase 2 — Prune, extraction, learning, caches

`core/{prune,pricing,budget}.py`, `extract/*`. **Verify:** 20 URLs on one template → **exactly 1** LLM call, 19 selector hits; a corrupted cached selector is rejected and re-learned; identical URL+prompt+model → **zero** LLM work on the second call; a 200k-token page never reaches the LLM over-window and `model_tokens_defaulted` stays False; schema validation catches a malformed extraction and the guided retry fixes it; `Cost.exact` is False whenever SGAI reports no usage; healed selectors demote status to `"partial"`.

### Phase 3 — Firecrawl, discovery, adapters

`engines/firecrawl.py`, `discover/*`, `adapters/{api,mcp,web}`. All adapters thin — zero logic below `core`.

- **Stream, don't block.** SSE emits `Attempt` events including pool waits.
- **MCP returns derived data, never raw HTML** — markdown or `data`, size-capped with explicit truncation notice, full HTML by resource id. The agent's context window is scarcer than any transport limit.
- **MCP fast path:** one server; `fetch(url, escalate=False)` hits tier 1 and bypasses the orchestrator, `scrape(url)` runs the ladder.
- **Format negotiation:** `?include=data,markdown,html&format=json|md|raw`, defaulting to data+markdown with HTML opt-in, so 10 MB payloads aren't routine.

**Verify:** identical `data` across four surfaces; tier-3 escalation streams rather than timing out; a 5 MB page through MCP returns bounded payload + resource id; killing `FIRECRAWL_API_KEY` disables egress/map/PDF rungs cleanly.

### Phase 4 — Platform channels, crawl, evaluation

`engines/platform_cli.py` (commands from `agent_reach/skill/SKILL_en.md:63-110`), Scrapling spiders seeded by `map`, reusing the politeness layer (cooldown + per-domain semaphore + robots) that Phase 1b already built.

**Evaluation candidates, not commitments** — each is a Protocol drop-in, which is precisely why none belongs earlier: **Crawl4AI** (overlaps ~90% with tier 2/3 + `main_content_only`; means a second Playwright stack — benchmark before adopting), **trafilatura** for `prune.py`, **self-hosted Firecrawl** and open-core anti-bot gateways, **real proxy pool** via Scrapling's `ProxyRotator` with sticky per-domain sessions (mutually exclusive with `proxy` — `_validators.py:107-110`).

---

## Deliberate omissions

- No new library adopted on reputation. Crawl4AI, trafilatura, proxy pools, and self-hosting are all drop-ins by construction — that is the argument for adding them *with benchmarks*, later.
- No cost optimizer, no expression language in `domain_policy`, no solver.
- No job queue — SSE covers long requests.
- No thread-pool subsystem — the audit found exactly one sync engine.
- No LangChain callback until estimates are measured against reality.
- No distributed workers. Bounded single-process asyncio until throughput is measured.
- No auth/multi-user on the API. Localhost only.
- Phase 4 last: social channels are the most brittle part of the stack and nothing depends on them.
