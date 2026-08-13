from __future__ import annotations

import unittest
from datetime import datetime, timezone

from liquidity_bot.commissioning import (
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

    def test_missing_arm_or_non_manual_mode_is_refused(self):
        overnight = datetime(2026, 8, 13, 5, 0, tzinfo=timezone.utc)
        with self.assertRaisesRegex(CommissioningRefused, "arming"):
            self.authorized(arm_live=False).assert_authorized(now=overnight)
        with self.assertRaisesRegex(CommissioningRefused, "operator stop"):
            self.authorized(manual_stop_only=False).assert_authorized(now=overnight)


if __name__ == "__main__":
    unittest.main()
