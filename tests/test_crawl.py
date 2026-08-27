import unittest
from unittest.mock import AsyncMock, patch

from creel.core.crawl import crawl_site
from creel.discover.map import MapResult
from creel.extract.base import ExtractOutcome
from tests.fixtures.server import FixtureServer


def _mapped(urls):
    return MapResult(urls=urls, source="sitemap")


class TestCrawlSite(unittest.IsolatedAsyncioTestCase):
    async def test_ok_and_404_pages_both_reach_parse(self):
        with FixtureServer() as server:
            urls = [server.url("/ok"), server.url("/does-not-exist")]
            with patch("creel.core.crawl.map_site", AsyncMock(return_value=_mapped(urls))):
                results = await crawl_site(server.url("/"), robots_txt_obey=False)

        by_url = {r.url: r for r in results}
        self.assertEqual(by_url[urls[0]].status, "ok")
        self.assertEqual(by_url[urls[1]].status, "failed")

    async def test_blocked_page_never_reaching_parse_still_shows_up_as_failed(self):
        # Scrapling's own retry_blocked_request exhausts internally and the
        # page never reaches parse() at all -- must still appear in the
        # final result set via seed-list reconciliation, not vanish.
        with FixtureServer() as server:
            urls = [server.url("/ok"), server.url("/cf-403")]
            with patch("creel.core.crawl.map_site", AsyncMock(return_value=_mapped(urls))):
                results = await crawl_site(server.url("/"), robots_txt_obey=False)

        by_url = {r.url: r for r in results}
        self.assertEqual(len(results), 2, "every seed URL must appear exactly once")
        self.assertEqual(by_url[urls[1]].status, "failed")
        self.assertIsNone(by_url[urls[1]].data)

    async def test_empty_map_result_returns_empty_list_without_spawning_a_spider(self):
        with patch("creel.core.crawl.map_site", AsyncMock(return_value=_mapped([]))):
            results = await crawl_site("https://x.com")
        self.assertEqual(results, [])

    async def test_extraction_runs_per_page_with_prompt_and_extractor(self):
        async def fake_extract(html, prompt, schema):
            return ExtractOutcome(data={"found": "yes"}, exact=True)

        with FixtureServer() as server:
            urls = [server.url("/ok")]
            with patch("creel.core.crawl.map_site", AsyncMock(return_value=_mapped(urls))):
                results = await crawl_site(
                    server.url("/"), prompt="find something", llm_extract=fake_extract,
                    model="test-model", robots_txt_obey=False,
                )

        self.assertEqual(results[0].status, "ok")
        self.assertEqual(results[0].data, {"found": "yes"})

    async def test_no_prompt_means_no_data(self):
        with FixtureServer() as server:
            urls = [server.url("/ok")]
            with patch("creel.core.crawl.map_site", AsyncMock(return_value=_mapped(urls))):
                results = await crawl_site(server.url("/"), robots_txt_obey=False)
        self.assertIsNone(results[0].data)


if __name__ == "__main__":
    unittest.main()
