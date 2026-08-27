"""Core dataclasses shared across engines, extractors, and adapters.

EngineContext and the FetchEngine/Extractor Protocols are deferred to Phase 1b
(orchestrator.py) — they need concrete BudgetState/BreakerState types that
don't exist yet. Defining them now against placeholder types would just mean
rewriting them later.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal, Mapping, Optional


class ExecutionModel(Enum):
    ASYNC = "async"
    THREAD = "thread"  # cannot be cancelled once started — see gotcha 16


class FailureClass(Enum):
    RATE_LIMITED = "rate_limited"          # 429, or 503 with Retry-After — same tier, backoff
    BLOCKED = "blocked"                    # 401/403/407/444, 503 w/o Retry-After, blocked_markers hit
    JS_REQUIRED = "js_required"
    AUTH_REQUIRED = "auth_required"
    NETWORK = "network"
    NOT_FOUND = "not_found"                # terminal
    UNSUPPORTED_CONTENT = "unsupported_content"  # terminal
    PARSE_FAILED = "parse_failed"


@dataclass
class FetchOutcome:
    status: Optional[int]
    headers: Mapping[str, str]
    body: bytes
    final_url: str
    redirect_chain: list[str] = field(default_factory=list)
    solver_engaged: bool = False
    # e.g. "cf_challenge" (solved) vs "cf_error_page" (still failed) — classify()
    # must re-escalate on the latter even when solver_engaged is True.
    signals: list[str] = field(default_factory=list)
    elapsed_ms: int = 0


@dataclass
class Attempt:
    engine: str
    started_at: float
    duration_ms: int
    failure_class: Optional[FailureClass]
    detail: str = ""  # MUST be passed through core.guard.redact before this is constructed


@dataclass
class Cost:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    credits: int = 0
    usd: float = 0.0
    exact: bool = False  # False = estimated. Never render an estimate as a measurement.


@dataclass
class ScrapeRequest:
    url: str
    prompt: Optional[str] = None
    schema: Optional[type] = None
    cost_mode: Literal["frugal", "reliable"] = "frugal"
    deadline_s: Optional[float] = None
    formats: tuple[str, ...] = ("markdown",)


@dataclass
class ScrapeResult:
    url: str  # canonical original — redirects must NOT poison identity
    final_url: str
    status: Literal["ok", "partial", "failed"]
    engine_path: list[str] = field(default_factory=list)
    html: Optional[str] = None
    markdown: Optional[str] = None
    data: Optional[dict] = None
    attempts: list[Attempt] = field(default_factory=list)
    cost: Cost = field(default_factory=Cost)
    from_cache: bool = False
