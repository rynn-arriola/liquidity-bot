from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from liquidity_bot.calibration import DailyCalibration, RewardCalibrator
from liquidity_bot.models import RewardEarning
from liquidity_bot.shadow_store import ShadowStore


UTC = timezone.utc


class CalibrationTests(unittest.TestCase):
    def test_pending_and_unattributable_rows_never_calibrate_rewards(self):
        rows = [
            DailyCalibration("2026-08-10", "m1", "PAID", 2, 1000, False),
            DailyCalibration("2026-08-11", "m1", "PENDING", 2, 1000, True),
            DailyCalibration("2026-08-12", "m1", "SKIPPED", 2, 1000, True),
        ]
        result = RewardCalibrator(minimum_days=1).fit(rows)
        self.assertFalse(result.ready)
        self.assertEqual(result.days, 0)

    def test_requires_multiple_paid_attributable_days(self):
        rows = [
            DailyCalibration("2026-08-10", "m1", "PAID", 1, 3600, True),
            DailyCalibration("2026-08-11", "m1", "PAID", 2, 3600, True),
        ]
        result = RewardCalibrator(minimum_days=3).fit(rows)
        self.assertFalse(result.ready)
        self.assertEqual(result.days, 2)

    def test_lower_bound_forecast_uses_paid_attributable_score_only(self):
        rows = [
            DailyCalibration("2026-08-10", "m1", "PAID", 1.0, 3600, True),
            DailyCalibration("2026-08-11", "m1", "PAID", 1.1, 3600, True),
            DailyCalibration("2026-08-12", "m1", "PAID", 0.9, 3600, True),
        ]
        result = RewardCalibrator(minimum_days=3).fit(rows)
        self.assertTrue(result.ready)
        self.assertGreater(result.lower_reward_per_score_second, 0)
        self.assertLess(result.forecast(3600), 1.1)


class StoreTests(unittest.TestCase):
    def test_earnings_upsert_is_idempotent_and_persistent(self):
        path = Path(tempfile.mkdtemp(prefix="liquidity-store-")) / "shadow.db"
        captured = datetime(2026, 8, 13, tzinfo=UTC)
        store = ShadowStore(path)
        earning = RewardEarning("2026-08-10", "m1", "liquidityProgram", 0.3, "PAID")
        store.record_earnings((earning,), captured_at=captured)
        store.record_earnings((earning,), captured_at=captured)
        store.close()

        reopened = ShadowStore(path)
        rows = reopened.query("SELECT * FROM shadow_earnings")
        reopened.close()
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]["reward"], 0.3)


if __name__ == "__main__":
    unittest.main()
