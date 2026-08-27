"""Per-domain acquisition-tier memory: which tier last succeeded, so the next
request starts there instead of re-walking the full ladder. The highest-
value optimization in the whole design — re-walking http->dynamic->stealth
against a known-hostile domain wastes the entire latency budget and, once
Firecrawl is in the ladder, real money too.

Entries are HINTS with a TTL, not verdicts. Two decay mechanisms keep them
honest:

1. TTL expiry — an entry older than ttl_s is discarded outright.
2. Probe-down — after enough consecutive successes at a tier, the NEXT
   suggestion is one tier cheaper. Without this, tier memory calcifies into
   a permanent browser launch long after a site relaxes its defenses.

Also encodes the IP-vs-domain inference: if local stealth failed but a
remote-egress rung (Jina/Firecrawl) then succeeded, the differing variable
was OUR IP, not the domain's policy. That case is tagged ip_suspect and
`suggest_tier` returns None for it — the next request retries the local
ladder from tier 1 rather than latching the domain as permanently hostile.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional


@dataclass
class MemoryEntry:
    tier: int
    updated_at: float
    ttl_s: float
    successes_since_probe: int = 0
    ip_suspect: bool = False


class TierMemory:
    def __init__(self, ttl_s: float = 3600.0, probe_every: int = 20) -> None:
        self._ttl_s = ttl_s
        self._probe_every = probe_every
        self._entries: dict[str, MemoryEntry] = {}

    def suggest_tier(self, domain: str) -> Optional[int]:
        entry = self._entries.get(domain)
        if entry is None:
            return None
        if time.time() - entry.updated_at > entry.ttl_s:
            del self._entries[domain]
            return None
        if entry.ip_suspect:
            return None
        if entry.tier > 1 and entry.successes_since_probe >= self._probe_every:
            return entry.tier - 1
        return entry.tier

    def record_success(self, domain: str, tier: int) -> None:
        entry = self._entries.get(domain)
        if entry is None or entry.tier != tier:
            self._entries[domain] = MemoryEntry(tier=tier, updated_at=time.time(), ttl_s=self._ttl_s)
        else:
            entry.updated_at = time.time()
            entry.successes_since_probe += 1

    def record_ip_suspect(self, domain: str) -> None:
        """Local stealth BLOCKED, remote egress ok -> our IP was the
        problem. Do not latch the domain as hostile."""
        entry = self._entries.setdefault(
            domain, MemoryEntry(tier=1, updated_at=time.time(), ttl_s=self._ttl_s)
        )
        entry.ip_suspect = True
        entry.updated_at = time.time()

    def record_domain_hostile(self, domain: str, tier: int) -> None:
        """Local AND remote both blocked -> genuinely the domain's policy."""
        self._entries[domain] = MemoryEntry(tier=tier, updated_at=time.time(), ttl_s=self._ttl_s)
