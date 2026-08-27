import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from firecrawl.v2.types import SearchData, SearchResultWeb

from creel.discover.search import search


def _fc_client(web_results):
    instance = AsyncMock()
    instance.search = AsyncMock(return_value=SearchData(web=web_results))
    instance.close = AsyncMock()
    return patch("firecrawl.v2.AsyncFirecrawlClient", return_value=instance)


class TestSearch(unittest.IsolatedAsyncioTestCase):
    async def test_firecrawl_path_returns_results(self):
        web = [SearchResultWeb(url="https://x.com/a", title="A", description="snippet a")]
        with _fc_client(web):
            resp = await search("query", api_key="fc-test")
        self.assertEqual(resp.source, "firecrawl")
        self.assertEqual(resp.results[0].url, "https://x.com/a")
        self.assertEqual(resp.results[0].title, "A")
        self.assertEqual(resp.results[0].snippet, "snippet a")

    async def test_ddgs_fallback_maps_href_body_keys(self):
        raw = [{"title": "Result 1", "href": "https://y.com/1", "body": "snippet 1"}]
        mock_ddgs_instance = MagicMock()
        mock_ddgs_instance.__enter__ = MagicMock(return_value=mock_ddgs_instance)
        mock_ddgs_instance.__exit__ = MagicMock(return_value=False)
        mock_ddgs_instance.text = MagicMock(return_value=raw)

        with patch("ddgs.DDGS", return_value=mock_ddgs_instance):
            resp = await search("query", api_key=None)

        self.assertEqual(resp.source, "ddgs")
        self.assertEqual(resp.results[0].url, "https://y.com/1")
        self.assertEqual(resp.results[0].title, "Result 1")
        self.assertEqual(resp.results[0].snippet, "snippet 1")


if __name__ == "__main__":
    unittest.main()
