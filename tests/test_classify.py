import unittest

from creel.core.classify import classify_status
from creel.core.models import FailureClass, FetchOutcome


def outcome(status, headers=None, body=b"", signals=None):
    return FetchOutcome(status=status, headers=headers or {}, body=body, final_url="https://x.com", signals=signals or [])


class TestClassifyStatus(unittest.TestCase):
    def test_network_when_status_none(self):
        self.assertEqual(classify_status(outcome(None)), FailureClass.NETWORK)

    def test_404_is_terminal_not_found(self):
        self.assertEqual(classify_status(outcome(404)), FailureClass.NOT_FOUND)

    def test_410_is_terminal_not_found(self):
        self.assertEqual(classify_status(outcome(410)), FailureClass.NOT_FOUND)

    def test_429_is_rate_limited(self):
        self.assertEqual(classify_status(outcome(429)), FailureClass.RATE_LIMITED)

    def test_503_with_retry_after_is_rate_limited(self):
        self.assertEqual(
            classify_status(outcome(503, headers={"Retry-After": "5"})), FailureClass.RATE_LIMITED
        )

    def test_503_without_retry_after_is_blocked(self):
        self.assertEqual(classify_status(outcome(503)), FailureClass.BLOCKED)

    def test_403_is_blocked(self):
        self.assertEqual(classify_status(outcome(403)), FailureClass.BLOCKED)

    def test_200_plain_is_ok(self):
        self.assertIsNone(classify_status(outcome(200, body=b"<html>hi</html>")))

    def test_200_with_login_wall_is_auth_required(self):
        body = b"<html>Please sign in to continue</html>"
        self.assertEqual(classify_status(outcome(200, body=body)), FailureClass.AUTH_REQUIRED)

    def test_200_with_login_only_in_href_attribute_is_not_auth_required(self):
        # Caught live: economictimes.com matched "login" only inside an
        # unrelated ad-network href, never in rendered text.
        body = (
            b"<html><body><nav><a href='/ads/loginselfservice.htm'>ad</a></nav>"
            b"<article>Real public headline content goes here.</article></body></html>"
        )
        self.assertIsNone(classify_status(outcome(200, body=body)))

    def test_200_large_real_page_with_incidental_nav_link_is_not_auth_required(self):
        # Caught live: a plain "sign in" nav link is present on nearly every
        # real site and says nothing about the page being gated -- only a
        # SMALL page dominated by that prompt (LinkedIn's real anonymous
        # login wall, ~32k visible chars) should count as genuinely gated.
        # economictimes.com's real homepage has ~51k visible chars of actual
        # article content plus an incidental nav "sign in" link.
        body = (
            b"<html><body><nav><a href='/account'>sign in</a></nav><article>"
            + b"Real news paragraph content. " * 2000
            + b"</article></body></html>"
        )
        self.assertIsNone(classify_status(outcome(200, body=body)))

    def test_200_with_blocked_marker_only_in_attribute_is_not_blocked(self):
        body = b"<html><a href='/access-denied-help'>Learn more</a><p>Normal page</p></html>"
        result = classify_status(outcome(200, body=body), blocked_markers=["access denied"])
        self.assertIsNone(result)

    def test_200_with_blocked_marker_is_blocked(self):
        body = b"<html>Access Denied by WAF</html>"
        result = classify_status(outcome(200, body=body), blocked_markers=["access denied"])
        self.assertEqual(result, FailureClass.BLOCKED)

    def test_200_with_cf_error_page_signal_is_blocked(self):
        # solver_engaged True but still landed on an error page -> must
        # re-escalate, not celebrate a 200.
        result = classify_status(outcome(200, body=b"ok looking body", signals=["cf_error_page"]))
        self.assertEqual(result, FailureClass.BLOCKED)

    def test_200_js_heavy_low_text_is_js_required(self):
        body = b"<html><body><div id='root'></div><script>" + b"x" * 2000 + b"</script></body></html>"
        self.assertEqual(classify_status(outcome(200, body=body)), FailureClass.JS_REQUIRED)

    def test_500_is_blocked(self):
        self.assertEqual(classify_status(outcome(500)), FailureClass.BLOCKED)


if __name__ == "__main__":
    unittest.main()
