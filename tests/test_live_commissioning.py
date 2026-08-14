from __future__ import annotations

import asyncio
import tempfile
import unittest
from argparse import Namespace
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, patch

from liquidity_bot.commissioning import (
    AUTO_REWARD_MARKET,
    CommissioningConfig,
    CommissioningRefused,
    CommissioningUnavailable,
)
from liquidity_bot.errors import AdapterError, RuntimeInvariantViolation
from liquidity_bot.execution_ledger import Execution, ExecutionLedger, OrderState
from liquidity_bot.live_commissioning import (
    MAX_RETIREMENT_ROUNDS,
    CommissioningResult,
    CommissioningRunner,
    _run,
    main,
)
from liquidity_bot.live_orders import OrderOutcomeUncertain, OrderRequest
from liquidity_bot.market_selector import (
    RewardMarketOpportunity,
    RewardMarketSelection,
    SideOpportunity,
)
from liquidity_bot.models import BookLevel, BookSnapshot, MarketIncentive, MarketMetadata
from liquidity_bot.notifications import NotificationResult
from liquidity_bot.private_stream import PrivateEvent, PrivateEventKind
from liquidity_bot.reconciler import ActionKind, PlanAction, WorkingOrder
from liquidity_bot.rewards import IncentivePeriod, RewardProjection


NOW = datetime(2026, 8, 13, 5, 0, tzinfo=timezone.utc)
GAME_START = datetime(2026, 8, 13, 10, 0, tzinfo=timezone.utc)


class ImmediateLimiter:
    def __init__(self):
        self.calls = 0

    async def wait(self):
        self.calls += 1


class FakeMarkets:
    def __init__(self):
        self.book_calls = 0

    async def retrieve_by_slug(self, slug):
        return {"market": {
            "slug": slug,
            "active": True,
            "closed": False,
            "status": "MARKET_STATE_OPEN",
            "gameStartTime": GAME_START.isoformat(),
            "orderPriceMinTickSize": {"value": "0.01", "currency": "USD"},
            "minimumTradeQty": "1",
            "sportsMarketType": "moneyline",
            "sportsMarketTypeV2": "SPORTS_MARKET_TYPE_MONEYLINE",
            "question": "Fake commissioning market?",
        }}

    async def book(self, slug):
        self.book_calls += 1
        return {"marketData": {
            "marketSlug": slug,
            "state": "MARKET_STATE_OPEN",
            "bids": [
                {"px": "0.49", "qty": "100"},
                {"px": "0.48", "qty": "100"},
            ],
            "offers": [
                {"px": "0.51", "qty": "100"},
                {"px": "0.52", "qty": "100"},
            ],
            "transactTime": NOW.isoformat(),
        }}


class FakeOrders:
    def __init__(self):
        self.create_calls = []
        self.cancel_calls = []
        self.rows = {}

    async def list(self, params):
        active = {
            "ORDER_STATE_OPEN", "ORDER_STATE_NEW",
            "ORDER_STATE_PARTIALLY_FILLED", "ORDER_STATE_PENDING",
        }
        return {"orders": [row for row in self.rows.values()
                           if row["state"] in active]}

    async def create(self, payload):
        self.create_calls.append(payload)
        return {"id": f"fake-order-{len(self.create_calls)}", "executions": []}

    async def cancel(self, order_id, payload):
        self.cancel_calls.append((order_id, payload))
        self.rows[order_id]["state"] = "ORDER_STATE_CANCELED"
        self.rows[order_id]["lastTransactTime"] = (
            datetime(2026, 8, 13, 5, 0, 1, tzinfo=timezone.utc).isoformat())

    async def retrieve(self, order_id):
        return {"order": self.rows[order_id]}


class FakePortfolio:
    def __init__(self):
        self.rows = {}

    async def positions(self, params):
        return {"positions": self.rows, "eof": True}


class FakeAccount:
    def __init__(self):
        self.current_balance = Decimal("100")
        self.asset_notional = Decimal("0")

    async def balances(self):
        return {"balances": [{
            "currency": "USD",
            "currentBalance": str(self.current_balance),
            "assetNotional": str(self.asset_notional),
            "buyingPower": str(self.current_balance),
        }]}


class FakeClient:
    def __init__(self):
        self.markets = FakeMarkets()
        self.orders = FakeOrders()
        self.portfolio = FakePortfolio()
        self.account = FakeAccount()
        self.reward_pool = 20000
        self.earnings_rows = []

    async def get(self, path, *, query=None, authenticated=False):
        if path == "/v1/incentives/earnings":
            return {"rewards": self.earnings_rows}
        if path != "/v1/incentives":
            raise AssertionError(f"unexpected GET {path}")
        symbols = list((query or {}).get("symbols") or ("market-one",))
        return {"programs": [{
            "marketSlug": slug,
            "eventStartTime": GAME_START.isoformat(),
            "timePeriods": [{
                "programId": "fake-day-of-program",
                "programType": "liquidityProgram",
                "period": "day_of",
                "status": "active",
                "rewardPool": self.reward_pool,
                "discountFactor": 0.5,
                "targetSize": 10,
                "start": None,
                "end": None,
            }],
        } for slug in symbols]}


class FakePrivateStream:
    def __init__(self, _client):
        self.events = [PrivateEvent(
            PrivateEventKind.ACCOUNT_BALANCE_SNAPSHOT,
            {"balances": []}, "fake-balance")]
        self.started = False
        self.started_with = None
        self.closed = False

    async def start(self, slugs=None):
        self.started = True
        self.started_with = slugs

    async def next_event(self, *, timeout=None):
        if self.events:
            return self.events.pop(0)
        await asyncio.Future()

    async def close(self):
        self.closed = True


class FakeMarketStream:
    def __init__(self, _client):
        self.started = False
        self.started_with = None
        self.closed = False

    async def start(self, slugs):
        self.started = True
        self.started_with = slugs

    async def next_book(self, *, timeout=None):
        await asyncio.Future()

    async def close(self):
        self.closed = True


class FailingPrivateStream(FakePrivateStream):
    async def start(self, slugs=None):
        self.started_with = slugs
        raise AdapterError("simulated private reconnect failure")


class FakeNotifier:
    def __init__(self, *, delivery_succeeds=True):
        self.messages = []
        self.payloads = []
        self.delivery_succeeds = delivery_succeeds

    async def send_async(self, content, *, critical=False):
        self.messages.append((content, critical))
        return NotificationResult(
            self.delivery_succeeds,
            "sent" if self.delivery_succeeds else "delivery_failed")

    async def send_payload_async(self, payload, *, critical=False):
        self.payloads.append((payload, critical))
        return NotificationResult(
            self.delivery_succeeds,
            "sent" if self.delivery_succeeds else "delivery_failed")


def fake_selection(slug: str, *, discovered: int = 4) -> RewardMarketSelection:
    market = MarketMetadata(
        slug=slug,
        game_start=GAME_START,
        tick_size=.01,
        active=True,
        closed=False,
        status="MARKET_STATUS_OPEN",
        minimum_trade_qty=1,
        sports_market_type_v2="SPORTS_MARKET_TYPE_MONEYLINE",
    )
    period = IncentivePeriod(
        program_id=f"reward-{slug}",
        period="day_of",
        status="active",
        reward_pool=200,
        discount_factor=.5,
        target_size=10,
    )
    incentive = MarketIncentive(slug, (period,))
    book = BookSnapshot(
        slug,
        (BookLevel(.49, 100), BookLevel(.48, 100)),
        (BookLevel(.51, 100), BookLevel(.52, 100)),
    )
    # The real selector only ever sets `selected` to an opportunity with a
    # viable side, so the fake must produce one too or it tests a state
    # that cannot occur.
    viable_side = SideOpportunity(
        side="bid",
        price=.49,
        reward_score_eligible=True,
        projection=RewardProjection(
            True, 1.0, 10.0, .1, .001, 1.0, "projected"),
        stressed_exit_complete=True,
        stressed_exit_pnl=-.01,
        expected_net_value=.0005,
        blocker=None,
        fill_probability=.01,
        fill_risk_driver="test",
    )
    opportunity = RewardMarketOpportunity(
        market, incentive, period, book, (viable_side,))
    return RewardMarketSelection(
        selected=opportunity,
        opportunities=(opportunity,),
        rewarded_executable_markets=discovered,
        catalog_markets=discovered,
        executable_catalog_markets=discovered,
        unsupported_programs=0,
        eligible_pregame_markets=discovered,
        books_evaluated=1,
        deferred_markets=(),
        failures=(),
    )


def no_viable_selection(slug: str = "monitor-only") -> RewardMarketSelection:
    # Nothing viable means nothing to bind: no selected market and no
    # opportunity a top-N accessor could pick up either.
    return replace(fake_selection(slug), selected=None, opportunities=())


class FakeRewardSelector:
    def __init__(self, *selections):
        self.selections = list(selections)
        self.calls = []

    async def select(self, *, now):
        self.calls.append(now)
        if len(self.selections) > 1:
            return self.selections.pop(0)
        return self.selections[0]


class TickingClock:
    def __init__(self):
        self.value = NOW

    def __call__(self):
        result = self.value
        self.value += timedelta(seconds=1)
        return result


