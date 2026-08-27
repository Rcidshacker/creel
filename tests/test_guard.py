import unittest
import urllib.request

from creel.core.guard import (
    GuardConfig,
    MaxBytesExceeded,
    SSRFError,
    enforce_max_bytes,
    preflight,
    redact,
    sniff_content_type,
)
from tests.fixtures.server import FixtureServer


class TestPreflightSSRF(unittest.TestCase):
    def test_rejects_link_local_metadata_ip(self):
        with self.assertRaises(SSRFError):
            preflight("http://169.254.169.254/latest/meta-data/")

    def test_rejects_localhost(self):
        with self.assertRaises(SSRFError):
            preflight("http://localhost:8080/admin")

    def test_rejects_loopback_literal(self):
        with self.assertRaises(SSRFError):
            preflight("http://127.0.0.1/admin")

    def test_rejects_file_scheme(self):
        with self.assertRaises(SSRFError):
            preflight("file:///etc/passwd")

    def test_allows_public_host(self):
        preflight("https://example.com/")  # must not raise

    def test_opt_out_allows_private_host(self):
        preflight("http://127.0.0.1/admin", GuardConfig(allow_private_hosts=True))


class TestMaxBytes(unittest.TestCase):
    def test_aborts_oversized_stream_from_fixture_server(self):
        config = GuardConfig(max_bytes=1_000_000)  # 1 MB cap, fixture serves ~13 MB
        with FixtureServer() as server:
            total = 0
            aborted = False
            with urllib.request.urlopen(server.url("/huge")) as resp:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    total += len(chunk)
                    try:
                        enforce_max_bytes(total, config)
                    except MaxBytesExceeded:
                        aborted = True
                        break
            self.assertTrue(aborted, "must abort before reading the full oversized body")
            self.assertLess(total, 13_000_000, "must not have read the entire ~13MB body")

    def test_does_not_abort_under_cap(self):
        enforce_max_bytes(1000, GuardConfig(max_bytes=5_000_000))  # must not raise


class TestContentTypeSniff(unittest.TestCase):
    def test_header_authoritative(self):
        self.assertEqual(sniff_content_type({"Content-Type": "application/json; charset=utf-8"}), "application/json")

    def test_pdf_magic_bytes_without_header(self):
        self.assertEqual(sniff_content_type({}, b"%PDF-1.4 rest"), "application/pdf")

    def test_defaults_to_html(self):
        self.assertEqual(sniff_content_type({}, b"<html></html>"), "text/html")


class TestRedact(unittest.TestCase):
    def test_strips_key_shaped_token(self):
        raw = 'auth failed: api_key="sk-abcdefghijklmnopqrstuvwx1234567890" for user'
        out = redact(raw)
        self.assertNotIn("sk-abcdefghijklmnopqrstuvwx1234567890", out)
        self.assertIn("<redacted>", out)

    def test_truncates_long_embedded_url(self):
        long_url = "https://example.com/" + "a" * 100
        out = redact(f"fetch failed for {long_url}")
        self.assertIn("<truncated>", out)
        self.assertLess(len(out), len(long_url))

    def test_leaves_plain_text_alone(self):
        self.assertEqual(redact("timeout after 30s"), "timeout after 30s")


if __name__ == "__main__":
    unittest.main()
