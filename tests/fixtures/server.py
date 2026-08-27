"""Local deterministic-failure server. Ladder tests hit this, never a live
third-party host — <cloudflare-site> changes posture, httpbin hiccups, Jina
throttles. Green today should not depend on the internet being unchanged
Thursday. Stdlib only (http.server) — no reason to add a dependency for
canned responses.
"""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread


class _FixtureHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:  # keep test output quiet
        pass

    def handle_one_request(self) -> None:
        # A guard-test client disconnecting mid-/huge/ on purpose (max_bytes
        # abort) is expected traffic, not a server error — don't let
        # socketserver print a traceback for it.
        try:
            super().handle_one_request()
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            self.close_connection = True

    def do_GET(self) -> None:
        route = self.path.split("?")[0]
        _ROUTES.get(route, _not_found)(self)


def _write(h: _FixtureHandler, status: int, headers: dict, body: bytes) -> None:
    h.send_response(status)
    for k, v in headers.items():
        h.send_header(k, v)
    h.send_header("Content-Length", str(len(body)))
    h.end_headers()
    h.wfile.write(body)


def _ok(h: _FixtureHandler) -> None:
    body = b"<html><body><article><h1>Fixture OK</h1><p>plain content, tier 1 should win.</p></article></body></html>"
    _write(h, 200, {"Content-Type": "text/html"}, body)


def _cf_403(h: _FixtureHandler) -> None:
    body = b"<html><body>Access denied. Checking your browser before accessing. Cloudflare Ray ID: fixture</body></html>"
    _write(h, 403, {"Content-Type": "text/html"}, body)


def _js_required(h: _FixtureHandler) -> None:
    body = b"<html><body><div id='root'></div><script>" + b"x" * 2000 + b"</script></body></html>"
    _write(h, 200, {"Content-Type": "text/html"}, body)


def _login_wall(h: _FixtureHandler) -> None:
    body = b"<html><body>Please sign in to continue. <form action='/login'></form></body></html>"
    _write(h, 200, {"Content-Type": "text/html"}, body)


def _rate_limited(h: _FixtureHandler) -> None:
    _write(h, 429, {"Content-Type": "text/plain", "Retry-After": "2"}, b"slow down")


def _blocked_no_retry(h: _FixtureHandler) -> None:
    _write(h, 503, {"Content-Type": "text/plain"}, b"service unavailable")


def _redirect_loop(h: _FixtureHandler) -> None:
    _write(h, 302, {"Location": "/redirect-loop"}, b"")


def _huge(h: _FixtureHandler) -> None:
    """~13 MB chunked response — well over a 5 MiB default guard.max_bytes,
    without a real gzip bomb's decompression-ratio complexity."""
    chunk = b"x" * 65536
    h.send_response(200)
    h.send_header("Content-Type", "application/octet-stream")
    h.send_header("Transfer-Encoding", "chunked")
    h.end_headers()
    for _ in range(200):
        h.wfile.write(b"%x\r\n" % len(chunk))
        h.wfile.write(chunk + b"\r\n")
    h.wfile.write(b"0\r\n\r\n")


def _pdf(h: _FixtureHandler) -> None:
    _write(h, 200, {"Content-Type": "application/pdf"}, b"%PDF-1.4 fixture")


def _soft_404(h: _FixtureHandler) -> None:
    body = b"<html><body>Sorry, page not found. Try our homepage instead.</body></html>"
    _write(h, 200, {"Content-Type": "text/html"}, body)


def _cf_error_200(h: _FixtureHandler) -> None:
    # A "solved" Cloudflare challenge that still lands on an error page —
    # status 200, but the body says otherwise. Must re-escalate, not
    # celebrate. See engines/scrapling_stealth.py's cf_error_page signal.
    body = b"<html><body>Cloudflare Ray ID: fixture-solved-but-denied. Access denied.</body></html>"
    _write(h, 200, {"Content-Type": "text/html"}, body)


def _not_found(h: _FixtureHandler) -> None:
    _write(h, 404, {"Content-Type": "text/plain"}, b"not found")


_ROUTES = {
    "/ok": _ok,
    "/cf-403": _cf_403,
    "/js-required": _js_required,
    "/login-wall": _login_wall,
    "/rate-limited": _rate_limited,
    "/blocked-no-retry": _blocked_no_retry,
    "/redirect-loop": _redirect_loop,
    "/huge": _huge,
    "/pdf": _pdf,
    "/soft-404": _soft_404,
    "/cf-error-200": _cf_error_200,
}


class FixtureServer:
    def __init__(self, port: int = 0) -> None:
        self._httpd = ThreadingHTTPServer(("127.0.0.1", port), _FixtureHandler)
        self.port = self._httpd.server_address[1]
        self._thread = Thread(target=self._httpd.serve_forever, daemon=True)

    def __enter__(self) -> "FixtureServer":
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"
