import unittest

from creel.core.pricing import compute_usd, is_known_model, price_for


class TestPricing(unittest.TestCase):
    def test_known_model_computes_expected_usd(self):
        price = price_for("openai/gpt-4o-mini")
        usd = compute_usd("openai/gpt-4o-mini", prompt_tokens=1000, completion_tokens=1000)
        expected = price.input_per_1k + price.output_per_1k
        self.assertAlmostEqual(usd, expected, places=6)

    def test_unknown_model_prices_at_zero(self):
        self.assertEqual(compute_usd("nonexistent/model-x", 1000, 1000), 0.0)

    def test_is_known_model(self):
        self.assertTrue(is_known_model("openai/gpt-4o-mini"))
        self.assertFalse(is_known_model("nonexistent/model-x"))

    def test_local_model_is_genuinely_free(self):
        self.assertEqual(compute_usd("ollama/llama3.2", 100000, 100000), 0.0)


if __name__ == "__main__":
    unittest.main()
