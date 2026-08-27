import tempfile
import time
import unittest
from pathlib import Path

from creel.core.store import Store


class TestStore(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self._tmp.name) / "creel.db")

    def tearDown(self):
        self.store.close()
        self._tmp.cleanup()

    def test_record_run_and_attempts_roundtrip(self):
        self.store.record_run("run1", "https://x.com", ["scrapling_http"], "ok", 0.0, True)
        self.store.record_attempt("run1", "scrapling_http", time.time(), 120, None, "ok")
        attempts = self.store.attempts_for_run("run1")
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]["engine"], "scrapling_http")

    def test_fetch_cache_hit_and_expiry(self):
        self.store.put_fetch("k1", "https://x.com", "http", 200, b"<html/>", {}, ttl_s=60)
        row = self.store.get_fetch("k1")
        self.assertIsNotNone(row)
        self.assertEqual(row["body"], b"<html/>")

        self.store.put_fetch("k2", "https://y.com", "http", 200, b"<html/>", {}, ttl_s=-1)
        self.assertIsNone(self.store.get_fetch("k2"), "TTL in the past must read as a miss")

    def test_invalidate_fetch_removes_entry(self):
        self.store.put_fetch("k3", "https://x.com", "http", 200, b"body", {}, ttl_s=60)
        self.store.invalidate_fetch("k3")
        self.assertIsNone(self.store.get_fetch("k3"))

    def test_extract_cache_roundtrip(self):
        self.store.put_extract("e1", {"title": "hello"})
        self.assertEqual(self.store.get_extract("e1"), {"title": "hello"})
        self.assertIsNone(self.store.get_extract("missing"))

    def test_purge_expired_removes_only_expired(self):
        self.store.put_fetch("fresh", "https://x.com", "http", 200, b"a", {}, ttl_s=3600)
        self.store.put_fetch("stale", "https://y.com", "http", 200, b"b", {}, ttl_s=-1)
        removed = self.store.purge_expired()
        self.assertEqual(removed, 1)
        self.assertIsNotNone(self.store.get_fetch("fresh"))


if __name__ == "__main__":
    unittest.main()
