"""FetchOutcome -> FailureClass.

Starts from Scrapling's own block set (scrapling/spiders/spider.py:16 —
{401,403,407,429,444,500,502,503,504}) then SPLITS it: 429 and 503-with-
Retry-After are RATE_LIMITED, a temporal problem the cooldown registry solves
with backoff, not an engine-escalation trigger. Escalating a rate limit
converts a cooldown problem into a spend problem and discards the
Retry-After instruction, and it hits one domain from three different fetch
signatures within seconds — which reads as an attack to any anti-abuse
system, not as a real user backing off.
"""
from __future__ import annotations

from typing import Iterable, Optional

from creel.core.models import FailureClass, FetchOutcome

_BLOCKED_CODES = {401, 403, 407, 444, 500, 502, 503, 504}
_TERMINAL_CODES = {404, 410}

_JS_MIN_BODY_LEN = 200
_JS_SCRIPT_DENSITY_THRESHOLD = 0.6
_JS_MAX_VISIBLE_LEN = 500

_DEFAULT_AUTH_MARKERS = ("sign in", "log in", "login", "please authenticate")


def classify_status(
    outcome: FetchOutcome,
    blocked_markers: Iterable[str] = (),
    auth_markers: Iterable[str] = _DEFAULT_AUTH_MARKERS,
) -> Optional[FailureClass]:
    """None means "treat as ok" — extraction's own PARSE_FAILED diagnosis is
    a separate, later concern (Phase 2), not this function's job."""
    status = outcome.status
    if status is None:
        return FailureClass.NETWORK

    headers_lower = {k.lower(): v for k, v in outcome.headers.items()}
    has_retry_after = "retry-after" in headers_lower

    if status in _TERMINAL_CODES:
        return FailureClass.NOT_FOUND

    if status == 429 or (status == 503 and has_retry_after):
        return FailureClass.RATE_LIMITED

    if status in _BLOCKED_CODES:
        return FailureClass.BLOCKED

    if 200 <= status < 300:
        # A solved Cloudflare challenge that still lands on an error page
        # must re-escalate, not celebrate — check this before anything else.
        if "cf_error_page" in outcome.signals:
            return FailureClass.BLOCKED

        text = outcome.body.decode("utf-8", errors="ignore").lower()
        for marker in blocked_markers:
            if marker.lower() in text:
                return FailureClass.BLOCKED
        for marker in auth_markers:
            if marker in text:
                return FailureClass.AUTH_REQUIRED
        if _looks_js_required(text):
            return FailureClass.JS_REQUIRED
        return None

    if status >= 500:
        return FailureClass.BLOCKED

    return None


def _looks_js_required(text: str) -> bool:
    if len(text) < _JS_MIN_BODY_LEN:
        return False
    script_len = 0
    idx = 0
    while True:
        start = text.find("<script", idx)
        if start == -1:
            break
        end = text.find("</script>", start)
        end = end + len("</script>") if end != -1 else len(text)
        script_len += end - start
        idx = end
    visible = max(len(text) - script_len, 1)
    density = script_len / (script_len + visible)
    return density >= _JS_SCRIPT_DENSITY_THRESHOLD and visible < _JS_MAX_VISIBLE_LEN
