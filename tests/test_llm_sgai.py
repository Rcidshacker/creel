"""SGAI wrapper tests. SmartScraperGraph itself is mocked — we don't have an
LLM API key in this environment (Phase 0 finding), and even if we did, this
suite is testing OUR integration logic (gotchas 1-4), not SGAI's own
correctness. Importing scrapegraphai here costs ~16s once (Phase 0 spike,
LangChain tree) — the first test in the suite to actually touch it.
"""
import unittest
from unittest.mock import MagicMock, patch

from creel.extract.llm_sgai import SGAIConfig, available, extract


def _mock_graph(run_return, model_tokens_defaulted=False, execution_info=None, run_side_effect=None):
    instance = MagicMock()
    if run_side_effect is not None:
        instance.run.side_effect = run_side_effect
    else:
        instance.run.return_value = run_return
    instance.model_tokens_defaulted = model_tokens_defaulted
    instance.get_execution_info.return_value = execution_info or {}
    return MagicMock(return_value=instance), instance


class TestAvailable(unittest.TestCase):
    def test_none_config_unavailable(self):
        self.assertFalse(available(None))

    def test_no_key_unavailable(self):
        self.assertFalse(available(SGAIConfig(model="openai/gpt-4o-mini", api_key="")))


class TestExtract(unittest.IsolatedAsyncioTestCase):
    async def test_no_config_returns_error(self):
        outcome = await extract("<html></html>", "prompt", config=None)
        self.assertIsNone(outcome.data)
        self.assertIsNotNone(outcome.error)

    async def test_empty_source_rejected_before_touching_sgai(self):
        # gotcha 2: SGAI raises ValueError on empty/whitespace source. We
        # must catch this BEFORE crossing into the thread.
        config = SGAIConfig(model="openai/gpt-4o-mini", api_key="sk-test")
        mock_class, instance = _mock_graph(run_return={"title": "x"})
        with patch("scrapegraphai.graphs.SmartScraperGraph", mock_class):
            outcome = await extract("   ", "prompt", config=config)
        self.assertIsNone(outcome.data)
        self.assertEqual(outcome.error, "empty source")
        mock_class.assert_not_called()

    async def test_source_passed_is_the_raw_html_not_a_path(self):
        # gotcha 1
        config = SGAIConfig(model="openai/gpt-4o-mini", api_key="sk-test")
        html = "<html><body>real content</body></html>"
        mock_class, instance = _mock_graph(run_return={"title": "x"}, execution_info={"prompt_tokens": 1, "completion_tokens": 1})
        with patch("scrapegraphai.graphs.SmartScraperGraph", mock_class):
            await extract(html, "prompt", config=config)
        _, kwargs = mock_class.call_args
        self.assertEqual(kwargs["source"], html)

    async def test_error_shape_dict_becomes_error_outcome_not_raise(self):
        # gotcha 3
        config = SGAIConfig(model="openai/gpt-4o-mini", api_key="sk-test")
        mock_class, instance = _mock_graph(run_return={"error": "no answer found", "raw_response": "..."})
        with patch("scrapegraphai.graphs.SmartScraperGraph", mock_class):
            outcome = await extract("<html>content</html>", "prompt", config=config)
        self.assertIsNone(outcome.data)
        self.assertEqual(outcome.error, "no answer found")

    async def test_model_tokens_defaulted_is_a_hard_failure(self):
        # gotcha 4
        config = SGAIConfig(model="openai/some-unknown-model", api_key="sk-test", model_tokens=128000)
        mock_class, instance = _mock_graph(run_return={"title": "x"}, model_tokens_defaulted=True)
        with patch("scrapegraphai.graphs.SmartScraperGraph", mock_class):
            outcome = await extract("<html>content</html>", "prompt", config=config)
        self.assertIsNone(outcome.data)
        self.assertIn("model_tokens_defaulted", outcome.error)

    async def test_successful_extraction_reports_exact_tokens(self):
        config = SGAIConfig(model="openai/gpt-4o-mini", api_key="sk-test")
        answer = {"title": "Widget", "price": "9.99"}
        mock_class, instance = _mock_graph(run_return=answer, execution_info={"prompt_tokens": 120, "completion_tokens": 30})
        with patch("scrapegraphai.graphs.SmartScraperGraph", mock_class):
            outcome = await extract("<html>content</html>", "prompt", config=config)
        self.assertEqual(outcome.data, answer)
        self.assertEqual(outcome.prompt_tokens, 120)
        self.assertEqual(outcome.completion_tokens, 30)
        self.assertTrue(outcome.exact)

    async def test_missing_execution_info_marks_inexact(self):
        # SGAI's cost tracking depends on the provider echoing usage, which
        # is often absent — must never be silently treated as "0 tokens,
        # exact".
        config = SGAIConfig(model="openai/gpt-4o-mini", api_key="sk-test")
        mock_class, instance = _mock_graph(run_return={"title": "Widget"}, execution_info={})
        with patch("scrapegraphai.graphs.SmartScraperGraph", mock_class):
            outcome = await extract("<html>content</html>", "prompt", config=config)
        self.assertFalse(outcome.exact)
        self.assertEqual(outcome.prompt_tokens, 0)

    async def test_get_execution_info_raising_does_not_crash_extraction(self):
        config = SGAIConfig(model="openai/gpt-4o-mini", api_key="sk-test")
        mock_class, instance = _mock_graph(run_return={"title": "Widget"})
        instance.get_execution_info.side_effect = RuntimeError("no telemetry")
        with patch("scrapegraphai.graphs.SmartScraperGraph", mock_class):
            outcome = await extract("<html>content</html>", "prompt", config=config)
        self.assertEqual(outcome.data, {"title": "Widget"})
        self.assertFalse(outcome.exact)

    async def test_run_raising_becomes_error_outcome(self):
        config = SGAIConfig(model="openai/gpt-4o-mini", api_key="sk-test")
        mock_class, instance = _mock_graph(run_return=None, run_side_effect=RuntimeError("network down"))
        with patch("scrapegraphai.graphs.SmartScraperGraph", mock_class):
            outcome = await extract("<html>content</html>", "prompt", config=config)
        self.assertIsNone(outcome.data)
        self.assertIn("network down", outcome.error)


if __name__ == "__main__":
    unittest.main()
