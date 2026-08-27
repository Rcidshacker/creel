"""Per-domain cooldown registry for RATE_LIMITED failures.

This is why 429 must never trigger engine escalation: a request landing
inside an active cooldown fails fast (caller chooses wait-or-refuse) instead
of burning the whole ladder — six wasted attempts per request for the whole
rate-limit window otherwise. Feeds from both acquisition (classify.py) and
discovery (search/map) — everything that talks to a network respects the
same per-peer state, since keyless Jina is politely rate-limited too and
`ddgs` is throttled aggressively.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from threading import Lock
from typing import Optional


@dataclass
class CooldownState:
    until: float
    reason: str = "rate_limited"


class CooldownRegistry:
    def __init__(self) -> None:
        self._lock = Lock()
        self._state: dict[str, CooldownState] = {}

    def register(self, domain: str, retry_after_s: float, reason: str = "rate_limited") -> None:
        until = time.time() + max(retry_after_s, 0.0)
        with self._lock:
            existing = self._state.get(domain)
            if existing is None or until > existing.until:
                self._state[domain] = CooldownState(until=until, reason=reason)

    def active(self, domain: str) -> Optional[CooldownState]:
        with self._lock:
            state = self._state.get(domain)
            if state is not None and state.until <= time.time():
                del self._state[domain]
                state = None
        return state

    def remaining(self, domain: str) -> float:
        state = self.active(domain)
        return max(state.until - time.time(), 0.0) if state else 0.0


def parse_retry_after(value: str, default_s: float = 5.0) -> float:
    """Retry-After is either a delta-seconds integer or an HTTP-date."""
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return default_s
    if dt is None:
        return default_s
    import datetime

    now = datetime.datetime.now(dt.tzinfo or datetime.timezone.utc)
    return max((dt - now).total_seconds(), 0.0)
