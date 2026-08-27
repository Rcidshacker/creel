import unittest

from creel.core.prune import count_tokens, enforce_budget, prune_html


class TestPruneHtml(unittest.TestCase):
    def test_strips_boilerplate_and_keeps_content(self):
        html = (
            "<html><body>"
            "<nav>" + "<a href='#'>link</a>" * 50 + "</nav>"
            "<article><h1>Real Title</h1><p>The actual content people came for.</p></article>"
            "<footer>" + "boilerplate " * 50 + "</footer>"
            "</body></html>"
        )
        result = prune_html(html, main_content_only=True)
        self.assertIn("Real Title", result)
        self.assertLess(len(result), len(html))

    def test_falls_back_to_raw_on_near_empty_readability_result(self):
        html = "<html><body></body></html>"
        result = prune_html(html)
        self.assertEqual(result, html)


class TestCountTokens(unittest.TestCase):
    def test_returns_positive_count(self):
        self.assertGreater(count_tokens("hello world, this is a test"), 0)

    def test_longer_text_has_more_tokens(self):
        short = count_tokens("hello")
        long = count_tokens("hello " * 500)
        self.assertGreater(long, short)


class TestEnforceBudget(unittest.TestCase):
    def test_under_budget_unchanged(self):
        text = "short text"
        result, truncated = enforce_budget(text, model_tokens=1000)
        self.assertEqual(result, text)
        self.assertFalse(truncated)

    def test_over_budget_truncates_and_flags(self):
        text = "word " * 5000  # way more than 50 tokens
        result, truncated = enforce_budget(text, model_tokens=50)
        self.assertTrue(truncated)
        self.assertLessEqual(count_tokens(result), 50)

    def test_never_exceeds_declared_window(self):
        # gotcha 4 enforcement: whatever comes out must never cross the
        # declared model_tokens, regardless of input size.
        text = "the quick brown fox jumps over the lazy dog. " * 20000
        result, truncated = enforce_budget(text, model_tokens=200)
        self.assertTrue(truncated)
        self.assertLessEqual(count_tokens(result), 200)


if __name__ == "__main__":
    unittest.main()