class LiveCommissioningTests(unittest.IsolatedAsyncioTestCase):
    def build_runner(
        self,
        *,
        clock=lambda: NOW,
        notifier=None,
        auto=False,
        reward_selector=None,
    ):
        root = Path(tempfile.mkdtemp(prefix="liquidity-commissioning-"))
        ledger = ExecutionLedger(root / "ledger.db")
        self.addCleanup(ledger.close)
        client = FakeClient()
        notifier = notifier or FakeNotifier()
        config = CommissioningConfig(
            market_slug=AUTO_REWARD_MARKET if auto else "market-one",
            economic_risk_accepted=True,
            exclusive_api_key_confirmed=True,
            maker_lab_stopped_confirmed=True,
            arm_live=True,
            auto_select_reward_markets=auto,
        )
        runner = CommissioningRunner(
            client, config, ledger, notifier,
            clock=clock,
            sleep=lambda _seconds: asyncio.sleep(0),
            market_stream_factory=FakeMarketStream,
            private_stream_factory=FakePrivateStream,
            reward_selector=reward_selector,
        )
        runner.gateway.limiter = ImmediateLimiter()
        return runner, client, notifier

    async def test_preflight_then_first_fake_order_is_exactly_one_contract(self):
        runner, client, _ = self.build_runner()
        await runner.preflight()
        self.assertEqual(client.orders.create_calls, [])
        self.assertTrue(runner.gate.ready(runner.ledger, now=NOW))
        self.assertEqual(runner.private_stream.started_with, ["market-one"])

        runner._evaluate_rewards(NOW)
        actions = runner.reconciler.plan(runner._frame(NOW))
        self.assertEqual({action.side for action in actions}, {"bid", "offer"})
        self.assertTrue(all(action.qty == Decimal("1") for action in actions))
        await runner.driver.apply_one(
            actions, market=runner.market, position=Decimal("0"), now=NOW)

        self.assertEqual(len(client.orders.create_calls), 1)
        payload = client.orders.create_calls[0]
        self.assertEqual(payload["quantity"], 1)
        self.assertTrue(payload["participateDontInitiate"])
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_score_seconds_do_not_accrue_without_a_working_order(self):
        runner, _, _ = self.build_runner()
        await runner.preflight()

        runner._evaluate_rewards(NOW)
        runner._evaluate_rewards(NOW + timedelta(seconds=10))

        self.assertTrue(runner._expected_net_by_side)
        self.assertEqual(runner._score_by_side, {})
        self.assertEqual(runner._score_seconds, 0)
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_confirmed_economic_working_order_accrues_score_seconds(self):
        runner, _, _ = self.build_runner()
        await runner.preflight()
        working = WorkingOrder(
            "owned-bid", "bid", .49, Decimal("1"))

        with patch.object(
                runner, "_working_orders", return_value=(working,)):
            runner._evaluate_rewards(NOW)
            self.assertIn("bid", runner._score_by_side)
            expected_score = runner._score_by_side["bid"] * 10
            runner._evaluate_rewards(NOW + timedelta(seconds=10))

        self.assertAlmostEqual(runner._score_seconds, expected_score)
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_blocked_working_order_stops_future_score_accrual(self):
        runner, client, _ = self.build_runner()
        client.reward_pool = 200
        await runner.preflight()
        working = WorkingOrder(
            "owned-bid", "bid", .49, Decimal("1"))

        with patch.object(
                runner, "_working_orders", return_value=(working,)):
            runner._evaluate_rewards(NOW)
            runner._evaluate_rewards(NOW + timedelta(seconds=10))

        self.assertEqual(runner._score_by_side, {})
        self.assertEqual(runner._score_seconds, 0)
        self.assertEqual(
            set(runner._economic_blocker_by_side.values()),
            {"projected_reward_does_not_cover_probability_weighted_exit_cost"},
        )
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_auto_preflight_selects_from_reward_universe(self):
        selector = FakeRewardSelector(fake_selection("market-two", discovered=7))
        runner, client, notifier = self.build_runner(
            auto=True, reward_selector=selector)

        mode = await runner.preflight()

        self.assertEqual(mode, "TRADE")
        self.assertEqual(runner.market_slug, "market-two")
        self.assertEqual(runner.private_stream.started_with, ["market-two"])
        self.assertEqual(runner.market_stream.started_with, ["market-two"])
        self.assertEqual(len(selector.calls), 1)
        self.assertEqual(client.orders.create_calls, [])
        self.assertTrue(any(
            "from 7 rewarded executable markets" in message
            for message, _ in notifier.messages))
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_transient_startup_condition_retries_instead_of_exiting(self):
        runner, client, _ = self.build_runner(clock=TickingClock())
        calls = []
        original = runner.preflight

        async def flaky():
            calls.append(1)
            if len(calls) < 3:
                # A late private snapshot or a Discord blip used to exit 2,
                # which the supervisor never restarts, ending the whole run.
                raise CommissioningUnavailable(
                    "private stream did not become reconciled and fresh")
            return await original()

        runner.preflight = flaky

        mode = await runner.preflight_until_ready()

        self.assertEqual(mode, "TRADE")
        self.assertEqual(len(calls), 3)
        self.assertEqual(client.orders.create_calls, [])
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_unsafe_startup_refusal_is_never_retried(self):
        runner, client, _ = self.build_runner(clock=TickingClock())
        calls = []

        async def unowned():
            calls.append(1)
            raise CommissioningRefused(
                "shared account has open orders")

        runner.preflight = unowned

        with self.assertRaisesRegex(CommissioningRefused, "open orders"):
            await runner.preflight_until_ready()

        # Ownership and account-state refusals must stay terminal: retrying
        # them would be retrying into a known-unsafe account.
        self.assertEqual(len(calls), 1)
        self.assertEqual(client.orders.create_calls, [])
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_operator_stop_ends_startup_retries(self):
        runner, client, _ = self.build_runner(clock=TickingClock())

        async def unavailable():
            runner.request_stop("operator_sigterm")
            raise CommissioningUnavailable("selected market is not open")

        runner.preflight = unavailable

        with self.assertRaisesRegex(CommissioningRefused, "operator stopped"):
            await runner.preflight_until_ready()

        self.assertEqual(client.orders.create_calls, [])
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_auto_preflight_waits_for_a_viable_market_then_binds(self):
        # An empty universe is the normal case, not a misconfiguration. The
        # runner must hold, not exit 2 and stay down until an operator notices.
        selector = FakeRewardSelector(
            no_viable_selection(),
            no_viable_selection(),
            fake_selection("market-one"),
        )
        runner, client, _ = self.build_runner(
            auto=True, reward_selector=selector, clock=TickingClock())

        mode = await runner.preflight()

        self.assertEqual(mode, "TRADE")
        self.assertEqual(runner.market_slug, "market-one")
        self.assertGreaterEqual(len(selector.calls), 3)
        self.assertEqual(client.orders.create_calls, [])
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_operator_stop_ends_the_wait_for_a_viable_market(self):
        selector = FakeRewardSelector(no_viable_selection())
        runner, client, _ = self.build_runner(
            auto=True, reward_selector=selector, clock=TickingClock())
        original = runner.sleep

        async def stop_then_sleep(seconds):
            # SIGTERM lands while the runner is waiting; handlers are installed
            # before run(), so the wait must observe it and exit cleanly.
            runner.request_stop("operator_sigterm")
            return await original(seconds)

        runner.sleep = stop_then_sleep

        with self.assertRaisesRegex(CommissioningRefused, "operator stopped"):
            await runner.preflight()

        self.assertEqual(client.orders.create_calls, [])
        self.assertEqual(runner.active_slugs, ())

    async def test_auto_rotation_rebinds_only_after_whole_account_flat(self):
        selector = FakeRewardSelector(
            fake_selection("market-one"),
            fake_selection("market-two"),
        )
        runner, client, notifier = self.build_runner(
            auto=True, reward_selector=selector)
        await runner.preflight()
        old_private = runner.private_stream
        old_market = runner.market_stream

        changed = await runner._rotate_reward_market(NOW)

        self.assertTrue(changed)
        self.assertEqual(runner.market_slug, "market-two")
        self.assertTrue(old_private.closed)
        self.assertTrue(old_market.closed)
        self.assertEqual(runner.private_stream.started_with, ["market-two"])
        self.assertEqual(runner.market_stream.started_with, ["market-two"])
        self.assertTrue(runner.gate.ready(runner.ledger, now=NOW))
        self.assertEqual(client.orders.create_calls, [])
        self.assertTrue(any(
            "rotated while flat" in message for message, _ in notifier.messages))
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_discovery_defers_while_a_viable_side_is_authorized(self):
        selector = FakeRewardSelector(fake_selection("market-one"))
        runner, _, _ = self.build_runner(
            auto=True, reward_selector=selector)
        await runner.preflight()

        runner._expected_net_by_side = {"bid": 1e-6}

        # Scanning here would cancel a quote that is currently accruing score
        # to buy an unmeasured chance of a better market.
        self.assertFalse(runner._discovery_is_free())
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_discovery_defers_while_an_owned_quote_rests(self):
        selector = FakeRewardSelector(fake_selection("market-one"))
        runner, _, _ = self.build_runner(
            auto=True, reward_selector=selector)
        await runner.preflight()
        runner._expected_net_by_side = {}
        runner.ledger.apply_order_event(
            "ws", "resting-owned",
            OrderState(
                client_order_id="liquidity-resting",
                exchange_order_id="resting-1",
                market_slug="market-one",
                side="ORDER_SIDE_BUY",
                price=Decimal("0.49"),
                original_qty=Decimal("1"),
                filled_qty=Decimal("0"),
                status="OPEN",
                event_at=runner._now()),
            received_at=runner._now())

        self.assertFalse(runner._discovery_is_free())
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_discovery_runs_when_nothing_rests_and_nothing_is_viable(self):
        selector = FakeRewardSelector(fake_selection("market-one"))
        runner, _, _ = self.build_runner(
            auto=True, reward_selector=selector)
        await runner.preflight()
        runner._expected_net_by_side = {}

        # Nothing resting and no authorized side means accrual is already zero,
        # so the scan cannot cost score-seconds.
        self.assertTrue(runner._discovery_is_free())
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_stale_book_recovery_refreshes_the_market_that_went_stale(self):
        runner, client, _ = self.build_runner(clock=TickingClock())
        await runner.preflight()
        runner.config = replace(runner.config, max_markets=8)
        runner._bind_markets(("market-one", "market-two"))

        refreshed: list[str | None] = []

        async def record(now, slug=None):
            refreshed.append(slug)

        runner._refresh_book = record

        await runner._recover_strategy_state("book_stale", "market-two")

        # Refreshing the primary would leave market-two stale forever: it would
        # report book_stale on every pass and never quote again.
        self.assertEqual(refreshed, ["market-two"])
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_recovery_backoff_is_tracked_per_market(self):
        runner, _, _ = self.build_runner(clock=TickingClock())
        await runner.preflight()
        runner.config = replace(runner.config, max_markets=8)
        runner._bind_markets(("market-one", "market-two"))

        for _ in range(6):
            await runner._report_runtime_error(
                "strategy_state:market-one", AdapterError("flapping"))

        # One market failing repeatedly must not push another market's sweep
        # onto the 30-second cap; that would cost score-seconds everywhere.
        self.assertEqual(
            runner._recovery_delay("strategy_state:market-one"), 30.0)
        self.assertEqual(
            runner._recovery_delay("strategy_state:market-two"), 1.0)
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_rotation_gate_sees_inventory_on_a_non_primary_market(self):
        from liquidity_bot.execution_ledger import PositionState

        selector = FakeRewardSelector(fake_selection("market-one"))
        runner, _, _ = self.build_runner(
            auto=True, reward_selector=selector)
        runner.config = replace(runner.config, max_markets=8)
        await runner.preflight()
        runner._bind_markets(("market-one", "market-two"))
        runner._expected_net_by_side = {}

        # Inventory on the second slot, nothing on the primary. Gating on
        # self.market_slug alone would call this flat and tear both streams
        # down for a universe scan while the position sat unhedged.
        runner.ledger.apply_position_event(
            "ws", "position-on-slot-two",
            PositionState("market-two", Decimal("1"), runner._now()),
            received_at=runner._now())

        # The primary reads flat, so a per-primary gate would let the scan run.
        self.assertEqual(runner.ledger.position(runner.market_slug), 0)
        self.assertTrue(runner._discovery_is_free())
        # The whole-account gate must still refuse.
        self.assertTrue(runner._has_open_position())
        self.assertFalse(runner._safe_to_scan())
        self.assertIn(
            "a nonzero position remains", runner._rotation_blockers())
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_zero_viable_scan_stays_idle_when_bound_market_expired(self):
        selector = FakeRewardSelector(
            fake_selection("market-one"),
            no_viable_selection(),
        )
        runner, client, notifier = self.build_runner(
            auto=True, reward_selector=selector, clock=TickingClock())
        await runner.preflight()
        old_private = runner.private_stream
        old_market = runner.market_stream

        async def expired(_now):
            raise CommissioningRefused("selected market is no longer pregame")

        runner._refresh_contracts = expired

        # A bound market that starts mid-scan is an idle result, not a
        # discovery failure. Escalating it would put the caller on its 60
        # second error retry and rescan the whole universe continuously.
        changed = await runner._rotate_reward_market(runner._now())

        self.assertFalse(changed)
        self.assertEqual(runner.market_slug, "market-one")
        self.assertIs(runner.private_stream, old_private)
        self.assertIs(runner.market_stream, old_market)
        self.assertIsNone(runner.incentive)
        self.assertEqual(runner._reward_eligible, frozenset())
        self.assertEqual(client.orders.create_calls, [])
        self.assertTrue(any(
            "idle_market_refresh" in message
            for message, _ in notifier.messages))
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_zero_viable_refresh_keeps_current_stream_scope(self):
        selector = FakeRewardSelector(
            fake_selection("market-one"),
            no_viable_selection(),
        )
        runner, client, _ = self.build_runner(
            auto=True, reward_selector=selector)
        await runner.preflight()
        old_private = runner.private_stream
        old_market = runner.market_stream

        changed = await runner._rotate_reward_market(NOW)

        self.assertFalse(changed)
        self.assertEqual(runner.market_slug, "market-one")
        self.assertIs(runner.private_stream, old_private)
        self.assertIs(runner.market_stream, old_market)
        self.assertFalse(old_private.closed)
        self.assertFalse(old_market.closed)
        self.assertIsNone(runner._market_selection.selected)
        self.assertEqual(client.orders.create_calls, [])
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_auto_rotation_refuses_nonzero_position_without_switch(self):
        selector = FakeRewardSelector(
            fake_selection("market-one"),
            fake_selection("market-two"),
        )
        runner, client, _ = self.build_runner(
            auto=True, reward_selector=selector)
        await runner.preflight()
        old_private = runner.private_stream
        client.portfolio.rows = {
            "market-one": {
                "netPosition": "1",
                "netPositionDecimal": "1.0000",
                "updateTime": NOW.isoformat(),
            }
        }

        with self.assertRaisesRegex(AdapterError, "nonzero position"):
            await runner._rotate_reward_market(NOW)

        self.assertEqual(runner.market_slug, "market-one")
        self.assertFalse(old_private.closed)
        self.assertEqual(len(selector.calls), 1)
        self.assertEqual(client.orders.create_calls, [])
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_auto_restart_recovers_ledger_market_before_new_selection(self):
        selector = FakeRewardSelector(fake_selection("market-two"))
        runner, client, _ = self.build_runner(
            auto=True, reward_selector=selector, clock=TickingClock())
        payload = {
            "marketSlug": "market-one",
            "intent": "ORDER_INTENT_BUY_LONG",
            "type": "ORDER_TYPE_LIMIT",
            "price": {"value": "0.49", "currency": "USD"},
            "quantity": 1,
            "tif": "TIME_IN_FORCE_GOOD_TILL_CANCEL",
            "participateDontInitiate": True,
            "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATIC",
            "synchronousExecution": False,
        }
        runner.ledger.prepare_intent(
            "prior-auto", "PLACE", "market-one", "prior-auto-client",
            payload, created_at=NOW)
        runner.ledger.transition_intent(
            "prior-auto", "SUBMITTING", updated_at=NOW)
        runner.ledger.transition_intent(
            "prior-auto", "ACKNOWLEDGED", updated_at=NOW,
            exchange_order_id="prior-auto-order")
        client.orders.rows["prior-auto-order"] = {
            "id": "prior-auto-order",
            "marketSlug": "market-one",
            "side": "ORDER_SIDE_BUY",
            "intent": "ORDER_INTENT_BUY_LONG",
            "price": {"value": "0.49", "currency": "USD"},
            "quantity": "1",
            "cumQuantity": "0",
            "state": "ORDER_STATE_NEW",
            "createTime": NOW.isoformat(),
        }

        result = await runner.run()

        self.assertEqual(result.stop_reason, "startup_recovery_only")
        self.assertEqual(runner.market_slug, "market-one")
        self.assertEqual(selector.calls, [])
        self.assertEqual(client.orders.create_calls, [])
        self.assertEqual(len(client.orders.cancel_calls), 1)

    async def test_auto_start_refuses_unattributed_position_in_selected_market(self):
        selector = FakeRewardSelector(fake_selection("market-one"))
        runner, client, _ = self.build_runner(
            auto=True, reward_selector=selector)
        client.portfolio.rows = {
            "market-one": {
                "netPosition": "1",
                "netPositionDecimal": "1.0000",
                "updateTime": NOW.isoformat(),
            }
        }

        with self.assertRaisesRegex(CommissioningRefused, "nonzero positions"):
            await runner.run()

        self.assertEqual(client.orders.create_calls, [])
        self.assertEqual(client.orders.cancel_calls, [])

    async def test_sub_dollar_reward_is_evaluated_but_risk_can_block_order(self):
        runner, client, _ = self.build_runner()
        client.reward_pool = 200
        await runner.preflight()
        runner._evaluate_rewards(NOW)

        actions = runner.reconciler.plan(runner._frame(NOW))

        self.assertEqual(actions, [])
        self.assertEqual(runner._expected_net_by_side, {})
        self.assertEqual(
            {item.reason for item in runner._reward_projection_by_side.values()},
            {"projected"})
        self.assertEqual(
            set(runner._economic_blocker_by_side.values()),
            {"projected_reward_does_not_cover_probability_weighted_exit_cost"},
        )
        self.assertTrue(all(
            0 < value < 1
            for value in runner._fill_probability_by_side.values()))
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_tight_book_is_economically_blocked_before_new_order(self):
        runner, client, _ = self.build_runner()
        await runner.preflight()
        runner.book = BookSnapshot(
            "market-one",
            (BookLevel(.49, 100), BookLevel(.48, 100)),
            (BookLevel(.50, 100), BookLevel(.51, 100)),
            "MARKET_STATE_OPEN",
            NOW,
        )
        runner.book_observed_at = NOW

        runner._evaluate_rewards(NOW)
        actions = runner.reconciler.plan(runner._frame(NOW))

        self.assertEqual(actions, [])
        self.assertEqual(runner._expected_net_by_side, {})
        self.assertEqual(
            set(runner._economic_blocker_by_side.values()),
            {"spread_below_entry_minimum"},
        )
        self.assertEqual(client.orders.create_calls, [])
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_non_moneyline_structure_cannot_build_risk(self):
        runner, client, _ = self.build_runner()
        await runner.preflight()
        runner.market = replace(
            runner.market,
            sports_market_type_v2="SPORTS_MARKET_TYPE_PROP",
        )

        runner._evaluate_rewards(NOW)
        actions = runner.reconciler.plan(runner._frame(NOW))

        self.assertEqual(actions, [])
        self.assertEqual(runner._reward_eligible, frozenset())
        self.assertEqual(
            runner._economic_blocker_by_side,
            {
                "bid": "unsupported_market_structure",
                "offer": "unsupported_market_structure",
            },
        )
        self.assertEqual(client.orders.create_calls, [])
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_selection_and_rotation_durations_are_instrumented(self):
        selector = FakeRewardSelector(
            fake_selection("market-one"),
            fake_selection("market-one"),
            fake_selection("market-two"),
        )
        runner, _, _ = self.build_runner(
            auto=True, reward_selector=selector)
        await runner._select_reward_market(NOW)
        self.assertIsNotNone(runner._selection_scan_seconds_last)
        self.assertGreaterEqual(runner._selection_scan_seconds_last, 0)

        await runner.preflight()
        changed = await runner._rotate_reward_market(NOW)

        self.assertTrue(changed)
        self.assertIsNotNone(runner._selection_scan_seconds_last)
        self.assertGreaterEqual(runner._selection_scan_seconds_last, 0)
        self.assertIsNotNone(runner._rotation_transition_seconds_last)
        self.assertGreaterEqual(runner._rotation_transition_seconds_last, 0)
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_ignored_earnings_query_cannot_import_other_market_rewards(self):
        runner, client, _ = self.build_runner()
        client.earnings_rows = [{
            "date": "2026-08-10",
            "marketSlug": "other-market",
            "programType": "liquidityProgram",
            "reward": "32.06",
            "status": "PAID",
        }]

        await runner.preflight()

        self.assertEqual(runner._earnings_by_status, {})
        self.assertEqual(runner.ledger.query(
            "SELECT * FROM execution_reward_earnings"), [])
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_taker_exits_are_allowed_up_to_the_evidence_budget(self):
        runner, _, _ = self.build_runner()
        await runner.preflight()
        budget = runner.config.max_taker_exit_orders
        at = NOW
        for index in range(budget):
            at = NOW + timedelta(seconds=index + 1)
            runner.ledger.apply_execution(Execution(
                f"maker-entry-{index}", "market-one", "ORDER_SIDE_BUY",
                Decimal("0.49"), Decimal("1"), Decimal("0"), at,
                client_order_id=f"entry-{index}", liquidity="MAKER",
                intent="ORDER_INTENT_BUY_LONG"), received_at=at)
            runner.ledger.apply_execution(Execution(
                f"taker-exit-{index}", "market-one", "ORDER_SIDE_SELL",
                Decimal("0.49"), Decimal("1"), Decimal("0.01"), at,
                client_order_id=f"exit-{index}", liquidity="TAKER",
                intent="ORDER_INTENT_SELL_LONG"), received_at=at)
            if index < budget - 1:
                # One crossed exit used to end the session, which capped exit
                # evidence at a single sample. SCALE_REVIEW asks for 100.
                self.assertIsNone(
                    runner._stop_reason(at),
                    "session must survive taker exit %d of %d" % (
                        index + 1, budget))

        self.assertEqual(
            runner._stop_reason(at), "commissioning_taker_exit_limit")
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_operator_stop_exits_flat_without_placing_an_order(self):
        runner, client, notifier = self.build_runner()
        runner.request_stop("operator_requested")
        result = await runner.run()

        self.assertEqual(result.stop_reason, "operator_requested")
        self.assertIsNone(result.cutoff_at)
        self.assertTrue(result.flat)
        self.assertEqual(result.orders_sent_to_polymarket, 0)
        self.assertEqual(client.orders.create_calls, [])
        self.assertTrue(runner.private_stream.closed)
        self.assertTrue(runner.market_stream.closed)
        self.assertGreaterEqual(len(notifier.messages), 3)

    async def test_single_stop_fires_at_thirty_then_the_ceiling(self):
        runner, client, _ = self.build_runner()
        await runner.preflight()
        start = Decimal("100")

        # Below the single operative stop nothing fires: there is no tiered
        # soft stop underneath it any more.
        client.account.current_balance = start - Decimal("8")
        await runner._refresh_balance(NOW)
        self.assertIsNone(runner._stop_reason(NOW))

        # At the $30 stop the session ends.
        client.account.current_balance = start - runner.config.max_session_loss_usd
        await runner._refresh_balance(NOW)
        self.assertTrue(runner._stop_reason(NOW).startswith("risk_stop:"))

        # The $35 ceiling sits above it only because the emergency stop must
        # exceed the normal stop; in practice the $30 stop fires first.
        client.account.current_balance = (
            start - runner.config.hard_stop_loss_usd - Decimal("1"))
        await runner._refresh_balance(NOW)
        self.assertEqual(
            runner._stop_reason(NOW), "EMERGENCY_EQUITY_HARD_STOP")
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_restart_recovers_identified_owned_order_without_new_risk(self):
        runner, client, notifier = self.build_runner(clock=TickingClock())
        payload = {
            "marketSlug": "market-one",
            "intent": "ORDER_INTENT_BUY_LONG",
            "type": "ORDER_TYPE_LIMIT",
            "price": {"value": "0.49", "currency": "USD"},
            "quantity": 1,
            "tif": "TIME_IN_FORCE_GOOD_TILL_CANCEL",
            "participateDontInitiate": True,
            "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATIC",
            "synchronousExecution": False,
        }
        runner.ledger.prepare_intent(
            "prior-place", "PLACE", "market-one", "prior-client", payload,
            created_at=NOW)
        runner.ledger.transition_intent(
            "prior-place", "SUBMITTING", updated_at=NOW)
        runner.ledger.transition_intent(
            "prior-place", "ACKNOWLEDGED", updated_at=NOW,
            exchange_order_id="prior-order")
        client.orders.rows["prior-order"] = {
            "id": "prior-order",
            "marketSlug": "market-one",
            "side": "ORDER_SIDE_BUY",
            "intent": "ORDER_INTENT_BUY_LONG",
            "price": {"value": "0.49", "currency": "USD"},
            "quantity": "1",
            "cumQuantity": "0",
            "state": "ORDER_STATE_NEW",
            "createTime": NOW.isoformat(),
        }

        result = await runner.run()

        self.assertEqual(result.stop_reason, "startup_recovery_only")
        self.assertTrue(result.flat)
        self.assertEqual(result.orders_sent_to_polymarket, 0)
        self.assertEqual(client.orders.create_calls, [])
        self.assertEqual(len(client.orders.cancel_calls), 1)
        self.assertTrue(any(critical for _, critical in notifier.messages))

    async def test_unowned_exchange_order_refuses_and_never_mutates(self):
        runner, client, _ = self.build_runner()
        client.orders.rows["external-order"] = {
            "id": "external-order",
            "marketSlug": "market-one",
            "side": "ORDER_SIDE_BUY",
            "intent": "ORDER_INTENT_BUY_LONG",
            "price": {"value": "0.49", "currency": "USD"},
            "quantity": "1",
            "cumQuantity": "0",
            "state": "ORDER_STATE_NEW",
            "createTime": NOW.isoformat(),
        }

        with self.assertRaisesRegex(
                CommissioningRefused, "open orders|ownership"):
            await runner.run()

        self.assertEqual(client.orders.create_calls, [])
        self.assertEqual(client.orders.cancel_calls, [])
        self.assertTrue(runner.private_stream.closed)
        self.assertTrue(runner.market_stream.closed)

    async def test_failed_startup_discord_delivery_blocks_first_order(self):
        notifier = FakeNotifier(delivery_succeeds=False)
        runner, client, _ = self.build_runner(notifier=notifier)

        with self.assertRaisesRegex(
                CommissioningRefused, "Discord startup alert delivery failed"):
            await runner.run()

        self.assertEqual(client.orders.create_calls, [])
        self.assertEqual(client.orders.cancel_calls, [])

    async def test_reconciliation_can_clear_last_unresolved_intent_mid_pass(self):
        runner, _, _ = self.build_runner()
        self.assertIsNone(runner._oldest_unresolved_age_seconds(NOW))
        runner.ledger.prepare_intent(
            "temporary", "PLACE", "market-one", "temporary-client",
            {"marketSlug": "market-one"}, created_at=NOW)
        self.assertEqual(runner._oldest_unresolved_age_seconds(NOW), 0)
        runner.ledger.transition_intent(
            "temporary", "FAILED", updated_at=NOW)
        self.assertIsNone(runner._oldest_unresolved_age_seconds(NOW))

    async def test_quiet_change_driven_book_refreshes_before_stale_limit(self):
        runner, _, _ = self.build_runner()
        runner.book_observed_at = NOW
        self.assertFalse(runner._quiet_book_needs_rest_refresh(
            NOW + timedelta(seconds=2)))
        self.assertTrue(runner._quiet_book_needs_rest_refresh(
            NOW + timedelta(seconds=3)))
        self.assertTrue(runner._quiet_book_needs_rest_refresh(
            NOW - timedelta(seconds=1)))

    async def test_rest_book_timestamp_is_response_receipt_not_request_start(self):
        received = NOW + timedelta(seconds=4)
        runner, _, _ = self.build_runner(clock=lambda: received)
        runner.market = await runner.gateway.fetch_market("market-one")
        await runner._refresh_book(NOW)
        self.assertEqual(runner.book_observed_at, received)

    async def test_book_due_check_reads_clock_after_upstream_api_latency(self):
        after_slow_io = NOW + timedelta(seconds=6)
        runner, client, _ = self.build_runner(clock=lambda: after_slow_io)
        runner.market = await runner.gateway.fetch_market("market-one")
        runner.book_observed_at = NOW
        next_refresh = await runner._refresh_quiet_book_if_due(
            NOW + timedelta(seconds=2))
        self.assertEqual(client.markets.book_calls, 1)
        self.assertEqual(runner.book_observed_at, after_slow_io)
        self.assertEqual(next_refresh, after_slow_io + timedelta(seconds=3))

    async def test_recent_ws_book_anchors_next_check_to_its_receipt(self):
        check_at = NOW + timedelta(seconds=4)
        runner, client, _ = self.build_runner(clock=lambda: check_at)
        runner.book_observed_at = NOW + timedelta(seconds=2)
        next_refresh = await runner._refresh_quiet_book_if_due(
            NOW + timedelta(seconds=3))
        self.assertEqual(client.markets.book_calls, 0)
        self.assertEqual(next_refresh, NOW + timedelta(seconds=5))

    async def test_periodic_rest_reconcile_backstops_quiet_private_stream(self):
        runner, _, _ = self.build_runner()
        await runner.preflight()
        later = NOW + timedelta(seconds=31)
        self.assertFalse(runner.gate.ready(runner.ledger, now=later))
        await runner._rest_reconcile(later)
        self.assertTrue(runner.gate.ready(runner.ledger, now=later))
        self.assertEqual(runner.gate.last_message_at, NOW)
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_private_adapter_error_recovers_without_placing_new_orders(self):
        runner, client, notifier = self.build_runner()
        await runner.preflight()
        old_stream = runner.private_stream

        stop = await runner._recover_private_stream(
            AdapterError("position market slug is missing"))

        self.assertIsNone(stop)
        self.assertTrue(old_stream.closed)
        self.assertIsNot(runner.private_stream, old_stream)
        self.assertEqual(runner.private_stream.started_with, ["market-one"])
        self.assertTrue(runner.gate.ready(runner.ledger, now=NOW))
        self.assertEqual(client.orders.create_calls, [])
        self.assertTrue(any(critical for _, critical in notifier.messages))
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_market_adapter_error_recovers_from_fresh_book_without_new_orders(self):
        runner, client, notifier = self.build_runner()
        await runner.preflight()
        old_stream = runner.market_stream
        recovered_book = runner.book

        class RecoveringMarketStream(FakeMarketStream):
            async def next_book(self, *, timeout=None):
                return recovered_book

        runner.market_stream_factory = RecoveringMarketStream
        stop = await runner._recover_market_stream(
            AdapterError("market WebSocket disconnected"))

        self.assertIsNone(stop)
        self.assertTrue(old_stream.closed)
        self.assertIsNot(runner.market_stream, old_stream)
        self.assertIs(runner.book, recovered_book)
        self.assertEqual(client.orders.create_calls, [])
        self.assertTrue(any(critical for _, critical in notifier.messages))
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_repeated_private_reconnect_failure_stays_alive_until_operator_stop(self):
        runner, client, _ = self.build_runner()
        await runner.preflight()
        runner.private_stream_factory = FailingPrivateStream
        sleeps = 0

        async def stop_after_retry(_seconds):
            nonlocal sleeps
            sleeps += 1
            runner.request_stop("operator_requested")

        runner.sleep = stop_after_retry
        stop = await runner._recover_private_stream(
            AdapterError("private WebSocket disconnected"))

        self.assertEqual(stop, "operator_requested")
        self.assertEqual(sleeps, 1)
        self.assertEqual(client.orders.create_calls, [])
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_hard_stop_remains_terminal_during_stream_recovery(self):
        runner, client, _ = self.build_runner()
        await runner.preflight()
        floor = Decimal(100) - runner.config.hard_stop_loss_usd
        client.account.current_balance = floor - Decimal(1)

        stop = await runner._recover_private_stream(
            AdapterError("private WebSocket disconnected"))

        self.assertEqual(stop, "EMERGENCY_EQUITY_HARD_STOP")
        self.assertEqual(client.orders.create_calls, [])
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_stale_equity_stays_alive_but_cannot_add_new_risk(self):
        runner, client, _ = self.build_runner()
        await runner.preflight()
        runner._evaluate_rewards(NOW)
        later = NOW + timedelta(seconds=16)
        runner.book_observed_at = later

        self.assertIsNone(runner._stop_reason(later))
        frame = runner._frame(later)
        self.assertEqual(frame.reward_eligible, frozenset())
        self.assertEqual(runner.reconciler.plan(frame), [])
        self.assertEqual(client.orders.create_calls, [])
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_pending_hard_exit_confirmation_flat_is_recoverable(self):
        runner, client, _ = self.build_runner(clock=TickingClock())
        await runner.preflight()
        slug = runner.config.market_slug
        runner.reconciler._hard_exit_sent[slug] = NOW

        outcome = await runner._reconcile_pending_hard_exit()

        self.assertEqual(outcome, "flat")
        self.assertNotIn(slug, runner.reconciler._hard_exit_sent)
        self.assertIsNone(runner._stop_reason(runner._now()))
        self.assertEqual(client.orders.create_calls, [])
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_terminal_unfilled_hard_exit_can_retry_without_process_stop(self):
        runner, client, _ = self.build_runner(clock=TickingClock())
        await runner.preflight()
        slug = runner.config.market_slug
        runner.reconciler._hard_exit_sent[slug] = NOW
        client.portfolio.rows = {
            slug: {
                "netPosition": "1",
                "netPositionDecimal": "1.0000",
                "updateTime": NOW.isoformat(),
            }
        }

        outcome = await runner._reconcile_pending_hard_exit()

        self.assertEqual(outcome, "retry")
        self.assertEqual(runner.ledger.position(slug), Decimal("1.0000"))
        self.assertNotIn(slug, runner.reconciler._hard_exit_sent)
        self.assertEqual(client.orders.create_calls, [])
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_confirmation_errors_escalate_at_the_deadline(self):
        runner, client, _ = self.build_runner(clock=TickingClock())
        await runner.preflight()
        slug = runner.market_slug
        runner.reconciler._hard_exit_sent[slug] = NOW - timedelta(hours=1)
        runner.reconciler.plan = lambda _frame: [PlanAction(
            ActionKind.CRITICAL, slug,
            "hard_exit_sent_but_inventory_remains")]

        async def fail_confirmation(_slug):
            raise AdapterError("confirmation endpoint unavailable")

        runner._reconcile_pending_hard_exit = fail_confirmation

        stop, _delay = await runner._decide_for_market(slug, runner._now())

        # Past the confirmation deadline this used to end the session, which
        # exits 4 and is never restarted. It is one market's stuck exit, so it
        # retires that market and the session carries on.
        self.assertIsNone(stop)
        self.assertTrue(runner._is_retired(slug))
        self.assertEqual(
            runner._retired[slug].reason, "hard_exit_confirmation_timeout")
        self.assertEqual(client.orders.create_calls, [])
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_a_market_that_never_exits_is_abandoned_not_endless(self):
        runner, _, notifier = self.build_runner(clock=TickingClock())
        await runner.preflight()
        slug = runner.market_slug

        for _ in range(MAX_RETIREMENT_ROUNDS):
            stop = await runner._retire_market(slug, "hard_exit_stuck")
            self.assertIsNone(stop)

        # Retirement resets the exit budget, so a market whose exit can never
        # complete would otherwise be replanned forever -- including after
        # kickoff, when nothing can be done. It stops being planned instead.
        self.assertEqual(runner._retired[slug].rounds, MAX_RETIREMENT_ROUNDS)
        stop, delay = await runner._decide_for_market(slug, runner._now())
        self.assertIsNone(stop)
        self.assertEqual(delay, 0.0)
        self.assertTrue(any(
            "abandoned" in message for message, _ in notifier.messages))
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_unknown_strategy_critical_is_terminal_by_default(self):
        runner, client, notifier = self.build_runner(clock=TickingClock())
        await runner.preflight()

        stop = await runner._handle_strategy_critical(
            "future_runtime_condition", runner.market_slug)

        self.assertEqual(stop, "critical:future_runtime_condition")
        self.assertEqual(client.orders.create_calls, [])
        self.assertTrue(any(critical for _, critical in notifier.messages))
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_reward_evaluation_invariant_keeps_its_terminal_code(self):
        runner, client, _ = self.build_runner(clock=TickingClock())
        invariant = RuntimeInvariantViolation(
            "working_order_intent_unmapped",
            "working order intent cannot be mapped",
        )

        with patch.object(
                runner, "_evaluate_rewards", side_effect=invariant):
            result = await runner.run()

        self.assertEqual(
            result.stop_reason, "critical:working_order_intent_unmapped")
        self.assertEqual(client.orders.create_calls, [])

    async def test_only_named_transient_strategy_critical_recovers(self):
        runner, client, _ = self.build_runner(clock=TickingClock())
        await runner.preflight()

        stop = await runner._handle_strategy_critical(
            "execution_state_unknown", runner.market_slug)

        self.assertIsNone(stop)
        self.assertEqual(client.orders.create_calls, [])
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_unexitable_inventory_conditions_retire_only_that_market(self):
        runner, client, _ = self.build_runner(clock=TickingClock())
        await runner.preflight()
        runner.config = replace(runner.config, max_markets=8)
        runner._bind_markets(("market-one", "market-two"))
        slug = "market-one"

        for reason in (
            "hard_exit_insufficient_stressed_depth",
            "hard_exit_price_cap_exceeded",
            "inventory_basis_or_clock_unknown",
            "inventory_clock_in_future",
            "hard_exit_attempt_limit_exceeded",
            "hard_exit_retry_window_exceeded",
        ):
            with self.subTest(reason=reason):
                # Each describes one market's inventory or exit. Ending the
                # session for them exits 4, which is never restarted, so one
                # thin book took down every other healthy market.
                self.assertIsNone(
                    await runner._handle_strategy_critical(reason, slug))
                self.assertTrue(runner._is_retired(slug))

        # The failing market is retired; the other one is untouched.
        self.assertFalse(runner._is_retired("market-two"))
        self.assertEqual(client.orders.create_calls, [])
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_a_retired_market_never_quotes_again_even_after_flat(self):
        runner, _, _ = self.build_runner(clock=TickingClock())
        await runner.preflight()
        slug = runner.market_slug

        await runner._retire_market(slug, "hard_exit_attempt_limit_exceeded")
        # Flat, so the exit machinery releases it.
        runner._release_retired_if_flat(slug)
        self.assertFalse(runner._is_retired(slug))

        runner._evaluate_rewards(runner._now(), slug)

        # Released from the exit machinery, still barred from quoting. The
        # exit failure is evidence about that book, not bad luck.
        self.assertTrue(runner._is_barred_from_quoting(slug))
        self.assertEqual(runner.state_for(slug).expected_net_by_side, {})
        self.assertEqual(
            runner.state_for(slug).economic_blocker_by_side,
            {"bid": "market_retired", "offer": "market_retired"})
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_retired_markets_are_not_reselected(self):
        selection = fake_selection("market-one")
        runner, _, _ = self.build_runner(
            auto=True, reward_selector=FakeRewardSelector(selection))
        await runner.preflight()

        self.assertEqual(
            [item.market.slug for item in selection.top_viable(8)],
            ["market-one"])
        # Once retired, it must not come back through the next scan.
        self.assertEqual(
            selection.top_viable(8, exclude={"market-one"}), ())
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_a_whole_slate_of_retirements_is_systemic_and_stops(self):
        runner, _, _ = self.build_runner(clock=TickingClock())
        await runner.preflight()

        stop = None
        for index in range(runner.config.max_markets + 1):
            stop = await runner._retire_market(f"market-{index}", "stuck")

        # Eight unlucky books is a coincidence; nine is a fault in us. Losing
        # one market to keep seven is the trade retirement makes -- losing
        # every market is not a trade, it is a broken session.
        self.assertEqual(stop, "critical:market_retirement_limit_exceeded")
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_shutdown_still_flattens_an_abandoned_market(self):
        from liquidity_bot.execution_ledger import PositionState

        runner, _, _ = self.build_runner(clock=TickingClock())
        await runner.preflight()
        slug = runner.market_slug
        runner.ledger.apply_position_event(
            "ws", "abandoned-position",
            PositionState(slug, Decimal("1"), runner._now()),
            received_at=runner._now())

        for _ in range(MAX_RETIREMENT_ROUNDS):
            await runner._retire_market(slug, "hard_exit_stuck")

        # Abandonment stops the runner *planning* the market. It must not hide
        # the position from shutdown, which is the last thing standing between
        # an unexitable contract and settlement.
        self.assertIn(slug, runner._shutdown_slugs())
        self.assertFalse(runner._whole_account_safe_to_stop())
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_a_retired_market_accrues_no_score(self):
        runner, _, _ = self.build_runner()
        await runner.preflight()
        slug = runner.market_slug

        # Stand in for a resting order that was scoring at retirement time.
        # Score only populates from working orders, which this fixture has
        # none of, so set it directly rather than assert a vacuous baseline.
        runner.state_for(slug).score_by_side = {"bid": 12.5}
        before = runner._score_seconds

        await runner._retire_market(slug, "hard_exit_attempt_limit_exceeded")
        runner._evaluate_rewards(runner._now(), slug)
        runner._accrue_score(runner._now() + timedelta(seconds=60))

        # Score is a time integral over resting orders. A retired market rests
        # nothing, so continuing to accrue for it would overstate the session.
        self.assertEqual(runner.state_for(slug).score_by_side, {})
        self.assertEqual(runner._score_seconds, before)
        self.assertNotIn(slug, runner._score_seconds_by_market)
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_retiring_every_market_idles_rather_than_spinning(self):
        runner, client, _ = self.build_runner(clock=TickingClock())
        await runner.preflight()
        runner.config = replace(runner.config, max_markets=8)
        slugs = ("market-one", "market-two")
        runner._bind_markets(slugs)

        for slug in slugs:
            await runner._retire_market(slug, "hard_exit_stuck")

        # Every slot barred: the sweep must stay quiet rather than quote or
        # escalate. Two retirements is well inside the systemic cap.
        for slug in slugs:
            runner._evaluate_rewards(runner._now(), slug)
            stop, _delay = await runner._decide_for_market(slug, runner._now())
            self.assertIsNone(stop)
            self.assertEqual(runner.state_for(slug).reward_eligible, frozenset())
        self.assertTrue(runner._discovery_is_free())
        self.assertEqual(client.orders.create_calls, [])
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_summary_reports_a_position_on_a_non_primary_market(self):
        from liquidity_bot.execution_ledger import PositionState

        runner, _, notifier = self.build_runner(clock=TickingClock())
        await runner.preflight()
        runner.config = replace(runner.config, max_markets=8)
        runner._bind_markets(("market-one", "market-two"))
        runner.ledger.apply_position_event(
            "ws", "position-slot-two",
            PositionState("market-two", Decimal("1"), runner._now()),
            received_at=runner._now())

        await runner._send_summary(runner._now())

        payload, _critical = notifier.payloads[-1]
        rendered = "\n".join(
            field["value"] for field in payload["embeds"][0]["fields"])
        # Reporting slot zero only showed no open position here, which is the
        # number an operator reads to decide whether to intervene.
        self.assertIn(f"{'Positions open':<16}{1:>10}", rendered)
        self.assertIn(f"Markets active   {2:>10}", rendered)
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_summary_names_retired_markets(self):
        runner, _, notifier = self.build_runner(clock=TickingClock())
        await runner.preflight()
        slug = runner.market_slug
        await runner._retire_market(slug, "hard_exit_attempt_limit_exceeded")

        await runner._send_summary(runner._now())

        footer = notifier.payloads[-1][0]["embeds"][0]["footer"]["text"]
        # A silently retired slot looks identical to an idle one otherwise.
        self.assertIn("retired 1", footer)
        self.assertIn(slug, footer)
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_restart_recovers_risk_left_on_a_non_primary_market(self):
        runner, _, _ = self.build_runner(clock=TickingClock())
        await runner.preflight()
        runner.config = replace(runner.config, max_markets=8)
        runner._bind_markets(("market-one", "market-two"))
        runner.ledger.apply_order_event(
            "ws", "order-left-on-slot-two",
            OrderState(
                client_order_id="liquidity-resting",
                exchange_order_id="resting-2",
                market_slug="market-two",
                side="ORDER_SIDE_BUY",
                price=Decimal("0.49"),
                original_qty=Decimal("1"),
                filled_qty=Decimal("0"),
                status="OPEN",
                event_at=runner._now()),
            received_at=runner._now())

        scoped = runner._startup_recovery_is_scoped(
            ("shared account has open orders",))

        # Scoping this to slot zero refused recovery for a position on any
        # other slot. Refusal raises CommissioningRefused, which exits 2, and
        # the supervisor never restarts exit 2 -- the bot would stay down for
        # exactly the state restart recovery exists to retire.
        self.assertTrue(scoped)
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_restart_still_refuses_risk_outside_the_active_set(self):
        runner, _, _ = self.build_runner(clock=TickingClock())
        await runner.preflight()
        runner.config = replace(runner.config, max_markets=8)
        runner._bind_markets(("market-one",))
        runner.ledger.apply_order_event(
            "ws", "someone-elses-order",
            OrderState(
                client_order_id="liquidity-resting",
                exchange_order_id="resting-x",
                market_slug="not-ours",
                side="ORDER_SIDE_BUY",
                price=Decimal("0.49"),
                original_qty=Decimal("1"),
                filled_qty=Decimal("0"),
                status="OPEN",
                event_at=runner._now()),
            received_at=runner._now())

        # Widening to the active set must not become "any market": this is a
        # shared account and that order belongs to something else.
        self.assertFalse(runner._startup_recovery_is_scoped(
            ("shared account has open orders",)))
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_an_unrecognised_critical_still_stops_the_session(self):
        runner, _, _ = self.build_runner(clock=TickingClock())
        await runner.preflight()

        # Retirement is opt-in by name. A condition nobody has classified is
        # not assumed to be market-scoped just because it arrived through a
        # per-market plan.
        stop = await runner._handle_strategy_critical(
            "some_condition_added_later", runner.market_slug)

        self.assertEqual(stop, "critical:some_condition_added_later")
        self.assertFalse(runner._is_retired(runner.market_slug))
        await runner.private_stream.close()
        await runner.market_stream.close()

    def test_furthest_resting_price_is_the_conservative_choice(self):
        runner, _, _ = self.build_runner()
        orders = (
            WorkingOrder("a", "bid", .49, Decimal("1")),
            WorkingOrder("b", "bid", .48, Decimal("1")),
            WorkingOrder("c", "offer", .51, Decimal("1")),
            WorkingOrder("d", "offer", .52, Decimal("1")),
        )

        self.assertEqual(runner._furthest_resting_price(orders, "bid"), .48)
        self.assertEqual(runner._furthest_resting_price(orders, "offer"), .52)
        self.assertIsNone(runner._furthest_resting_price((), "bid"))

    async def test_drifted_resting_order_scores_below_the_touch(self):
        runner, _, _ = self.build_runner()
        await runner.preflight()
        period = runner.incentive.periods[0]
        touch = runner.book.best_bid.price

        at_touch = runner._resting_score(
            state=runner.state_for(runner.market_slug),
            side="bid", period=period, touch_price=touch,
            touch_score=1.0, resting_price=touch)
        drifted = runner._resting_score(
            state=runner.state_for(runner.market_slug),
            side="bid", period=period, touch_price=touch,
            touch_score=1.0, resting_price=touch - .01)

        self.assertEqual(at_touch, 1.0)
        # Score decays as discount_factor ** ticks_from_best, so a quote one
        # tick behind the touch must never be credited the touch score.
        self.assertLess(drifted, at_touch)
        self.assertGreaterEqual(drifted, 0.0)
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_resting_score_is_zero_outside_the_reward_cutoff(self):
        runner, _, _ = self.build_runner()
        await runner.preflight()
        period = runner.incentive.periods[0]
        touch = runner.book.best_bid.price

        outside = runner._resting_score(
            state=runner.state_for(runner.market_slug),
            side="bid", period=period, touch_price=touch,
            touch_score=1.0, resting_price=.05)

        self.assertEqual(outside, 0.0)
        await runner.private_stream.close()
        await runner.market_stream.close()

    def _owned_order_in(self, runner, slug, suffix):
        runner.ledger.apply_order_event(
            "ws", "scope-" + suffix,
            OrderState(
                client_order_id="liquidity-" + suffix,
                exchange_order_id="ex-" + suffix,
                market_slug=slug,
                side="ORDER_SIDE_BUY",
                price=Decimal("0.49"),
                original_qty=Decimal("1"),
                filled_qty=Decimal("0"),
                status="OPEN",
                event_at=runner._now()),
            received_at=runner._now())

    async def test_owned_order_in_a_second_authorized_market_passes(self):
        runner, _, _ = self.build_runner(clock=TickingClock())
        await runner.preflight()
        runner.config = replace(runner.config, max_markets=8)
        runner._bind_markets(("market-one", "market-two"))
        self._owned_order_in(runner, "market-two", "two")

        # Several markets may legitimately hold risk once the set authorizes
        # them; this must no longer be an ownership violation.
        runner._assert_runtime_scope()
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_order_outside_the_authorized_set_is_still_terminal(self):
        runner, _, _ = self.build_runner(clock=TickingClock())
        await runner.preflight()
        runner.config = replace(runner.config, max_markets=8)
        runner._bind_markets(("market-one", "market-two"))
        self._owned_order_in(runner, "market-three", "three")

        with self.assertRaises(RuntimeInvariantViolation) as raised:
            runner._assert_runtime_scope()

        self.assertEqual(
            raised.exception.code, "cross_market_working_order")
        self.assertIn("market-three", str(raised.exception))
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_unowned_row_is_terminal_even_inside_the_set(self):
        runner, _, _ = self.build_runner(clock=TickingClock())
        await runner.preflight()
        runner.config = replace(runner.config, max_markets=8)
        runner._bind_markets(("market-one", "market-two"))
        runner.ledger.apply_order_event(
            "ws", "scope-venue",
            OrderState(
                client_order_id="venue:external",
                exchange_order_id="external",
                market_slug="market-two",
                side="ORDER_SIDE_BUY",
                price=Decimal("0.49"),
                original_qty=Decimal("1"),
                filled_qty=Decimal("0"),
                status="OPEN",
                event_at=runner._now()),
            received_at=runner._now())

        # An authorized market is not permission to adopt someone else's
        # order on the shared account.
        with self.assertRaises(RuntimeInvariantViolation) as raised:
            runner._assert_runtime_scope()

        self.assertEqual(raised.exception.code, "unowned_working_order")
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_runtime_unowned_order_is_terminal_without_mutation(self):
        runner, client, _ = self.build_runner(clock=TickingClock())
        await runner.preflight()
        runner.ledger.apply_order_event(
            "ws", "runtime-unowned",
            OrderState(
                client_order_id="venue:external",
                exchange_order_id="external",
                market_slug="market-one",
                side="ORDER_SIDE_BUY",
                price=Decimal("0.50"),
                original_qty=Decimal("1"),
                filled_qty=Decimal("0"),
                status="OPEN",
                event_at=runner._now()),
            received_at=runner._now())

        with self.assertRaises(RuntimeInvariantViolation) as raised:
            runner._frame(runner._now())
        stop = await runner._handle_strategy_plan_failure(raised.exception)

        self.assertEqual(stop, "critical:unowned_working_order")
        self.assertEqual(client.orders.create_calls, [])
        self.assertEqual(client.orders.cancel_calls, [])
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_cross_market_action_is_terminal_without_mutation(self):
        runner, client, _ = self.build_runner(clock=TickingClock())
        await runner.preflight()
        action = PlanAction(
            ActionKind.PLACE_POST_ONLY, "other-market", "bad-market",
            side="bid", price=.49, qty=Decimal("1"))

        with self.assertRaises(RuntimeInvariantViolation) as raised:
            await runner.driver.apply_one(
                [action], market=runner.market, position=Decimal("0"),
                now=runner._now())
        stop = await runner._handle_action_failure(
            raised.exception, runner.market_slug)

        self.assertEqual(stop, "critical:action_market_mismatch")
        self.assertEqual(client.orders.create_calls, [])
        self.assertEqual(client.orders.cancel_calls, [])
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_adapter_action_failure_remains_recoverable(self):
        runner, client, _ = self.build_runner(clock=TickingClock())
        await runner.preflight()

        stop = await runner._handle_action_failure(
            AdapterError("temporary order adapter failure"),
            runner.market_slug)

        self.assertIsNone(stop)
        self.assertEqual(client.orders.create_calls, [])
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_shutdown_does_not_submit_a_fourth_ioc(self):
        runner, client, notifier = self.build_runner(clock=TickingClock())
        await runner.preflight()
        slug = runner.config.market_slug
        self._hold_one_contract(client, slug)
        # Every shutdown IOC expires unfilled, so the position never clears and
        # the loop keeps asking for another exit until its budget is spent.
        self._expire_every_created_order(runner, client, slug)
        runner.config = replace(runner.config, max_shutdown_seconds=120)

        await runner._shutdown("critical:hard_exit_attempt_limit_exceeded")

        self.assertEqual(
            len(client.orders.create_calls),
            runner.config.max_shutdown_exit_attempts)
        self.assertTrue(any(
            "shutdown exit budget" in message and critical
            for message, critical in notifier.messages))
        self.assertFalse(runner._shutdown_proved_flat)
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_runtime_recovery_delay_is_exponential_and_capped(self):
        runner, _, _ = self.build_runner()
        runner._runtime_error_counts["action_driver"] = 1
        self.assertEqual(runner._recovery_delay("action_driver"), 1)
        runner._runtime_error_counts["action_driver"] = 4
        self.assertEqual(runner._recovery_delay("action_driver"), 8)
        runner._runtime_error_counts["action_driver"] = 20
        self.assertEqual(runner._recovery_delay("action_driver"), 30)
        await runner.private_stream.close()
        await runner.market_stream.close()

    def _hold_one_contract(self, client, slug):
        client.portfolio.rows = {slug: {
            "netPosition": "1",
            "netPositionDecimal": "1.0000",
            "updateTime": NOW.isoformat(),
        }}

    def _expire_every_created_order(self, runner, client, slug):
        """Accept each order at the venue and let it expire unfilled."""

        original_create = client.orders.create

        async def create_then_expire(payload):
            response = await original_create(payload)
            moment = runner._now().isoformat()
            client.orders.rows[response["id"]] = {
                "id": response["id"],
                "marketSlug": slug,
                "intent": payload["intent"],
                "state": "ORDER_STATE_EXPIRED",
                "side": "ORDER_SIDE_SELL",
                "price": payload["price"],
                "quantity": payload["quantity"],
                "cumQuantity": "0",
                "updateTime": moment,
                "lastTransactTime": moment,
            }
            return response

        client.orders.create = create_then_expire

    def _fill_first_created_order(self, runner, client, slug):
        """Accept the first shutdown IOC and expose terminal flat venue state."""

        original_create = client.orders.create

        async def create_then_fill(payload):
            response = await original_create(payload)
            moment = runner._now().isoformat()
            client.orders.rows[response["id"]] = {
                "id": response["id"],
                "marketSlug": slug,
                "intent": payload["intent"],
                "state": "ORDER_STATE_FILLED",
                "side": "ORDER_SIDE_SELL",
                "price": payload["price"],
                "quantity": payload["quantity"],
                "cumQuantity": payload["quantity"],
                "updateTime": moment,
                "lastTransactTime": moment,
            }
            client.portfolio.rows = {}
            return response

        client.orders.create = create_then_fill

    async def test_shutdown_flattens_on_its_own_budget_after_runtime_ceiling(self):
        runner, client, _ = self.build_runner(clock=TickingClock())
        await runner.preflight()
        slug = runner.config.market_slug
        # The quoting loop already spent its ceiling before going terminal.
        runner.reconciler._hard_exit_attempts[slug] = (
            runner.reconciler.policy.max_hard_exit_attempts)
        self._hold_one_contract(client, slug)
        self._fill_first_created_order(runner, client, slug)
        runner.config = replace(runner.config, max_shutdown_seconds=30)

        await runner._shutdown("critical:hard_exit_attempt_limit_exceeded")

        self.assertEqual(len(client.orders.create_calls), 1)
        self.assertEqual(
            client.orders.create_calls[0]["tif"],
            "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL")
        self.assertTrue(runner._shutdown_proved_flat)
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_stuck_shutdown_repeats_its_critical_alert(self):
        notifier = FakeNotifier()
        runner, client, _ = self.build_runner(
            clock=TickingClock(), notifier=notifier)
        await runner.preflight()
        slug = runner.config.market_slug
        self._hold_one_contract(client, slug)
        runner.config = replace(
            runner.config, max_shutdown_exit_attempts=0,
            shutdown_alert_interval_seconds=1, max_shutdown_seconds=30)

        await runner._shutdown("critical:hard_exit_insufficient_stressed_depth")

        stuck = [message for message, critical in notifier.messages
                 if critical and "shutdown exit budget" in message]
        self.assertGreater(len(stuck), 1)
        self.assertEqual(client.orders.create_calls, [])
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_nonthrowing_unowned_shutdown_state_repeats_alerts(self):
        notifier = FakeNotifier()
        runner, client, _ = self.build_runner(
            clock=TickingClock(), notifier=notifier)
        await runner.preflight()
        slug = runner.config.market_slug
        client.orders.rows["external"] = {
            "id": "external",
            "marketSlug": slug,
            "intent": "ORDER_INTENT_BUY_LONG",
            "state": "ORDER_STATE_NEW",
            "side": "ORDER_SIDE_BUY",
            "price": {"value": "0.49", "currency": "USD"},
            "quantity": "1",
            "cumQuantity": "0",
            "updateTime": NOW.isoformat(),
        }
        runner.config = replace(
            runner.config, shutdown_alert_interval_seconds=1,
            max_shutdown_seconds=15)

        await runner._shutdown("critical:unowned_working_order")

        repeated = [message for message, critical in notifier.messages
                    if critical and "still cannot prove flat" in message]
        self.assertGreater(len(repeated), 1)
        self.assertEqual(client.orders.create_calls, [])
        self.assertEqual(client.orders.cancel_calls, [])
        self.assertFalse(runner._shutdown_proved_flat)
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_expired_shutdown_window_reports_a_non_flat_exit(self):
        notifier = FakeNotifier()
        runner, client, _ = self.build_runner(
            clock=TickingClock(), notifier=notifier)
        await runner.preflight()
        slug = runner.config.market_slug
        self._hold_one_contract(client, slug)
        runner.config = replace(
            runner.config, max_shutdown_exit_attempts=0, max_shutdown_seconds=10)

        await runner._shutdown("critical:hard_exit_insufficient_stressed_depth")

        self.assertFalse(runner._shutdown_proved_flat)
        self.assertTrue(any(
            critical and "shutdown window expired" in message
            for message, critical in notifier.messages))
        await runner.private_stream.close()
        await runner.market_stream.close()

    def test_cli_returns_exit_three_for_unproven_flat_state(self):
        result = CommissioningResult(
            started_at=NOW,
            cutoff_at=None,
            stopped_at=NOW + timedelta(minutes=15),
            stop_reason="critical:hard_exit_attempt_limit_exceeded",
            flat=False,
            orders_sent_to_polymarket=6,
        )
        argv = [
            "live_commissioning",
            "--market-slug", "market-one",
            "--arm-live",
            "--accept-one-contract-risk",
            "--exclusive-api-key",
            "--maker-lab-stopped",
        ]

        with patch("sys.argv", argv), patch(
                "liquidity_bot.live_commissioning._run",
                new=AsyncMock(return_value=result)), patch("builtins.print"):
            self.assertEqual(main(), 3)

    def _exit_code_for(self, stop_reason, flat=True):
        result = CommissioningResult(
            started_at=NOW,
            cutoff_at=None,
            stopped_at=NOW + timedelta(minutes=5),
            stop_reason=stop_reason,
            flat=flat,
            orders_sent_to_polymarket=2,
        )
        argv = [
            "live_commissioning",
            "--market-slug", "market-one",
            "--arm-live",
            "--accept-one-contract-risk",
            "--exclusive-api-key",
            "--maker-lab-stopped",
        ]
        with patch("sys.argv", argv), patch(
                "liquidity_bot.live_commissioning._run",
                new=AsyncMock(return_value=result)), patch("builtins.print"):
            return main()

    def test_flat_runner_failure_is_restartable_but_criticals_are_not(self):
        # A transient defect that still proved the account flat must not leave
        # the bot down: a fresh process re-runs the whole flat/ownership
        # preflight before any order. Loss stops and named criticals stay
        # terminal so the supervisor never restarts into a known-bad state.
        self.assertEqual(
            self._exit_code_for("runner_failure:ValueError"), 5)
        self.assertEqual(
            self._exit_code_for("critical:unowned_working_order"), 4)
        self.assertEqual(
            self._exit_code_for("risk_stop:marked account equity"), 0)
        self.assertEqual(
            self._exit_code_for("EMERGENCY_EQUITY_HARD_STOP"), 0)
        # A recovery run exists only to retire what a previous process left
        # behind. Once it proves flat the work is done and a normal session
        # should follow at once; exiting 0 left the bot down after every
        # recovery until a human restarted it.
        self.assertEqual(
            self._exit_code_for("startup_recovery_only"), 5)
        # Never restart while inventory state is unproven, whatever the reason.
        self.assertEqual(
            self._exit_code_for("runner_failure:ValueError", flat=False), 3)
        self.assertEqual(
            self._exit_code_for("startup_recovery_only", flat=False), 3)

    def test_cli_accepts_automatic_reward_selection(self):
        result = CommissioningResult(
            started_at=NOW,
            cutoff_at=None,
            stopped_at=NOW,
            stop_reason="operator_requested",
            flat=True,
            orders_sent_to_polymarket=0,
        )
        argv = [
            "live_commissioning",
            "--auto-select-rewards",
            "--arm-live",
            "--accept-one-contract-risk",
            "--exclusive-api-key",
            "--maker-lab-stopped",
        ]
        mocked = AsyncMock(return_value=result)

        with patch("sys.argv", argv), patch(
                "liquidity_bot.live_commissioning._run", new=mocked), patch(
                    "builtins.print"):
            self.assertEqual(main(), 0)

        args = mocked.await_args.args[0]
        self.assertTrue(args.auto_select_rewards)
        self.assertIsNone(args.market_slug)

    async def test_ambiguous_place_is_adopted_instead_of_unowned(self):
        runner, client, _ = self.build_runner(clock=TickingClock())
        await runner.preflight()
        slug = runner.config.market_slug

        async def never_answers(_payload):
            raise AdapterError("create response never arrived")

        client.orders.create = never_answers
        with self.assertRaises(OrderOutcomeUncertain):
            await runner.executor.place(
                OrderRequest(slug, "bid", Decimal("0.49"), Decimal("1")),
                runner.market, now=runner._now())

        # The venue did accept it. A private order update now carries an
        # exchange id the ledger has never seen bound to its intent.
        raw_order = {
            "id": "fake-ambiguous",
            "marketSlug": slug,
            "intent": "ORDER_INTENT_BUY_LONG",
            "state": "ORDER_STATE_NEW",
            "side": "ORDER_SIDE_BUY",
            "price": {"value": "0.49", "currency": "USD"},
            "quantity": "1",
            "cumQuantity": "0",
            "updateTime": NOW.isoformat(),
        }
        await runner._observe_private(PrivateEvent(
            PrivateEventKind.ORDER_UPDATE,
            {"execution": {
                "id": "private-new-ambiguous",
                "type": "EXECUTION_TYPE_NEW",
                "order": raw_order,
            }},
            "private-order-update"), runner._now())

        working = runner.ledger.working_orders(slug)
        self.assertEqual(len(working), 1)
        self.assertFalse(
            str(working[0]["client_order_id"]).startswith("venue:"))
        self.assertEqual(runner.ledger.unresolved_intents(), [])
        # The ambiguous outcome must stay recoverable rather than tripping the
        # unowned-order invariant against the bot's own order.
        runner._assert_runtime_scope()
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_cli_refuses_before_loading_secrets_when_not_armed(self):
        args = Namespace(
            market_slug="market-one",
            accept_one_contract_risk=True,
            exclusive_api_key=True,
            maker_lab_stopped=True,
            arm_live=False,
            lock_file=Path("unused.lock"),
            database=Path("unused.db"),
        )
        with patch("liquidity_bot.live_commissioning.load_runtime_secrets") as load:
            with self.assertRaisesRegex(CommissioningRefused, "arming"):
                await _run(args)
        load.assert_not_called()


if __name__ == "__main__":
    unittest.main()
