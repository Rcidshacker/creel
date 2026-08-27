import unittest

from creel.core.models import FetchOutcome
from creel.core.robots import RobotsChecker


def _fake_fetch(body: bytes, status: int = 200):
    async def _fetch(url, *a, **kw):
        return FetchOutcome(status=status, headers={}, body=body, final_url=url)

    return _fetch


class TestRobotsChecker(unittest.IsolatedAsyncioTestCase):
    async def test_disallowed_path_returns_false(self):
        robots = b"User-agent: *\nDisallow: /private/\n"
        checker = RobotsChecker(user_agent="creel")
        allowed = await checker.allowed("https://x.com/private/page", fetch_fn=_fake_fetch(robots))
        self.assertFalse(allowed)

    async def test_allowed_path_returns_true(self):
        robots = b"User-agent: *\nDisallow: /private/\n"
        checker = RobotsChecker(user_agent="creel")
        allowed = await checker.allowed("https://x.com/public/page", fetch_fn=_fake_fetch(robots))
        self.assertTrue(allowed)

    async def test_unreachable_robots_txt_fails_open(self):
        checker = RobotsChecker()
        allowed = await checker.allowed("https://x.com/anything", fetch_fn=_fake_fetch(b"", status=404))
        self.assertTrue(allowed)

    async def test_fetch_exception_fails_open(self):
        async def _raising_fetch(url, *a, **kw):
            raise OSError("network down")

        checker = RobotsChecker()
        allowed = await checker.allowed("https://x.com/anything", fetch_fn=_raising_fetch)
        self.assertTrue(allowed)

    async def test_caches_parser_across_calls_for_same_origin(self):
        calls = {"n": 0}

        async def counting_fetch(url, *a, **kw):
            calls["n"] += 1
            return FetchOutcome(status=200, headers={}, body=b"User-agent: *\nDisallow: /x\n", final_url=url)

        checker = RobotsChecker()
        await checker.allowed("https://x.com/a", fetch_fn=counting_fetch)
        await checker.allowed("https://x.com/b", fetch_fn=counting_fetch)
        self.assertEqual(calls["n"], 1, "robots.txt must be fetched once per origin, not per URL")

    async def test_different_origins_fetch_independently(self):
        calls = {"n": 0}

        async def counting_fetch(url, *a, **kw):
            calls["n"] += 1
            return FetchOutcome(status=200, headers={}, body=b"User-agent: *\n", final_url=url)

        checker = RobotsChecker()
        await checker.allowed("https://a.com/x", fetch_fn=counting_fetch)
        await checker.allowed("https://b.com/x", fetch_fn=counting_fetch)
        self.assertEqual(calls["n"], 2)


if __name__ == "__main__":
    unittest.main()
