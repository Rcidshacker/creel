import asyncio
import unittest

from creel.core.pool import ConcurrencyPool


class TestConcurrencyPool(unittest.IsolatedAsyncioTestCase):
    async def test_browser_tier_never_exceeds_configured_limit(self):
        """The test that would have caught the original design's OOM: a
        burst of 50 requests classifying JS_REQUIRED must never launch more
        than `browser` concurrent Chromium instances."""
        pool = ConcurrencyPool(http=20, browser=3, per_domain=50)
        current = 0
        peak = 0
        lock = asyncio.Lock()

        async def worker():
            nonlocal current, peak
            async with pool.acquire("example.com", needs_browser=True):
                async with lock:
                    current += 1
                    peak = max(peak, current)
                await asyncio.sleep(0.02)
                async with lock:
                    current -= 1

        await asyncio.gather(*(worker() for _ in range(50)))
        self.assertLessEqual(peak, 3)

    async def test_http_tier_uses_separate_limit_from_browser(self):
        pool = ConcurrencyPool(http=10, browser=1, per_domain=50)
        current = 0
        peak = 0
        lock = asyncio.Lock()

        async def worker():
            nonlocal current, peak
            async with pool.acquire("example.com", needs_browser=False):
                async with lock:
                    current += 1
                    peak = max(peak, current)
                await asyncio.sleep(0.02)
                async with lock:
                    current -= 1

        await asyncio.gather(*(worker() for _ in range(10)))
        self.assertEqual(peak, 10, "http tier must not be bounded by the browser limit")

    async def test_per_domain_limit_enforced(self):
        pool = ConcurrencyPool(http=50, browser=50, per_domain=2)
        current = 0
        peak = 0
        lock = asyncio.Lock()

        async def worker():
            nonlocal current, peak
            async with pool.acquire("hot-domain.com", needs_browser=False):
                async with lock:
                    current += 1
                    peak = max(peak, current)
                await asyncio.sleep(0.02)
                async with lock:
                    current -= 1

        await asyncio.gather(*(worker() for _ in range(20)))
        self.assertLessEqual(peak, 2)

    async def test_different_domains_do_not_share_the_domain_limit(self):
        pool = ConcurrencyPool(http=50, browser=50, per_domain=1)

        async def worker(domain):
            async with pool.acquire(domain, needs_browser=False):
                await asyncio.sleep(0.02)
                return domain

        results = await asyncio.gather(worker("a.com"), worker("b.com"))
        self.assertEqual(set(results), {"a.com", "b.com"})


if __name__ == "__main__":
    unittest.main()
