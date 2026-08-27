import asyncio
import unittest

from creel.core.flight import SingleFlight


class TestSingleFlight(unittest.IsolatedAsyncioTestCase):
    async def test_two_concurrent_identical_calls_produce_one_fetch(self):
        sf = SingleFlight()
        call_count = 0

        async def do_fetch():
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.05)
            return "result"

        results = await asyncio.gather(
            sf.run("https://x.com", do_fetch),
            sf.run("https://x.com", do_fetch),
        )
        self.assertEqual(call_count, 1, "concurrent identical requests must share one fetch")
        self.assertEqual(results, ["result", "result"])

    async def test_different_keys_both_run(self):
        sf = SingleFlight()
        call_count = 0

        async def do_fetch():
            nonlocal call_count
            call_count += 1
            return "ok"

        await asyncio.gather(sf.run("a", do_fetch), sf.run("b", do_fetch))
        self.assertEqual(call_count, 2)

    async def test_sequential_calls_after_completion_both_run(self):
        sf = SingleFlight()
        call_count = 0

        async def do_fetch():
            nonlocal call_count
            call_count += 1
            return call_count

        first = await sf.run("k", do_fetch)
        second = await sf.run("k", do_fetch)
        self.assertEqual((first, second), (1, 2))

    async def test_exception_propagates_to_all_waiters(self):
        sf = SingleFlight()

        async def failing():
            await asyncio.sleep(0.02)
            raise ValueError("boom")

        results = await asyncio.gather(
            sf.run("k", failing), sf.run("k", failing), return_exceptions=True
        )
        self.assertTrue(all(isinstance(r, ValueError) for r in results))

    async def test_key_removed_after_completion(self):
        sf = SingleFlight()
        await sf.run("k", lambda: _ok())
        self.assertNotIn("k", sf._inflight)


async def _ok():
    return "done"


if __name__ == "__main__":
    unittest.main()
