import time
import unittest

from creel.core.breaker import BreakerState, CircuitBreaker


class TestCircuitBreaker(unittest.TestCase):
    def test_starts_closed(self):
        b = CircuitBreaker()
        self.assertEqual(b.state("engine", "d.com"), BreakerState.CLOSED)
        self.assertTrue(b.allow("engine", "d.com"))

    def test_opens_after_threshold_failures(self):
        b = CircuitBreaker(failure_threshold=3, base_cooldown_s=60)
        for _ in range(3):
            b.record_failure("engine", "d.com")
        self.assertEqual(b.state("engine", "d.com"), BreakerState.OPEN)
        self.assertFalse(b.allow("engine", "d.com"))

    def test_half_open_after_cooldown_then_close_on_success(self):
        b = CircuitBreaker(failure_threshold=1, base_cooldown_s=0.05)
        b.record_failure("engine", "d.com")
        self.assertFalse(b.allow("engine", "d.com"))
        time.sleep(0.08)
        self.assertTrue(b.allow("engine", "d.com"), "must transition to half-open and allow one trial")
        self.assertEqual(b.state("engine", "d.com"), BreakerState.HALF_OPEN)
        b.record_success("engine", "d.com")
        self.assertEqual(b.state("engine", "d.com"), BreakerState.CLOSED)

    def test_half_open_failure_reopens_with_doubled_cooldown(self):
        b = CircuitBreaker(failure_threshold=1, base_cooldown_s=0.05, max_cooldown_s=10)
        b.record_failure("engine", "d.com")
        time.sleep(0.08)
        b.allow("engine", "d.com")  # transitions to half-open
        b.record_failure("engine", "d.com")
        self.assertEqual(b.state("engine", "d.com"), BreakerState.OPEN)
        self.assertFalse(b.allow("engine", "d.com"), "doubled cooldown must not have elapsed yet")

    def test_cooldown_capped_at_max(self):
        b = CircuitBreaker(failure_threshold=1, base_cooldown_s=100, max_cooldown_s=150)
        b.record_failure("engine", "d.com")
        b._entry("engine", "d.com").state = BreakerState.HALF_OPEN
        b.record_failure("engine", "d.com")
        self.assertLessEqual(b._entry("engine", "d.com").cooldown_s, 150)

    def test_engines_and_domains_are_independent(self):
        b = CircuitBreaker(failure_threshold=1, base_cooldown_s=60)
        b.record_failure("engineA", "d.com")
        self.assertEqual(b.state("engineA", "d.com"), BreakerState.OPEN)
        self.assertEqual(b.state("engineB", "d.com"), BreakerState.CLOSED)
        self.assertEqual(b.state("engineA", "other.com"), BreakerState.CLOSED)


if __name__ == "__main__":
    unittest.main()
