import time
import unittest

from creel.core.cooldown import CooldownRegistry, parse_retry_after


class TestCooldownRegistry(unittest.TestCase):
    def test_register_and_active(self):
        reg = CooldownRegistry()
        reg.register("example.com", retry_after_s=0.2)
        self.assertIsNotNone(reg.active("example.com"))
        self.assertGreater(reg.remaining("example.com"), 0)

    def test_expires_after_retry_after(self):
        reg = CooldownRegistry()
        reg.register("example.com", retry_after_s=0.05)
        time.sleep(0.1)
        self.assertIsNone(reg.active("example.com"))
        self.assertEqual(reg.remaining("example.com"), 0.0)

    def test_no_cooldown_for_unregistered_domain(self):
        reg = CooldownRegistry()
        self.assertIsNone(reg.active("never-registered.com"))

    def test_longer_cooldown_extends_shorter(self):
        reg = CooldownRegistry()
        reg.register("example.com", retry_after_s=0.05)
        reg.register("example.com", retry_after_s=10)
        self.assertGreater(reg.remaining("example.com"), 5)

    def test_shorter_cooldown_does_not_shrink_longer(self):
        reg = CooldownRegistry()
        reg.register("example.com", retry_after_s=10)
        reg.register("example.com", retry_after_s=0.05)
        self.assertGreater(reg.remaining("example.com"), 5)


class TestParseRetryAfter(unittest.TestCase):
    def test_integer_seconds(self):
        self.assertEqual(parse_retry_after("120"), 120.0)

    def test_invalid_falls_back_to_default(self):
        self.assertEqual(parse_retry_after("not-a-date", default_s=7.0), 7.0)


if __name__ == "__main__":
    unittest.main()
