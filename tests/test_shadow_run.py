from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from liquidity_bot.models import MarketIncentive, MarketMetadata
from liquidity_bot.rewards import IncentivePeriod
from liquidity_bot.shadow_engine import ShadowPolicy
from liquidity_bot.shadow_run import (
    RunConfig,
    ShadowRunner,
    is_two_way_slug,
    rank_candidates,
)


UTC = timezone.utc
NOW = datetime(2026, 8, 12, 20, 0, tzinfo=UTC)


def market(slug: str, *, hours: int = 2) -> MarketMetadata:
    return MarketMetadata(
        slug=slug,
        game_start=NOW + timedelta(hours=hours),
        tick_size=0.01,
        active=True,
        closed=False,
        status="MARKET_STATE_OPEN",
        sports_market_type="moneyline",
    )


def program(slug: str, *, pool: float, target: float) -> MarketIncentive:
    return MarketIncentive(
        slug=slug,
        periods=(IncentivePeriod(
            program_id=f"program-{slug}",
            period="day_of",
            status="active",
            reward_pool=pool,
            discount_factor=0.5,
            target_size=target,
        ),),
    )


class CandidateTests(unittest.TestCase):
    def test_ranking_uses_pool_density_only_as_observation_priority(self):
        low = market("low-2026-08-13")
        high = market("high-2026-08-13")
        live = market("already-live-2026-08-12", hours=0)
        incentives = {
            low.slug: program(low.slug, pool=10, target=1000),
            high.slug: program(high.slug, pool=30, target=1000),
            live.slug: MarketIncentive(
                slug=live.slug,
                periods=(IncentivePeriod(
                    program_id="live", period="live", status="active",
                    reward_pool=100, discount_factor=0.5, target_size=1000),)),
        }
        ranked = rank_candidates([low, live, high], incentives, now=NOW)
        self.assertEqual([item.slug for item in ranked], [high.slug, low.slug])

    def test_suffix_after_event_date_is_not_treated_as_two_way(self):
        self.assertTrue(is_two_way_slug("table-tennis-a-b-2026-08-13"))
        self.assertFalse(is_two_way_slug("soccer-a-b-2026-08-13-draw"))


class IncentiveRefreshTests(unittest.IsolatedAsyncioTestCase):
    async def test_disappeared_program_invalidates_engine_periods(self):
        class Gateway:
            async def fetch_incentives(self, _symbols):
                return {}

        class Engine:
            def __init__(self):
                self.calls = []

            def register_market(self, item, periods, *, observed_at):
                self.calls.append((item.slug, tuple(periods), observed_at))

        path = Path(tempfile.mkdtemp(prefix="liquidity-runner-")) / "shadow.db"
        config = RunConfig(
            slugs=("market-one",),
            minutes=1,
            database=path,
            max_markets=1,
            discovery_horizon_minutes=360,
            rest_requests_per_second=4,
            incentive_refresh_minutes=15,
            discovery_refresh_minutes=15,
            policy=ShadowPolicy(),
        )
        runner = ShadowRunner(object(), config)
        engine = Engine()
        runner.gateway = Gateway()
        runner.engine = engine
        runner.markets = {"market-one": market("market-one")}
        runner.selected_slugs = {"market-one"}
        try:
            await runner._refresh_incentives(NOW)
        finally:
            runner.close()
        self.assertEqual(engine.calls, [("market-one", (), NOW)])

    async def test_shadow_discord_summary_is_labeled_and_never_claims_orders(self):
        class Notifier:
            def __init__(self):
                self.payloads = []

            async def send_payload_async(self, payload):
                self.payloads.append(payload)

        path = Path(tempfile.mkdtemp(prefix="liquidity-summary-")) / "shadow.db"
        config = RunConfig(
            slugs=(), minutes=1, database=path, max_markets=1,
            discovery_horizon_minutes=360, rest_requests_per_second=4,
            incentive_refresh_minutes=15, discovery_refresh_minutes=15,
            policy=ShadowPolicy(), discord_summary_minutes=5)
        notifier = Notifier()
        runner = ShadowRunner(object(), config, notifier)
        runner.started_at = NOW - timedelta(minutes=30)
        runner.store.start_run(runner.run_id, runner.started_at, {})
        try:
            await runner.post_discord_summary(observed_at=NOW, reconnects=0)
        finally:
            runner.close()
        self.assertEqual(len(notifier.payloads), 1)
        embed = notifier.payloads[0]["embeds"][0]
        self.assertIn("SHADOW", embed["title"])
        self.assertIn("DO_NOT_START_TRIAL", embed["title"])
        risk = next(field for field in embed["fields"]
                    if "stop loss" in field["name"])
        self.assertIn(f"{'Working orders':<19}{0:>10}", risk["value"])


if __name__ == "__main__":
    unittest.main()
