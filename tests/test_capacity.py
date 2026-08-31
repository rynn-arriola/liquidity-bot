"""Offline capacity model; no venue or credential boundary is imported."""

from __future__ import annotations

import unittest

from liquidity_bot.capacity import (
    ApiWorkload,
    ExposureProfile,
    HardwareProfile,
    assess_capacity,
    highest_comfortable_slot,
    simulate_request_schedule,
)


class CapacityTests(unittest.TestCase):
    def test_breakdown_includes_every_steady_business_consumer(self):
        workload = ApiWorkload(
            quiet_market_fraction=0.25,
            mutation_requests_per_second=0.2,
            absent_order_requests_per_second=0.1,
            selection_catalog_pages=52,
            selection_incentive_pages=4,
            selection_book_requests=60,
        )

        result = workload.request_breakdown(24)

        self.assertEqual(
            set(result),
            {
                "account_mark",
                "cash_adjustment",
                "account_reconciliation",
                "market_contracts",
                "quiet_books",
                "selection",
                "earnings",
                "absent_order_repair",
                "mutations",
            },
        )
        self.assertAlmostEqual(result["account_mark"], 0.6)
        self.assertAlmostEqual(result["cash_adjustment"], 2 / 15)
        self.assertAlmostEqual(result["account_reconciliation"], 0.3)
        self.assertAlmostEqual(result["market_contracts"], 0.8)
        self.assertAlmostEqual(result["quiet_books"], 2.0)
        self.assertAlmostEqual(result["selection"], 116 / 900)
        self.assertAlmostEqual(result["mutations"], 0.2)

    def test_read_retry_multiplier_never_multiplies_mutations(self):
        workload = ApiWorkload(
            mutation_requests_per_second=0.25,
            read_attempt_multiplier=2,
        )

        result = workload.request_breakdown(8)

        self.assertEqual(result["mutations"], 0.25)
        self.assertEqual(result["account_mark"], 1.2)

    def test_bounded_contract_batches_remove_per_slot_request_growth(self):
        workload = ApiWorkload(contract_batch_size=8)

        result = workload.request_breakdown(24)

        self.assertAlmostEqual(result["market_contracts"], 6 / 60)

    def test_twenty_four_uses_three_complete_private_shards(self):
        item = assess_capacity(24, ApiWorkload())

        self.assertEqual(item.private_connections, 3)
        self.assertEqual(item.public_subscriptions, 24)

    def test_dynamic_exposure_policy_preserves_ten_quote_units(self):
        workload = ApiWorkload(requested_headroom_fraction=0)
        exposure = ExposureProfile(
            portfolio_cushion_quote_units=10,
            required_cushion_over_slots=0.25,
        )

        self.assertTrue(
            assess_capacity(40, workload, exposure=exposure).comfortable
        )
        item = assess_capacity(41, workload, exposure=exposure)
        self.assertFalse(item.comfortable)
        self.assertIn("portfolio exposure", " ".join(item.blockers))

    def test_cpu_projection_is_a_constraint_not_a_slot_assumption(self):
        hardware = HardwareProfile(
            baseline_slots=20,
            baseline_runner_cpu_cores=0.5,
            comfortable_runner_cpu_cores=0.75,
        )
        workload = ApiWorkload(requested_headroom_fraction=0)
        exposure = ExposureProfile(
            portfolio_cushion_quote_units=100,
            required_cushion_over_slots=0,
        )

        self.assertTrue(
            assess_capacity(
                30, workload, hardware=hardware, exposure=exposure
            ).comfortable
        )
        item = assess_capacity(
            31, workload, hardware=hardware, exposure=exposure
        )
        self.assertFalse(item.comfortable)
        self.assertIn("CPU", " ".join(item.blockers))

    def test_full_silent_feed_fails_at_current_twenty_slot_design(self):
        workload = ApiWorkload(
            quiet_market_fraction=1,
            execution_activity_pages=2,
            requested_headroom_fraction=0.25,
        )

        item = assess_capacity(20, workload)

        self.assertGreater(item.attempted_requests_per_second, 4)
        self.assertFalse(item.comfortable)
        self.assertIn("REST workload", " ".join(item.blockers))

    def test_highest_comfortable_slot_applies_all_constraints(self):
        workload = ApiWorkload(
            quiet_market_fraction=0.1,
            mutation_requests_per_second=0.1,
            selection_catalog_pages=52,
            selection_incentive_pages=4,
            selection_book_requests=60,
        )
        hardware = HardwareProfile(
            baseline_slots=20,
            baseline_runner_cpu_cores=0.564,
            comfortable_runner_cpu_cores=0.75,
        )
        exposure = ExposureProfile(
            portfolio_cushion_quote_units=10,
            required_cushion_over_slots=0.25,
        )

        self.assertEqual(
            highest_comfortable_slot(
                workload,
                maximum=48,
                hardware=hardware,
                exposure=exposure,
            ),
            26,
        )

    def test_deadline_replay_passes_a_light_normal_workload(self):
        workload = ApiWorkload(
            quiet_market_fraction=0,
            mutation_requests_per_second=0.02,
            contract_batch_size=8,
            selection_catalog_pages=2,
            selection_incentive_pages=1,
            selection_book_requests=4,
        )

        result = simulate_request_schedule(
            12, workload, duration_seconds=180)

        self.assertEqual(result.missed_deadlines, 0)
        self.assertEqual(result.remaining_requests, 0)

    def test_deadline_replay_exposes_current_silent_feed_starvation(self):
        workload = ApiWorkload(
            quiet_market_fraction=1,
            execution_activity_pages=2,
            selection_catalog_pages=0,
            selection_incentive_pages=0,
            selection_book_requests=0,
        )

        result = simulate_request_schedule(
            20, workload, duration_seconds=120)

        misses = dict(result.misses_by_category)
        self.assertGreater(misses.get("quiet_books", 0), 0)
        self.assertGreater(result.remaining_requests, 0)

    def test_slow_responses_can_fail_when_average_rate_math_passes(self):
        workload = ApiWorkload(
            contract_batch_size=8,
            quiet_market_fraction=0.1,
            mutation_requests_per_second=0.1,
            selection_catalog_pages=52,
            selection_incentive_pages=4,
            selection_book_requests=60,
        )

        steady = assess_capacity(24, workload)
        replay = simulate_request_schedule(
            24,
            workload,
            duration_seconds=300,
            response_seconds=0.5,
        )

        self.assertTrue(steady.comfortable)
        self.assertTrue(
            replay.missed_deadlines > 0 or replay.remaining_requests > 0)

    def test_priority_replay_prevents_selection_from_hiding_safety_work(self):
        workload = ApiWorkload(
            quiet_market_fraction=0,
            mutation_requests_per_second=0.1,
            selection_catalog_pages=30,
            selection_incentive_pages=4,
            selection_book_requests=60,
        )

        fifo = simulate_request_schedule(
            20,
            workload,
            duration_seconds=180,
            priority_scheduling=False,
        )
        priority = simulate_request_schedule(
            20,
            workload,
            duration_seconds=180,
            priority_scheduling=True,
        )

        fifo_misses = dict(fifo.misses_by_category)
        priority_misses = dict(priority.misses_by_category)
        self.assertGreater(
            fifo_misses.get("account_mark", 0)
            + fifo_misses.get("account_reconciliation", 0),
            priority_misses.get("account_mark", 0)
            + priority_misses.get("account_reconciliation", 0),
        )


if __name__ == "__main__":
    unittest.main()
