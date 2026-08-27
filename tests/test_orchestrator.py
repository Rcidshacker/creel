"""Ladder-level acceptance tests — the Phase 1b verify criteria from the
plan. All hermetic: hits the local fixture server only. The local ladder
here uses scrapling_http.fetch for all three "tiers" (renamed) rather than
real browser engines — engine-specific rendering correctness is already
covered by test_engines.py; what's under test here is ORCHESTRATION logic
(escalation, cooldown, cache, single-flight, breaker, memory), which does
not depend on which engine produced a given status code.
"""
import asyncio
import unittest

from creel.core.breaker import CircuitBreaker
from creel.core.cooldown import CooldownRegistry
from creel.core.guard import GuardConfig
from creel.core.memory import TierMemory
from creel.core.orchestrator import CooldownActive, EngineSpec, Orchestrator
from creel.core.store import Store
from creel.engines import scrapling_http
from tests.fixtures.server import FixtureServer

_LOCAL = GuardConfig(allow_private_hosts=True)


def _stub_local_ladder():
    """Three tiers, all real HTTP calls to whatever URL is actually passed —
    correct for exercising escalation logic against fixed-response routes."""
    return [
        EngineSpec("tier1", 1, False, scrapling_http.fetch),
        EngineSpec("tier2", 2, True, scrapling_http.fetch),
        EngineSpec("tier3", 3, True, scrapling_http.fetch),
    ]


def _fixed_target_remote(name: str, target_url: str) -> EngineSpec:
    """A remote-egress stand-in that always fetches `target_url` regardless
    of what the orchestrator asks for — simulates "the remote rung reaches a
    different outcome than local" without needing a real second network."""

    async def _fetch(url, guard_config=None, **_ignored):
        return await scrapling_http.fetch(target_url, guard_config=_LOCAL)

    return EngineSpec(name, 4, False, _fetch)


def _counting_engine(name: str, tier: int, needs_browser: bool):
    calls = {"n": 0}

    async def _fetch(url, guard_config=None, **_ignored):
        calls["n"] += 1
        return await scrapling_http.fetch(url, guard_config=guard_config)

    return EngineSpec(name, tier, needs_browser, _fetch), calls


def _make_orchestrator(server: FixtureServer, remote_target: str = "/cf-403", store: Store = None) -> Orchestrator:
    return Orchestrator(
        store=store,
        breaker=CircuitBreaker(failure_threshold=1, base_cooldown_s=9999),  # 1 failure opens; irrelevant to these tests
        cooldowns=CooldownRegistry(),
        memory=TierMemory(),
        guard_config=_LOCAL,
        local_ladder=_stub_local_ladder(),
        remote_egress=_fixed_target_remote("remote", server.url(remote_target)),
    )


