"""Circuit breaker per (engine, domain): closed -> open after N consecutive
failures -> half-open after a cooldown -> one success closes it, one failure
reopens with the cooldown doubled up to a ceiling.

Without a stated reset policy, a transient outage bans a domain from an
engine forever, and core.memory's tier hints become a one-way ratchet toward
always assuming the worst.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum


class BreakerState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class _Entry:
    state: BreakerState
    consecutive_failures: int
    opened_at: float
    cooldown_s: float
    base_cooldown_s: float
    max_cooldown_s: float


class CircuitBreaker:
    def __init__(self, failure_threshold: int = 3, base_cooldown_s: float = 5.0, max_cooldown_s: float = 300.0):
        self._threshold = failure_threshold
        self._base_cooldown = base_cooldown_s
        self._max_cooldown = max_cooldown_s
        self._entries: dict[tuple[str, str], _Entry] = {}

    def _entry(self, engine: str, domain: str) -> _Entry:
        key = (engine, domain)
        entry = self._entries.get(key)
        if entry is None:
            entry = _Entry(
                state=BreakerState.CLOSED,
                consecutive_failures=0,
                opened_at=0.0,
                cooldown_s=self._base_cooldown,
                base_cooldown_s=self._base_cooldown,
                max_cooldown_s=self._max_cooldown,
            )
            self._entries[key] = entry
        return entry

    def allow(self, engine: str, domain: str) -> bool:
        entry = self._entry(engine, domain)
        if entry.state == BreakerState.OPEN:
            if time.time() - entry.opened_at >= entry.cooldown_s:
                entry.state = BreakerState.HALF_OPEN
                return True
            return False
        return True  # CLOSED or HALF_OPEN (one trial request permitted)

    def record_success(self, engine: str, domain: str) -> None:
        entry = self._entry(engine, domain)
        entry.state = BreakerState.CLOSED
        entry.consecutive_failures = 0
        entry.cooldown_s = entry.base_cooldown_s

    def record_failure(self, engine: str, domain: str) -> None:
        entry = self._entry(engine, domain)
        entry.consecutive_failures += 1
        if entry.state == BreakerState.HALF_OPEN:
            entry.cooldown_s = min(entry.cooldown_s * 2, entry.max_cooldown_s)
            entry.state = BreakerState.OPEN
            entry.opened_at = time.time()
            return
        if entry.consecutive_failures >= self._threshold:
            entry.state = BreakerState.OPEN
            entry.opened_at = time.time()

    def state(self, engine: str, domain: str) -> BreakerState:
        return self._entry(engine, domain).state
