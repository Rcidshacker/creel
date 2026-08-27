"""Resolves the acquisition policy for one domain from three sources,
evaluated in a single place: global cost_mode default <- domain_policy
entries (glob match) <- learned tier memory. No expression language, no
solver — deliberately. TOML loading into DomainPolicy objects is core.config's
job (Phase 2); this module only resolves already-parsed policy.
"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from typing import Optional

from creel.core.memory import TierMemory


@dataclass
class DomainPolicy:
    glob: str
    start_tier: Optional[int] = None
    allowed_engines: Optional[list[str]] = None


@dataclass
class ResolvedPolicy:
    cost_mode: str
    start_tier: int
    allowed_engines: Optional[list[str]]


class PolicyResolver:
    def __init__(
        self,
        default_cost_mode: str = "frugal",
        domain_policies: Optional[list[DomainPolicy]] = None,
        memory: Optional[TierMemory] = None,
    ) -> None:
        self._default_cost_mode = default_cost_mode
        self._domain_policies = domain_policies or []
        self._memory = memory

    def resolve(self, domain: str, cost_mode_override: Optional[str] = None) -> ResolvedPolicy:
        cost_mode = cost_mode_override or self._default_cost_mode
        start_tier = 1
        allowed_engines: Optional[list[str]] = None

        for policy in self._domain_policies:
            if fnmatch.fnmatch(domain, policy.glob):
                if policy.start_tier is not None:
                    start_tier = policy.start_tier
                if policy.allowed_engines is not None:
                    allowed_engines = policy.allowed_engines

        if self._memory is not None:
            suggested = self._memory.suggest_tier(domain)
            if suggested is not None:
                start_tier = suggested  # freshest evidence wins over static config

        return ResolvedPolicy(cost_mode=cost_mode, start_tier=start_tier, allowed_engines=allowed_engines)
