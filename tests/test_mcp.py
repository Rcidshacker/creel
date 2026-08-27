"""Note: MCPServer does not populate `structured_content` from a bare
`-> dict` return annotation (verified directly — it stays None even for a
successful call with no error). The tool still works correctly: it
serializes the dict as JSON into the text content, which is also the
universal fallback every MCP client supports (structured_content is a
newer, optional extension). Tests read from there.
"""
import json
import unittest
from unittest.mock import AsyncMock, patch

from creel.adapters.mcp import create_server
from creel.core.guard import GuardConfig
from creel.core.models import FetchOutcome
from creel.core.orchestrator import Orchestrator
from creel.extract.base import ExtractOutcome
from tests.fixtures.server import FixtureServer

_LOCAL = GuardConfig(allow_private_hosts=True)


def _payload(result) -> dict:
    return json.loads(result.content[0].text)


class TestFetchFastPath(unittest.IsolatedAsyncioTestCase):
    async def test_fast_path_bypasses_orchestrator_and_returns_markdown(self):
        mock_outcome = FetchOutcome(
            status=200, headers={}, body=b"<html><body><h1>Hi</h1></body></html>", final_url="https://x.com"
        )
        with patch("creel.engines.scrapling_http.fetch", AsyncMock(return_value=mock_outcome)):
            mcp = create_server()
            result = await mcp.call_tool("fetch", {"url": "https://x.com", "escalate": False})
        payload = _payload(result)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["engine_path"], ["scrapling_http"])
        self.assertIn("Hi", payload["markdown"])
        self.assertFalse(payload["truncated"])
        self.assertIsNotNone(payload["html_resource_id"])

    async def test_fast_path_failure_status(self):
        mock_outcome = FetchOutcome(status=404, headers={}, body=b"not found", final_url="https://x.com")
        with patch("creel.engines.scrapling_http.fetch", AsyncMock(return_value=mock_outcome)):
            mcp = create_server()
            result = await mcp.call_tool("fetch", {"url": "https://x.com"})
        self.assertEqual(_payload(result)["status"], "failed")


class TestFetchEscalated(unittest.IsolatedAsyncioTestCase):
    async def test_escalate_true_runs_full_ladder(self):
        with FixtureServer() as server:
            orch = Orchestrator(guard_config=_LOCAL)
            mcp = create_server(orchestrator=orch)
            result = await mcp.call_tool("fetch", {"url": server.url("/ok"), "escalate": True})
        payload = _payload(result)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["engine_path"], ["scrapling_http"])

    async def test_cooldown_active_returns_failed_not_an_exception(self):
        with FixtureServer() as server:
            orch = Orchestrator(guard_config=_LOCAL)
            mcp = create_server(orchestrator=orch)
            await mcp.call_tool("fetch", {"url": server.url("/rate-limited"), "escalate": True})
            result = await mcp.call_tool("fetch", {"url": server.url("/rate-limited"), "escalate": True})
        payload = _payload(result)
        self.assertEqual(payload["status"], "failed")
        self.assertIn("cooling down", payload["error"])


class TestScrape(unittest.IsolatedAsyncioTestCase):
    async def test_scrape_without_prompt_has_no_data(self):
        with FixtureServer() as server:
            orch = Orchestrator(guard_config=_LOCAL)
            mcp = create_server(orchestrator=orch)
            result = await mcp.call_tool("scrape", {"url": server.url("/ok")})
        self.assertIsNone(_payload(result)["data"])

    async def test_scrape_with_prompt_and_extractor_populates_data(self):
        async def fake_extract(html, prompt, schema):
            return ExtractOutcome(data={"title": "Fixture OK"}, exact=True)

        with FixtureServer() as server:
            orch = Orchestrator(guard_config=_LOCAL)
            mcp = create_server(orchestrator=orch, llm_extract=fake_extract, model="test-model")
            result = await mcp.call_tool("scrape", {"url": server.url("/ok"), "prompt": "get the title"})
        self.assertEqual(_payload(result)["data"], {"title": "Fixture OK"})


class TestMarkdownTruncation(unittest.IsolatedAsyncioTestCase):
    async def test_large_page_is_truncated_and_flagged(self):
        huge_body = ("<html><body><article>" + "word " * 20000 + "</article></body></html>").encode()
        mock_outcome = FetchOutcome(status=200, headers={}, body=huge_body, final_url="https://x.com")
        with patch("creel.engines.scrapling_http.fetch", AsyncMock(return_value=mock_outcome)):
            mcp = create_server()
            result = await mcp.call_tool("fetch", {"url": "https://x.com"})
        payload = _payload(result)
        self.assertTrue(payload["truncated"])
        self.assertLessEqual(len(payload["markdown"]), 20_000)


class TestHtmlResource(unittest.IsolatedAsyncioTestCase):
    async def test_full_html_retrievable_by_resource_id(self):
        mock_outcome = FetchOutcome(
            status=200, headers={}, body=b"<html><body>full content here</body></html>", final_url="https://x.com"
        )
        with patch("creel.engines.scrapling_http.fetch", AsyncMock(return_value=mock_outcome)):
            mcp = create_server()
            result = await mcp.call_tool("fetch", {"url": "https://x.com"})
        resource_id = _payload(result)["html_resource_id"]
        contents = await mcp.read_resource(f"creel://html/{resource_id}")
        self.assertIn("full content here", contents[0].content)

    async def test_unknown_resource_id_returns_empty(self):
        mcp = create_server()
        contents = await mcp.read_resource("creel://html/does-not-exist")
        self.assertEqual(contents[0].content, "")


if __name__ == "__main__":
    unittest.main()
