"""Minimal emit hook for ladder progress. Designed in now because
retrofitting an event bus into a finished orchestrator is painful, and a
callback is trivial.

CLI (Phase 1b) prints these incrementally. API (Phase 3) turns them into SSE
so a long ladder walk streams progress instead of the client timing out
(gotcha 14). MCP ignores them.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional


@dataclass
class AttemptStarted:
    run_id: str
    engine: str
    url: str
    at: float


@dataclass
class AttemptFinished:
    run_id: str
    engine: str
    url: str
    at: float
    duration_ms: int
    status: str  # "ok" | "failed"
    failure_class: Optional[str] = None
    detail: str = ""  # already passed through core.guard.redact by the caller


Event = AttemptStarted | AttemptFinished
EventHandler = Callable[[Event], None]


class EventBus:
    """Synchronous pub/sub. No queue, no async — handlers run inline on emit."""

    def __init__(self) -> None:
        self._handlers: list[EventHandler] = []

    def subscribe(self, handler: EventHandler) -> None:
        self._handlers.append(handler)

    def emit(self, event: Event) -> None:
        for handler in self._handlers:
            handler(event)
