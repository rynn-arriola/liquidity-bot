from __future__ import annotations

import unittest

from liquidity_bot.fill_hazard import (
    CALIBRATION_ID,
    GLOBAL_CELL,
    GLOBAL_REWARD_YIELD_PER_CONTRACT_HOUR,
    MondayFillHazardModel,
    historical_reward_yield_prior,
)
from liquidity_bot.risk import OpportunityEconomics


class FillHazardTests(unittest.TestCase):
    def setUp(self):
        self.model = MondayFillHazardModel()

    def test_global_calibration_is_exact_and_versioned(self):
        self.assertEqual(GLOBAL_CELL.fills, 114)
        self.assertEqual(GLOBAL_CELL.orders, 301)
        self.assertAlmostEqual(GLOBAL_CELL.exposure_seconds, 372_259.527)
        self.assertAlmostEqual(
            GLOBAL_CELL.upper_probability(), 0.011024368602363)
        result = self.model.estimate(
            slug="unseen",
            side="bid",
            price=.01,
            spread=.30,
            queue_ahead=100,
            seconds_to_event=20_000,
            horizon_seconds=30,
        )
        self.assertEqual(result.calibration_id, CALIBRATION_ID)
        self.assertAlmostEqual(
            result.global_probability, GLOBAL_CELL.upper_probability())

    def test_most_conservative_applicable_cell_drives_estimate(self):
        result = self.model.estimate(
            slug="aec-itfme-a-b-2026-08-10",
            side="bid",
            price=.49,
            spread=.02,
            queue_ahead=5,
            seconds_to_event=7_200,
            horizon_seconds=30,
        )
        self.assertEqual(result.driver, "queue:5_25")
        self.assertAlmostEqual(result.probability, 0.024161384603502034)
        self.assertGreaterEqual(result.probability, result.global_probability)

    def test_horizon_scales_probability_not_raw_loss(self):
        short = self.model.estimate(
            slug="aec-itfme-a-b-2026-08-10",
            side="offer",
            price=.50,
            spread=.04,
            queue_ahead=100,
            seconds_to_event=3_000,
            horizon_seconds=30,
        )
        long = self.model.estimate(
            slug="aec-itfme-a-b-2026-08-10",
            side="offer",
            price=.50,
            spread=.04,
            queue_ahead=100,
            seconds_to_event=3_000,
            horizon_seconds=60,
        )
        self.assertAlmostEqual(
            long.probability, 1 - (1 - short.probability) ** 2)

    def test_sparse_reward_yields_are_shrunk_toward_global(self):
        cs2 = historical_reward_yield_prior("aec-cs2-a-b-2026-08-10")
        itfme = historical_reward_yield_prior("aec-itfme-a-b-2026-08-10")
        unknown = historical_reward_yield_prior("unseen")
        self.assertGreater(cs2, GLOBAL_REWARD_YIELD_PER_CONTRACT_HOUR)
        self.assertLess(cs2, .2131)
        self.assertLess(itfme, GLOBAL_REWARD_YIELD_PER_CONTRACT_HOUR)
        self.assertEqual(unknown, GLOBAL_REWARD_YIELD_PER_CONTRACT_HOUR)


class MondayEconomicsReplayTests(unittest.TestCase):
    """Replay paid Monday reward yields through the conservative gate."""

    @staticmethod
    def economics(reward_yield_per_hour: float) -> OpportunityEconomics:
        probability = GLOBAL_CELL.upper_probability()
        unwind_cost = .02
        return OpportunityEconomics(
            conservative_reward=(
                reward_yield_per_hour / 120 * (1 - probability)),
            maker_rebate=0,
            spread_capture=0,
            fill_probability=probability,
            unwind_loss_if_filled=unwind_cost,
            operational_buffer=probability * unwind_cost,
        )

    def test_average_paid_market_remains_blocked(self):
        self.assertFalse(self.economics(.0304).should_quote)

    def test_high_yield_paid_market_can_cover_probability_weighted_exit(self):
        # aec-itfme-juaote-pabcer-2026-08-10: $0.3176/contract-hour
        self.assertTrue(self.economics(.3176).should_quote)

    def test_low_yield_paid_market_remains_blocked(self):
        # aec-itfme-erioli-chelu-2026-08-10: $0.0097/contract-hour
        self.assertFalse(self.economics(.0097).should_quote)


if __name__ == "__main__":
    unittest.main()
