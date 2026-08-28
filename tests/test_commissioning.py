from __future__ import annotations

import unittest
from unittest.mock import patch
from tempfile import TemporaryDirectory
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from liquidity_bot.commissioning import (
    AUTO_REWARD_MARKET,
    CommissioningConfig,
    CommissioningRefused,
    configured_quote_size,
    next_six_am_new_york,
)
from liquidity_bot.quote_size import MAX_QUOTE_SIZE, MIN_QUOTE_SIZE, main


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

    def test_authorization_is_locked_and_operator_stopped(self):
        now = datetime(2026, 8, 13, 5, 0, tzinfo=timezone.utc)
        self.assertIsNone(self.authorized().assert_authorized(now=now))
        self.assertTrue(self.authorized().manual_stop_only)
        # Five is the restart-scoped default after the 2026-08-24 rollback.
        self.assertEqual(str(self.authorized().quote_size), "5")
        # Returned with size so it keeps its contract-level meaning.
        self.assertEqual(
            self.authorized().max_early_exit_loss_usd, Decimal("4.0"))
        # The safe missing-file fallback remains size 5, so every quantity-linked
        # risk control is half of its live size-10 baseline.
        self.assertEqual(str(self.authorized().hard_stop_loss_usd), "40")
        self.assertEqual(str(self.authorized().max_session_loss_usd), "40")
        self.assertEqual(str(self.authorized().max_market_loss_usd), "20")
        self.assertEqual(
            str(self.authorized().max_market_exposure_usd), "45")
        self.assertEqual(
            str(self.authorized().max_portfolio_exposure_usd), "150")
        self.assertEqual(self.authorized().max_hard_exit_attempts, 3)
        self.assertEqual(self.authorized().max_hard_exit_retry_seconds, 30)
        self.assertEqual(
            self.authorized().hard_exit_confirmation_timeout_seconds, 10)
        self.assertEqual(self.authorized().max_shutdown_exit_attempts, 3)
        self.assertEqual(self.authorized().shutdown_alert_interval_seconds, 300)
        self.assertEqual(self.authorized().max_shutdown_seconds, 900)
        self.assertEqual(self.authorized().reward_projection_horizon_seconds, 30)
        # 1, not 2. P is already a Wilson upper bound and unwind is priced
        # against stressed depth, so a 2x multiple padded the cost side a third
        # time while the reward side carried only the 0.5 haircut.
        self.assertEqual(
            self.authorized().reward_cost_safety_multiple, Decimal("1"))
        self.assertEqual(
            self.authorized().reward_projection_haircut, Decimal("0.5"))
        self.assertEqual(
            self.authorized().aggregate_reward_payout_floor_usd, Decimal("1"))
        self.assertEqual(self.authorized().max_taker_exit_orders, 20)
        self.assertEqual(
            self.authorized().max_session_execution_fees_usd, Decimal("1.50"))
        self.assertEqual(self.authorized().max_inventory_age_seconds, 900)
        self.assertEqual(
            self.authorized().force_exit_before_start_seconds, 300)
        self.assertEqual(
            self.authorized().minimum_entry_spread, Decimal("0.02"))
        self.assertEqual(
            self.authorized().minimum_entry_spread, Decimal("0.02"))

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
        with self.assertRaisesRegex(CommissioningRefused, "two cents"):
            self.authorized(
                minimum_entry_spread=Decimal("0.01")).assert_authorized(
                    now=overnight)
        for invalid_size in (Decimal("4"), Decimal("31"), Decimal("5.5")):
            with self.subTest(invalid_size=invalid_size), self.assertRaisesRegex(
                    CommissioningRefused, "whole number from 5 to 30"):
                self.authorized(quote_size=invalid_size).assert_authorized(
                    now=overnight)

    def test_auto_selection_caps_markets_at_the_measured_ceiling(self):
        now = datetime(2026, 8, 13, 5, 0, tzinfo=timezone.utc)
        config = self.authorized(
            market_slug=AUTO_REWARD_MARKET,
            auto_select_reward_markets=True,
        )

        self.assertIsNone(config.assert_authorized(now=now))
        # Eight per connection is the measured ceiling: 10 subscriptions, of
        # which one is account-wide orders and one is balance, leaving eight
        # per-market position subscriptions. Connections are independent, so
        # Twenty, and the socket cap is not what sets it: the feed sustains
        # 29. The ceiling is what REST can carry ALONE if the socket drops,
        # because a held position must stay exitable -- N/4 seconds against a
        # 5s staleness bound puts 20 at the limit.
        self.assertEqual(config.max_markets, 20)
        # Size per quote is independent of breadth. The 20-slot ceiling stays
        # unchanged by the five-contract rollback.
        self.assertEqual(config.quote_size, Decimal("5"))
        self.assertEqual(config.market_selection_refresh_seconds, 900)
        self.assertEqual(config.economic_idle_replacement_seconds, 600)
        # Only ~33 markets survive the day_of and catalog-spread filters, so
        # 25 left a blind spot for no reason. 100 covers the whole pool.
        self.assertEqual(config.max_selection_book_candidates, 100)

    def test_more_markets_than_the_subscription_ceiling_is_refused(self):
        now = datetime(2026, 8, 13, 5, 0, tzinfo=timezone.utc)
        with self.assertRaisesRegex(CommissioningRefused, "twenty markets"):
            self.authorized(max_markets=21).assert_authorized(now=now)

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

    def test_live_and_diagnostic_selection_share_the_authorized_policy(self):
        live = self.authorized().reward_selection_policy()
        diagnostic_config = CommissioningConfig.diagnostic_defaults(
            quote_size=Decimal("5"))
        diagnostic = diagnostic_config.reward_selection_policy()

        self.assertEqual(diagnostic, live)
        self.assertEqual(diagnostic.quote_size, 5.0)
        self.assertEqual(diagnostic.selection_horizon_seconds, 21600.0)
        self.assertEqual(diagnostic.max_book_candidates, 100)
        self.assertEqual(diagnostic.exit_cost_safety_multiple, 1.0)
        self.assertTrue(diagnostic_config.blockers(now=datetime(
            2026, 8, 13, 5, 0, tzinfo=timezone.utc)))

    def test_size_coupled_limits_derive_for_every_authorized_size(self):
        for size in range(int(MIN_QUOTE_SIZE), int(MAX_QUOTE_SIZE) + 1):
            with self.subTest(size=size):
                config = self.authorized(quote_size=Decimal(size))
                config.assert_authorized(now=datetime(
                    2026, 8, 13, 5, 0, tzinfo=timezone.utc))
                self.assertEqual(
                    config.max_early_exit_loss_usd, Decimal(size) * Decimal("0.8"))
                self.assertEqual(
                    config.max_market_exposure_usd, Decimal(size) * Decimal("9"))
                self.assertEqual(
                    config.max_session_execution_fees_usd,
                    Decimal(size) * Decimal("0.30"))
                self.assertEqual(
                    config.reward_selection_policy().quote_size, float(size))
                self.assertEqual(config.max_markets, 20)
                self.assertEqual(
                    config.max_portfolio_exposure_usd,
                    Decimal(size) * Decimal("30"))
                self.assertEqual(
                    config.max_market_loss_usd, Decimal(size) * Decimal("4"))
                self.assertEqual(
                    config.max_session_loss_usd, Decimal(size) * Decimal("8"))
                self.assertEqual(
                    config.hard_stop_loss_usd, Decimal(size) * Decimal("8"))

    def test_size_ten_and_twenty_match_operator_risk_profiles(self):
        expected = {
            10: ("300", "40", "80", "80"),
            20: ("600", "80", "160", "160"),
        }
        for size, values in expected.items():
            with self.subTest(size=size):
                config = self.authorized(quote_size=Decimal(size))
                actual = tuple(str(value) for value in (
                    config.max_portfolio_exposure_usd,
                    config.max_market_loss_usd,
                    config.max_session_loss_usd,
                    config.hard_stop_loss_usd,
                ))
                self.assertEqual(actual, values)

    def test_runtime_size_file_drives_diagnostics_and_rejects_bad_values(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "quote_size"
            path.write_text("7\n", encoding="utf-8")
            self.assertEqual(configured_quote_size(path), Decimal("7"))
            self.assertEqual(
                CommissioningConfig.diagnostic_defaults(
                    runtime_quote_size_path=path).quote_size,
                Decimal("7"),
            )
            path.write_text("30\n", encoding="utf-8")
            self.assertEqual(configured_quote_size(path), Decimal("30"))
            path.write_text("31\n", encoding="utf-8")
            with self.assertRaisesRegex(
                    CommissioningRefused, "whole number from 5 to 30"):
                configured_quote_size(path)

    def test_shell_launchers_use_the_single_quote_size_authority(self):
        root = Path(__file__).resolve().parents[1]
        for name in ("start_commissioning.sh", "supervise_commissioning.sh"):
            with self.subTest(name=name):
                contents = (root / "scripts" / name).read_text(encoding="utf-8")
                self.assertIn("-m liquidity_bot.quote_size", contents)
                self.assertNotIn("5|6|7|8|9|10", contents)

    def test_quote_size_shell_authority_normalizes_authorized_integers(self):
        for raw in ("5", "13", "29", "30", "13.0"):
            with self.subTest(raw=raw), patch("builtins.print") as output:
                self.assertEqual(main([raw]), 0)
                output.assert_called_once_with(int(Decimal(raw)))

    def test_quote_size_shell_authority_refuses_bad_values(self):
        for raw in ("4", "30.5", "31", "nan", "not-a-number"):
            with self.subTest(raw=raw), patch("builtins.print") as output:
                self.assertEqual(main([raw]), 2)
                self.assertIn("REFUSED", output.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
