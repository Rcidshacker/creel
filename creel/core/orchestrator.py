"""Wires classify + cooldown + pool + flight + breaker + memory + policy +
dispatch + store + events + engines into the acquisition ladder. This is the
one place the escalation matrix from the design doc is actually encoded:

    RATE_LIMITED -> register cooldown, terminal for this request (no engine switch)
    NETWORK      -> Scrapling's own retries already ran; move to next tier once
    JS_REQUIRED  -> next tier (http -> dynamic renders JS)
    BLOCKED      -> next tier, eventually remote egress
    AUTH_REQUIRED / NOT_FOUND / UNSUPPORTED_CONTENT -> terminal

The local ladder and the remote-egress engine are constructor-injectable so
tests can substitute fixture-friendly stand-ins instead of hitting Jina's
real servers — Phase 1a's hermetic-tests principle applies here too.
"""
from __future__ import annotations

import hashlib
import os
import time
import uuid
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from creel.core.breaker import CircuitBreaker
from creel.core.classify import classify_status
from creel.core.cooldown import CooldownRegistry, parse_retry_after
from creel.core.dispatch import ContentClass, classify_content
from creel.core.events import AttemptFinished, AttemptStarted, EventBus
from creel.core.flight import SingleFlight
from creel.core.guard import GuardConfig, redact
from creel.core.memory import TierMemory
from creel.core.models import Attempt, FailureClass, FetchOutcome, ScrapeResult
from creel.core.policy import PolicyResolver
from creel.core.pool import ConcurrencyPool
from creel.core.store import Store
from creel.core.urlnorm import canonicalize, registrable_domain


@dataclass
class EngineSpec:
    name: str
    tier: int
    needs_browser: bool
    fetch: Callable[..., Awaitable[FetchOutcome]]


class CooldownActive(Exception):
    """Raised when a domain is inside an active rate-limit cooldown. The
    caller (adapter) chooses wait-or-refuse — the orchestrator never burns
    the ladder trying to work around a 429."""

    def __init__(self, domain: str, remaining_s: float):
        super().__init__(f"{domain} is cooling down for {remaining_s:.1f}s more")
        self.domain = domain
        self.remaining_s = remaining_s


def default_local_ladder() -> list[EngineSpec]:
    from creel.engines import scrapling_dynamic, scrapling_http, scrapling_stealth

    return [
        EngineSpec("scrapling_http", 1, False, scrapling_http.fetch),
        EngineSpec("scrapling_dynamic", 2, True, scrapling_dynamic.fetch),
        EngineSpec("scrapling_stealth", 3, True, scrapling_stealth.fetch),
    ]


def _jina_spec() -> EngineSpec:
    from creel.engines import jina as jina_engine

    async def _fetch(url: str, guard_config=None, **_ignored) -> FetchOutcome:
        return await jina_engine.fetch(url)

    return EngineSpec("jina", 4, False, _fetch)


def _firecrawl_spec(api_key: str) -> EngineSpec:
    from creel.engines import firecrawl as firecrawl_engine

    async def _fetch(url: str, guard_config=None, **_ignored) -> FetchOutcome:
        return await firecrawl_engine.fetch(url, api_key=api_key)

    return EngineSpec("firecrawl", 5, False, _fetch)


def default_remote_egress_chain(cost_mode: str, firecrawl_api_key: Optional[str] = None) -> list[EngineSpec]:
    """Order is the cost_mode policy knob: frugal tries the free rung
    first, reliable tries the higher-success-rate paid rung first. Jina is
    always present (keyless); Firecrawl only joins the chain when a key is
    configured — its absence must disable the rung, not crash anything."""
    from creel.engines import firecrawl as firecrawl_engine

    jina = _jina_spec()
    if not firecrawl_engine.available(firecrawl_api_key):
        return [jina]
    firecrawl = _firecrawl_spec(firecrawl_api_key)
    return [firecrawl, jina] if cost_mode == "reliable" else [jina, firecrawl]


