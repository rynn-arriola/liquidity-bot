from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from liquidity_bot.rewards import (
    AmbiguousIncentiveError,
    IncentivePeriod,
    evaluate_order_score,
    phase_for_event,
    project_order_reward,
    select_applicable_period,
)


UTC = timezone.utc
NOW = datetime(2026, 8, 12, 20, 0, tzinfo=UTC)


def period(name: str, *, status: str = "active", target: float = 5000,
           pool: float = 35, discount: float = 0.5,
           program_id: str | None = None) -> IncentivePeriod:
    return IncentivePeriod(
        program_id=program_id or name,
        period=name,
        status=status,
        reward_pool=pool,
        discount_factor=discount,
        target_size=target,
    )


class LifecycleTests(unittest.TestCase):
    def test_more_than_six_hours_is_early(self):
        self.assertEqual(phase_for_event(NOW + timedelta(hours=6, seconds=1), NOW), "early")

    def test_exactly_six_hours_is_day_of(self):
        self.assertEqual(phase_for_event(NOW + timedelta(hours=6), NOW), "day_of")

    def test_kickoff_is_live(self):
        self.assertEqual(phase_for_event(NOW, NOW), "live")

    def test_selects_only_the_phase_applying_now(self):
        chosen = select_applicable_period(
            [period("early"), period("day_of"), period("live")],
            NOW + timedelta(hours=2), NOW)
        self.assertEqual(chosen.period, "day_of")
        self.assertEqual(chosen.reward_pool, 35)

    def test_inactive_matching_phase_fails_closed(self):
        chosen = select_applicable_period(
            [period("day_of", status="ended"), period("live")],
            NOW + timedelta(hours=2), NOW)
        self.assertIsNone(chosen)

    def test_duplicate_matching_programs_are_ambiguous(self):
        with self.assertRaises(AmbiguousIncentiveError):
            select_applicable_period(
                [period("day_of", program_id="a"),
                 period("day_of", program_id="b")],
                NOW + timedelta(hours=2), NOW)


class RewardScoreTests(unittest.TestCase):
    def test_best_bid_scores_full_size_when_side_reaches_target(self):
        result = evaluate_order_score(
            side="bid", price=0.50, size=10,
            levels=[(0.50, 1000), (0.49, 2000), (0.48, 2000)],
            incentive=period("day_of"), tick_size=0.01)
        self.assertTrue(result.eligible)
        self.assertEqual(result.ticks_from_best, 0)
        self.assertAlmostEqual(result.score, 10)
        self.assertAlmostEqual(result.cutoff_price, 0.48)

    def test_one_tick_receives_discounted_score(self):
        result = evaluate_order_score(
            side="bid", price=0.49, size=10,
            levels=[(0.50, 2000), (0.49, 2000), (0.48, 1500)],
            incentive=period("day_of"), tick_size=0.01)
        self.assertTrue(result.eligible)
        self.assertEqual(result.ticks_from_best, 1)
        self.assertAlmostEqual(result.score, 5)

    def test_our_order_can_be_the_size_that_reaches_target(self):
        result = evaluate_order_score(
            side="bid", price=0.50, size=10,
            levels=[(0.50, 1000), (0.49, 2000), (0.48, 1990)],
            incentive=period("day_of"), tick_size=0.01)
        self.assertTrue(result.eligible)
        self.assertAlmostEqual(result.total_depth, 5000)

    def test_side_below_target_scores_zero(self):
        result = evaluate_order_score(
            side="offer", price=0.51, size=10,
            levels=[(0.51, 1000), (0.52, 1000)],
            incentive=period("day_of"), tick_size=0.01)
        self.assertFalse(result.eligible)
        self.assertEqual(result.reason, "target_not_met")
        self.assertEqual(result.score, 0)

    def test_offer_outside_cutoff_scores_zero(self):
        result = evaluate_order_score(
            side="offer", price=0.54, size=10,
            levels=[(0.51, 2000), (0.52, 2000), (0.53, 1500)],
            incentive=period("day_of"), tick_size=0.01)
        self.assertFalse(result.eligible)
        self.assertEqual(result.reason, "outside_reward_cutoff")
        self.assertAlmostEqual(result.cutoff_price, 0.53)

    def test_projection_uses_competing_score_and_half_pool(self):
        incentive = period(
            "day_of", target=100, pool=20_000, discount=0.5)
        result = project_order_reward(
            side="bid",
            price=0.50,
            size=1,
            levels=[(0.50, 99)],
            incentive=incentive,
            tick_size=0.01,
            event_start=NOW + timedelta(hours=1),
            now=NOW,
            horizon_seconds=30,
            haircut=0.5,
            minimum_payout=1,
        )
        self.assertTrue(result.eligible)
        self.assertAlmostEqual(result.total_score, 100)
        self.assertAlmostEqual(result.snapshot_share, 0.01)
        self.assertAlmostEqual(result.projected_reward, 0.0694444444)
        self.assertGreater(result.maximum_remaining_reward, 1)

    def test_projection_rejects_sub_dollar_maximum_payout(self):
        result = project_order_reward(
            side="offer",
            price=0.51,
            size=1,
            levels=[(0.51, 25_000)],
            incentive=period(
                "day_of", target=10_000, pool=200, discount=0.35),
            tick_size=0.01,
            event_start=NOW + timedelta(hours=5),
            now=NOW,
            horizon_seconds=30,
        )
        self.assertFalse(result.eligible)
        self.assertEqual(result.reason, "below_minimum_payout")
        self.assertLess(result.maximum_remaining_reward, 1)

    def test_projection_fails_closed_without_a_proven_period_duration(self):
        result = project_order_reward(
            side="bid",
            price=0.50,
            size=1,
            levels=[(0.50, 99)],
            incentive=period("early", target=100, pool=20_000),
            tick_size=0.01,
            event_start=NOW + timedelta(hours=7),
            now=NOW,
            horizon_seconds=30,
        )
        self.assertFalse(result.eligible)
        self.assertEqual(result.reason, "period_duration_unproven")

    def test_eligibility_does_not_flip_on_our_own_resting_order(self):
        """The place/cancel churn of 2026-08-14, isolated.

        Eligibility turns on cumulative depth reaching `target_size`, and our
        own size is added to that depth only when we are flat. On a book
        sitting exactly on the boundary the answer therefore changes the moment
        our order rests: eligible while flat, `target_not_met` while resting.

        Live that produced place, cancel, place at the loop tick -- 1,817
        cycles in 5.7 hours, all at the same price -- and reward pays for
        resting time, so the churn attacked the only thing that earns.

        The two calls must answer the same question about the same book.
        """

        levels = [(0.74, 9.0)]          # 9 resting, target 10, our size 1
        flat = evaluate_order_score(
            side="offer", price=0.74, size=1.0, levels=levels,
            incentive=period("day_of", target=10, pool=35),
            tick_size=0.01, order_already_resting=False)
        resting = evaluate_order_score(
            side="offer", price=0.74, size=1.0, levels=levels,
            incentive=period("day_of", target=10, pool=35),
            tick_size=0.01, order_already_resting=True)

        self.assertEqual(
            (flat.eligible, resting.eligible), (True, True),
            "a resting quote that was worth placing is still worth keeping")


if __name__ == "__main__":
    unittest.main()
