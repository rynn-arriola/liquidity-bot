from __future__ import annotations

import unittest

from liquidity_bot.risk import OpportunityEconomics, maximum_safe_order_size
from liquidity_bot.settings import StrategySettings, UnsafeConfiguration


class EconomicsTests(unittest.TestCase):
    def test_small_reward_does_not_cover_inventory_loss(self):
        economics = OpportunityEconomics(
            conservative_reward=0.30,
            maker_rebate=0.02,
            spread_capture=0.05,
            fill_probability=0.25,
            unwind_loss_if_filled=3.00,
            operational_buffer=0.05,
        )
        self.assertLess(economics.expected_net_value, 0)
        self.assertFalse(economics.should_quote)

    def test_positive_lower_bound_can_quote(self):
        economics = OpportunityEconomics(
            conservative_reward=1.00,
            maker_rebate=0.02,
            spread_capture=0.05,
            fill_probability=0.10,
            unwind_loss_if_filled=1.00,
            operational_buffer=0.10,
        )
        self.assertGreater(economics.expected_net_value, 0)
        self.assertTrue(economics.should_quote)

    def test_order_size_is_bounded_by_worst_case_collateral(self):
        self.assertAlmostEqual(
            maximum_safe_order_size("bid", 0.80, loss_budget=4.0, size_cap=10),
            5.0)
        self.assertAlmostEqual(
            maximum_safe_order_size("offer", 0.80, loss_budget=4.0, size_cap=10),
            10.0)

    def test_missing_budget_fails_closed_instead_of_returning_full_cap(self):
        for kwargs in ({"loss_budget": float("nan"), "size_cap": 10},
                       {"loss_budget": 4.0, "size_cap": float("nan")}):
            with self.subTest(**{k: str(v) for k, v in kwargs.items()}):
                with self.assertRaises(ValueError):
                    maximum_safe_order_size("bid", 0.80, **kwargs)


class SettingsTests(unittest.TestCase):
    def test_defaults_are_non_live_and_small(self):
        settings = StrategySettings()
        self.assertFalse(settings.live)
        self.assertLessEqual(settings.max_markets, 5)
        self.assertLessEqual(settings.quote_size_cap, 2)
        settings.assert_safe()

    def test_live_without_maximum_inventory_age_is_rejected(self):
        settings = StrategySettings(live=True, max_unpaired_seconds=None)
        with self.assertRaises(UnsafeConfiguration):
            settings.assert_safe()

    def test_live_without_loss_budgets_is_rejected(self):
        settings = StrategySettings(
            live=True,
            max_unpaired_loss_per_market=0,
            max_total_unpaired_loss=0,
        )
        with self.assertRaises(UnsafeConfiguration):
            settings.assert_safe()

    def test_portfolio_budget_must_cover_one_market(self):
        settings = StrategySettings(
            max_unpaired_loss_per_market=5,
            max_total_unpaired_loss=4,
        )
        with self.assertRaises(UnsafeConfiguration):
            settings.assert_safe()

    def test_live_shared_account_requires_explicit_exclusive_handoff(self):
        settings = StrategySettings(
            live=True, max_markets=1, quote_size_cap=1,
            max_unpaired_seconds=30,
            isolated_account_confirmed=False,
            exclusive_api_key_confirmed=True,
            maker_bot_stopped_confirmed=True,
            manual_supervision_confirmed=True,
            kill_switch_tested=True)
        settings.assert_safe()

    def test_live_without_maker_stop_or_exclusivity_is_rejected(self):
        settings = StrategySettings(
            live=True, max_unpaired_seconds=30,
            manual_supervision_confirmed=True, kill_switch_tested=True)
        with self.assertRaises(UnsafeConfiguration) as error:
            settings.assert_safe()
        self.assertIn("exclusive API-key ownership", str(error.exception))
        self.assertIn("maker-lab must be stopped", str(error.exception))


if __name__ == "__main__":
    unittest.main()
