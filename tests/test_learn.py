import os
import tempfile
import unittest
from pathlib import Path

from creel.core.store import Store
from creel.extract.learn import load_learned_selectors, template_hash, try_learn


class TestTemplateHash(unittest.TestCase):
    def test_numeric_segments_normalized(self):
        self.assertEqual(template_hash("https://x.com/product/123"), template_hash("https://x.com/product/456"))

    def test_different_path_shapes_differ(self):
        self.assertNotEqual(template_hash("https://x.com/product/123"), template_hash("https://x.com/category/123"))

    def test_root_path(self):
        self.assertEqual(template_hash("https://x.com/"), "/")


class TestTryLearn(unittest.TestCase):
    def setUp(self):
        # ignore_cleanup_errors: Scrapling's adaptive SQLite connection is
        # lru_cache-wrapped and never explicitly closed by our code, so
        # Windows won't allow deleting the file afterward.
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.store = Store(Path(self._tmp.name) / "creel.db")
        self.selector_db = str(Path(self._tmp.name) / "adaptive.db")

    def tearDown(self):
        self.store.close()
        self._tmp.cleanup()

    def test_valid_matching_selectors_are_cached(self):
        html = "<html><body><h1 class='title'>Widget</h1></body></html>"
        ok = try_learn(
            self.store, "x.com", "https://x.com/product/1", html,
            extracted_data={"title": "Widget"},
            candidate_selectors={"title": ".title"},
            storage_file=self.selector_db,
        )
        self.assertTrue(ok)
        learned = load_learned_selectors(self.store, "x.com", "https://x.com/product/2")  # same template
        self.assertEqual(learned, {"title": ".title"})

    def test_selector_that_produces_wrong_value_is_rejected(self):
        html = "<html><body><h1 class='title'>Actual Value</h1></body></html>"
        ok = try_learn(
            self.store, "x.com", "https://x.com/product/1", html,
            extracted_data={"title": "Hallucinated Value"},  # LLM's claimed value doesn't match the page
            candidate_selectors={"title": ".title"},
            storage_file=self.selector_db,
        )
        self.assertFalse(ok)
        self.assertIsNone(load_learned_selectors(self.store, "x.com", "https://x.com/product/2"))

    def test_selector_that_does_not_match_anything_is_rejected(self):
        html = "<html><body><h1 class='title'>Widget</h1></body></html>"
        ok = try_learn(
            self.store, "x.com", "https://x.com/product/1", html,
            extracted_data={"title": "Widget"},
            candidate_selectors={"title": ".nonexistent-class"},
            storage_file=self.selector_db,
        )
        self.assertFalse(ok)

    def test_no_candidate_selectors_is_a_no_op(self):
        ok = try_learn(
            self.store, "x.com", "https://x.com/product/1", "<html></html>",
            extracted_data={"title": "Widget"}, candidate_selectors={}, storage_file=self.selector_db,
        )
        self.assertFalse(ok)

    def test_unlearned_domain_returns_none(self):
        self.assertIsNone(load_learned_selectors(self.store, "never-seen.com", "https://never-seen.com/x"))


if __name__ == "__main__":
    unittest.main()
