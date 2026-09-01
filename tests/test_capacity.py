"""Offline capacity model; no venue or credential boundary is imported."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from liquidity_bot.capacity import (
    ApiWorkload,
    BATCHED_API_CATEGORIES,
    ExposureProfile,
    FIXED_API_CATEGORIES,
    HardwareProfile,
    KNOWN_API_CATEGORIES,
    SLOT_SCALED_API_CATEGORIES,
    assess_capacity,
    highest_comfortable_slot,
    parse_api_budget_line,
    project_api_budget_window,
    simulate_request_schedule,
)
from scripts.api_capacity_report import _attributed_windows, _windows


class CapacityTests(unittest.TestCase):
    def test_breakdown_includes_every_steady_business_consumer(self):
        workload = ApiWorkload(
            quiet_market_fraction=0.25,
            mutation_requests_per_second=0.2,
            absent_order_requests_per_second=0.1,
            lifecycle_requests_per_second=0.05,
            recovery_requests_per_second=0.02,
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
                "order_retrieve",
                "market_lifecycle",
                "account_recovery",
                "scheduled_reporting",
                "mutations",
            },
        )
        self.assertAlmostEqual(result["account_mark"], 0.6)
        self.assertAlmostEqual(result["cash_adjustment"], 2 / 15)
        self.assertAlmostEqual(result["account_reconciliation"], 0.3)
        self.assertAlmostEqual(result["market_contracts"], 0.8)
        self.assertAlmostEqual(result["quiet_books"], 2.0)
        self.assertAlmostEqual(result["selection"], 116 / 900)
        self.assertAlmostEqual(result["order_retrieve"], 0.1)
        self.assertAlmostEqual(result["market_lifecycle"], 0.05)
        self.assertAlmostEqual(result["account_recovery"], 0.02)
        self.assertAlmostEqual(result["scheduled_reporting"], 12 / 3600)
        self.assertAlmostEqual(result["mutations"], 0.2)

    def test_runtime_api_categories_have_one_projection_class(self):
        self.assertFalse(FIXED_API_CATEGORIES & BATCHED_API_CATEGORIES)
        self.assertFalse(FIXED_API_CATEGORIES & SLOT_SCALED_API_CATEGORIES)
        self.assertFalse(BATCHED_API_CATEGORIES & SLOT_SCALED_API_CATEGORIES)
        self.assertEqual(
            KNOWN_API_CATEGORIES,
            FIXED_API_CATEGORIES
            | BATCHED_API_CATEGORIES
            | SLOT_SCALED_API_CATEGORIES,
        )

    def test_api_budget_parser_requires_complete_classified_attempts(self):
        line = (
            "[api-budget] slots=24 seconds=60.0 attempts=12 failures=0 "
            "rate_limited=0 max_waiting=2 "
            "categories=balance:3,market_book:6,market_contract_batch:3"
        )

        result = parse_api_budget_line(line)

        self.assertIsNotNone(result)
        self.assertEqual(result.attempts, 12)
        self.assertEqual(result.attempts_by_category["market_book"], 6)
        with self.assertRaisesRegex(ValueError, "do not equal"):
            parse_api_budget_line(line.replace("attempts=12", "attempts=13"))
        with self.assertRaisesRegex(ValueError, "unclassified"):
            parse_api_budget_line(line.replace("balance:3", "new_api:3"))
        empty = parse_api_budget_line(
            "[api-budget] slots=24 seconds=60 attempts=0 failures=0 "
            "rate_limited=0 max_waiting=0 categories=none")
        self.assertEqual(empty.attempts_by_category, {})

    def test_empirical_projection_counts_fixed_batched_scaled_and_peer_work(self):
        window = parse_api_budget_line(
            "[api-budget] slots=24 seconds=60 attempts=30 failures=0 "
            "rate_limited=0 max_waiting=1 "
            "categories=balance:6,market_contract_batch:6,market_book:18")
        self.assertIsNotNone(window)

        result = project_api_budget_window(window, 32)

        self.assertEqual(result.projected_by_category["balance"], 6)
        self.assertEqual(result.projected_by_category["market_contract_batch"], 8)
        self.assertEqual(result.projected_by_category["market_book"], 24)
        self.assertAlmostEqual(result.projected_runner_attempts, 38)
        self.assertAlmostEqual(
            result.projected_host_average_rps, 38 / 60 + 12 / 3600)
        self.assertAlmostEqual(result.scheduled_report_peak_rps, 0.2)
        self.assertAlmostEqual(
            result.projected_host_coincident_report_rps, 38 / 60 + 12 / 60)

    def test_empirical_projection_caps_a_coincident_report_at_spare_host_rate(self):
        window = parse_api_budget_line(
            "[api-budget] slots=24 seconds=10 attempts=39 failures=0 "
            "rate_limited=0 max_waiting=1 categories=market_book:39")

        result = project_api_budget_window(window, 24)

        self.assertEqual(result.scheduled_report_peak_rps, 0.25)
        self.assertEqual(result.projected_host_coincident_report_rps, 4.0)

    def test_capacity_report_skips_legacy_windows_without_slot_evidence(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "commissioning.log"
            path.write_text(
                "[api-budget] seconds=60 attempts=1 failures=0 "
                "rate_limited=0 max_waiting=1 categories=balance:1\n"
                "[api-budget] slots=24 seconds=60 attempts=1 failures=0 "
                "rate_limited=0 max_waiting=1 categories=balance:1\n",
                encoding="utf-8",
            )

            result = _windows(path, source_slots=24, last=20)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].slots, 24)

    def test_capacity_report_attributes_burst_time_and_logged_drivers(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "commissioning.log"
            path.write_text(
                "[selection] at=2026-09-01T12:00:00+00:00 viable=30\n"
                "[runtime-recovery] at=2026-09-01T12:00:01+00:00 x=y\n"
                "[book-stale-why] at=2026-09-01T12:00:02+00:00 x=y\n"
                "[api-budget] at=2026-09-01T12:01:00+00:00 slots=24 "
                "seconds=60 attempts=100 failures=0 rate_limited=0 "
                "max_waiting=2 categories=balance:10,market_book:70,"
                "mutation_cancel:10,mutation_create:10\n",
                encoding="utf-8",
            )

            result = _attributed_windows(path, source_slots=24, last=20)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].observed_at, "2026-09-01T12:01:00+00:00")
        self.assertEqual(
            result[0].drivers,
            (
                "runtime recovery",
                "selection/refill",
                "mutation churn",
                "quiet-book fallback",
            ),
        )

    def test_capacity_report_does_not_leak_signals_between_windows(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "commissioning.log"
            path.write_text(
                "[unresolved-repair] at=one\n"
                "[api-budget] at=one slots=20 seconds=60 attempts=3 "
                "failures=0 rate_limited=0 max_waiting=1 "
                "categories=order_retrieve:3\n"
                "[api-budget] at=two slots=24 seconds=60 attempts=1 "
                "failures=0 rate_limited=0 max_waiting=1 "
                "categories=balance:1\n",
                encoding="utf-8",
            )

            result = _attributed_windows(path, source_slots=24, last=20)

        self.assertEqual(result[0].drivers, ("account proofs/reconciliation",))

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

    def test_thirty_two_uses_four_complete_private_shards(self):
        item = assess_capacity(32, ApiWorkload())

        self.assertEqual(item.private_connections, 4)
        self.assertEqual(item.public_subscriptions, 32)

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
