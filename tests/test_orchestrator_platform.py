"""AUTH_REQUIRED -> platform_cli orchestrator wiring."""
import unittest
from unittest.mock import AsyncMock, patch

from creel.core.guard import GuardConfig
from creel.core.models import FetchOutcome
from creel.core.orchestrator import EngineSpec, Orchestrator
from creel.engines import scrapling_http
from tests.fixtures.server import FixtureServer

_LOCAL = GuardConfig(allow_private_hosts=True)


def _stub_local_ladder():
    return [
        EngineSpec("tier1", 1, False, scrapling_http.fetch),
        EngineSpec("tier2", 2, True, scrapling_http.fetch),
        EngineSpec("tier3", 3, True, scrapling_http.fetch),
    ]


class TestAuthRequiredRoutesToPlatformCli(unittest.IsolatedAsyncioTestCase):
    async def test_success_stops_at_tier1_plus_platform_cli(self):
        mock_outcome = FetchOutcome(status=200, headers={}, body=b'{"ok": true}', final_url="x")
        with FixtureServer() as server:
            with patch("creel.engines.platform_cli.fetch", AsyncMock(return_value=mock_outcome)) as mock_fetch:
                orch = Orchestrator(guard_config=_LOCAL, local_ladder=_stub_local_ladder())
                result = await orch.fetch(server.url("/login-wall"))
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.engine_path, ["tier1", "platform_cli"])
        mock_fetch.assert_awaited_once()

    async def test_platform_cli_failure_terminates_no_further_escalation(self):
        failing_outcome = FetchOutcome(status=None, headers={}, body=b"", final_url="x", signals=["exception:x"])
        with FixtureServer() as server:
            with patch("creel.engines.platform_cli.fetch", AsyncMock(return_value=failing_outcome)):
                orch = Orchestrator(guard_config=_LOCAL, local_ladder=_stub_local_ladder())
                result = await orch.fetch(server.url("/login-wall"))
        self.assertEqual(result.status, "failed")
        self.assertEqual(
            result.engine_path, ["tier1", "platform_cli"], "must not fall through to tier2/tier3 or remote egress"
        )


if __name__ == "__main__":
    unittest.main()
