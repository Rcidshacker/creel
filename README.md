# Creel

Unified scraping interface over four independent building blocks, wired
together by a failure-classifying orchestrator rather than a flat
try-everything fallback list:

- **Scrapling** — acquisition (HTTP / browser / stealth tiers) and parsing.
- **Scrapegraph-ai** — LLM structured extraction over HTML already in hand.
- **Firecrawl** — managed egress, `map`, `search` (Phase 3).
- **Agent-Reach** — credentials and capability checks for auth-walled
  platforms (Phase 4).

Design doc, gotchas, and the full phase plan live in the project's plan
file; this README will grow as adapters (HTTP API, MCP, web UI) land.

## Status

Phase 1b complete: the acquisition ladder (HTTP → dynamic → stealth →
remote egress) with failure classification, per-domain cooldowns, a circuit
breaker, tier memory, single-flight dedup, and a bounded concurrency pool.
Phase 2 (extraction ladder, selector learning, cost accounting) in
progress.

## Running the tests

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests
```

No network access required except for the real local fixture server the
test suite starts itself — no live third-party hosts are hit.

## CLI

```powershell
.venv\Scripts\python.exe -m creel.adapters.cli fetch https://example.com --show-trace
```