class Orchestrator:
    def __init__(
        self,
        store: Optional[Store] = None,
        pool: Optional[ConcurrencyPool] = None,
        breaker: Optional[CircuitBreaker] = None,
        cooldowns: Optional[CooldownRegistry] = None,
        memory: Optional[TierMemory] = None,
        policy: Optional[PolicyResolver] = None,
        events: Optional[EventBus] = None,
        guard_config: Optional[GuardConfig] = None,
        blocked_markers: Optional[list[str]] = None,
        fetch_ttl_s: float = 3600.0,
        local_ladder: Optional[list[EngineSpec]] = None,
        remote_egress_chain: Optional[list[EngineSpec]] = None,
        firecrawl_api_key: Optional[str] = None,
    ) -> None:
        self.store = store
        self.pool = pool or ConcurrencyPool()
        self.breaker = breaker or CircuitBreaker()
        self.cooldowns = cooldowns or CooldownRegistry()
        self.memory = memory or TierMemory()
        self.policy = policy or PolicyResolver(memory=self.memory)
        self.events = events or EventBus()
        self.guard_config = guard_config or GuardConfig()
        self.blocked_markers = blocked_markers or []
        self.fetch_ttl_s = fetch_ttl_s
        self.local_ladder = local_ladder if local_ladder is not None else default_local_ladder()
        # None means "compute per-call from cost_mode" — the chain's ORDER
        # depends on cost_mode, which is a per-fetch argument, not fixed at
        # construction. An explicit override (tests, or a caller pinning a
        # chain) always wins over that dynamic resolution.
        self._remote_egress_chain_override = remote_egress_chain
        self.firecrawl_api_key = (
            firecrawl_api_key if firecrawl_api_key is not None else os.environ.get("FIRECRAWL_API_KEY")
        )
        self._flight = SingleFlight()

    def with_events(self, events: EventBus) -> "Orchestrator":
        """A child Orchestrator sharing all stateful, cross-request infra
        (pool, breaker, cooldowns, memory, store) but with its own isolated
        EventBus. Exists so an SSE handler can stream one request's
        progress without mutating a shared instance's event bus, which
        would clobber any other concurrent request listening on it.

        The child gets its own SingleFlight, so single-flight dedup does
        not cross the streaming/non-streaming boundary — an accepted
        trade-off for a request-scoped clone rather than a fully separate
        Orchestrator construction."""
        return Orchestrator(
            store=self.store,
            pool=self.pool,
            breaker=self.breaker,
            cooldowns=self.cooldowns,
            memory=self.memory,
            policy=self.policy,
            events=events,
            guard_config=self.guard_config,
            blocked_markers=self.blocked_markers,
            fetch_ttl_s=self.fetch_ttl_s,
            local_ladder=self.local_ladder,
            remote_egress_chain=self._remote_egress_chain_override,
            firecrawl_api_key=self.firecrawl_api_key,
        )

    async def fetch(self, url: str, cost_mode: Optional[str] = None) -> ScrapeResult:
        canonical = canonicalize(url)
        domain = registrable_domain(url)
        return await self._flight.run(canonical, lambda: self._fetch_uncached(url, canonical, domain, cost_mode))

    async def _fetch_uncached(
        self, url: str, canonical: str, domain: str, cost_mode: Optional[str]
    ) -> ScrapeResult:
        run_id = uuid.uuid4().hex

        if self.store is not None:
            cached = self.store.get_fetch(_cache_key(canonical))
            if cached is not None:
                return ScrapeResult(
                    url=canonical,
                    final_url=cached["url"],
                    status="ok",
                    engine_path=[],
                    html=cached["body"].decode("utf-8", errors="ignore"),
                    from_cache=True,
                )

        active = self.cooldowns.active(domain)
        if active is not None:
            raise CooldownActive(domain, self.cooldowns.remaining(domain))

        # Cheap pre-fetch check: catches the common /report.pdf case for
        # free, with zero network calls. This alone is NOT sufficient — a
        # PDF behind an extensionless URL (/download?id=123) only reveals
        # itself via the Content-Type header, checked again below once we
        # actually have a response.
        content = classify_content(url)
        if content == ContentClass.PDF:
            return await self._handle_pdf(url, canonical, run_id, domain)
        if content == ContentClass.UNSUPPORTED:
            return ScrapeResult(url=canonical, final_url=url, status="failed", engine_path=[])

        resolved = self.policy.resolve(domain, cost_mode)
        attempts: list[Attempt] = []
        engine_path: list[str] = []

        for spec in self.local_ladder:
            if spec.tier < resolved.start_tier:
                continue
            if resolved.allowed_engines is not None and spec.name not in resolved.allowed_engines:
                continue
            if not self.breaker.allow(spec.name, domain):
                continue

            outcome, failure = await self._run_engine(spec, url, run_id, domain, attempts)
            engine_path.append(spec.name)

            # Content-type is only knowable AFTER a fetch. Re-check here so a
            # PDF/binary response discovered only via headers (no .pdf in the
            # URL) never gets pushed through browser tiers or cached as HTML.
            if outcome is not None and outcome.status is not None:
                actual_content = classify_content(url, _content_type_header(outcome))
                if actual_content == ContentClass.PDF:
                    return await self._handle_pdf(url, canonical, run_id, domain, engine_path, attempts)
                if actual_content == ContentClass.UNSUPPORTED:
                    return self._finalize_failed(run_id, canonical, url, engine_path, attempts)

            if failure is None:
                self.breaker.record_success(spec.name, domain)
                self.memory.record_success(domain, spec.tier)
                self._cache_put(canonical, spec.name, outcome)
                return self._finalize_ok(run_id, canonical, engine_path, outcome, attempts)

            self.breaker.record_failure(spec.name, domain)

            if failure == FailureClass.RATE_LIMITED:
                self.cooldowns.register(domain, _extract_retry_after(outcome))
                return self._finalize_failed(run_id, canonical, url, engine_path, attempts)
            if failure in (FailureClass.NOT_FOUND, FailureClass.UNSUPPORTED_CONTENT, FailureClass.AUTH_REQUIRED):
                return self._finalize_failed(run_id, canonical, url, engine_path, attempts)
            # JS_REQUIRED / BLOCKED / NETWORK -> try the next local tier

        # Local ladder exhausted (or fully breaker-tripped) -> remote egress.
        # Order is the cost_mode policy knob (frugal: jina then firecrawl;
        # reliable: the reverse) — resolved dynamically per call unless a
        # caller pinned an explicit chain (tests do this to stay hermetic).
        remote_chain = self._remote_egress_chain_override
        if remote_chain is None:
            remote_chain = default_remote_egress_chain(resolved.cost_mode, self.firecrawl_api_key)

        local_was_blocked = any(a.failure_class == FailureClass.BLOCKED for a in attempts)
        last_remote_failure: Optional[FailureClass] = None

        for spec in remote_chain:
            remote_outcome, remote_failure = await self._run_engine(spec, url, run_id, domain, attempts)
            engine_path.append(spec.name)

            if remote_failure is None:
                if local_was_blocked:
                    # Local stealth failed, this remote rung succeeded: the
                    # differing variable was our IP, not the domain's policy.
                    self.memory.record_ip_suspect(domain)
                self._cache_put(canonical, spec.name, remote_outcome)
                return self._finalize_ok(run_id, canonical, engine_path, remote_outcome, attempts)

            last_remote_failure = remote_failure

        if local_was_blocked and last_remote_failure == FailureClass.BLOCKED:
            self.memory.record_domain_hostile(domain, tier=3)
        return self._finalize_failed(run_id, canonical, url, engine_path, attempts)

    async def _handle_pdf(
        self,
        url: str,
        canonical: str,
        run_id: str,
        domain: str,
        engine_path: Optional[list[str]] = None,
        attempts: Optional[list[Attempt]] = None,
    ) -> ScrapeResult:
        """PDF routes straight to Firecrawl's native PDF->markdown rung when
        a key is configured; otherwise it terminates rather than laundering
        through jina (never designed for binary content) or three browser
        tiers that can't render a PDF into anything useful anyway."""
        engine_path = engine_path if engine_path is not None else []
        attempts = attempts if attempts is not None else []

        if not self.firecrawl_api_key:
            return self._finalize_failed(run_id, canonical, url, engine_path, attempts)

        spec = _firecrawl_spec(self.firecrawl_api_key)
        outcome, failure = await self._run_engine(spec, url, run_id, domain, attempts)
        engine_path.append(spec.name)
        if failure is None:
            self._cache_put(canonical, spec.name, outcome)
            return self._finalize_ok(run_id, canonical, engine_path, outcome, attempts)
        return self._finalize_failed(run_id, canonical, url, engine_path, attempts)

    async def _run_engine(
        self, spec: EngineSpec, url: str, run_id: str, domain: str, attempts: list[Attempt]
    ) -> tuple[Optional[FetchOutcome], Optional[FailureClass]]:
        self.events.emit(AttemptStarted(run_id=run_id, engine=spec.name, url=url, at=time.time()))
        started = time.time()
        async with self.pool.acquire(domain, spec.needs_browser):
            outcome = await spec.fetch(url, guard_config=self.guard_config)
        failure = classify_status(outcome, self.blocked_markers)
        duration_ms = int((time.time() - started) * 1000)
        detail = redact(f"status={outcome.status} signals={outcome.signals}")
        attempts.append(
            Attempt(engine=spec.name, started_at=started, duration_ms=duration_ms, failure_class=failure, detail=detail)
        )
        self.events.emit(
            AttemptFinished(
                run_id=run_id,
                engine=spec.name,
                url=url,
                at=time.time(),
                duration_ms=duration_ms,
                status="failed" if failure else "ok",
                failure_class=failure.value if failure else None,
                detail=detail,
            )
        )
        if self.store is not None:
            self.store.record_attempt(run_id, spec.name, started, duration_ms, failure.value if failure else None, detail)
        return outcome, failure

    def _cache_put(self, canonical: str, engine_name: str, outcome: FetchOutcome) -> None:
        if self.store is not None:
            self.store.put_fetch(
                _cache_key(canonical), canonical, engine_name, outcome.status, outcome.body, dict(outcome.headers), self.fetch_ttl_s
            )

    def _finalize_ok(self, run_id, canonical, engine_path, outcome: FetchOutcome, attempts) -> ScrapeResult:
        result = ScrapeResult(
            url=canonical,
            final_url=outcome.final_url,
            status="ok",
            engine_path=engine_path,
            html=outcome.body.decode("utf-8", errors="ignore"),
            attempts=attempts,
        )
        if self.store is not None:
            self.store.record_run(run_id, canonical, engine_path, "ok")
        return result

    def _finalize_failed(self, run_id, canonical, url, engine_path, attempts) -> ScrapeResult:
        result = ScrapeResult(url=canonical, final_url=url, status="failed", engine_path=engine_path, attempts=attempts)
        if self.store is not None:
            self.store.record_run(run_id, canonical, engine_path, "failed")
        return result


def _cache_key(canonical_url: str) -> str:
    return hashlib.sha256(canonical_url.encode()).hexdigest()


def _content_type_header(outcome: FetchOutcome) -> Optional[str]:
    for k, v in outcome.headers.items():
        if k.lower() == "content-type":
            return v
    return None


def _extract_retry_after(outcome: FetchOutcome) -> float:
    for k, v in outcome.headers.items():
        if k.lower() == "retry-after":
            return parse_retry_after(v)
    return 5.0
