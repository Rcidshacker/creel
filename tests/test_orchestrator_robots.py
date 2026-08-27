"""robots.txt gating the stealth tier, at the orchestrator level."""
import unittest

from creel.core.guard import GuardConfig
from creel.core.models import FetchOutcome
from creel.core.orchestrator import EngineSpec, Orchestrator
from creel.core.robots import RobotsChecker
from creel.engines import scrapling_http
from tests.fixtures.server import FixtureServer

_LOCAL = GuardConfig(allow_private_hosts=True)


def _ladder_with_named_stealth():
    # tier3 is deliberately named "scrapling_stealth" to exercise the real
    # gate condition (`spec.name == "scrapling_stealth"`) -- it points at
    # plain scrapling_http.fetch since only the orchestration gate itself
    # is under test here, not real stealth-engine behavior.
    return [
        EngineSpec("tier1", 1, False, scrapling_http.fetch),
        EngineSpec("tier2", 2, True, scrapling_http.fetch),
        EngineSpec("scrapling_stealth", 3, True, scrapling_http.fetch),
    ]


def _remote_stub(target_url: str) -> EngineSpec:
    async def _fetch(url, guard_config=None, **_ignored):
        return await scrapling_http.fetch(target_url, guard_config=_LOCAL)

    return EngineSpec("remote", 4, False, _fetch)


class TestRobotsGatesStealthTier(unittest.IsolatedAsyncioTestCase):
    async def test_disallowed_path_skips_stealth_and_goes_to_remote(self):
        async def deny_fetch(url, *a, **kw):
            return FetchOutcome(status=200, headers={}, body=b"User-agent: *\nDisallow: /\n", final_url=url)

        with FixtureServer() as server:
            checker = RobotsChecker()
            orch = Orchestrator(
                guard_config=_LOCAL,
                local_ladder=_ladder_with_named_stealth(),
                remote_egress_chain=[_remote_stub(server.url("/ok"))],
                robots_checker=checker,
            )
            # Prime the checker with a disallow-everything robots.txt so
            # the ladder gate actually has something to enforce.
            await checker._get_parser(server.url("/cf-403"), fetch_fn=deny_fetch)

            result = await orch.fetch(server.url("/cf-403"))

        self.assertNotIn("scrapling_stealth", result.engine_path, "disallowed path must skip the stealth tier")
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.engine_path, ["tier1", "tier2", "remote"])

    async def test_allowed_path_runs_stealth_normally(self):
        async def allow_fetch(url, *a, **kw):
            return FetchOutcome(status=200, headers={}, body=b"User-agent: *\n", final_url=url)

        with FixtureServer() as server:
            checker = RobotsChecker()
            orch = Orchestrator(
                guard_config=_LOCAL,
                local_ladder=_ladder_with_named_stealth(),
                remote_egress_chain=[_remote_stub(server.url("/ok"))],
                robots_checker=checker,
            )
            await checker._get_parser(server.url("/cf-403"), fetch_fn=allow_fetch)

            result = await orch.fetch(server.url("/cf-403"))

        self.assertIn("scrapling_stealth", result.engine_path)

    async def test_respect_robots_false_disables_the_gate_entirely(self):
        async def deny_fetch(url, *a, **kw):
            return FetchOutcome(status=200, headers={}, body=b"User-agent: *\nDisallow: /\n", final_url=url)

        with FixtureServer() as server:
            checker = RobotsChecker()
            await checker._get_parser(server.url("/cf-403"), fetch_fn=deny_fetch)
            orch = Orchestrator(
                guard_config=_LOCAL,
                local_ladder=_ladder_with_named_stealth(),
                remote_egress_chain=[_remote_stub(server.url("/ok"))],
                robots_checker=checker,
                respect_robots=False,
            )
            result = await orch.fetch(server.url("/cf-403"))

        self.assertIn("scrapling_stealth", result.engine_path)


if __name__ == "__main__":
    unittest.main()
