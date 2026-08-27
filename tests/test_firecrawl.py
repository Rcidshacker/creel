"""Firecrawl engine tests. AsyncFirecrawlClient is mocked -- no Firecrawl
API key exists in this environment, and this suite tests OUR translation
logic (FirecrawlError -> FetchOutcome, Document -> body/status), not
Firecrawl's own service correctness.
"""
import unittest
from unittest.mock import AsyncMock, patch

from firecrawl.v2.types import Document, DocumentMetadata
from firecrawl.v2.utils.error_handler import RateLimitError, WebsiteNotSupportedError

from creel.engines.firecrawl import available, fetch


def _mock_client(scrape_return=None, scrape_side_effect=None):
    instance = AsyncMock()
    if scrape_side_effect is not None:
        instance.scrape = AsyncMock(side_effect=scrape_side_effect)
    else:
        instance.scrape = AsyncMock(return_value=scrape_return)
    instance.close = AsyncMock()
    return patch("firecrawl.v2.AsyncFirecrawlClient", return_value=instance), instance


class TestAvailable(unittest.TestCase):
    def test_no_key_unavailable(self):
        self.assertFalse(available(None))
        self.assertFalse(available(""))

    def test_with_key_available(self):
        self.assertTrue(available("fc-test"))


class TestFetch(unittest.IsolatedAsyncioTestCase):
    async def test_no_api_key_returns_status_none(self):
        outcome = await fetch("https://example.com", api_key=None)
        self.assertIsNone(outcome.status)
        self.assertIn("NoAPIKey", outcome.signals[0])

    async def test_successful_scrape_returns_html_body_and_status(self):
        doc = Document(
            markdown="# Title",
            html="<html><body>content</body></html>",
            metadata=DocumentMetadata(status_code=200, url="https://example.com/final"),
        )
        patcher, instance = _mock_client(scrape_return=doc)
        with patcher:
            outcome = await fetch("https://example.com", api_key="fc-test")
        self.assertEqual(outcome.status, 200)
        self.assertIn(b"content", outcome.body)
        self.assertEqual(outcome.final_url, "https://example.com/final")
        instance.close.assert_awaited_once()

    async def test_falls_back_to_markdown_when_no_html(self):
        doc = Document(markdown="# Just markdown", html=None, metadata=DocumentMetadata(status_code=200))
        patcher, _ = _mock_client(scrape_return=doc)
        with patcher:
            outcome = await fetch("https://example.com", api_key="fc-test")
        self.assertIn(b"Just markdown", outcome.body)

    async def test_metadata_error_surfaces_as_signal(self):
        doc = Document(html="<html></html>", metadata=DocumentMetadata(status_code=200, error="partial render"))
        patcher, _ = _mock_client(scrape_return=doc)
        with patcher:
            outcome = await fetch("https://example.com", api_key="fc-test")
        self.assertTrue(any("firecrawl_meta_error" in s for s in outcome.signals))

    async def test_firecrawl_error_carries_real_status_code(self):
        err = RateLimitError("rate limited", status_code=429, response=None)
        patcher, instance = _mock_client(scrape_side_effect=err)
        with patcher:
            outcome = await fetch("https://example.com", api_key="fc-test")
        self.assertEqual(outcome.status, 429)
        instance.close.assert_awaited_once()

    async def test_website_not_supported_maps_to_403(self):
        err = WebsiteNotSupportedError("blocked", status_code=403, response=None)
        patcher, _ = _mock_client(scrape_side_effect=err)
        with patcher:
            outcome = await fetch("https://example.com", api_key="fc-test")
        self.assertEqual(outcome.status, 403)

    async def test_generic_exception_becomes_status_none(self):
        patcher, instance = _mock_client(scrape_side_effect=OSError("connection refused"))
        with patcher:
            outcome = await fetch("https://example.com", api_key="fc-test")
        self.assertIsNone(outcome.status)
        self.assertTrue(any("exception:" in s for s in outcome.signals))
        instance.close.assert_awaited_once()

    async def test_client_always_closed_even_on_error(self):
        patcher, instance = _mock_client(scrape_side_effect=RuntimeError("boom"))
        with patcher:
            await fetch("https://example.com", api_key="fc-test")
        instance.close.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
