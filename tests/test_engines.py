"""Integration tests against the local fixture server (and, for jina.py, a
mocked httpx client — no real network in the test suite per Phase 1a's
hermetic-tests principle). Browser-tier tests are real Playwright launches;
they are slower but exercise the actual vendor integration, which is the
point of Phase 1b.

The fixture server binds to 127.0.0.1, which the default SSRF guard
correctly rejects — that rejection is the behavior under test elsewhere
(test_guard.py). Fixture-hitting tests here use the documented
allow_private_hosts opt-out, exactly the "legitimate intranet scraping"
case guard.py's docstring describes.
"""
import unittest
from unittest.mock import AsyncMock, patch

from creel.core.guard import GuardConfig, SSRFError
from creel.engines import jina, scrapling_dynamic, scrapling_http, scrapling_stealth
from tests.fixtures.server import FixtureServer

_LOCAL = GuardConfig(allow_private_hosts=True)


class TestScraplingHttp(unittest.IsolatedAsyncioTestCase):
    async def test_ok_page(self):
        with FixtureServer() as server:
            outcome = await scrapling_http.fetch(server.url("/ok"), guard_config=_LOCAL)
        self.assertEqual(outcome.status, 200)
        self.assertIn(b"Fixture OK", outcome.body)

    async def test_403_status_passed_through_not_raised(self):
        with FixtureServer() as server:
            outcome = await scrapling_http.fetch(server.url("/cf-403"), guard_config=_LOCAL)
        self.assertEqual(outcome.status, 403)  # gotcha 6: never raises on non-2xx

    async def test_429_retry_after_header_preserved(self):
        with FixtureServer() as server:
            outcome = await scrapling_http.fetch(server.url("/rate-limited"), guard_config=_LOCAL)
        self.assertEqual(outcome.status, 429)
        headers_lower = {k.lower(): v for k, v in outcome.headers.items()}
        self.assertEqual(headers_lower.get("retry-after"), "2")

    async def test_404_terminal(self):
        with FixtureServer() as server:
            outcome = await scrapling_http.fetch(server.url("/does-not-exist"), guard_config=_LOCAL)
        self.assertEqual(outcome.status, 404)

    async def test_ssrf_preflight_blocks_before_any_network_call(self):
        with self.assertRaises(SSRFError):
            await scrapling_http.fetch("http://169.254.169.254/latest/meta-data/")

    async def test_pdf_bytes_passed_through(self):
        with FixtureServer() as server:
            outcome = await scrapling_http.fetch(server.url("/pdf"), guard_config=_LOCAL)
        self.assertTrue(outcome.body.startswith(b"%PDF-"))


class TestScraplingDynamic(unittest.IsolatedAsyncioTestCase):
    async def test_renders_ok_page(self):
        with FixtureServer() as server:
            outcome = await scrapling_dynamic.fetch(server.url("/ok"), timeout_ms=20000, guard_config=_LOCAL)
        self.assertEqual(outcome.status, 200)
        self.assertIn(b"Fixture OK", outcome.body)

    async def test_ssrf_preflight_blocks_before_launching_browser(self):
        with self.assertRaises(SSRFError):
            await scrapling_dynamic.fetch("http://127.0.0.1:1/admin")


class TestScraplingStealth(unittest.IsolatedAsyncioTestCase):
    async def test_renders_ok_page(self):
        with FixtureServer() as server:
            outcome = await scrapling_stealth.fetch(server.url("/ok"), timeout_ms=30000, guard_config=_LOCAL)
        self.assertEqual(outcome.status, 200)

    async def test_solved_challenge_landing_on_error_page_signals_cf_error_page(self):
        with FixtureServer() as server:
            outcome = await scrapling_stealth.fetch(
                server.url("/cf-error-200"), timeout_ms=30000, guard_config=_LOCAL
            )
        self.assertEqual(outcome.status, 200)
        self.assertIn("cf_error_page", outcome.signals)

    async def test_genuine_ok_page_has_no_cf_signal(self):
        with FixtureServer() as server:
            outcome = await scrapling_stealth.fetch(server.url("/ok"), timeout_ms=30000, guard_config=_LOCAL)
        self.assertNotIn("cf_error_page", outcome.signals)


class TestJina(unittest.IsolatedAsyncioTestCase):
    async def test_builds_reader_url_and_parses_response(self):
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "text/plain"}
        mock_response.content = b"# Example\n\nHello from Jina."

        with patch("httpx.AsyncClient") as MockClient:
            instance = MockClient.return_value.__aenter__.return_value
            instance.get = AsyncMock(return_value=mock_response)
            outcome = await jina.fetch("https://example.com/article")
            instance.get.assert_awaited_once()
            called_url = instance.get.await_args.args[0]

        self.assertEqual(called_url, "https://r.jina.ai/https://example.com/article")
        self.assertEqual(outcome.status, 200)
        self.assertIn(b"Hello from Jina", outcome.body)
        self.assertEqual(outcome.final_url, "https://example.com/article")

    async def test_network_exception_becomes_status_none(self):
        with patch("httpx.AsyncClient") as MockClient:
            instance = MockClient.return_value.__aenter__.return_value
            instance.get = AsyncMock(side_effect=OSError("connection refused"))
            outcome = await jina.fetch("https://example.com/x")
        self.assertIsNone(outcome.status)
        self.assertTrue(any("exception:" in s for s in outcome.signals))

    async def test_no_ssrf_preflight_jina_fetches_from_its_own_network(self):
        # Must NOT raise SSRFError even for a target that would fail
        # preflight on a local engine — Jina's egress is not ours.
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.headers = {}
        mock_response.content = b"ok"
        with patch("httpx.AsyncClient") as MockClient:
            instance = MockClient.return_value.__aenter__.return_value
            instance.get = AsyncMock(return_value=mock_response)
            outcome = await jina.fetch("http://169.254.169.254/x")
        self.assertEqual(outcome.status, 200)


if __name__ == "__main__":
    unittest.main()
