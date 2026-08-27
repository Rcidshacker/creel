"""Firecrawl-specific orchestrator wiring: remote-egress chain ordering by
cost_mode, and PDF routing. Separate from test_orchestrator.py's Phase 1b
suite since this exercises Phase 3 additions specifically.
"""
import unittest
from unittest.mock import AsyncMock, patch

from creel.core.guard import GuardConfig
from creel.core.models import FetchOutcome
from creel.core.orchestrator import Orchestrator, default_remote_egress_chain
from tests.fixtures.server import FixtureServer

_LOCAL = GuardConfig(allow_private_hosts=True)


class TestDefaultRemoteEgressChainOrdering(unittest.TestCase):
    def test_no_key_returns_jina_only(self):
        chain = default_remote_egress_chain("frugal", firecrawl_api_key=None)
        self.assertEqual([s.name for s in chain], ["jina"])

    def test_frugal_tries_jina_before_firecrawl(self):
        chain = default_remote_egress_chain("frugal", firecrawl_api_key="fc-test")
        self.assertEqual([s.name for s in chain], ["jina", "firecrawl"])

    def test_reliable_tries_firecrawl_before_jina(self):
        chain = default_remote_egress_chain("reliable", firecrawl_api_key="fc-test")
        self.assertEqual([s.name for s in chain], ["firecrawl", "jina"])


class TestOrchestratorRemoteChainWiring(unittest.IsolatedAsyncioTestCase):
    async def test_resolved_cost_mode_reaches_the_chain_builder(self):
        captured = {}

        def fake_chain(cost_mode, firecrawl_api_key=None):
            captured["cost_mode"] = cost_mode
            return default_remote_egress_chain(cost_mode, firecrawl_api_key)

        with FixtureServer() as server:
            orch = Orchestrator(guard_config=_LOCAL, firecrawl_api_key=None)
            with patch("creel.core.orchestrator.default_remote_egress_chain", side_effect=fake_chain):
                # /cf-403 will exhaust the local ladder against the real
                # Scrapling engines and reach remote resolution -- slower
                # but exercises the actual code path, not a stub.
                await orch.fetch(server.url("/cf-403"), cost_mode="reliable")
        self.assertEqual(captured["cost_mode"], "reliable")


class TestPdfRoutesToFirecrawl(unittest.IsolatedAsyncioTestCase):
    async def test_pdf_by_extension_routes_to_firecrawl_when_key_present(self):
        mock_outcome = FetchOutcome(
            status=200, headers={}, body=b"# PDF content as markdown", final_url="https://x.com/report.pdf"
        )
        with patch("creel.engines.firecrawl.fetch", AsyncMock(return_value=mock_outcome)) as mock_fetch:
            orch = Orchestrator(guard_config=_LOCAL, firecrawl_api_key="fc-test")
            result = await orch.fetch("https://x.com/report.pdf")
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.engine_path, ["firecrawl"])
        mock_fetch.assert_awaited_once()

    async def test_pdf_without_key_terminates_with_no_engine_call(self):
        orch = Orchestrator(guard_config=_LOCAL, firecrawl_api_key=None)
        result = await orch.fetch("https://x.com/report.pdf")
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.engine_path, [])

    async def test_pdf_discovered_via_content_type_appends_firecrawl_after_tier1(self):
        # The fixture's /pdf route has no .pdf extension -- content-type is
        # only knowable after the first fetch (tier1), then must route to
        # Firecrawl rather than terminating or continuing to browser tiers.
        mock_outcome = FetchOutcome(status=200, headers={}, body=b"# PDF as markdown", final_url="http://x/pdf")
        with FixtureServer() as server:
            with patch("creel.engines.firecrawl.fetch", AsyncMock(return_value=mock_outcome)):
                orch = Orchestrator(guard_config=_LOCAL, firecrawl_api_key="fc-test")
                result = await orch.fetch(server.url("/pdf"))
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.engine_path, ["scrapling_http", "firecrawl"])

    async def test_pdf_firecrawl_failure_still_terminates_cleanly(self):
        failing_outcome = FetchOutcome(status=500, headers={}, body=b"error", final_url="https://x.com/report.pdf")
        with patch("creel.engines.firecrawl.fetch", AsyncMock(return_value=failing_outcome)):
            orch = Orchestrator(guard_config=_LOCAL, firecrawl_api_key="fc-test")
            result = await orch.fetch("https://x.com/report.pdf")
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.engine_path, ["firecrawl"])


if __name__ == "__main__":
    unittest.main()
