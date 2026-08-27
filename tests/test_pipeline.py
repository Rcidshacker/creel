"""The Phase 2 acceptance suite. The LLM is a fake/injected extractor
throughout — real llm_direct/llm_sgai integration is covered separately
(test_llm_direct.py, test_llm_sgai.py, both mocked at their own network/SDK
boundary since no LLM API key exists in this environment). What's under
test here is the PIPELINE's own logic: caching, selector learning and
validation, budget enforcement, schema retry, and cost/partial-status
propagation — none of which depends on which LLM actually answered.
"""
import tempfile
import unittest
from pathlib import Path
from typing import Optional

from pydantic import BaseModel

from creel.core.prune import count_tokens
from creel.core.store import Store
from creel.extract.base import ExtractOutcome
from creel.extract.pipeline import extract


def _page(n: int) -> str:
    return f"<html><body><h1 class='title'>Widget {n}</h1><span class='price'>${n}.99</span></body></html>"


def _drifted_page(n: int) -> str:
    # Same content, renamed class and reordered elements -- selectors from
    # the original template will miss the literal CSS, forcing adaptive
    # relocation.
    return f"<html><body><span class='cost'>${n}.99</span><h1 class='heading'>Widget {n}</h1></body></html>"


class _CountingExtractor:
    """A fake Extractor. Each call returns exactly what's literally in the
    page it's given (so try_learn's replay-validation succeeds), plus a
    selector guess -- exactly what a real LLM is asked to do."""

    def __init__(self, responses=None):
        self.calls: list[tuple[str, str]] = []
        self._responses = responses or []
        self._i = 0

    async def __call__(self, html: str, prompt: str, schema: Optional[type]) -> ExtractOutcome:
        self.calls.append((html, prompt))
        if self._responses:
            outcome = self._responses[min(self._i, len(self._responses) - 1)]
            self._i += 1
            return outcome
        # Default: parse the "Widget N" / "$N.99" pattern out of whatever
        # html it was actually handed, and claim CSS class selectors.
        import re

        title_m = re.search(r"Widget \d+", html)
        price_m = re.search(r"\$\d+\.99", html)
        return ExtractOutcome(
            data={"title": title_m.group(0), "price": price_m.group(0)},
            prompt_tokens=10,
            completion_tokens=5,
            exact=True,
            learned_selectors={"title": ".title", "price": ".price"},
        )


class PipelineTestBase(unittest.IsolatedAsyncioTestCase):
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


class TestTemplateLearning(PipelineTestBase):
    async def test_20_urls_one_template_exactly_one_llm_call(self):
        extractor = _CountingExtractor()
        results = []
        for n in range(1, 21):
            url = f"https://x.com/product/{n}"
            result = await extract(
                url, _page(n), "extract title and price",
                store=self.store, llm_extract=extractor, model="test-model",
                selector_storage_file=self.selector_db,
            )
            results.append(result)

        self.assertEqual(len(extractor.calls), 1, "only the first page should ever reach the LLM")
        self.assertEqual(results[0].source, "llm")
        for r in results[1:]:
            self.assertEqual(r.source, "learned_selectors")
            self.assertEqual(r.status, "ok")

        # And the actual per-page values are still correct, not just cached
        # boilerplate from page 1.
        self.assertEqual(results[9].data, {"title": "Widget 10", "price": "$10.99"})

    async def test_corrupted_cached_selector_is_rejected_and_relearned(self):
        extractor = _CountingExtractor()
        await extract(
            "https://x.com/product/1", _page(1), "extract",
            store=self.store, llm_extract=extractor, model="m", selector_storage_file=self.selector_db,
        )
        self.assertEqual(len(extractor.calls), 1)

        # Directly corrupt the cached selector map. NOTE: corrupting the CSS
        # string for the SAME field name ("title") is not enough to force a
        # miss -- adaptive relocation is scoped by identifier, and "title"
        # already has a baseline from the successful learn above, so it
        # would self-heal right past a merely-wrong selector string
        # (verified empirically; see extract/selectors.py). Using field
        # names that were never learned (no baseline exists) forces a
        # genuine, unrecoverable miss.
        from creel.extract.learn import _selector_key, template_hash

        key = _selector_key("x.com", template_hash("https://x.com/product/2"))
        self.store.put_extract(key, {"headline": ".nonexistent-class", "cost": ".also-nonexistent"})

        result = await extract(
            "https://x.com/product/2", _page(2), "extract",
            store=self.store, llm_extract=extractor, model="m", selector_storage_file=self.selector_db,
        )
        self.assertEqual(len(extractor.calls), 2, "a selector miss must fall through to the LLM again")
        self.assertEqual(result.source, "llm")
        self.assertEqual(result.data, {"title": "Widget 2", "price": "$2.99"})


