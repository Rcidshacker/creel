import unittest

from creel.core.memory import TierMemory
from creel.core.policy import DomainPolicy, PolicyResolver


class TestPolicyResolver(unittest.TestCase):
    def test_default_cost_mode_and_tier(self):
        r = PolicyResolver(default_cost_mode="frugal")
        resolved = r.resolve("example.com")
        self.assertEqual(resolved.cost_mode, "frugal")
        self.assertEqual(resolved.start_tier, 1)
        self.assertIsNone(resolved.allowed_engines)

    def test_cost_mode_override_wins_over_default(self):
        r = PolicyResolver(default_cost_mode="frugal")
        self.assertEqual(r.resolve("example.com", cost_mode_override="reliable").cost_mode, "reliable")

    def test_domain_policy_glob_match_sets_start_tier(self):
        policies = [DomainPolicy(glob="*.stubborn-site.com", start_tier=3)]
        r = PolicyResolver(domain_policies=policies)
        self.assertEqual(r.resolve("api.stubborn-site.com").start_tier, 3)
        self.assertEqual(r.resolve("unrelated.com").start_tier, 1)

    def test_domain_policy_restricts_allowed_engines(self):
        policies = [DomainPolicy(glob="*.gated.com", allowed_engines=["scrapling_stealth", "firecrawl"])]
        r = PolicyResolver(domain_policies=policies)
        self.assertEqual(r.resolve("x.gated.com").allowed_engines, ["scrapling_stealth", "firecrawl"])

    def test_learned_memory_overrides_domain_config(self):
        memory = TierMemory()
        memory.record_success("api.stubborn-site.com", tier=1)  # site relaxed
        policies = [DomainPolicy(glob="*.stubborn-site.com", start_tier=3)]
        r = PolicyResolver(domain_policies=policies, memory=memory)
        self.assertEqual(
            r.resolve("api.stubborn-site.com").start_tier, 1,
            "freshest evidence (memory) must win over static domain config",
        )


if __name__ == "__main__":
    unittest.main()
