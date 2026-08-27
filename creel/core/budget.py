"""Daily/monthly USD spend ceilings for paid rungs (LLM tokens, and later
Firecrawl credits). A breached ceiling disables a rung the same way a
missing API key does — no crash, just one fewer option in the ladder.

`now` is always injectable so tests don't depend on real wall-clock time.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

_DAY_S = 86400.0
_MONTH_S = _DAY_S * 30


@dataclass
class BudgetState:
    daily_usd_limit: float = float("inf")
    monthly_usd_limit: float = float("inf")
    # Lazily set to the FIRST `now` this instance ever sees (real or
    # injected by a test) rather than time.time() at construction — a
    # construction-time default would silently ignore every injected `now`
    # in record_spend/remaining_*, since `now - <real epoch>` for a small
    # fake `now` never crosses the rollover threshold.
    _day_start: Optional[float] = None
    _month_start: Optional[float] = None
    _daily_spent: float = 0.0
    _monthly_spent: float = 0.0

    def _roll(self, now: float) -> None:
        if self._day_start is None:
            self._day_start = now
        if self._month_start is None:
            self._month_start = now
        if now - self._day_start >= _DAY_S:
            self._daily_spent = 0.0
            self._day_start = now
        if now - self._month_start >= _MONTH_S:
            self._monthly_spent = 0.0
            self._month_start = now

    def record_spend(self, usd: float, now: float | None = None) -> None:
        now = time.time() if now is None else now
        self._roll(now)
        self._daily_spent += usd
        self._monthly_spent += usd

    def remaining_daily(self, now: float | None = None) -> float:
        now = time.time() if now is None else now
        self._roll(now)
        return max(self.daily_usd_limit - self._daily_spent, 0.0)

    def remaining_monthly(self, now: float | None = None) -> float:
        now = time.time() if now is None else now
        self._roll(now)
        return max(self.monthly_usd_limit - self._monthly_spent, 0.0)

    def has_budget(self, now: float | None = None) -> bool:
        return self.remaining_daily(now) > 0 and self.remaining_monthly(now) > 0
