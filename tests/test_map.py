import unittest
from unittest.mock import AsyncMock, patch

from firecrawl.v2.types import MapData, SearchResult as FCSearchResult

from creel.core.models import FetchOutcome
from creel.discover.map import map_site


def _fc_client(links):
    instance = AsyncMock()
    instance.map = AsyncMock(return_value=MapData(links=links))
    instance.close = AsyncMock()
    return patch("firecrawl.v2.AsyncFirecrawlClient", return_value=instance)


class TestMapSite(unittest.IsolatedAsyncioTestCase):
    async def test_firecrawl_path_returns_urls(self):
        links = [FCSearchResult(url="https://x.com/a"), FCSearchResult(url="https://x.com/b")]
        with _fc_client(links):
            result = await map_site("https://x.com", api_key="fc-test", limit=100)
        self.assertEqual(result.source, "firecrawl")
        self.assertEqual(result.urls, ["https://x.com/a", "https://x.com/b"])

    async def test_fallback_uses_sitemap_when_available(self):
        sitemap_body = b"<urlset><url><loc>https://x.com/1</loc></url><url><loc>https://x.com/2</loc></url></urlset>"

        async def fake_fetch(url, *a, **kw):
            if url.endswith("/sitemap.xml"):
                return FetchOutcome(status=200, headers={}, body=sitemap_body, final_url=url)
            return FetchOutcome(status=404, headers={}, body=b"", final_url=url)

        with patch("creel.engines.scrapling_http.fetch", side_effect=fake_fetch):
            result = await map_site("https://x.com", api_key=None, limit=100)
        self.assertEqual(result.source, "sitemap")
        self.assertEqual(result.urls, ["https://x.com/1", "https://x.com/2"])

    async def test_fallback_uses_links_when_no_sitemap(self):
        page_body = b"<html><body><a href='/a'>A</a><a href='/b'>B</a></body></html>"

        async def fake_fetch(url, *a, **kw):
            if url.endswith("/sitemap.xml"):
                return FetchOutcome(status=404, headers={}, body=b"", final_url=url)
            return FetchOutcome(status=200, headers={}, body=page_body, final_url=url)

        with patch("creel.engines.scrapling_http.fetch", side_effect=fake_fetch):
            result = await map_site("https://x.com/", api_key=None, limit=100)
        self.assertEqual(result.source, "links")
        self.assertEqual(set(result.urls), {"https://x.com/a", "https://x.com/b"})

    async def test_limit_caps_results_and_flags_truncated(self):
        sitemap_body = b"".join(f"<url><loc>https://x.com/{i}</loc></url>".encode() for i in range(10))
        sitemap_body = b"<urlset>" + sitemap_body + b"</urlset>"

        async def fake_fetch(url, *a, **kw):
            return FetchOutcome(status=200, headers={}, body=sitemap_body, final_url=url)

        with patch("creel.engines.scrapling_http.fetch", side_effect=fake_fetch):
            result = await map_site("https://x.com", api_key=None, limit=3)
        self.assertEqual(len(result.urls), 3)
        self.assertTrue(result.truncated)


if __name__ == "__main__":
    unittest.main()
