import time
import unittest

from creel.core.memory import TierMemory


class TestTierMemory(unittest.TestCase):
    def test_no_hint_for_unknown_domain(self):
        self.assertIsNone(TierMemory().suggest_tier("unknown.com"))

    def test_records_and_suggests_tier(self):
        m = TierMemory(ttl_s=60)
        m.record_success("d.com", tier=3)
        self.assertEqual(m.suggest_tier("d.com"), 3)

    def test_entry_expires_after_ttl(self):
        m = TierMemory(ttl_s=0.05)
        m.record_success("d.com", tier=3)
        time.sleep(0.08)
        self.assertIsNone(m.suggest_tier("d.com"))

    def test_probe_down_after_n_consecutive_successes(self):
        m = TierMemory(ttl_s=3600, probe_every=3)
        m.record_success("d.com", tier=3)
        self.assertEqual(m.suggest_tier("d.com"), 3)
        m.record_success("d.com", tier=3)
        m.record_success("d.com", tier=3)
        m.record_success("d.com", tier=3)
        self.assertEqual(m.suggest_tier("d.com"), 2, "must probe one tier cheaper, not latch forever")

    def test_ip_suspect_suppresses_hint(self):
        m = TierMemory()
        m.record_success("d.com", tier=3)
        m.record_ip_suspect("d.com")
        self.assertIsNone(
            m.suggest_tier("d.com"), "ip_suspect must not latch the domain as hostile"
        )

    def test_domain_hostile_sets_a_trusted_hint(self):
        m = TierMemory()
        m.record_domain_hostile("d.com", tier=3)
        self.assertEqual(m.suggest_tier("d.com"), 3)

    def test_changing_tier_resets_probe_counter(self):
        m = TierMemory(probe_every=2)
        m.record_success("d.com", tier=3)
        m.record_success("d.com", tier=3)  # would trigger probe-down at tier 3
        m.record_success("d.com", tier=2)  # but tier changed -> counter resets
        self.assertEqual(m.suggest_tier("d.com"), 2)


if __name__ == "__main__":
    unittest.main()
