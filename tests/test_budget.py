import unittest

from creel.core.budget import BudgetState


class TestBudgetState(unittest.TestCase):
    def test_unlimited_by_default(self):
        b = BudgetState()
        self.assertTrue(b.has_budget())
        self.assertEqual(b.remaining_daily(), float("inf"))

    def test_spend_reduces_remaining(self):
        b = BudgetState(daily_usd_limit=10.0, monthly_usd_limit=100.0)
        b.record_spend(3.0, now=1000.0)
        self.assertAlmostEqual(b.remaining_daily(now=1000.0), 7.0)
        self.assertAlmostEqual(b.remaining_monthly(now=1000.0), 97.0)

    def test_has_budget_false_when_daily_exhausted(self):
        b = BudgetState(daily_usd_limit=5.0)
        b.record_spend(5.0, now=1000.0)
        self.assertFalse(b.has_budget(now=1000.0))

    def test_daily_resets_after_a_day(self):
        b = BudgetState(daily_usd_limit=5.0)
        b.record_spend(5.0, now=1000.0)
        self.assertFalse(b.has_budget(now=1000.0))
        self.assertTrue(b.has_budget(now=1000.0 + 86400 + 1))

    def test_monthly_does_not_reset_with_daily(self):
        b = BudgetState(daily_usd_limit=5.0, monthly_usd_limit=5.0)
        b.record_spend(5.0, now=1000.0)
        # a day passes -> daily resets, but monthly ceiling still binds
        self.assertFalse(b.has_budget(now=1000.0 + 86400 + 1))

    def test_remaining_never_negative(self):
        b = BudgetState(daily_usd_limit=5.0)
        b.record_spend(50.0, now=1000.0)
        self.assertEqual(b.remaining_daily(now=1000.0), 0.0)


if __name__ == "__main__":
    unittest.main()