class TestExtractCache(PipelineTestBase):
    async def test_identical_url_prompt_model_costs_zero_llm_work_on_repeat(self):
        extractor = _CountingExtractor()
        url = "https://x.com/product/1"
        first = await extract(
            url, _page(1), "same prompt", store=self.store, llm_extract=extractor,
            model="m", selector_storage_file=self.selector_db,
        )
        second = await extract(
            url, _page(1), "same prompt", store=self.store, llm_extract=extractor,
            model="m", selector_storage_file=self.selector_db,
        )
        self.assertEqual(first.source, "llm")
        self.assertEqual(second.source, "cache")
        self.assertEqual(len(extractor.calls), 1)
        self.assertEqual(second.data, first.data)


class TestBudgetEnforcement(PipelineTestBase):
    async def test_oversized_page_never_reaches_llm_over_window(self):
        huge_html = "<html><body><article>" + ("the quick brown fox jumps over the lazy dog. " * 20000) + "</article></body></html>"
        extractor = _CountingExtractor(responses=[ExtractOutcome(data={"ok": True}, exact=True)])

        await extract(
            "https://big.com/page", huge_html, "summarize",
            store=self.store, llm_extract=extractor, model="m", model_tokens=200,
            selector_storage_file=self.selector_db,
        )
        self.assertEqual(len(extractor.calls), 1)
        received_html, _ = extractor.calls[0]
        self.assertLessEqual(count_tokens(received_html, "m"), 200)


class ProductSchema(BaseModel):
    title: str
    price: str


class TestSchemaRetry(PipelineTestBase):
    async def test_malformed_extraction_triggers_one_guided_retry_then_succeeds(self):
        bad = ExtractOutcome(data={"title": "Widget"}, exact=True)  # missing required "price"
        good = ExtractOutcome(data={"title": "Widget", "price": "$1.99"}, exact=True)
        extractor = _CountingExtractor(responses=[bad, good])

        result = await extract(
            "https://x.com/product/1", _page(1), "extract",
            schema=ProductSchema, store=self.store, llm_extract=extractor,
            model="m", selector_storage_file=self.selector_db,
        )
        self.assertEqual(len(extractor.calls), 2, "exactly one retry, not an open-ended loop")
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.data, {"title": "Widget", "price": "$1.99"})
        self.assertIn("llm_retry", result.attempts)

    async def test_two_consecutive_failures_terminate_as_failed(self):
        bad1 = ExtractOutcome(data={"title": "Widget"}, exact=True)
        bad2 = ExtractOutcome(data={"title": "Widget"}, exact=True)  # still missing price after retry
        extractor = _CountingExtractor(responses=[bad1, bad2])

        result = await extract(
            "https://x.com/product/1", _page(1), "extract",
            schema=ProductSchema, store=self.store, llm_extract=extractor,
            model="m", selector_storage_file=self.selector_db,
        )
        self.assertEqual(len(extractor.calls), 2)
        self.assertEqual(result.status, "failed")
        self.assertIsNone(result.data)


class TestCostExactPropagation(PipelineTestBase):
    async def test_inexact_llm_cost_propagates_as_inexact(self):
        extractor = _CountingExtractor(
            responses=[ExtractOutcome(data={"a": "b"}, prompt_tokens=100, completion_tokens=20, exact=False)]
        )
        result = await extract(
            "https://x.com/x", "<html><body>a b</body></html>", "extract",
            store=self.store, llm_extract=extractor, model="m", selector_storage_file=self.selector_db,
        )
        self.assertFalse(result.cost_exact)
        self.assertEqual(result.prompt_tokens, 100)


class TestHealedSelectorsPartialStatus(PipelineTestBase):
    async def test_adaptively_healed_match_demotes_status_to_partial(self):
        extractor = _CountingExtractor()
        # Learn on the original template.
        await extract(
            "https://x.com/product/1", _page(1), "extract",
            store=self.store, llm_extract=extractor, model="m", selector_storage_file=self.selector_db,
        )
        self.assertEqual(len(extractor.calls), 1)

        # Second page: same template_hash, but the DOM drifted (renamed
        # classes) -- the learned .title/.price selectors will miss the
        # literal CSS and require adaptive relocation.
        result = await extract(
            "https://x.com/product/2", _drifted_page(2), "extract",
            store=self.store, llm_extract=extractor, model="m", selector_storage_file=self.selector_db,
        )
        self.assertEqual(len(extractor.calls), 1, "adaptive relocation must still avoid a second LLM call")
        self.assertEqual(result.source, "learned_selectors")
        self.assertEqual(result.status, "partial", "a healed match must never look like a clean hit")
        self.assertEqual(result.data, {"title": "Widget 2", "price": "$2.99"})


if __name__ == "__main__":
    unittest.main()