class TestLadderEscalation(unittest.IsolatedAsyncioTestCase):
    async def test_ok_page_uses_tier1_only(self):
        with FixtureServer() as server:
            orch = _make_orchestrator(server)
            result = await orch.fetch(server.url("/ok"))
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.engine_path, ["tier1"])

    async def test_404_terminates_without_escalation(self):
        with FixtureServer() as server:
            orch = _make_orchestrator(server)
            result = await orch.fetch(server.url("/does-not-exist"))
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.engine_path, ["tier1"], "404 must not escalate to any other tier")

    async def test_pdf_by_extension_terminates_before_any_engine_call(self):
        with FixtureServer() as server:
            orch = _make_orchestrator(server)
            result = await orch.fetch(server.url("/report.pdf"))
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.engine_path, [], "a .pdf URL must be rejected before any fetch")

    async def test_pdf_by_content_type_terminates_after_one_fetch_no_escalation(self):
        # The fixture's /pdf route has no .pdf extension — content-type is
        # only knowable after the first fetch. Must still terminate rather
        # than being pushed through browser tiers or cached as HTML.
        with FixtureServer() as server:
            orch = _make_orchestrator(server)
            result = await orch.fetch(server.url("/pdf"))
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.engine_path, ["tier1"], "must stop at tier1, never reach tier2/tier3")

    async def test_blocked_escalates_through_full_local_ladder_then_remote(self):
        with FixtureServer() as server:
            orch = _make_orchestrator(server, remote_target="/cf-403")  # remote also blocked
            result = await orch.fetch(server.url("/cf-403"))
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.engine_path, ["tier1", "tier2", "tier3", "remote"])

    async def test_blocked_locally_but_remote_ok_succeeds_and_tags_ip_suspect(self):
        memory = TierMemory()
        with FixtureServer() as server:
            orch = Orchestrator(
                breaker=CircuitBreaker(failure_threshold=1, base_cooldown_s=9999),
                cooldowns=CooldownRegistry(),
                memory=memory,
                guard_config=_LOCAL,
                local_ladder=_stub_local_ladder(),
                remote_egress=_fixed_target_remote("remote", server.url("/ok")),
            )
            result = await orch.fetch(server.url("/cf-403"))
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.engine_path, ["tier1", "tier2", "tier3", "remote"])
        domain = list(memory._entries.keys())[0]
        self.assertTrue(memory._entries[domain].ip_suspect)
        self.assertIsNone(memory.suggest_tier(domain), "ip_suspect must not latch the domain as hostile")


class TestRateLimit(unittest.IsolatedAsyncioTestCase):
    async def test_429_registers_cooldown_and_does_not_switch_engine(self):
        with FixtureServer() as server:
            orch = _make_orchestrator(server)
            result = await orch.fetch(server.url("/rate-limited"))
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.engine_path, ["tier1"], "429 must never trigger engine escalation")
        domain = list(orch.cooldowns._state.keys())[0]
        self.assertIsNotNone(orch.cooldowns.active(domain))

    async def test_second_request_inside_cooldown_fails_fast(self):
        with FixtureServer() as server:
            orch = _make_orchestrator(server)
            await orch.fetch(server.url("/rate-limited"))
            with self.assertRaises(CooldownActive):
                await orch.fetch(server.url("/rate-limited"))


class TestCache(unittest.IsolatedAsyncioTestCase):
    async def test_cache_hit_does_zero_engine_calls(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp, FixtureServer() as server:
            store = Store(Path(tmp) / "creel.db")
            counting_spec, calls = _counting_engine("tier1", 1, False)
            orch = Orchestrator(
                store=store,
                breaker=CircuitBreaker(),
                cooldowns=CooldownRegistry(),
                memory=TierMemory(),
                guard_config=_LOCAL,
                local_ladder=[counting_spec],
                remote_egress=_fixed_target_remote("remote", server.url("/ok")),
            )
            first = await orch.fetch(server.url("/ok"))
            self.assertFalse(first.from_cache)
            self.assertEqual(calls["n"], 1)

            second = await orch.fetch(server.url("/ok"))
            self.assertTrue(second.from_cache)
            self.assertEqual(calls["n"], 1, "cache hit must not invoke any engine")
            store.close()


class TestSingleFlight(unittest.IsolatedAsyncioTestCase):
    async def test_two_concurrent_identical_urls_produce_one_fetch(self):
        with FixtureServer() as server:
            counting_spec, calls = _counting_engine("tier1", 1, False)
            orch = Orchestrator(
                breaker=CircuitBreaker(),
                cooldowns=CooldownRegistry(),
                memory=TierMemory(),
                guard_config=_LOCAL,
                local_ladder=[counting_spec],
                remote_egress=_fixed_target_remote("remote", server.url("/ok")),
            )
            url = server.url("/ok")
            results = await asyncio.gather(orch.fetch(url), orch.fetch(url))
        self.assertEqual(calls["n"], 1, "concurrent identical requests must share one fetch")
        self.assertTrue(all(r.status == "ok" for r in results))


if __name__ == "__main__":
    unittest.main()
