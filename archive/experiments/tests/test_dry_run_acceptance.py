from __future__ import annotations

import unittest

from liquidity_bot.dry_run_acceptance import run_scenario


class DryRunAcceptanceTests(unittest.TestCase):
    def test_four_logical_hours_resolve_every_fill_for_each_candidate_horizon(self):
        for horizon in (15, 30, 60, 120):
            with self.subTest(horizon=horizon):
                result = run_scenario(logical_hours=4, horizon_seconds=horizon)
                self.assertGreater(result["entry_fills"], 0)
                self.assertEqual(result["entry_fills"], result["hard_ioc_exits"])
                self.assertLessEqual(
                    result["max_observed_inventory_age_seconds"], horizon + 1)
                self.assertEqual(result["orders_sent_to_polymarket"], 0)


if __name__ == "__main__":
    unittest.main()
