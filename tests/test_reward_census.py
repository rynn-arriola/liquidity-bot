from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from liquidity_bot.market_selector import (
    RewardMarketSelector,
    RewardSelectionPolicy,
)
from liquidity_bot.reward_census import (
    CENSUS_BOOK_CANDIDATES,
    census_from_selection,
    run_census,
    summarize,
    write_csv,
)

from tests.test_market_selector import (
    NOW,
    FakeGateway,
    FixedFillHazard,
    book,
    incentive,
    market,
)


class CensusTests(unittest.IsolatedAsyncioTestCase):
    def build(self, slugs, *, pool, touch_size, hazard=None):
        markets = {slug: market(slug) for slug in slugs}
        incentives = {slug: incentive(slug, pool=pool) for slug in slugs}
        books = {slug: book(slug, touch_size=touch_size) for slug in slugs}
        gateway = FakeGateway(markets, incentives, books)
        selector = RewardMarketSelector(
            gateway,
            RewardSelectionPolicy(max_book_candidates=CENSUS_BOOK_CANDIDATES,
                                  enforce_reward_cost_gate=True),
            fill_hazard_model=hazard,
        )
        return gateway, selector

    async def test_census_reports_one_row_per_side_and_no_mutation(self):
        gateway, selector = self.build(
            ("alpha", "beta"), pool=20_000, touch_size=5,
            hazard=FixedFillHazard(.01))

        result = await run_census(gateway, now=NOW, selector=selector)

        self.assertEqual(len(result.rows), 4)
        self.assertEqual({row.slug for row in result.rows}, {"alpha", "beta"})
        self.assertEqual(
            {row.side for row in result.rows}, {"bid", "offer"})
        self.assertTrue(result.coverage_complete)
        self.assertEqual(gateway.metadata_calls, [])
        self.assertEqual(len(gateway.upcoming_calls), 1)
        self.assertEqual(gateway.incentive_symbol_calls, [("alpha", "beta")])
        self.assertEqual(result.catalog_markets, 2)

    async def test_gate_ratio_agrees_with_the_live_viability_decision(self):
        gateway, selector = self.build(
            ("rich",), pool=20_000, touch_size=5,
            hazard=FixedFillHazard(.01))

        result = await run_census(gateway, now=NOW, selector=selector)

        for row in result.rows:
            self.assertIsNotNone(row.gate_ratio)
            # The published rule is projected*(1-P) > 2*P*unwind, so a ratio
            # above one must coincide exactly with the selector's own verdict.
            self.assertEqual(row.viable, row.gate_ratio > 1.0)
        self.assertTrue(any(row.viable for row in result.rows))

    async def test_unviable_universe_still_reports_the_measured_margin(self):
        gateway, selector = self.build(
            ("thin",), pool=1, touch_size=5_000,
            hazard=FixedFillHazard(.02))

        result = await run_census(gateway, now=NOW, selector=selector)

        self.assertEqual(result.viable_markets, 0)
        self.assertIsNone(result.selected_slug)
        priced = [row for row in result.rows if row.gate_ratio is not None]
        self.assertTrue(priced, "a deep book must still be priced, not skipped")
        for row in priced:
            self.assertFalse(row.viable)
            self.assertLess(row.gate_ratio, 1.0)
            # The census must say how thin the book would have to be, so an
            # empty universe is actionable rather than merely negative.
            self.assertIsNotNone(row.max_total_score_for_entry)
            self.assertLess(row.max_total_score_for_entry, row.total_score)

    async def test_summary_counts_blockers_and_depth(self):
        gateway, selector = self.build(
            ("thin",), pool=1, touch_size=5_000,
            hazard=FixedFillHazard(.02))

        result = await run_census(gateway, now=NOW, selector=selector)
        summary = summarize([result])

        self.assertEqual(summary["passes"], 1)
        self.assertEqual(summary["rows"], 2)
        self.assertEqual(summary["distinct_markets"], 1)
        self.assertEqual(summary["viable_rows"], 0)
        self.assertTrue(summary["coverage_complete"])
        self.assertEqual(summary["touch_depth_min"], 5_000)
        self.assertTrue(summary["blockers"])

    async def test_rows_round_trip_through_csv(self):
        gateway, selector = self.build(
            ("alpha",), pool=20_000, touch_size=5,
            hazard=FixedFillHazard(.01))
        result = await run_census(gateway, now=NOW, selector=selector)
        target = Path(tempfile.mkdtemp(prefix="census-")) / "rows.csv"

        written = write_csv(target, result.rows)

        self.assertEqual(written, len(result.rows))
        text = target.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(text), written + 1)
        self.assertIn("gate_ratio", text[0])
        self.assertIn("max_total_score_for_entry", text[0])

    async def test_census_requires_an_aware_clock(self):
        gateway, selector = self.build(
            ("alpha",), pool=20_000, touch_size=5)

        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            await run_census(
                gateway, now=datetime(2026, 8, 13, 5, 0), selector=selector)


class CensusFlatteningTests(unittest.TestCase):
    def test_selection_counts_are_carried_verbatim(self):
        from liquidity_bot.market_selector import RewardMarketSelection

        selection = RewardMarketSelection(
            selected=None,
            opportunities=(),
            rewarded_executable_markets=7,
            catalog_markets=19_179,
            executable_catalog_markets=584,
            unsupported_programs=3,
            eligible_pregame_markets=5,
            books_evaluated=0,
            deferred_markets=("later",),
            failures=("slug:book:AdapterError",),
            catalog_spread_filtered_markets=2,
        )

        result = census_from_selection(
            selection,
            observed_at=datetime(2026, 8, 13, 5, 0, tzinfo=timezone.utc),
            scan_seconds=42.1,
        )

        self.assertEqual(result.catalog_markets, 19_179)
        self.assertEqual(result.rewarded_executable_markets, 7)
        self.assertEqual(result.unsupported_programs, 3)
        self.assertEqual(result.catalog_spread_filtered_markets, 2)
        self.assertEqual(result.failures, ("slug:book:AdapterError",))
        self.assertAlmostEqual(result.scan_seconds, 42.1)
        # A deferred candidate means the pass did not see the whole universe,
        # so a negative census result cannot yet be treated as conclusive.
        self.assertFalse(result.coverage_complete)


if __name__ == "__main__":
    unittest.main()
