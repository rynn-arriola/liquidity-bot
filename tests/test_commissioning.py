from __future__ import annotations

import unittest
from datetime import datetime, timezone
from decimal import Decimal

from liquidity_bot.commissioning import (
    AUTO_REWARD_MARKET,
    CommissioningConfig,
    CommissioningRefused,
    next_six_am_new_york,
)


class CommissioningTests(unittest.TestCase):
    def authorized(self, **changes):
        values = {
            "market_slug": "market-one",
            "economic_risk_accepted": True,
            "exclusive_api_key_confirmed": True,
            "maker_lab_stopped_confirmed": True,
            "arm_live": True,
        }
        values.update(changes)
        return CommissioningConfig(**values)

    def test_cutoff_is_six_new_york_across_daylight_saving(self):
        summer_now = datetime(2026, 8, 13, 5, 0, tzinfo=timezone.utc)
        winter_now = datetime(2026, 1, 13, 5, 0, tzinfo=timezone.utc)
        summer = next_six_am_new_york(summer_now)
        winter = next_six_am_new_york(winter_now)
        self.assertEqual((summer.hour, summer.utcoffset().total_seconds()), (6, -14400))
        self.assertEqual((winter.hour, winter.utcoffset().total_seconds()), (6, -18000))

    def test_authorization_is_one_contract_and_operator_stopped(self):
        now = datetime(2026, 8, 13, 5, 0, tzinfo=timezone.utc)
        self.assertIsNone(self.authorized().assert_authorized(now=now))
        self.assertTrue(self.authorized().manual_stop_only)
        self.assertEqual(str(self.authorized().quote_size), "1")
        self.assertEqual(str(self.authorized().hard_stop_loss_usd), "15")
        self.assertEqual(self.authorized().max_hard_exit_attempts, 3)
        self.assertEqual(self.authorized().max_hard_exit_retry_seconds, 30)
        self.assertEqual(
            self.authorized().hard_exit_confirmation_timeout_seconds, 10)
        self.assertEqual(self.authorized().max_shutdown_exit_attempts, 3)
        self.assertEqual(self.authorized().shutdown_alert_interval_seconds, 300)
        self.assertEqual(self.authorized().max_shutdown_seconds, 900)
        self.assertEqual(self.authorized().reward_projection_horizon_seconds, 30)
        self.assertEqual(
            self.authorized().reward_cost_safety_multiple, Decimal("2"))
        self.assertEqual(
            self.authorized().aggregate_reward_payout_floor_usd, Decimal("1"))
        self.assertEqual(self.authorized().max_taker_exit_orders, 20)
        self.assertEqual(
            self.authorized().max_session_execution_fees_usd, Decimal("0.25"))
        self.assertEqual(self.authorized().max_inventory_age_seconds, 900)
        self.assertEqual(
            self.authorized().force_exit_before_start_seconds, 300)
        self.assertEqual(
            self.authorized().minimum_entry_spread, Decimal("0.02"))
        self.assertEqual(
            self.authorized().spread_hysteresis, Decimal("0.005"))

    def test_missing_arm_or_non_manual_mode_is_refused(self):
        overnight = datetime(2026, 8, 13, 5, 0, tzinfo=timezone.utc)
        with self.assertRaisesRegex(CommissioningRefused, "arming"):
            self.authorized(arm_live=False).assert_authorized(now=overnight)
        with self.assertRaisesRegex(CommissioningRefused, "operator stop"):
            self.authorized(manual_stop_only=False).assert_authorized(now=overnight)
        with self.assertRaisesRegex(CommissioningRefused, "hard-exit limits"):
            self.authorized(max_hard_exit_attempts=4).assert_authorized(now=overnight)
        with self.assertRaisesRegex(CommissioningRefused, "shutdown limits"):
            self.authorized(
                max_shutdown_seconds=60).assert_authorized(now=overnight)
        with self.assertRaisesRegex(CommissioningRefused, "reward economics"):
            self.authorized(
                reward_projection_haircut=Decimal("0.75")).assert_authorized(
                    now=overnight)
        with self.assertRaisesRegex(CommissioningRefused, "loss learning"):
            self.authorized(max_taker_exit_orders=2).assert_authorized(
                now=overnight)
        with self.assertRaisesRegex(CommissioningRefused, "inventory limits"):
            self.authorized(max_inventory_age_seconds=30).assert_authorized(
                now=overnight)
        with self.assertRaisesRegex(CommissioningRefused, "entry spread"):
            self.authorized(
                minimum_entry_spread=Decimal("0.01")).assert_authorized(
                    now=overnight)

    def test_auto_selection_expands_discovery_not_live_exposure(self):
        now = datetime(2026, 8, 13, 5, 0, tzinfo=timezone.utc)
        config = self.authorized(
            market_slug=AUTO_REWARD_MARKET,
            auto_select_reward_markets=True,
        )

        self.assertIsNone(config.assert_authorized(now=now))
        self.assertEqual(config.max_markets, 1)
        self.assertEqual(config.quote_size, Decimal("1"))
        self.assertEqual(config.market_selection_refresh_seconds, 900)
        self.assertEqual(config.max_selection_book_candidates, 25)

    def test_auto_selection_constants_and_reserved_slug_are_locked(self):
        now = datetime(2026, 8, 13, 5, 0, tzinfo=timezone.utc)
        with self.assertRaisesRegex(CommissioningRefused, "reserved"):
            self.authorized(auto_select_reward_markets=True).assert_authorized(
                now=now)
        with self.assertRaisesRegex(CommissioningRefused, "900 seconds"):
            self.authorized(
                market_slug=AUTO_REWARD_MARKET,
                auto_select_reward_markets=True,
                market_selection_refresh_seconds=60,
            ).assert_authorized(now=now)


if __name__ == "__main__":
    unittest.main()
