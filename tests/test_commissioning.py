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

    def test_authorization_is_one_contract_and_stops_at_six(self):
        now = datetime(2026, 8, 13, 5, 0, tzinfo=timezone.utc)
        cutoff = self.authorized().assert_authorized(now=now)
        self.assertEqual(cutoff.hour, 6)
        self.assertEqual(str(self.authorized().quote_size), "1")
        self.assertEqual(str(self.authorized().hard_stop_loss_usd), "15")

    def test_missing_arm_or_too_early_start_is_refused(self):
        overnight = datetime(2026, 8, 13, 5, 0, tzinfo=timezone.utc)
        with self.assertRaisesRegex(CommissioningRefused, "arming"):
            self.authorized(arm_live=False).assert_authorized(now=overnight)
        morning = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)
        with self.assertRaisesRegex(CommissioningRefused, "more than 12 hours"):
            self.authorized().assert_authorized(now=morning)


if __name__ == "__main__":
    unittest.main()
