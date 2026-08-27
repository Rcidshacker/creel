import tempfile
import unittest
from pathlib import Path

from creel.extract.selectors import establish_adaptive_baseline, extract_with_selectors


class TestExtractWithSelectors(unittest.TestCase):
    def test_matches_all_fields(self):
        html = "<html><body><h1 class='title'>Widget</h1><span class='price'>$9.99</span></body></html>"
        result = extract_with_selectors(
            html, "https://x.com/a", {"title": ".title", "price": ".price"}, allow_adaptive=False
        )
        self.assertEqual(result.data, {"title": "Widget", "price": "$9.99"})
        self.assertFalse(result.healed)

    def test_any_missing_field_returns_none_entirely(self):
        html = "<html><body><h1 class='title'>Widget</h1></body></html>"
        result = extract_with_selectors(
            html, "https://x.com/a", {"title": ".title", "price": ".price"}, allow_adaptive=False
        )
        self.assertIsNone(result.data)


class TestAdaptiveHealing(unittest.TestCase):
    def setUp(self):
        # Scrapling's SQLiteStorageSystem is lru_cache-wrapped internally and
        # never explicitly closed by our code, so the file handle stays open
        # for the life of the process — Windows refuses to delete an
        # open file. ignore_cleanup_errors=True means teardown just leaves
        # it behind in the OS temp dir rather than failing the test.
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = str(Path(self._tmp.name) / "adaptive.db")

    def tearDown(self):
        self._tmp.cleanup()

    def test_adaptive_relocates_element_after_dom_drift(self):
        url = "https://x.com/product/1"
        html1 = "<html><body><div class='title'>Hello World</div><p>other</p></body></html>"
        html2 = "<html><body><p>other</p><span class='different-title'>Hello World</span></body></html>"

        establish_adaptive_baseline(html1, url, {"title": ".title"}, storage_file=self.db_path)

        drifted = extract_with_selectors(
            html2, url, {"title": ".title"}, allow_adaptive=True, storage_file=self.db_path
        )
        self.assertEqual(drifted.data, {"title": "Hello World"})
        self.assertTrue(drifted.healed, "a relocated match must be flagged healed")

    def test_no_healing_flag_when_literal_selector_still_matches(self):
        url = "https://x.com/product/2"
        html = "<html><body><div class='title'>Same Structure</div></body></html>"
        establish_adaptive_baseline(html, url, {"title": ".title"}, storage_file=self.db_path)

        result = extract_with_selectors(html, url, {"title": ".title"}, allow_adaptive=True, storage_file=self.db_path)
        self.assertFalse(result.healed)

    def test_without_adaptive_baseline_a_missed_selector_stays_missed(self):
        url = "https://x.com/product/3"
        html = "<html><body><span class='different-title'>No baseline</span></body></html>"
        result = extract_with_selectors(html, url, {"title": ".title"}, allow_adaptive=True, storage_file=self.db_path)
        self.assertIsNone(result.data)


if __name__ == "__main__":
    unittest.main()
