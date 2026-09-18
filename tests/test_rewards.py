from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

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
           program_id: str | None = None,
           starts_at: datetime | None = None,
           ends_at: datetime | None = None) -> IncentivePeriod:
    return IncentivePeriod(
        program_id=program_id or name,
        period=name,
        status=status,
        reward_pool=pool,
        discount_factor=discount,
        target_size=target,
        starts_at=starts_at,
        ends_at=ends_at,
    )


class LifecycleTests(unittest.TestCase):
    def test_expanded_daily_uses_active_rewards_not_game_date(self):
        daily = period("daily_event")
        for seconds, expected in ((86401, False), (86400, True), (43200, True),
                                  (21601, True), (21600, True), (1, True),
                                  (0, False), (-1, False)):
            with self.subTest(seconds=seconds):
                selected = select_applicable_period(
                    [daily], NOW+timedelta(seconds=seconds), NOW,
                    allow_daily_event=True, daily_event_horizon_seconds=86400,
                    daily_event_observed_at=NOW)
                self.assertEqual(selected == daily, expected)
        # Active early rows do not hide the separate daily pool; day-of is
        # still preferred once the actual six-hour lifecycle begins.
        rows = [period("early"), period("day_of"), daily]
        for hours, expected in ((12, daily), (6, rows[1])):
            self.assertEqual(select_applicable_period(
                iter(rows), NOW+timedelta(hours=hours), NOW,
                allow_daily_event=True, daily_event_horizon_seconds=86400,
                daily_event_observed_at=NOW), expected)

    def test_expanded_daily_midnight_needs_fresh_evidence(self):
        before = datetime(2026, 9, 19, 3, 59, 59, tzinfo=UTC)
        after = before + timedelta(seconds=1)
        start = after + timedelta(hours=12)
        for stamp, expected in ((None, False), (before, False), (after, True),
                                (after+timedelta(seconds=1), False)):
            with self.subTest(stamp=stamp):
                selected = select_applicable_period(
                    [period("daily_event")], start, after,
                    allow_daily_event=True, daily_event_horizon_seconds=86400,
                    daily_event_observed_at=stamp)
                self.assertEqual(selected is not None, expected)

    def test_expanded_daily_keeps_bounds_duplicates_and_disable(self):
        for rows, enabled in (([period("daily_event", starts_at=NOW+timedelta(seconds=1))], True),
                              ([period("daily_event", ends_at=NOW)], True),
                              ([period("daily_event", status="pending")], True),
                              ([period("daily")], True),
                              ([period("early")], True),
                              ([period("daily_event")], False)):
            self.assertIsNone(select_applicable_period(
                rows, NOW+timedelta(hours=12), NOW, allow_daily_event=enabled,
                daily_event_horizon_seconds=86400, daily_event_observed_at=NOW))
        with self.assertRaises(AmbiguousIncentiveError):
            select_applicable_period(
                [period("daily_event", program_id="a"), period("daily-event", program_id="b")],
                NOW+timedelta(hours=12), NOW, allow_daily_event=True,
                daily_event_horizon_seconds=86400, daily_event_observed_at=NOW)

    def test_expanded_daily_horizon_uses_elapsed_time_across_dst(self):
        zone = ZoneInfo("America/New_York")
        for month, day, hour, expected in ((11, 1, 0, False), (3, 8, 1, True)):
            now = datetime(2026, month, day, hour, tzinfo=zone)
            start = now + timedelta(days=1)
            selected = select_applicable_period(
                [period("daily_event")], start, now, allow_daily_event=True,
                daily_event_horizon_seconds=86400, daily_event_observed_at=now)
            self.assertEqual(selected is not None, expected)

    def test_daily_fallback_and_day_of_priority_including_generators(self):
        daily = period("daily_event", pool=1000)
        day_of = period("day_of", pool=35)
        start = NOW + timedelta(hours=2)
        self.assertIsNone(select_applicable_period([daily], start, NOW))
        self.assertEqual(select_applicable_period(
            iter([daily]), start, NOW, allow_daily_event=True), daily)
        for rows in ([daily, day_of], [day_of, daily]):
            self.assertEqual(select_applicable_period(
                iter(rows), start, NOW, allow_daily_event=True), day_of)

    def test_daily_fallback_respects_six_hours_and_kickoff(self):
        for seconds, expected in ((21601, False), (21600, True),
                                  (1, True), (0, False), (-1, False)):
            with self.subTest(seconds=seconds):
                selected = select_applicable_period(
                    [period("daily_event")], NOW + timedelta(seconds=seconds),
                    NOW, allow_daily_event=True)
                self.assertEqual(selected is not None, expected)

    def test_daily_fallback_filters_status_timestamps_and_ambiguity(self):
        start = NOW + timedelta(hours=2)
        for inactive in (period("daily_event", status="ended"),
                         period("daily_event", starts_at=NOW+timedelta(seconds=1)),
                         period("daily_event", ends_at=NOW),
                         period("daily")):
            self.assertIsNone(select_applicable_period(
                [inactive], start, NOW, allow_daily_event=True))
        daily = period("daily_event", starts_at=NOW)
        self.assertEqual(select_applicable_period(
            [period("day_of", ends_at=NOW), daily], start, NOW,
            allow_daily_event=True), daily)
        for name in ("day_of", "daily_event"):
            with self.assertRaises(AmbiguousIncentiveError):
                select_applicable_period(
                    [period(name, program_id="a"), period(name, program_id="b")],
                    start, NOW, allow_daily_event=True)

    def test_six_hour_boundary_uses_elapsed_time_across_dst(self):
        zone = ZoneInfo("America/New_York")
        start = datetime(2026, 11, 1, 6, tzinfo=zone)
        now = datetime(2026, 11, 1, 0, tzinfo=zone)
        self.assertIsNone(select_applicable_period(
            [period("daily_event")], start, now, allow_daily_event=True))

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
    def test_entry_remaining_reward_excludes_cutoff_without_changing_ranking(self):
        for kind, pool in (("day_of", 28800), ("daily_event", 115200)):
            with self.subTest(kind=kind):
                result = project_order_reward(
                    side="bid", price=.50, size=1, levels=[(.50, 99)],
                    incentive=period(kind, pool=pool, target=100),
                    tick_size=.01, event_start=NOW+timedelta(minutes=6),
                    now=NOW, horizon_seconds=30, haircut=.5, minimum_payout=0)
                self.assertTrue(result.eligible)
                self.assertAlmostEqual(result.maximum_remaining_reward, 1.20)
                self.assertAlmostEqual(result.projected_reward, .10)
                self.assertAlmostEqual(result.remaining_entry_reward, .20)

    def test_entry_reward_cutoff_boundary_does_not_block_original_projection(self):
        for seconds in (1, 299, 300, 301, 360):
            with self.subTest(seconds=seconds):
                result = self.daily_projection(event_start=NOW+timedelta(seconds=seconds))
                self.assertTrue(result.eligible)
                self.assertGreater(result.projected_reward, 0)
                self.assertAlmostEqual(
                    result.remaining_entry_reward, 50*max(0, seconds-300)/86400)

    def test_entry_reward_retains_earlier_midnight_and_expiry(self):
        before_midnight = datetime(2026, 9, 19, 3, 59, 50, tzinfo=UTC)
        for result, seconds in (
            (self.daily_projection(before_midnight), 10),
            (self.daily_projection(ends_at=NOW+timedelta(seconds=20)), 20),
        ):
            self.assertAlmostEqual(result.remaining_entry_reward, 50*seconds/86400)
            self.assertEqual(result.remaining_entry_reward, result.maximum_remaining_reward)

    def daily_projection(self, now=NOW, *, starts_at=None, ends_at=None,
                         event_start=None, horizon=30):
        return project_order_reward(
            side="bid", price=.50, size=1, levels=[(.50, 99)],
            incentive=period("daily_event", pool=20000, target=100,
                             starts_at=starts_at, ends_at=ends_at),
            tick_size=.01, event_start=event_start or now+timedelta(hours=1),
            now=now, horizon_seconds=horizon, haircut=.5, minimum_payout=0)

    def test_daily_projection_uses_real_et_day_duration(self):
        for now, hours in ((NOW, 24),
                           (datetime(2026, 3, 8, 12, tzinfo=UTC), 23),
                           (datetime(2026, 11, 1, 12, tzinfo=UTC), 25)):
            with self.subTest(hours=hours):
                result = self.daily_projection(now)
                self.assertTrue(result.eligible)
                self.assertAlmostEqual(result.projected_reward, 50*30/(hours*3600))
                self.assertAlmostEqual(result.maximum_remaining_reward, 50/hours)
                self.assertAlmostEqual(result.remaining_entry_reward, 50*55/(hours*60))

    def test_daily_projection_stops_at_midnight_kickoff_or_program_end(self):
        before_midnight = datetime(2026, 9, 19, 3, 59, 50, tzinfo=UTC)
        for result in (
            self.daily_projection(before_midnight),
            self.daily_projection(event_start=NOW+timedelta(seconds=10)),
            self.daily_projection(ends_at=NOW+timedelta(seconds=10)),
        ):
            self.assertTrue(result.eligible)
            self.assertAlmostEqual(result.projected_reward, 50*10/86400)
            self.assertAlmostEqual(result.maximum_remaining_reward, 50*10/86400)
        midnight = before_midnight + timedelta(seconds=10)
        self.assertAlmostEqual(self.daily_projection(midnight).projected_reward,
                               50*30/86400)

    def test_partial_daily_program_does_not_inflate_rate(self):
        result = self.daily_projection(starts_at=NOW-timedelta(seconds=10),
                                       ends_at=NOW+timedelta(seconds=10))
        self.assertAlmostEqual(result.projected_reward, 50*10/86400)
        for result in (self.daily_projection(starts_at=NOW+timedelta(seconds=1)),
                       self.daily_projection(ends_at=NOW),
                       self.daily_projection(event_start=NOW)):
            self.assertFalse(result.eligible)

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

    def test_projection_uses_explicit_reward_program_bounds(self):
        """A short program must not be valued as if it lasted six hours."""

        result = project_order_reward(
            side="bid",
            price=0.50,
            size=1,
            levels=[(0.50, 99)],
            incentive=period(
                "day_of", target=100, pool=20_000,
                starts_at=NOW - timedelta(minutes=5),
                ends_at=NOW + timedelta(minutes=5)),
            tick_size=0.01,
            event_start=NOW + timedelta(hours=2),
            now=NOW,
            horizon_seconds=30,
            haircut=0.5,
            minimum_payout=0,
        )

        self.assertTrue(result.eligible)
        self.assertAlmostEqual(
            result.maximum_remaining_reward,
            result.projected_reward * 10,
        )

    def test_projection_stops_at_explicit_reward_program_end(self):
        result = project_order_reward(
            side="bid",
            price=0.50,
            size=1,
            levels=[(0.50, 99)],
            incentive=period(
                "day_of", target=100, pool=20_000,
                starts_at=NOW - timedelta(minutes=10),
                ends_at=NOW),
            tick_size=0.01,
            event_start=NOW + timedelta(hours=2),
            now=NOW,
            horizon_seconds=30,
            minimum_payout=0,
        )

        self.assertFalse(result.eligible)
        self.assertEqual(result.reason, "outside_projection_period")

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
