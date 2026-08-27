import json
import unittest
from unittest.mock import AsyncMock, patch

from creel.extract.llm_direct import ProviderConfig, available, extract


def _mock_response(payload: dict, usage: dict | None = None):
    body = {"choices": [{"message": {"content": json.dumps(payload)}}]}
    if usage is not None:
        body["usage"] = usage
    resp = AsyncMock()
    resp.raise_for_status = lambda: None
    resp.json = lambda: body
    return resp


class TestAvailable(unittest.TestCase):
    def test_none_config_unavailable(self):
        self.assertFalse(available(None))

    def test_no_api_key_unavailable(self):
        self.assertFalse(available(ProviderConfig(model="x", api_key="")))

    def test_with_key_available(self):
        self.assertTrue(available(ProviderConfig(model="x", api_key="sk-123")))


class TestExtract(unittest.IsolatedAsyncioTestCase):
    async def test_no_config_returns_error(self):
        outcome = await extract("<html></html>", "prompt", config=None)
        self.assertIsNone(outcome.data)
        self.assertIsNotNone(outcome.error)

    async def test_parses_data_and_selectors_with_exact_usage(self):
        config = ProviderConfig(model="gpt-4o-mini", api_key="sk-test")
        payload = {"data": {"title": "Widget"}, "selectors": {"title": ".title"}}
        with patch("httpx.AsyncClient") as MockClient:
            instance = MockClient.return_value.__aenter__.return_value
            instance.post = AsyncMock(return_value=_mock_response(payload, usage={"prompt_tokens": 50, "completion_tokens": 10}))
            outcome = await extract("<html>...</html>", "extract the title", config=config)

        self.assertEqual(outcome.data, {"title": "Widget"})
        self.assertEqual(outcome.learned_selectors, {"title": ".title"})
        self.assertEqual(outcome.prompt_tokens, 50)
        self.assertEqual(outcome.completion_tokens, 10)
        self.assertTrue(outcome.exact)

    async def test_missing_usage_falls_back_to_estimated_tokens(self):
        config = ProviderConfig(model="gpt-4o-mini", api_key="sk-test")
        payload = {"data": {"title": "Widget"}, "selectors": {}}
        with patch("httpx.AsyncClient") as MockClient:
            instance = MockClient.return_value.__aenter__.return_value
            instance.post = AsyncMock(return_value=_mock_response(payload, usage=None))
            outcome = await extract("<html>content</html>", "extract the title", config=config)

        self.assertGreater(outcome.prompt_tokens, 0)
        self.assertFalse(outcome.exact)

    async def test_request_failure_becomes_error_outcome(self):
        config = ProviderConfig(model="gpt-4o-mini", api_key="sk-test")
        with patch("httpx.AsyncClient") as MockClient:
            instance = MockClient.return_value.__aenter__.return_value
            instance.post = AsyncMock(side_effect=OSError("connection refused"))
            outcome = await extract("<html></html>", "prompt", config=config)
        self.assertIsNone(outcome.data)
        self.assertIn("request failed", outcome.error)

    async def test_malformed_json_content_becomes_error_outcome(self):
        config = ProviderConfig(model="gpt-4o-mini", api_key="sk-test")
        resp = AsyncMock()
        resp.raise_for_status = lambda: None
        resp.json = lambda: {"choices": [{"message": {"content": "not json"}}]}
        with patch("httpx.AsyncClient") as MockClient:
            instance = MockClient.return_value.__aenter__.return_value
            instance.post = AsyncMock(return_value=resp)
            outcome = await extract("<html></html>", "prompt", config=config)
        self.assertIsNone(outcome.data)
        self.assertIn("malformed response", outcome.error)

    async def test_sends_json_object_response_format_and_auth_header(self):
        config = ProviderConfig(model="gpt-4o-mini", api_key="sk-test", base_url="https://api.openai.com/v1")
        payload = {"data": {"a": "b"}, "selectors": {}}
        with patch("httpx.AsyncClient") as MockClient:
            instance = MockClient.return_value.__aenter__.return_value
            instance.post = AsyncMock(return_value=_mock_response(payload))
            await extract("<html></html>", "prompt", config=config)
            _, kwargs = instance.post.await_args
            self.assertEqual(kwargs["json"]["response_format"], {"type": "json_object"})
            self.assertEqual(kwargs["headers"]["Authorization"], "Bearer sk-test")
            self.assertEqual(kwargs["json"]["model"], "gpt-4o-mini")


if __name__ == "__main__":
    unittest.main()
