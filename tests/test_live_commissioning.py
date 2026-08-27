from __future__ import annotations

import asyncio
import io
import sys
import tempfile
import unittest
from argparse import Namespace
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import AsyncMock, patch

from liquidity_bot.commissioning import (
    AUTO_REWARD_MARKET,
    CommissioningConfig,
    CommissioningRefused,
    CommissioningUnavailable,
)
from liquidity_bot.discord_summary import GRAY, RED
from liquidity_bot.errors import AdapterError, RuntimeInvariantViolation
from liquidity_bot.execution_ledger import Execution, ExecutionLedger, OrderState
from liquidity_bot.live_commissioning import (
    MAX_BOUND_STREAM_RECOVERY_ATTEMPTS,
    MAX_MARKET_STREAM_RECOVERY_ATTEMPTS,
    MAX_PRIVATE_STREAM_RECOVERY_ATTEMPTS,
    MAINTENANCE_STABLE_READS,
    MAX_RECOVERY_ATTEMPTS,
    MAX_RETIREMENT_ROUNDS,
    RETIREMENT_COOLDOWN_SECONDS,
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
from liquidity_bot.market_state import MarketState
from liquidity_bot.models import (
    BookLevel, BookSnapshot, MarketIncentive, MarketMetadata, TradePrint,
)
from liquidity_bot.notifications import NotificationResult
from liquidity_bot.private_stream import PrivateEvent, PrivateEventKind
from liquidity_bot.reconciler import ActionKind, PlanAction, WorkingOrder
from liquidity_bot.rewards import IncentivePeriod, RewardProjection
from liquidity_bot.reward_calendar import risk_day


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
        self.started: set[str] = set()

    async def retrieve_by_slug(self, slug):
        start = (NOW - timedelta(hours=2)) if slug in self.started else GAME_START
        return {"market": {
            "slug": slug,
            "active": True,
            "closed": False,
            "status": "MARKET_STATE_OPEN",
            "gameStartTime": start.isoformat(),
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
        self.trades = []
        self.cash_activities = []

    async def positions(self, params):
        return {"positions": self.rows, "eof": True}

    async def activities(self, params):
        if params.get("types"):
            allowed = set(params["types"])
            return {
                "activities": [
                    row for row in self.cash_activities
                    if row.get("type") in allowed],
                "eof": True,
            }
        return {"activities": [{"type": "ACTIVITY_TYPE_TRADE", "trade": t}
                               for t in self.trades], "eof": True}


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


class FailingMarketStream(FakeMarketStream):
    async def start(self, slugs):
        self.started_with = slugs
        raise AdapterError("simulated market reconnect failure")


class FakeNotifier:
    def __init__(self, *, delivery_succeeds=True):
        self.messages = []
        self.payloads = []
        self.channels = []
        self.delivery_succeeds = delivery_succeeds

    async def send_async(self, content, *, critical=False, channel=None):
        self.messages.append((content, critical))
        self.channels.append(channel)
        return NotificationResult(
            self.delivery_succeeds,
            "sent" if self.delivery_succeeds else "delivery_failed")

    async def send_payload_async(self, payload, *, critical=False,
                                 channel=None):
        self.payloads.append((payload, critical))
        self.channels.append(channel)
        # Flatten the embed into `messages` too, so assertions about what was
        # announced do not have to care whether it went as text or as an embed.
        embeds = payload.get("embeds") if isinstance(payload, dict) else None
        if embeds:
            embed = embeds[0]
            flat = " ".join(str(part) for part in (
                embed.get("title", ""), embed.get("description", ""),
                " ".join(f"{f.get('name')} {f.get('value')}"
                         for f in embed.get("fields", []))))
            self.messages.append((flat, critical))
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


def multi_selection(*slugs: str) -> RewardMarketSelection:
    """A scan in which several markets are viable, ranked in the given order."""

    parts = [fake_selection(slug) for slug in slugs]
    opportunities = tuple(part.selected for part in parts)
    return replace(
        parts[0], selected=opportunities[0], opportunities=opportunities)


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
        ledger=None,
        client=None,
    ):
        # `ledger` and `client` let a test build a SECOND runner over the state
        # a previous one left behind -- which is what a restart actually is,
        # and the only way to cover what preflight does with a ledger it did
        # not create.
        if ledger is None:
            root = Path(tempfile.mkdtemp(prefix="liquidity-commissioning-"))
            ledger = ExecutionLedger(root / "ledger.db")
            self.addCleanup(ledger.close)
        if client is None:
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

    async def test_building_observation_records_actual_queue_separately_from_model(self):
        runner, _, _ = self.build_runner()
        state = runner.state_for("market-one")
        state.market = MarketMetadata(
            slug="market-one", game_start=GAME_START, tick_size=.01,
            minimum_trade_qty=1, active=True, closed=False,
            status="MARKET_STATUS_OPEN",
            sports_market_type_v2="SPORTS_MARKET_TYPE_MONEYLINE")
        period = IncentivePeriod(
            "queue-study", "day_of", "active", 35, .5, 500)
        state.incentive = MarketIncentive("market-one", (period,))
        prior = BookSnapshot(
            "market-one", (BookLevel(.49, 90),), (BookLevel(.51, 90),))
        state.observe_book_touch(
            prior, observed_at=NOW - timedelta(seconds=20))
        state.book = BookSnapshot(
            "market-one",
            (BookLevel(.50, 100), BookLevel(.49, 200), BookLevel(.48, 500)),
            (BookLevel(.50, 120), BookLevel(.51, 200), BookLevel(.52, 500)),
        )
        state.book_observed_at = NOW - timedelta(seconds=2)
        state.observe_book_touch(
            state.book, observed_at=state.book_observed_at)

        runner._evaluate_rewards_inner(NOW, "market-one")
        with patch.object(
                runner.ledger, "position",
                side_effect=AssertionError("telemetry added a position read")):
            observation = runner._quote_observation_for_action(
                state,
                PlanAction(
                    ActionKind.PLACE_POST_ONLY, "market-one",
                    "reward_positive_candidate", side="bid", price=.49,
                    qty=runner.config.quote_size),
                NOW,
                Decimal("0"),
            )

        self.assertIsNotNone(observation)
        # Current production model uses best-touch size only.
        self.assertEqual(observation.model_queue_ahead, 100)
        # At a backed-off 0.49 quote, both the 0.50 and existing 0.49 levels
        # are ahead under price-time priority.
        self.assertEqual(observation.observed_queue_ahead, 300)
        self.assertEqual(observation.depth_better_than_quote, 100)
        self.assertEqual(observation.depth_at_quote, 200)
        self.assertEqual(observation.target_size, 500)
        self.assertEqual(observation.touch_changes_120s, 1)
        self.assertIsNone(observation.trade_volume_30s)

    async def test_successful_live_building_action_persists_exact_observation(self):
        runner, _, _ = self.build_runner()
        await runner.preflight()
        runner._evaluate_rewards(NOW, "market-one")

        outcome, delay = await runner._decide_for_market("market-one", NOW)

        self.assertIsNone(outcome)
        self.assertEqual(delay, 0)
        observation = runner.ledger.query(
            "SELECT * FROM execution_quote_observations")
        self.assertEqual(len(observation), 1)
        intent = runner.ledger.query(
            "SELECT intent_id,client_order_id,quote_observation_required "
            "FROM execution_intents WHERE operation='PLACE'")[0]
        self.assertEqual(observation[0]["intent_id"], intent["intent_id"])
        self.assertEqual(
            observation[0]["client_order_id"], intent["client_order_id"])
        self.assertEqual(intent["quote_observation_required"], 1)
        self.assertIsNone(observation[0]["trade_volume_30s"])
        queue_rows = runner.ledger.query(
            "SELECT client_order_id,status,coverage_gaps "
            "FROM execution_queue_measurements")
        self.assertEqual(len(queue_rows), 1)
        self.assertEqual(queue_rows[0]["client_order_id"], intent["client_order_id"])
        self.assertEqual(queue_rows[0]["status"], "ACTIVE")
        # This unit path does not start the isolated trade collector. The
        # order still places, but the sample is visibly invalid rather than
        # silently pretending it had public-trade coverage.
        self.assertEqual(queue_rows[0]["coverage_gaps"], 1)
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_missing_telemetry_never_suppresses_a_valid_building_action(self):
        runner, client, _ = self.build_runner()
        await runner.preflight()
        runner._evaluate_rewards(NOW, "market-one")
        # Simulate one reporting field being unavailable after the unchanged
        # plan was authorized.  The order still follows that plan, while the
        # required marker makes the telemetry gap visible to the report.
        runner.state_for("market-one").reward_score_by_side.clear()

        outcome, delay = await runner._decide_for_market("market-one", NOW)

        self.assertIsNone(outcome)
        self.assertEqual(delay, 0)
        self.assertEqual(len(client.orders.create_calls), 1)
        intent = runner.ledger.query(
            "SELECT quote_observation_required FROM execution_intents "
            "WHERE operation='PLACE'")[0]
        self.assertEqual(intent["quote_observation_required"], 1)
        self.assertEqual(runner.ledger.query(
            "SELECT * FROM execution_quote_observations"), [])
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_queue_measurement_failure_cannot_suppress_or_duplicate_an_order(self):
        runner, client, _ = self.build_runner()
        await runner.preflight()
        runner._evaluate_rewards(NOW, "market-one")

        class BrokenMeasurement:
            def sync_orders(self, **_kwargs):
                raise RuntimeError("optional queue telemetry failed")

            def on_book(self, *_args, **_kwargs):
                raise RuntimeError("optional queue telemetry failed")

        runner._queue_measurement = BrokenMeasurement()
        outcome, delay = await runner._decide_for_market("market-one", NOW)

        self.assertIsNone(outcome)
        self.assertEqual(delay, 0)
        self.assertEqual(len(client.orders.create_calls), 1)
        self.assertEqual(len(runner.ledger.query(
            "SELECT * FROM execution_intents WHERE operation='PLACE'")), 1)
        self.assertEqual(runner.config.max_markets, 20)
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_queue_trade_collector_is_isolated_and_records_its_own_health(self):
        runner, client, _ = self.build_runner()

        class OneTradeStream:
            def __init__(self):
                self.started = None
                self.closed = False
                self.sent = False
                self.timeouts = 0
                self.dropped_trades = 0
                self.decode_failures = 0

            async def start(self, slugs):
                self.started = tuple(slugs)

            async def next_trade(self, *, timeout=None):
                if not self.sent:
                    self.sent = True
                    return TradePrint(
                        "market-one", .49, 2, "ORDER_SIDE_BUY", NOW,
                        request_id="public-one")
                self.timeouts += 1
                if self.timeouts <= 3:
                    await asyncio.sleep(0)
                    raise asyncio.TimeoutError
                await asyncio.Future()

            async def close(self):
                self.closed = True

        stream = OneTradeStream()
        runner.trade_stream_factory = lambda _client: stream
        health_updates = 0
        original_update = runner._queue_measurement.update_stream_session

        def counted_update(*args, **kwargs):
            nonlocal health_updates
            health_updates += 1
            return original_update(*args, **kwargs)

        runner._queue_measurement.update_stream_session = counted_update
        task = asyncio.create_task(runner._queue_measurement_loop())
        for _ in range(12):
            await asyncio.sleep(0)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

        self.assertEqual(stream.started, ("market-one",))
        self.assertTrue(stream.closed)
        sessions = runner.ledger.query(
            "SELECT status,trades_received,scope_size "
            "FROM execution_queue_stream_sessions")
        self.assertEqual([tuple(row) for row in sessions], [("STOPPED", 1, 1)])
        self.assertEqual(health_updates, 1)
        # The collector has no path to either the trading gateway or REST.
        self.assertEqual(client.orders.create_calls, [])
        self.assertEqual(runner.gateway.limiter.calls, 0)

    async def test_decision_sweep_skips_only_the_market_with_an_unresolved_intent(self):
        runner, _, _ = self.build_runner()
        await runner.preflight()
        runner._bind_markets(("market-one", "market-two"))
        runner.ledger.prepare_intent(
            "pending-one", "PLACE", "market-one", "client-one", {},
            created_at=NOW)

        with patch.object(
                runner, "_refresh_equity_for_decision",
                new=AsyncMock()) as refresh, patch.object(
                    runner, "_decide_for_market", new=AsyncMock(
                        return_value=(None, 0.0))) as decide:
            outcome = await runner._decision_sweep()

        self.assertIsNone(outcome)
        self.assertEqual(
            [call.args[0] for call in decide.await_args_list],
            ["market-two"],
        )
        self.assertEqual(refresh.await_count, 1)
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_decision_backoff_is_per_market_not_a_global_sleep(self):
        runner, _, _ = self.build_runner()
        await runner.preflight()
        runner._bind_markets(("market-one", "market-two"))

        with patch.object(
                runner, "_refresh_equity_for_decision",
                new=AsyncMock()), patch.object(
                    runner, "_decide_for_market", new=AsyncMock(
                        side_effect=[(None, 30.0), (None, 0.0),
                                     (None, 0.0)])) as decide:
            await runner._decision_sweep()
            await runner._decision_sweep()

        self.assertEqual(
            [call.args[0] for call in decide.await_args_list],
            ["market-one", "market-two", "market-two"],
        )
        self.assertEqual(
            runner._next_decision_at["market-one"],
            NOW + timedelta(seconds=30),
        )
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_operator_stop_interrupts_an_in_progress_decision_sweep(self):
        """A delivered SIGTERM must bar the next ordinary venue mutation.

        The outer run loop checks the stop flag, but a twenty-market sweep can
        already be in progress when the signal callback sets it.  The live
        2026-08-25 stop placed several fresh orders before that sweep returned.
        Shutdown owns cancellation and flattening after this boundary.
        """

        runner, client, _ = self.build_runner()
        await runner.preflight()
        runner._bind_markets(("market-one", "market-two"))
        planned: list[str] = []

        async def decide(slug, now):
            planned.append(slug)
            runner.request_stop("operator_sigterm")
            return None, 0.0

        with patch.object(runner, "_decide_for_market", side_effect=decide), \
                patch.object(
                    runner, "_refresh_equity_for_decision",
                    new=AsyncMock()):
            outcome = await runner._decision_sweep()

        self.assertEqual(outcome, "operator_sigterm")
        self.assertEqual(planned, ["market-one"])
        self.assertEqual(client.orders.create_calls, [])
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_all_twenty_slots_are_planned_and_equity_refreshes_mid_sweep(self):
        moment = [NOW]
        runner, _, _ = self.build_runner(clock=lambda: moment[0])
        await runner.preflight()
        slugs = tuple(f"market-{index}" for index in range(20))
        runner._bind_markets(slugs)
        planned: list[str] = []
        refreshes: list[datetime] = []

        async def decide(slug, now):
            planned.append(slug)
            moment[0] += timedelta(seconds=1)
            return None, 0.0

        async def refresh(now):
            refreshes.append(now)
            runner.risk_guard.observe_equity(
                runner.balance.marked_equity, observed_at=moment[0])

        with patch.object(runner, "_decide_for_market", side_effect=decide), \
                patch.object(runner, "_refresh_balance", side_effect=refresh):
            outcome = await runner._decision_sweep()

        self.assertIsNone(outcome)
        self.assertEqual(planned, list(slugs))
        self.assertEqual(refreshes, [NOW + timedelta(seconds=10)])
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_all_twenty_slots_refresh_execution_state_mid_sweep(self):
        moment = [NOW]
        runner, _, _ = self.build_runner(clock=lambda: moment[0])
        await runner.preflight()
        slugs = tuple(f"market-{index}" for index in range(20))
        runner._bind_markets(slugs)
        planned: list[str] = []
        reconciles: list[datetime] = []
        reconcile_scopes: list[frozenset[str] | None] = []

        async def decide(slug, now):
            planned.append(slug)
            moment[0] += timedelta(seconds=2)
            return None, 0.0

        async def reconcile(now, *, strict_order_markets=None):
            reconciles.append(now)
            reconcile_scopes.append(strict_order_markets)
            runner.gate.on_rest_reconciled(moment[0])

        with patch.object(runner, "_decide_for_market", side_effect=decide), \
                patch.object(runner, "_rest_reconcile", side_effect=reconcile), \
                patch.object(
                    runner, "_refresh_equity_for_decision",
                    new=AsyncMock()):
            outcome = await runner._decision_sweep()

        self.assertIsNone(outcome)
        self.assertEqual(planned, list(slugs))
        self.assertEqual(reconciles, [NOW + timedelta(seconds=26)])
        self.assertEqual(reconcile_scopes, [frozenset()])
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_execution_state_refresh_uses_the_pre_expiry_boundary(self):
        runner, _, _ = self.build_runner()
        await runner.preflight()

        with patch.object(
                runner, "_rest_reconcile", new=AsyncMock()) as reconcile:
            await runner._refresh_execution_state_for_decision(
                NOW + timedelta(seconds=24.999))
            reconcile.assert_not_awaited()
            await runner._refresh_execution_state_for_decision(
                NOW + timedelta(seconds=25))
            reconcile.assert_awaited_once()
            self.assertEqual(
                reconcile.await_args.kwargs["strict_order_markets"],
                frozenset(),
            )

        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_failed_state_refresh_leaves_the_private_gate_fail_closed(self):
        runner, _, _ = self.build_runner()
        await runner.preflight()
        stale = NOW + timedelta(seconds=31)

        with patch.object(
                runner, "_rest_reconcile", new=AsyncMock(
                    side_effect=AdapterError("REST unavailable"))):
            with self.assertRaisesRegex(AdapterError, "REST unavailable"):
                await runner._refresh_execution_state_for_decision(stale)

        self.assertFalse(runner.gate.session_ready(runner.ledger, now=stale))
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_failed_state_refresh_is_attempted_once_per_sweep(self):
        moment = [NOW]
        runner, _, _ = self.build_runner(clock=lambda: moment[0])
        await runner.preflight()
        moment[0] += timedelta(seconds=31)
        slugs = tuple(f"market-{index}" for index in range(20))
        runner._bind_markets(slugs)

        with patch.object(
                runner, "_rest_reconcile", new=AsyncMock(
                    side_effect=AdapterError("REST unavailable"))) as reconcile, \
                patch.object(
                    runner, "_decide_for_market", new=AsyncMock(
                        return_value=(None, 0.0))) as decide, \
                patch.object(
                    runner, "_refresh_equity_for_decision",
                    new=AsyncMock()):
            outcome = await runner._decision_sweep()

        self.assertIsNone(outcome)
        self.assertEqual(reconcile.await_count, 1)
        self.assertEqual(decide.await_count, 20)
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_rest_state_freshness_is_stamped_after_slow_response(self):
        moment = [NOW]
        runner, _, _ = self.build_runner(clock=lambda: moment[0])
        await runner.preflight()
        original = runner.account.fetch_positions

        async def delayed_positions(*, observed_at):
            result = await original(observed_at=observed_at)
            moment[0] += timedelta(seconds=7)
            return result

        runner.account.fetch_positions = delayed_positions
        await runner._rest_reconcile(NOW)

        self.assertEqual(
            runner.gate.last_reconcile_at,
            NOW + timedelta(seconds=7),
        )
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_balance_freshness_is_stamped_when_the_response_arrives(self):
        moment = [NOW]
        runner, _, _ = self.build_runner(clock=lambda: moment[0])
        await runner.preflight()
        original = runner.account.fetch_balance

        async def delayed_balance(*, observed_at):
            result = await original(observed_at=observed_at)
            moment[0] += timedelta(seconds=7)
            return result

        runner.account.fetch_balance = delayed_balance
        await runner._refresh_balance(NOW)

        self.assertEqual(
            runner.risk_guard.equity_observed_at,
            NOW + timedelta(seconds=7),
        )
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_stream_book_keeps_socket_receipt_time_when_dequeued_late(self):
        runner, _, _ = self.build_runner(clock=lambda: NOW + timedelta(seconds=20))
        runner._bind_markets(("market-one",))
        received = NOW + timedelta(seconds=2)

        runner._accept_book(BookSnapshot(
            "market-one",
            (BookLevel(.49, 10),),
            (BookLevel(.51, 10),),
            received_at=received,
        ))

        self.assertEqual(runner.state_for("market-one").book_observed_at, received)

    async def test_unresolved_create_prevents_shutdown_flat_proof(self):
        runner, _, _ = self.build_runner()
        runner.ledger.prepare_intent(
            "ambiguous-create", "PLACE", "market-one", "client-one", {},
            created_at=NOW)

        self.assertFalse(runner._venue_proves_flat())

    async def test_empty_plan_is_not_reported_as_recent_order_activity(self):
        runner, _, _ = self.build_runner()
        await runner.preflight()
        runner._evaluate_rewards(NOW)

        with patch.object(runner.reconciler, "plan", return_value=[]), \
                patch.object(runner.driver, "apply_one", new=AsyncMock()):
            outcome, delay = await runner._decide_for_market("market-one", NOW)

        self.assertIsNone(outcome)
        self.assertEqual(delay, 0.0)
        self.assertNotIn("market-one", runner._last_action_at)
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_private_event_keeps_socket_receipt_time_when_processed_late(self):
        runner, _, _ = self.build_runner()
        event = PrivateEvent(
            PrivateEventKind.HEARTBEAT, {},
            received_at=NOW + timedelta(seconds=2),
        )
        runner.gate.observe = lambda observed, *, received_at: self.assertEqual(
            received_at, NOW + timedelta(seconds=2))
        runner.projector.apply = lambda observed, *, received_at: self.assertEqual(
            received_at, NOW + timedelta(seconds=2))

        await runner._observe_private(event, NOW + timedelta(seconds=20))

    async def test_terminal_cancel_private_event_prioritizes_only_its_market(self):
        runner, _, _ = self.build_runner()
        await runner.preflight()
        runner._bind_markets(("market-one", "market-two"))
        runner.ledger.apply_order_event(
            "ws", "priority-open",
            OrderState(
                client_order_id="liquidity-priority",
                exchange_order_id="exchange-priority",
                market_slug="market-two",
                side="ORDER_SIDE_BUY",
                price=Decimal("0.49"),
                original_qty=Decimal("1"),
                filled_qty=Decimal("0"),
                status="OPEN",
                event_at=NOW,
            ),
            received_at=NOW,
        )
        runner.ledger.prepare_intent(
            "cancel-priority", "CANCEL", "market-two",
            "liquidity-priority", {"orderId": "exchange-priority"},
            created_at=NOW,
        )
        runner.ledger.transition_intent(
            "cancel-priority", "SUBMITTING", updated_at=NOW)
        runner.ledger.transition_intent(
            "cancel-priority", "ACKNOWLEDGED", updated_at=NOW,
            exchange_order_id="exchange-priority")

        await runner._observe_private(PrivateEvent(
            PrivateEventKind.ORDER_UPDATE,
            {"execution": {
                "id": "private-cancel-priority",
                "type": "EXECUTION_TYPE_CANCEL",
                "order": {
                    "id": "exchange-priority",
                    "marketSlug": "market-two",
                    "intent": "ORDER_INTENT_BUY_LONG",
                    "side": "ORDER_SIDE_BUY",
                    "state": "ORDER_STATE_CANCELED",
                    "price": {"value": "0.49", "currency": "USD"},
                    "quantity": "1",
                    "cumQuantity": "0",
                    "updateTime": (NOW + timedelta(seconds=1)).isoformat(),
                },
            }},
            "priority-cancel-update",
        ), NOW + timedelta(seconds=1))

        self.assertEqual(runner._take_priority_replans(), ("market-two",))
        self.assertEqual(runner._take_priority_replans(), ())
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_terminal_cancel_rest_proof_also_prioritizes_its_market(self):
        runner, client, _ = self.build_runner()
        await runner.preflight()
        runner.ledger.apply_order_event(
            "ws", "rest-priority-open",
            OrderState(
                client_order_id="liquidity-rest-priority",
                exchange_order_id="exchange-rest-priority",
                market_slug="market-one",
                side="ORDER_SIDE_BUY",
                price=Decimal("0.49"),
                original_qty=Decimal("1"),
                filled_qty=Decimal("0"),
                status="OPEN",
                event_at=NOW,
            ),
            received_at=NOW,
        )
        runner.ledger.prepare_intent(
            "cancel-rest-priority", "CANCEL", "market-one",
            "liquidity-rest-priority",
            {"orderId": "exchange-rest-priority"}, created_at=NOW)
        runner.ledger.transition_intent(
            "cancel-rest-priority", "SUBMITTING", updated_at=NOW)
        runner.ledger.transition_intent(
            "cancel-rest-priority", "ACKNOWLEDGED", updated_at=NOW,
            exchange_order_id="exchange-rest-priority")
        client.orders.rows["exchange-rest-priority"] = {
            "id": "exchange-rest-priority",
            "marketSlug": "market-one",
            "side": "ORDER_SIDE_BUY",
            "intent": "ORDER_INTENT_BUY_LONG",
            "price": {"value": "0.49", "currency": "USD"},
            "quantity": "1",
            "cumQuantity": "0",
            "state": "ORDER_STATE_CANCELED",
            "createTime": NOW.isoformat(),
            "lastTransactTime": (NOW + timedelta(seconds=1)).isoformat(),
        }

        await runner._rest_reconcile(NOW + timedelta(seconds=2))

        self.assertEqual(runner._take_priority_replans(), ("market-one",))
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_priority_decision_sweep_does_not_replan_other_slots(self):
        runner, _, _ = self.build_runner()
        await runner.preflight()
        runner._bind_markets(("market-one", "market-two", "market-three"))

        with patch.object(
                runner, "_refresh_equity_for_decision",
                new=AsyncMock()), patch.object(
                    runner, "_decide_for_market", new=AsyncMock(
                        return_value=(None, 0.0))) as decide:
            outcome = await runner._decision_sweep(("market-two",))

        self.assertIsNone(outcome)
        decide.assert_awaited_once()
        self.assertEqual(decide.await_args.args[0], "market-two")
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_rebind_discards_priority_for_released_market(self):
        runner, _, _ = self.build_runner()
        runner._bind_markets(("market-one", "market-two"))
        runner._priority_replan_slugs.update(("market-one", "market-two"))

        runner._bind_markets(("market-two",))

        self.assertEqual(runner._take_priority_replans(), ("market-two",))

    async def test_events_arriving_during_rest_service_are_applied_before_decisions(self):
        runner, _, _ = self.build_runner(clock=lambda: NOW + timedelta(seconds=20))
        runner._bind_markets(("market-one",))
        book = BookSnapshot(
            "market-one",
            (BookLevel(.49, 10),),
            (BookLevel(.51, 10),),
            received_at=NOW + timedelta(seconds=2),
        )
        private = PrivateEvent(
            PrivateEventKind.HEARTBEAT, {},
            received_at=NOW + timedelta(seconds=3),
        )

        async def completed(value):
            return value

        market_task = asyncio.create_task(completed(book))
        private_task = asyncio.create_task(completed(private))
        await asyncio.sleep(0)
        market_task, private_task, stop = (
            await runner._consume_ready_stream_tasks(
                market_task, private_task))

        self.assertIsNone(stop)
        self.assertEqual(
            runner.state_for("market-one").book_observed_at,
            NOW + timedelta(seconds=2),
        )
        self.assertEqual(
            runner.gate.last_message_at,
            NOW + timedelta(seconds=3),
        )
        market_task.cancel()
        private_task.cancel()
        await asyncio.gather(
            market_task, private_task, return_exceptions=True)

    async def test_preflight_then_first_fake_order_is_exactly_the_quote_size(self):
        runner, client, _ = self.build_runner()
        await runner.preflight()
        self.assertEqual(client.orders.create_calls, [])
        self.assertTrue(runner.gate.ready(runner.ledger, now=NOW))
        self.assertEqual(runner.private_stream.started_with, ["market-one"])

        runner._evaluate_rewards(NOW)
        actions = runner.reconciler.plan(runner._frame(NOW))
        self.assertEqual({action.side for action in actions}, {"bid", "offer"})
        self.assertTrue(all(
            action.qty == runner.config.quote_size for action in actions))
        await runner.driver.apply_one(
            actions, market=runner.market, position=Decimal("0"), now=NOW)

        self.assertEqual(len(client.orders.create_calls), 1)
        payload = client.orders.create_calls[0]
        # Asserted against the config, not a literal, so the payload can
        # never silently disagree with the authorized envelope -- which is the
        # thing this test exists to prove.
        self.assertEqual(payload["quantity"], int(runner.config.quote_size))
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
        # 200 cleared the gate once the cost margin went 2 -> 1. The invariant
        # under test is "economically blocked means no accrual", so the fixture
        # has to be genuinely blocked; 100 still is.
        client.reward_pool = 100
        # Armed explicitly: this test is about the gate itself, which is
        # advisory by default now (maker-lab's entry rule).
        runner.config = replace(runner.config, enforce_reward_cost_gate=True)
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

    async def test_startup_binds_the_whole_authorized_set_not_just_the_best(self):
        # Binding one market meant the session quoted a single market until the
        # first rotation 900 seconds later. Authorized uptime is the measured
        # constraint, so a quarter hour on one slot is the expensive part.
        slugs = tuple(f"market-{i}" for i in range(8))
        selector = FakeRewardSelector(multi_selection(*slugs))
        runner, _, _ = self.build_runner(
            auto=True, reward_selector=selector, clock=TickingClock())

        await runner.preflight()

        self.assertEqual(runner.active_slugs, slugs)
        # And the streams cover every bound market, not just the primary.
        self.assertEqual(tuple(runner.private_stream.started_with), slugs)
        self.assertEqual(tuple(runner.market_stream.started_with), slugs)
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_startup_never_binds_more_than_the_authorized_maximum(self):
        slugs = tuple(f"market-{i}" for i in range(40))
        selector = FakeRewardSelector(multi_selection(*slugs))
        runner, _, _ = self.build_runner(
            auto=True, reward_selector=selector, clock=TickingClock())

        await runner.preflight()

        # Ten private subscriptions is the measured per-connection ceiling,
        # and eight markets is exactly what fits alongside orders and balance.
        # Twenty is three connections; more slugs on offer must not widen it.
        self.assertEqual(len(runner.active_slugs), runner.config.max_markets)
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_waiting_for_a_viable_market_does_not_page_as_critical(self):
        # Every scan used to fire a critical Discord alert reading "new risk is
        # disabled until reconnect and reconciliation pass", which describes a
        # broken connection. Nothing is broken: the gate rejected every book,
        # which the backtest says is the common case. Alerts that cry wolf on
        # the normal idle state are how a real one gets ignored.
        selector = FakeRewardSelector(
            no_viable_selection(),
            no_viable_selection(),
            no_viable_selection(),
            fake_selection("market-one"),
        )
        runner, _, notifier = self.build_runner(
            auto=True, reward_selector=selector, clock=TickingClock())

        await runner.preflight()

        waiting = [message for message, critical in notifier.messages
                   if "clears the reward gate" in message]
        self.assertEqual(len(waiting), 1, "should say it once, not per scan")
        self.assertFalse(any(
            critical for message, critical in notifier.messages
            if "clears the reward gate" in message))
        self.assertFalse(any(
            "until reconnect and reconciliation" in message
            for message, _ in notifier.messages))
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
        self.assertNotIn("strategy_state:market-two",
                         runner._runtime_error_counts)
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_failed_stale_book_refresh_is_counted_as_an_error(self):
        runner, _, _ = self.build_runner(clock=TickingClock())
        await runner.preflight()
        slug = runner.market_slug
        runner._refresh_book = AsyncMock(
            side_effect=AdapterError("book endpoint unavailable"))

        await runner._recover_strategy_state("book_stale", slug)

        self.assertEqual(
            runner._runtime_error_counts[f"strategy_state:{slug}"], 1)
        self.assertEqual(
            runner._runtime_error_counts[
                f"strategy_state_recovery:{slug}"], 1)
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
        # A first-attempt transient is logged, not announced. It is one
        # market's expired incentive clearing itself on the next pass; twenty
        # markets each announcing that at startup is how a real alert gets
        # missed. Persistence still announces, at attempt 4, 8, 16.
        self.assertFalse(any(
            "idle_market_refresh" in message
            for message, _ in notifier.messages))
        self.assertEqual(
            runner._runtime_error_counts.get("idle_market_refresh"), 1)
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

    async def test_action_failure_retires_one_market_not_the_session(self):
        """An unexpected action exception must not stop the other seven."""
        runner, client, _ = self.build_runner(
            auto=True, reward_selector=FakeRewardSelector(
                fake_selection("market-one")), clock=TickingClock())
        runner._bind_markets(["market-one", "market-two"])

        stop = await runner._handle_action_failure(
            ValueError("driver blew up on this market"), "market-one")

        self.assertIsNone(stop)
        self.assertTrue(runner._is_barred_from_quoting("market-one"))
        self.assertFalse(runner._is_barred_from_quoting("market-two"))

    async def test_invariant_violation_in_an_action_still_stops_the_session(self):
        """A violated invariant is about the process. It stays terminal."""
        runner, _client, _ = self.build_runner(
            auto=True, reward_selector=FakeRewardSelector(
                fake_selection("market-one")), clock=TickingClock())
        runner._bind_markets(["market-one"])

        stop = await runner._handle_action_failure(
            RuntimeInvariantViolation("scope_violation", "scope"), "market-one")

        self.assertIsNotNone(stop)
        self.assertIn("scope_violation", stop)

    async def test_plan_failure_retires_one_market_not_the_session(self):
        """One market's planning exception must not stop the other seven."""
        runner, client, _ = self.build_runner(
            auto=True, reward_selector=FakeRewardSelector(
                fake_selection("market-one")), clock=TickingClock())
        runner._bind_markets(["market-one", "market-two"])

        def explode(frame):
            if frame.market.slug == "market-one":
                raise ValueError("planner blew up on this market's state")
            return []

        runner.reconciler.plan = explode

        stop, _delay = await runner._decide_for_market("market-one", runner._now())

        self.assertIsNone(stop)
        self.assertTrue(runner._is_barred_from_quoting("market-one"))
        self.assertFalse(runner._is_barred_from_quoting("market-two"))

    async def test_a_stale_ledger_position_does_not_force_a_recovery_bind(self):
        """Observed 2026-08-16T21:36Z and again at 21:41.

        The ledger stops updating the moment a runner dies, so after a crash it
        describes the account as it was then. It still held a working order and
        a 5-lot position from the 19:01 crash while the venue held neither.
        Preflight read the ledger, bound that market for recovery, and its game
        had long since finished -- so its book came back empty on both sides and
        startup exited 2, which the supervisor never restarts. The bot refused
        to start over a position that did not exist.
        """

        from liquidity_bot.execution_ledger import PositionState

        runner, _, _ = self.build_runner(auto=True, clock=TickingClock())
        stale = "aec-dead-market-2026-08-16"
        runner.ledger.apply_position_event(
            "ws", "stale-from-a-crashed-session",
            PositionState(stale, Decimal("5"), runner._now()),
            received_at=runner._now())

        # Precondition: read straight from the ledger, this looks like risk.
        self.assertIn(stale, runner._identified_recovery_markets())

        # The venue snapshot is authoritative and says the account is flat.
        await runner._rest_reconcile(runner._now(), tag="startup")

        self.assertEqual(runner._identified_recovery_markets(), ())
        await runner.private_stream.close()
        await runner.market_stream.close()

    def test_a_venue_transport_failure_exits_retryable_not_terminal(self):
        """Exit 2 is never restarted; exit 5 is. The venue not answering must
        not be permanent.

        These shared exit 2 with a genuine state refusal, so three separate
        venue blips on 2026-08-16/17 downed the bot for good on a flat account:
        `GET /v1/incentives/earnings` 500, `GET /v1/orders/open` 500 returning
        an HTML error page, and a book empty on both sides.
        """
        import liquidity_bot.live_commissioning as module

        for error, expected in ((AdapterError("GET /v1/orders/open failed"), 5),
                                (CommissioningRefused("open orders"), 2)):
            with self.subTest(error=type(error).__name__):
                def blow_up(_args, exc=error):
                    raise exc

                original = module._run
                module._run = blow_up
                argv = sys.argv
                sys.argv = ["live_commissioning", "--market-slug", "m",
                            "--arm-live", "--accept-one-contract-risk",
                            "--exclusive-api-key", "--maker-lab-stopped"]
                try:
                    with self.assertRaises(SystemExit) as caught:
                        module.main()
                finally:
                    module._run = original
                    sys.argv = argv
                self.assertEqual(caught.exception.code, expected)

    async def test_an_earnings_failure_does_not_refuse_startup(self):
        """Observed 2026-08-17T03:05Z on a flat account with nothing wrong.

        `GET /v1/incentives/earnings` returned InternalServerError during
        preflight and the process exited 2 -- a code the supervisor never
        restarts. Earnings are reporting, not a safety input: delayed by days,
        never authorizing an entry or an exit, and read by nothing downstream.
        The run loop already treated a failure here as recoverable; preflight
        did not.
        """

        runner, client, _ = self.build_runner()
        original = client.get

        async def failing(path, *, query=None, authenticated=False):
            if path == "/v1/incentives/earnings":
                raise AdapterError(
                    "GET /v1/incentives/earnings failed: InternalServerError")
            return await original(path, query=query,
                                  authenticated=authenticated)

        client.get = failing

        # Must not raise.
        await runner.preflight()

        self.assertIsNone(runner._earnings_observed_at)
        self.assertIn("reward_earnings_refresh", runner._runtime_error_counts)
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_a_dud_primary_market_is_dropped_not_refused(self):
        """Observed 2026-08-16T21:36Z, on the restart after the crash fix.

        Slot zero was refreshed unguarded while every other slot was best
        effort, so it was the single point of failure the rest of that loop
        exists to avoid. Its book came back with no levels on either side,
        `parse_book_response` raised, and startup exited 2 -- which the
        supervisor never restarts -- over one dud market out of twenty. About
        one market in four publishes a one-sided book, so drawing a bad slot
        zero is not rare.
        """

        runner, _, _ = self.build_runner()
        await runner.preflight()
        runner._bind_markets(("market-one", "market-two"))
        original = runner._refresh_book

        async def empty_primary(now, slug=None):
            if (slug or runner.market_slug) == "market-one":
                raise AdapterError("book is empty on both sides")
            return await original(now, slug)

        runner._refresh_book = empty_primary

        # Must not raise: the healthy market carries the session.
        await runner._refresh_bound_books(runner._now())

        self.assertEqual(runner.active_slugs, ("market-two",))
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_startup_refuses_when_no_market_has_a_book(self):
        """Dropping every market would bind an empty set, which leaves
        `market_slug` raising and takes the run loop down on the next call."""

        runner, _, _ = self.build_runner()
        await runner.preflight()
        runner._bind_markets(("market-one", "market-two"))

        async def all_empty(now, slug=None):
            raise AdapterError("book is empty on both sides")

        runner._refresh_book = all_empty

        with self.assertRaises(CommissioningRefused):
            await runner._refresh_bound_books(runner._now())
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_a_recovering_market_still_refuses_rather_than_be_dropped(self):
        """It holds a position we were restarted to retire. Dropping it
        abandons live risk, which is the one thing startup may not do."""

        runner, _, _ = self.build_runner()
        await runner.preflight()
        runner._bind_markets(("market-one", "market-two"))
        runner._recovery_slugs = {"market-one"}

        async def empty_primary(now, slug=None):
            if (slug or runner.market_slug) == "market-one":
                raise AdapterError("book is empty on both sides")
            return None

        runner._refresh_book = empty_primary

        with self.assertRaises(AdapterError):
            await runner._refresh_bound_books(runner._now())
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_a_servicing_failure_retires_one_market_not_the_session(self):
        """The 2026-08-16T19:01Z crash, as a test.

        The guard sat only around `reconciler.plan`. `_evaluate_rewards` runs
        in the servicing steps AROUND that call, so when it raised on a book
        with every bid pulled the exception escaped and ended the session --
        taking nineteen healthy markets with it, parking a live position, and
        leaving the supervisor unable to restart because flat could not be
        proven. 79 minutes down for a fault in one market.
        """

        runner, _, _ = self.build_runner(
            auto=True, reward_selector=FakeRewardSelector(
                fake_selection("market-one")), clock=TickingClock())
        runner._bind_markets(["market-one", "market-two"])
        serviced = []
        original = runner._service_market_state

        async def explode(slug, now):
            serviced.append(slug)
            if slug == "market-one":
                raise ValueError("max() iterable argument is empty")
            return await original(slug, now)

        runner._service_market_state = explode

        stop = await runner._service_bound_markets()

        self.assertEqual(stop, "", "one market must not stop the session")
        self.assertTrue(runner._is_barred_from_quoting("market-one"))
        self.assertFalse(runner._is_barred_from_quoting("market-two"))
        # The healthy market was still visited, not skipped by the failure.
        self.assertIn("market-two", serviced)
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_a_runtime_invariant_still_stops_the_session(self):
        """Absorbing a local defect must not absorb a safety violation."""

        runner, _, _ = self.build_runner(
            auto=True, reward_selector=FakeRewardSelector(
                fake_selection("market-one")), clock=TickingClock())
        runner._bind_markets(["market-one", "market-two"])

        async def violate(slug, now):
            raise RuntimeInvariantViolation(
                "unowned_working_order", "someone else is on our key")

        runner._service_market_state = violate

        stop = await runner._service_bound_markets()

        self.assertTrue(stop, "an invariant violation must remain terminal")
        self.assertIn("unowned_working_order", stop)
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_no_alarm_before_the_quiet_threshold(self):
        runner, _, notifier = self.build_runner()
        await runner.preflight()
        runner._bind_markets(["market-one"])
        notifier.messages.clear()  # preflight sends its own startup banner
        start = runner._now()

        await runner._alarm_if_quoting_nothing(start, resting=0, reasons=())
        await runner._alarm_if_quoting_nothing(
            start + timedelta(seconds=runner.QUIET_ALARM_SECONDS - 1),
            resting=0, reasons=())

        self.assertEqual(notifier.messages, [])
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_quoting_nothing_for_too_long_alarms_once_with_the_reason(self):
        """Both 2026-08-16 outages looked healthy and earned nothing, and both
        were found by an operator happening to look. Reward accrues only for
        resting time, so this alarms on the symptom and reports the cause."""

        runner, _, notifier = self.build_runner()
        await runner.preflight()
        runner._bind_markets(["market-one"])
        notifier.messages.clear()  # preflight sends its own startup banner
        start = runner._now()
        late = start + timedelta(seconds=runner.QUIET_ALARM_SECONDS + 1)

        await runner._alarm_if_quoting_nothing(start, resting=0, reasons=())
        await runner._alarm_if_quoting_nothing(
            late, resting=0, reasons=(("execution_state_unknown", 20),))

        self.assertEqual(len(notifier.messages), 1)
        body, critical = notifier.messages[0]
        self.assertIn("execution_state_unknown=20", body)
        self.assertTrue(critical, "an earning outage needs to page, not log")

        # Edge-triggered: an alarm that repeats every summary gets muted.
        await runner._alarm_if_quoting_nothing(
            late + timedelta(seconds=3600), resting=0, reasons=())
        self.assertEqual(len(notifier.messages), 1)
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_quoting_again_clears_and_reports_recovery(self):
        runner, _, notifier = self.build_runner()
        await runner.preflight()
        runner._bind_markets(["market-one"])
        notifier.messages.clear()  # preflight sends its own startup banner
        start = runner._now()
        await runner._alarm_if_quoting_nothing(start, resting=0, reasons=())
        await runner._alarm_if_quoting_nothing(
            start + timedelta(seconds=runner.QUIET_ALARM_SECONDS + 1),
            resting=0, reasons=())

        await runner._alarm_if_quoting_nothing(
            start + timedelta(seconds=1200), resting=3, reasons=())

        self.assertEqual(len(notifier.messages), 2)
        self.assertIn("quoting again", notifier.messages[1][0])
        self.assertIsNone(runner._quiet_since)
        # ...and it can arm again for the next outage.
        self.assertFalse(runner._quiet_alarm_sent)
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_a_recovery_before_the_threshold_says_nothing(self):
        """A rotation or top-up rebind passes through zero resting. That is
        not an outage and must not page anyone."""

        runner, _, notifier = self.build_runner()
        await runner.preflight()
        runner._bind_markets(["market-one"])
        notifier.messages.clear()  # preflight sends its own startup banner
        start = runner._now()

        await runner._alarm_if_quoting_nothing(start, resting=0, reasons=())
        await runner._alarm_if_quoting_nothing(
            start + timedelta(seconds=60), resting=4, reasons=())

        self.assertEqual(notifier.messages, [])
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_quarantine_scoped_to_one_market_spares_the_others(self):
        """One market's fault must not cancel seven healthy quotes.

        Observed 2026-08-15T01:42Z: a single market reporting
        `execution_state_unknown` drove _quarantine_working_orders, which
        cancelled every bound market's resting order -- seconds after placement,
        on repeat. Reward is paid for resting time, so this earned nothing.
        """
        runner, client, _ = self.build_runner(
            auto=True, reward_selector=FakeRewardSelector(
                fake_selection("market-one")), clock=TickingClock())
        runner._bind_markets(["market-one", "market-two"])
        for slug, oid in (("market-one", "order-one"), ("market-two", "order-two")):
            payload = {
                "marketSlug": slug,
                "intent": "ORDER_INTENT_BUY_LONG",
                "type": "ORDER_TYPE_LIMIT",
                "price": {"value": "0.49", "currency": "USD"},
                "quantity": 5,
                "tif": "TIME_IN_FORCE_GOOD_TILL_CANCEL",
                "participateDontInitiate": True,
                "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATIC",
                "synchronousExecution": False,
            }
            runner.ledger.prepare_intent(
                oid, "PLACE", slug, f"{oid}-client", payload, created_at=NOW)
            runner.ledger.transition_intent(oid, "SUBMITTING", updated_at=NOW)
            runner.ledger.transition_intent(
                oid, "ACKNOWLEDGED", updated_at=NOW, exchange_order_id=oid)
            client.orders.rows[oid] = {
                "id": oid,
                "marketSlug": slug,
                "side": "ORDER_SIDE_BUY",
                "intent": "ORDER_INTENT_BUY_LONG",
                "price": {"value": "0.49", "currency": "USD"},
                "quantity": "5",
                "cumQuantity": "0",
                "state": "ORDER_STATE_NEW",
                "createTime": NOW.isoformat(),
            }

        # The other market's order is temporarily unreadable by id. Its
        # positive presence in the complete open-order snapshot keeps it
        # known and working, and must not block market-one's quarantine.
        original_retrieve = client.orders.retrieve
        other_market_reads = 0

        async def other_market_not_found(order_id):
            nonlocal other_market_reads
            if order_id == "order-two":
                other_market_reads += 1
                raise AdapterError(
                    f"GET /v1/order/{order_id} failed: NotFoundError")
            return await original_retrieve(order_id)

        client.orders.retrieve = other_market_not_found
        runner.executor.gateway.RETRIEVE_NOT_FOUND_ATTEMPTS = 1

        await runner._quarantine_working_orders("market-one")

        cancelled = [str(call) for call in client.orders.cancel_calls]
        self.assertTrue(any("order-one" in call for call in cancelled))
        self.assertFalse(any("order-two" in call for call in cancelled))
        self.assertEqual(
            other_market_reads, 0,
            "scoped recovery must not spend time reading another market")

    async def test_quarantine_uses_snapshot_before_target_terminal_read(self):
        runner, client, _ = self.build_runner(clock=TickingClock())
        runner._bind_markets(("market-one",))
        runner.ledger.prepare_intent(
            "place-lag", "PLACE", "market-one", "lag-client",
            {"marketSlug": "market-one"}, created_at=NOW)
        runner.ledger.transition_intent(
            "place-lag", "SUBMITTING", updated_at=NOW)
        runner.ledger.transition_intent(
            "place-lag", "ACKNOWLEDGED", updated_at=NOW,
            exchange_order_id="lag-order")
        client.orders.rows["lag-order"] = {
            "id": "lag-order",
            "clientOrderId": "lag-client",
            "marketSlug": "market-one",
            "side": "ORDER_SIDE_BUY",
            "intent": "ORDER_INTENT_BUY_LONG",
            "price": {"value": "0.49", "currency": "USD"},
            "quantity": "5",
            "cumQuantity": "0",
            "state": "ORDER_STATE_NEW",
            "createTime": NOW.isoformat(),
        }
        original_retrieve = client.orders.retrieve
        reads = 0

        async def count_terminal_read(order_id):
            nonlocal reads
            reads += 1
            return await original_retrieve(order_id)

        client.orders.retrieve = count_terminal_read
        runner.executor.gateway.RETRIEVE_NOT_FOUND_ATTEMPTS = 1

        await runner._quarantine_working_orders("market-one")

        self.assertTrue(any(
            order_id == "lag-order"
            for order_id, _payload in client.orders.cancel_calls))
        self.assertEqual(
            reads, 1,
            "the observational pass uses the snapshot; only terminal proof "
            "reads the target id")
        self.assertFalse(runner.ledger.unresolved_intents("market-one"))
        self.assertFalse(runner.ledger.working_orders("market-one"))

    async def test_global_quarantine_cancels_other_known_orders_before_failing(self):
        runner, client, _ = self.build_runner(clock=TickingClock())
        runner._bind_markets(("market-one", "market-two"))
        runner.ledger.prepare_intent(
            "unknown-place", "PLACE", "market-one", "unknown-client",
            {"marketSlug": "market-one"}, created_at=NOW)
        runner.ledger.transition_intent(
            "unknown-place", "SUBMITTING", updated_at=NOW)
        runner.ledger.apply_order_event(
            "ws", "known-other",
            OrderState(
                client_order_id="known-client",
                exchange_order_id="known-order",
                market_slug="market-two",
                side="ORDER_SIDE_BUY",
                price=Decimal("0.49"),
                original_qty=Decimal("5"),
                filled_qty=Decimal("0"),
                status="OPEN",
                event_at=NOW,
            ),
            received_at=NOW,
        )
        client.orders.rows["known-order"] = {
            "id": "known-order",
            "clientOrderId": "known-client",
            "marketSlug": "market-two",
            "side": "ORDER_SIDE_BUY",
            "intent": "ORDER_INTENT_BUY_LONG",
            "price": {"value": "0.49", "currency": "USD"},
            "quantity": "5",
            "cumQuantity": "0",
            "state": "ORDER_STATE_NEW",
            "createTime": NOW.isoformat(),
        }

        with self.assertRaises(AdapterError):
            await runner._quarantine_working_orders()

        self.assertTrue(any(
            order_id == "known-order"
            for order_id, _payload in client.orders.cancel_calls))
        self.assertFalse(runner.ledger.working_orders("market-two"))

    async def test_unresolved_background_reconcile_rotates_bounded_owner_batches(self):
        runner, _, _ = self.build_runner()
        rows = [
            {
                "market_slug": f"market-{index:02d}",
                "exchange_order_id": f"order-{index:02d}",
            }
            for index in range(12)
        ]

        scopes = [
            runner._next_unresolved_reconcile_scope(rows)
            for _ in range(3)
        ]

        self.assertEqual(scopes, [
            frozenset(f"market-{index:02d}" for index in range(5)),
            frozenset(f"market-{index:02d}" for index in range(5, 10)),
            frozenset({"market-10", "market-11", "market-00",
                       "market-01", "market-02"}),
        ])

    async def test_conflicting_order_owners_stay_account_wide(self):
        runner, _, _ = self.build_runner()
        rows = [
            {"market_slug": "market-one", "exchange_order_id": "same-id"},
            {"market_slug": "market-two", "exchange_order_id": "same-id"},
        ]

        self.assertIsNone(runner._next_unresolved_reconcile_scope(rows))

    async def test_unresolved_repair_does_not_serialize_terminal_owners_behind_one_404(self):
        """One consistency-lagged id cannot hide four terminal answers."""

        runner, client, _ = self.build_runner()
        await runner.preflight()
        slugs = tuple(f"market-{index}" for index in range(5))
        runner._bind_markets(slugs)
        quick_read_started = asyncio.Event()
        slow_observed_overlap = False

        for index, slug in enumerate(slugs):
            exchange_id = f"order-{index}"
            intent_id = f"cancel-{index}"
            runner.ledger.prepare_intent(
                intent_id, "CANCEL", slug, f"client-{index}",
                {"marketSlug": slug}, created_at=NOW)
            runner.ledger.transition_intent(
                intent_id, "SUBMITTING", updated_at=NOW)
            runner.ledger.transition_intent(
                intent_id, "ACKNOWLEDGED", updated_at=NOW,
                exchange_order_id=exchange_id)
            client.orders.rows[exchange_id] = {
                "id": exchange_id,
                "clientOrderId": f"client-{index}",
                "marketSlug": slug,
                "side": "ORDER_SIDE_BUY",
                "intent": "ORDER_INTENT_BUY_LONG",
                "price": {"value": "0.49", "currency": "USD"},
                "quantity": "5",
                "cumQuantity": "0",
                "state": "ORDER_STATE_CANCELED",
                "createTime": NOW.isoformat(),
                "lastTransactTime": NOW.isoformat(),
            }

        original_retrieve = client.orders.retrieve

        async def one_lagged_read(order_id):
            nonlocal slow_observed_overlap
            if order_id == "order-0":
                await asyncio.wait_for(quick_read_started.wait(), timeout=0.2)
                slow_observed_overlap = True
                raise AdapterError("NotFound")
            quick_read_started.set()
            return await original_retrieve(order_id)

        client.orders.retrieve = one_lagged_read
        runner.executor.gateway.RETRIEVE_NOT_FOUND_ATTEMPTS = 1

        with self.assertRaises(AdapterError):
            await runner._repair_unresolved_orders(
                NOW, markets=frozenset(slugs))

        self.assertTrue(slow_observed_overlap)
        self.assertEqual(
            [row["market_slug"] for row in runner.ledger.unresolved_intents()],
            ["market-0"],
        )
        self.assertEqual(
            runner._take_priority_replans(),
            ("market-1", "market-2", "market-3", "market-4"),
        )
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_daily_score_splits_at_et_midnight_and_survives_restart(self):
        runner, client, notifier = self.build_runner()
        # 03:59:50Z is 23:59:50 ET during daylight time. A two-point score
        # rate over twenty seconds belongs half to each ET reward date.
        before_midnight = datetime(2026, 8, 25, 3, 59, 50, tzinfo=timezone.utc)
        after_midnight = before_midnight + timedelta(seconds=20)
        runner._score_by_side = {"bid": 2.0}
        runner._score_observed_at = before_midnight

        runner._accrue_score(after_midnight)

        self.assertAlmostEqual(
            runner._daily_score_pending["2026-08-24"], 20.0)
        self.assertAlmostEqual(
            runner._daily_score_pending["2026-08-25"], 20.0)
        self.assertAlmostEqual(runner._flush_daily_score(after_midnight), 20.0)
        self.assertEqual(
            runner.ledger.score_seconds_for_day("2026-08-24"),
            Decimal("20.0"))

        # A new runner over the same durable ledger continues the same ET day
        # instead of resetting today's display to zero.
        restarted, _, _ = self.build_runner(
            ledger=runner.ledger, client=client, notifier=notifier)
        restarted._score_by_side = {"offer": 2.0}
        restarted._score_observed_at = after_midnight
        ten_seconds_later = after_midnight + timedelta(seconds=10)
        restarted._accrue_score(ten_seconds_later)

        self.assertAlmostEqual(
            restarted._flush_daily_score(ten_seconds_later), 40.0)
        self.assertEqual(
            runner.ledger.score_seconds_for_day("2026-08-25"),
            Decimal("40.0"))

    async def test_twenty_recovery_cancel_owners_all_gain_terminal_proof(self):
        """A disconnect-sized cancel set repairs in bounded fair batches."""

        runner, client, _ = self.build_runner()
        await runner.preflight()
        slugs = tuple(f"market-{index:02d}" for index in range(20))
        runner._bind_markets(slugs)

        for index, slug in enumerate(slugs):
            exchange_id = f"order-{index:02d}"
            intent_id = f"cancel-{index:02d}"
            runner.ledger.prepare_intent(
                intent_id, "CANCEL", slug, f"client-{index:02d}",
                {"marketSlug": slug}, created_at=NOW)
            runner.ledger.transition_intent(
                intent_id, "SUBMITTING", updated_at=NOW)
            runner.ledger.transition_intent(
                intent_id, "ACKNOWLEDGED", updated_at=NOW,
                exchange_order_id=exchange_id)
            client.orders.rows[exchange_id] = {
                "id": exchange_id,
                "clientOrderId": f"client-{index:02d}",
                "marketSlug": slug,
                "side": "ORDER_SIDE_BUY",
                "intent": "ORDER_INTENT_BUY_LONG",
                "price": {"value": "0.49", "currency": "USD"},
                "quantity": "5",
                "cumQuantity": "0",
                "state": "ORDER_STATE_CANCELED",
                "createTime": NOW.isoformat(),
                "lastTransactTime": NOW.isoformat(),
            }

        passes = 0
        while runner.ledger.unresolved_intents():
            scope = runner._next_unresolved_reconcile_scope()
            self.assertIsNotNone(scope)
            await runner._repair_unresolved_orders(NOW, markets=scope)
            passes += 1
            self.assertLessEqual(passes, 4)

        self.assertEqual(passes, 4)
        self.assertEqual(runner._take_priority_replans(), slugs)
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_recovery_loads_a_market_whose_game_already_started(self):
        """A leftover order does not stop being ours because the game began.

        Recovery used to load its market through the same gates that stop us
        *opening* risk, so `no longer pregame` made startup fail and retry
        forever -- with contracts still held. Observed 2026-08-15T01:2xZ.
        """
        selector = FakeRewardSelector(fake_selection("market-two"))
        runner, client, _ = self.build_runner(
            auto=True, reward_selector=selector, clock=TickingClock())
        client.markets.started.add("market-one")
        payload = {
            "marketSlug": "market-one",
            "intent": "ORDER_INTENT_BUY_LONG",
            "type": "ORDER_TYPE_LIMIT",
            "price": {"value": "0.49", "currency": "USD"},
            "quantity": 5,
            "tif": "TIME_IN_FORCE_GOOD_TILL_CANCEL",
            "participateDontInitiate": True,
            "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATIC",
            "synchronousExecution": False,
        }
        runner.ledger.prepare_intent(
            "stranded", "PLACE", "market-one", "stranded-client",
            payload, created_at=NOW)
        runner.ledger.transition_intent("stranded", "SUBMITTING", updated_at=NOW)
        runner.ledger.transition_intent(
            "stranded", "ACKNOWLEDGED", updated_at=NOW,
            exchange_order_id="stranded-order")
        client.orders.rows["stranded-order"] = {
            "id": "stranded-order",
            "marketSlug": "market-one",
            "side": "ORDER_SIDE_BUY",
            "intent": "ORDER_INTENT_BUY_LONG",
            "price": {"value": "0.49", "currency": "USD"},
            "quantity": "5",
            "cumQuantity": "0",
            "state": "ORDER_STATE_NEW",
            "createTime": NOW.isoformat(),
        }

        result = await runner.run()

        self.assertNotEqual(result.stop_reason, "startup_unavailable")
        self.assertEqual(runner.market_slug, "market-one")
        # Loaded to be exited, never to be quoted.
        self.assertTrue(runner._is_barred_from_quoting("market-one"))
        self.assertEqual(client.orders.create_calls, [])

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
        client.reward_pool = 100
        # Armed explicitly: this test is about the gate itself, which is
        # advisory by default now (maker-lab's entry rule).
        runner.config = replace(runner.config, enforce_reward_cost_gate=True)
        await runner.preflight()
        runner._evaluate_rewards(NOW)

        actions = runner.reconciler.plan(runner._frame(NOW))

        self.assertEqual(actions, [])
        # The refusal is RECORDED, not represented by an absent key. The
        # reconciler reads a missing entry as "not evaluated this pass" and
        # defers to the eligibility dwell, so an enforced refusal has to say
        # so explicitly or it would place the order the gate forbade.
        self.assertEqual(
            set(runner._expected_net_by_side), {"bid", "offer"})
        self.assertTrue(
            all(value <= 0 for value in runner._expected_net_by_side.values()),
            "a refused side must record a non-positive expected net")
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

    async def test_an_empty_plan_is_the_healthy_case_and_does_not_crash(self):
        # apply_one already treats an empty plan as a no-op. The action log
        # added alongside it indexed actions[0] unconditionally and raised
        # IndexError on every satisfied pass, which is most of them -- it
        # crash-looped the runner in production on 2026-08-14T11:29Z.
        runner, client, _ = self.build_runner()
        await runner.preflight()
        slug = runner.market_slug
        runner.reconciler.plan = lambda _frame: []

        stop, delay = await runner._decide_for_market(slug, runner._now())

        self.assertIsNone(stop)
        self.assertEqual(delay, 0.0)
        self.assertEqual(client.orders.create_calls, [])
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_one_markets_cancel_does_not_blank_another_market(self):
        """FINDINGS 6g: the cross-market blackout, as a test.

        A cancel in flight leaves its intent unresolved. Both that test and
        the absent-order test were account-wide, so every routine cancel made
        every market report execution_state_unknown and quarantine its resting
        quote. Sampled live, that held state "unknown" 35% of wall-clock time.
        """

        runner, _, _ = self.build_runner()
        await runner.preflight()
        runner.config = replace(runner.config, max_markets=8)
        runner._bind_markets(("market-one", "market-two"))
        # Give the neighbour a complete frame so state_known is what decides.
        primary = runner.state_for("market-one")
        neighbour = runner.state_for("market-two")
        neighbour.market = replace(primary.market, slug="market-two")
        neighbour.incentive = primary.incentive
        neighbour.book = replace(primary.book, slug="market-two")
        neighbour.book_observed_at = primary.book_observed_at
        runner.ledger.prepare_intent(
            "cancel-in-flight", "CANCEL", "market-one", "liquidity-one", {},
            created_at=runner._now())
        runner.ledger.transition_intent(
            "cancel-in-flight", "SUBMITTING", updated_at=runner._now())
        runner.ledger.transition_intent(
            "cancel-in-flight", "ACKNOWLEDGED", updated_at=runner._now(),
            exchange_order_id="exchange-one")

        # The market with the mutation in flight is genuinely unknown.
        self.assertFalse(runner.ledger.risk_state_known("market-one"))
        # Its neighbour is not, and must keep its quote.
        self.assertTrue(runner.ledger.risk_state_known("market-two"))
        # Account-wide callers still see the truth, so stopping and rotating
        # remain as conservative as before.
        self.assertFalse(runner.ledger.risk_state_known())

        # And the value the reconciler actually reads, which reaches
        # risk_state_known through PrivateStateGate.ready rather than
        # directly. Scoping the ledger alone changes nothing if the gate
        # keeps asking account-wide.
        self.assertFalse(runner._frame(runner._now(), "market-one").state_known)
        self.assertTrue(runner._frame(runner._now(), "market-two").state_known)
        await runner.private_stream.close()
        await runner.market_stream.close()

    def _complete_neighbour_frame(self, runner):
        """Give market-two a full frame so `state_known` is what decides."""

        primary = runner.state_for("market-one")
        neighbour = runner.state_for("market-two")
        neighbour.market = replace(primary.market, slug="market-two")
        neighbour.incentive = primary.incentive
        neighbour.book = replace(primary.book, slug="market-two")
        neighbour.book_observed_at = primary.book_observed_at

    async def _absent_order(self, runner, client, *, venue_state):
        """One order the ledger holds working and the venue no longer lists."""

        client.orders.rows["exchange-absent"] = {
            "id": "exchange-absent",
            "marketSlug": "market-one",
            "side": "ORDER_SIDE_BUY",
            "intent": "ORDER_INTENT_BUY_LONG",
            "price": {"value": "0.49", "currency": "USD"},
            "quantity": "5",
            "cumQuantity": "0",
            "state": venue_state,
            "createTime": NOW.isoformat(),
        }
        runner.ledger.apply_order_event(
            "ws", "leaving-the-book",
            OrderState(
                client_order_id="liquidity-absent",
                exchange_order_id="exchange-absent",
                market_slug="market-one",
                side="ORDER_SIDE_BUY",
                price=Decimal("0.49"),
                original_qty=Decimal("5"),
                filled_qty=Decimal("0"),
                status="OPEN",
                event_at=runner._now()),
            received_at=runner._now())
        runner.ledger.apply_order_snapshot(
            "snapshot-without-it", [], as_of=runner._now(),
            observed_at=runner._now(), complete=True)

    async def test_rest_reconcile_reasks_about_an_absent_order(self):
        """The 2026-08-16 wedge, at its root.

        `UNKNOWN_ABSENT` is in neither set `_rest_reconcile` re-read -- it is
        not a working state, and the order's intent is already RECONCILED -- so
        nothing ever asked the venue about it again. It stayed absent, and
        because `risk_state_known(None)` counts absences account-wide, the
        account-wide gate stayed false forever. Every stream bind then timed
        out waiting for a readiness that could never arrive: four binds, four
        identical `TimeoutError`s, 36 minutes of quoting nothing.
        """

        runner, client, _ = self.build_runner()
        await runner.preflight()
        runner._bind_markets(("market-one", "market-two"))
        await self._absent_order(
            runner, client, venue_state="ORDER_STATE_CANCELED")

        # Precondition: wedged exactly as production was.
        self.assertEqual(len(runner.ledger.absent_orders()), 1)
        self.assertFalse(runner.ledger.risk_state_known())

        await runner._rest_reconcile(runner._now())

        # Asked, answered, and released -- by a terminal state from the venue,
        # never by reading absence as cancellation.
        self.assertEqual(runner.ledger.absent_orders(), [])
        self.assertTrue(runner.ledger.risk_state_known())
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_preflight_clears_an_absence_left_by_a_previous_session(self):
        """Otherwise the bot can never start again.

        The absent row survives in the ledger across a restart. Preflight only
        reconciled when there were working orders or unresolved intents -- an
        absence is neither -- so the sweep that could clear it never ran.
        `ControlledTrialStartup` then reads `risk_state_known()` account-wide,
        blocks on "exchange order ownership or state is unknown", and that
        blocker is not in `_startup_recovery_is_scoped`'s allowed set: preflight
        raises CommissioningRefused and exits 2, which the supervisor never
        restarts.
        """

        runner, client, _ = self.build_runner()
        await runner.preflight()
        runner._bind_markets(("market-one", "market-two"))
        await self._absent_order(
            runner, client, venue_state="ORDER_STATE_CANCELED")
        self.assertFalse(runner.ledger.risk_state_known())

        # A second session against the same ledger, exactly as a restart sees
        # it -- the absence is already there before preflight runs.
        restarted, _, _ = self.build_runner(ledger=runner.ledger, client=client)
        self.assertEqual(len(restarted.ledger.absent_orders()), 1)

        await restarted.preflight()

        self.assertEqual(restarted.ledger.absent_orders(), [])
        self.assertTrue(restarted.ledger.risk_state_known())
        for bot in (runner, restarted):
            await bot.private_stream.close()
            await bot.market_stream.close()

    async def test_re_asking_about_an_absence_is_throttled(self):
        """`retrieve` answers a 404 by retrying four times with 0.5/1/2s
        backoff. Unthrottled, an order the venue will not resolve adds up to
        3.5s of sleeping to EVERY reconcile, indefinitely -- and `_rest_reconcile`
        runs on every service cycle and every bind. That ages books, and
        `book_stale` blocks quoting on its own."""

        runner, client, _ = self.build_runner()
        await runner.preflight()
        runner._bind_markets(("market-one", "market-two"))
        await self._absent_order(
            runner, client, venue_state="ORDER_STATE_CANCELED")
        asked = []

        async def gone(order_id):
            asked.append(order_id)
            raise AdapterError(f"GET /v1/order/{order_id} failed: NotFoundError")

        client.orders.retrieve = gone
        # One low-level attempt per sweep, so `asked` counts sweeps rather
        # than `retrieve`'s internal 404 backoff. Unpinned, a single sweep
        # makes four calls over 3.5s of real sleeping -- which is the cost the
        # throttle exists to keep off every reconcile.
        runner.executor.gateway.RETRIEVE_NOT_FOUND_ATTEMPTS = 1

        # Three reconciles inside the throttle window.
        for _ in range(3):
            await runner._rest_reconcile(runner._now())

        self.assertEqual(len(runner.ledger.absent_orders()), 1)
        self.assertEqual(
            asked.count("exchange-absent"), 1,
            "one ask per window, not one per reconcile")

        # ...and it asks again once the window has passed.
        later = runner._now() + timedelta(
            seconds=runner.ABSENT_RECHECK_SECONDS + 1)
        await runner._rest_reconcile(later)
        self.assertEqual(asked.count("exchange-absent"), 2)
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_one_unreadable_order_does_not_hide_every_other_one(self):
        """The 2026-08-18 outage, at its root.

        The strict sweep asked about working orders in `sorted()` order and
        raised on the first failure, so which id the venue could not answer
        for stopped being harmless and started being decisive. A just-placed
        order the venue served no state for two minutes sorted first; the
        order behind it -- which would have resolved on the first try -- was
        never asked at all. Both intents stayed unresolved, and an unresolved
        intent is exactly what arms `unresolved_mutation_timeout_seconds`, a
        terminal account-wide stop. It fired, and took twenty markets down for
        four hours over one order.

        The reconcile must still fail: a working order we could not re-read is
        live risk. It must fail *after* asking about all of them.
        """

        runner, client, _ = self.build_runner()
        await runner.preflight()
        runner._bind_markets(("market-one", "market-two"))

        # Two orders whose PLACE we have not yet proven. "exchange-aaa" sorts
        # first and is the one the venue will not serve.
        for cid, oid in (("liquidity-aaa", "exchange-aaa"),
                         ("liquidity-zzz", "exchange-zzz")):
            runner.ledger.prepare_intent(
                f"intent-{oid}", "PLACE", "market-one", cid,
                {"marketSlug": "market-one"}, created_at=runner._now())
            runner.ledger.transition_intent(
                f"intent-{oid}", "SUBMITTING", updated_at=runner._now())
            runner.ledger.transition_intent(
                f"intent-{oid}", "ACKNOWLEDGED", updated_at=runner._now(),
                exchange_order_id=oid)
        self.assertEqual(len(runner.ledger.unresolved_intents()), 2)

        client.orders.rows["exchange-zzz"] = {
            "id": "exchange-zzz",
            "clientOrderId": "liquidity-zzz",
            "marketSlug": "market-one",
            "side": "ORDER_SIDE_BUY",
            "intent": "ORDER_INTENT_BUY_LONG",
            "price": {"value": "0.49", "currency": "USD"},
            "quantity": "5",
            "cumQuantity": "0",
            "state": "ORDER_STATE_NEW",
            "createTime": NOW.isoformat(),
        }

        asked = []
        original = client.orders.retrieve

        async def flaky(order_id):
            asked.append(order_id)
            if order_id == "exchange-aaa":
                raise AdapterError(
                    f"GET /v1/order/{order_id} failed: NotFoundError")
            return await original(order_id)

        client.orders.retrieve = flaky
        runner.executor.gateway.RETRIEVE_NOT_FOUND_ATTEMPTS = 1

        # Still fails -- one order remains unknown, which is live risk.
        with self.assertRaises(AdapterError):
            await runner._rest_reconcile(runner._now())

        # ...but not before asking about the one behind it.
        self.assertIn(
            "exchange-zzz", asked,
            "an unreadable order must not stop the sweep before the next one")
        unresolved = {row["client_order_id"]
                      for row in runner.ledger.unresolved_intents()}
        self.assertEqual(
            unresolved, {"liquidity-aaa"},
            "the readable order's intent must be resolved by its own answer")
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def _uncertain_cancel(self, runner, client, *, venue_state):
        """A cancel that failed, over an order the venue still lists."""

        client.orders.rows["exchange-stuck"] = {
            "id": "exchange-stuck",
            "clientOrderId": "liquidity-stuck",
            "marketSlug": "market-one",
            "side": "ORDER_SIDE_BUY",
            "intent": "ORDER_INTENT_BUY_LONG",
            "price": {"value": "0.60", "currency": "USD"},
            "quantity": "5",
            "cumQuantity": "0",
            "state": venue_state,
            "createTime": NOW.isoformat(),
        }
        runner.ledger.apply_order_event(
            "ws", "the-order-itself",
            OrderState(
                client_order_id="liquidity-stuck",
                exchange_order_id="exchange-stuck",
                market_slug="market-one",
                side="ORDER_SIDE_BUY",
                price=Decimal("0.60"),
                original_qty=Decimal("5"),
                filled_qty=Decimal("0"),
                status="OPEN",
                event_at=runner._now()),
            received_at=runner._now())
        runner.ledger.prepare_intent(
            "cancel-stuck", "CANCEL", "market-one", "liquidity-stuck",
            {"orderId": "exchange-stuck", "marketSlug": "market-one"},
            created_at=runner._now())
        runner.ledger.transition_intent(
            "cancel-stuck", "SUBMITTING", updated_at=runner._now())
        runner.ledger.transition_intent(
            "cancel-stuck", "UNCERTAIN", updated_at=runner._now(),
            exchange_order_id="exchange-stuck",
            error=("AdapterError: POST /v1/order/exchange-stuck/cancel outcome "
                   "uncertain: InternalServerError"))

    async def test_a_cancel_the_venue_500d_is_re_issued(self):
        """The 2026-08-18 second outage.

        `POST /v1/order/BYRYM21VADK0/cancel` returned InternalServerError. The
        intent went UNCERTAIN, and a CANCEL intent resolves only on a terminal
        order state -- correctly, since an HTTP response is not cancellation
        confirmation. But nothing re-sent the request, so the venue answered
        OPEN for sixteen minutes, the 60s critical stop fired, and the same
        intent then made its own market's flatten unprovable, so shutdown
        exited 3 and the supervisor never restarted. One 500 cost ~3 hours.
        """

        runner, client, _ = self.build_runner()
        await runner.preflight()
        runner._bind_markets(("market-one", "market-two"))
        await self._uncertain_cancel(
            runner, client, venue_state="ORDER_STATE_NEW")
        self.assertEqual(len(runner.ledger.unresolved_intents()), 1)
        before = len(client.orders.cancel_calls)

        await runner._rest_reconcile(runner._now())

        # Re-sent, because the venue had just confirmed the order still live.
        self.assertTrue(
            any("exchange-stuck" in str(call)
                for call in client.orders.cancel_calls[before:]),
            "a cancel the venue confirmed did not land must be re-issued")
        # Not resolved yet, and that is the invariant: our own cancel request
        # succeeding is not cancellation proof. Only the venue's terminal
        # state clears a CANCEL intent, and that arrives on the next read.
        self.assertTrue(runner.ledger.unresolved_intents())

        # On that next read it does clear -- which is the whole point. The
        # stuck intent and the retry resolve together, because _upsert_order
        # resolves every intent matching the exchange id at once, so the
        # critical stop stands down and the flatten becomes provable.
        await runner._rest_reconcile(runner._now())
        self.assertEqual(
            [dict(row) for row in runner.ledger.unresolved_intents()], [],
            "the venue's terminal state must clear the stuck cancel")
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_a_cancel_is_not_re_issued_while_the_order_is_unknown(self):
        """Only positive evidence licenses re-sending it.

        "The venue confirmed the order is still live" justifies a retry. "We
        cannot see the order" does not, and must never be allowed to read as
        the same thing -- that is how absence gets treated as fact.
        """

        runner, client, _ = self.build_runner()
        await runner.preflight()
        runner._bind_markets(("market-one", "market-two"))
        await self._uncertain_cancel(
            runner, client, venue_state="ORDER_STATE_NEW")
        # We have lost sight of it: not terminal, not active, just unknown.
        runner.ledger.apply_order_snapshot(
            "snapshot-without-it", [], as_of=runner._now(),
            observed_at=runner._now(), complete=True)
        self.assertEqual(len(runner.ledger.absent_orders()), 1)
        before = len(client.orders.cancel_calls)

        await runner._retry_uncertain_cancels(runner._now())

        self.assertEqual(
            client.orders.cancel_calls[before:], [],
            "an order we cannot see must not be cancelled on a guess")
        # Still unresolved -- correctly, we have no evidence either way.
        self.assertEqual(len(runner.ledger.unresolved_intents()), 1)
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_a_terminal_state_at_an_unchanged_timestamp_still_applies(self):
        """The venue can change an order's STATE without moving its clock.

        Measured 2026-08-18: BYTCVSJ4EDJT was read as PARTIAL at
        lastTransactTime 18:08:27.222776Z. It was then cancelled, and the
        venue reported CANCELED at the *same* 18:08:27.222776Z. Keyed on time
        alone that is a replay, so `_accept_event` rejected it before
        `_upsert_order` could run -- and `_upsert_order` is where the ledger
        already knows a terminal state outranks an absence. The row could
        never leave `UNKNOWN_ABSENT`, and rotation is blocked account-wide
        while any absence stands, so the bot stopped rebinding entirely.

        The collision has to be built through `_rest_reconcile` itself. An
        earlier version of this test seeded the order with a `ws` event, so
        the reconcile event id was never consumed, no replay occurred, and it
        passed against the unfixed code.
        """

        runner, client, _ = self.build_runner()
        await runner.preflight()
        runner._bind_markets(("market-one", "market-two"))
        client.orders.rows["exchange-frozen"] = {
            "id": "exchange-frozen",
            "clientOrderId": "liquidity-frozen",
            "marketSlug": "market-one",
            "side": "ORDER_SIDE_BUY",
            "intent": "ORDER_INTENT_BUY_LONG",
            "price": {"value": "0.49", "currency": "USD"},
            "quantity": "5",
            "cumQuantity": "0",
            "state": "ORDER_STATE_NEW",
            # The only time field, so event_at cannot drift between reads.
            "createTime": NOW.isoformat(),
        }
        runner.ledger.prepare_intent(
            "intent-frozen", "PLACE", "market-one", "liquidity-frozen",
            {"marketSlug": "market-one"}, created_at=runner._now())
        runner.ledger.transition_intent(
            "intent-frozen", "SUBMITTING", updated_at=runner._now())
        runner.ledger.transition_intent(
            "intent-frozen", "ACKNOWLEDGED", updated_at=runner._now(),
            exchange_order_id="exchange-frozen")

        # First read consumes order:exchange-frozen:<createTime> as OPEN.
        await runner._rest_reconcile(runner._now())
        self.assertEqual(runner.ledger.absent_orders(), [])

        # The venue cancels it and reports the terminal state at the SAME
        # event time, then stops listing it so a complete snapshot marks it
        # absent -- which is precisely what happened in production.
        client.orders.rows["exchange-frozen"]["state"] = "ORDER_STATE_CANCELED"
        runner.ledger.apply_order_snapshot(
            "snapshot-without-frozen", [], as_of=runner._now(),
            observed_at=runner._now(), complete=True)
        self.assertEqual(len(runner.ledger.absent_orders()), 1)

        # Distinct tag: the test clock is frozen, and two reconciles at one
        # clock reading would build the same snapshot event id from different
        # content, which the ledger correctly rejects. That is what `tag` is
        # for and is unrelated to what is under test here.
        await runner._rest_reconcile(runner._now(), tag="after-cancel")

        self.assertEqual(
            runner.ledger.absent_orders(), [],
            "a terminal state must resolve an absence even when the venue "
            "reports it at an already-seen timestamp")
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_an_absence_is_repaired_when_its_terminal_state_is_already_on_file(self):
        """2026-08-19: rotation blocked for hours by one unrepairable row.

        `UNKNOWN_ABSENT` is a status we invent locally, from an order missing
        out of a complete snapshot. The venue emits no new event for it, so
        once its terminal state has been recorded, every re-ask through
        `reconcile_order` rebuilds the same (id, event_at, status) event id,
        `_accept_event` rejects it as a replay, and `_upsert_order` never runs
        -- including `resolves_absence`, the guard that exists precisely so a
        terminal state can outrank an absence.

        Measured: BZ9PPEV4RDK0 sat absent while the venue plainly reported
        CANCELED at a timestamp already on file. `risk_state_known()` counts
        absences account-wide, so rotation stopped for every market: fifteen
        slots bound, two quoting.
        """

        runner, client, _ = self.build_runner()
        await runner.preflight()
        runner._bind_markets(("market-one", "market-two"))
        client.orders.rows["exchange-stuck2"] = {
            "id": "exchange-stuck2",
            "clientOrderId": "liquidity-stuck2",
            "marketSlug": "market-one",
            "side": "ORDER_SIDE_BUY",
            "intent": "ORDER_INTENT_BUY_LONG",
            "price": {"value": "0.56", "currency": "USD"},
            "quantity": "5",
            "cumQuantity": "0",
            "state": "ORDER_STATE_NEW",
            "createTime": NOW.isoformat(),
        }
        runner.ledger.apply_order_event(
            "ws", "seed-open",
            OrderState(
                client_order_id="liquidity-stuck2",
                exchange_order_id="exchange-stuck2",
                market_slug="market-one",
                side="ORDER_SIDE_BUY",
                price=Decimal("0.56"),
                original_qty=Decimal("5"),
                filled_qty=Decimal("0"),
                status="OPEN",
                event_at=runner._now()),
            received_at=runner._now())

        # The venue cancels it, and that terminal event is recorded through the
        # normal reconcile path -- consuming the event id for that (time,status).
        client.orders.rows["exchange-stuck2"]["state"] = "ORDER_STATE_CANCELED"
        await runner.executor.reconcile_order(
            "exchange-stuck2", observed_at=runner._now())

        # Then the row is marked absent, which is the state production was in.
        runner.ledger.connection.execute(
            "UPDATE execution_orders SET status='UNKNOWN_ABSENT',"
            "unknown_reason='missing_from_complete_snapshot' "
            "WHERE exchange_order_id=?", ("exchange-stuck2",))
        runner.ledger.connection.commit()
        self.assertEqual(len(runner.ledger.absent_orders()), 1)

        # The venue's answer is unchanged, so the normal path is a pure replay.
        await runner._rest_reconcile(runner._now(), tag="repair")

        self.assertEqual(
            runner.ledger.absent_orders(), [],
            "an absence must be repaired from the venue's terminal state even "
            "when that exact event has already been applied")
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_an_unresolvable_absence_does_not_break_the_reconcile(self):
        """Best-effort, or the cure is the disease.

        These orders are already lost, so a persistent 404 on one must not take
        down the reconcile that every stream bind depends on -- that would swap
        one permanent wedge for another. It stays absent and gets reported.
        """

        runner, client, _ = self.build_runner()
        await runner.preflight()
        runner._bind_markets(("market-one", "market-two"))
        await self._absent_order(
            runner, client, venue_state="ORDER_STATE_CANCELED")

        async def gone(order_id):
            raise AdapterError(f"GET /v1/order/{order_id} failed: NotFoundError")

        client.orders.retrieve = gone

        # Must not raise.
        await runner._rest_reconcile(runner._now())

        # Still absent and still blocking -- correctly, we have no evidence --
        # but now it is reported instead of silently wedging the bot.
        self.assertEqual(len(runner.ledger.absent_orders()), 1)
        self.assertIn("absent_order", runner._runtime_error_counts)
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_an_unresolvable_absence_only_stops_its_own_market(self):
        """The containment the 2026-08-16 outage was missing.

        An order we cannot get an answer about must block the market it is in
        -- we genuinely do not know that market's state -- and nothing else.
        Account-wide, one such row failed every stream bind, and a failed bind
        closes both streams, so nineteen healthy markets stopped quoting for a
        row belonging to a twentieth that had already been released.
        """

        runner, client, _ = self.build_runner()
        await runner.preflight()
        runner._bind_markets(("market-one", "market-two"))
        self._complete_neighbour_frame(runner)
        await self._absent_order(
            runner, client, venue_state="ORDER_STATE_CANCELED")

        async def gone(order_id):
            raise AdapterError(f"GET /v1/order/{order_id} failed: NotFoundError")

        client.orders.retrieve = gone
        # Precondition: it really is unresolvable, so containment is the only
        # thing that can keep the other market quoting.
        self.assertFalse(runner.ledger.risk_state_known())
        self.assertEqual(len(runner.ledger.absent_orders()), 1)

        # The bind must survive it -- this is what failed four times live.
        await runner._open_streams_for_active_market()
        self.assertTrue(runner.gate.connected)
        self.assertFalse(runner._streams_need_rebind)
        self.assertTrue(runner.gate.session_ready(runner.ledger, now=runner._now()))

        # Blocked where we are actually blind, and nowhere else.
        self.assertFalse(runner._frame(runner._now(), "market-one").state_known)
        self.assertTrue(runner._frame(runner._now(), "market-two").state_known)
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_an_unresolvable_absence_does_not_refuse_startup(self):
        """It must never again be able to keep the bot from starting."""

        runner, client, _ = self.build_runner()
        await runner.preflight()
        await self._absent_order(
            runner, client, venue_state="ORDER_STATE_CANCELED")

        async def gone(order_id):
            raise AdapterError(f"GET /v1/order/{order_id} failed: NotFoundError")

        client.orders.retrieve = gone

        restarted, _, _ = self.build_runner(ledger=runner.ledger, client=client)
        # Must not raise CommissioningRefused.
        await restarted.preflight()

        self.assertEqual(len(restarted.ledger.absent_orders()), 1)
        self.assertTrue(restarted.ledger.ownership_is_known())
        for bot in (runner, restarted):
            await bot.private_stream.close()
            await bot.market_stream.close()

    async def test_an_unowned_venue_order_still_stops_everything(self):
        """The one fact that IS account-wide must not have been loosened.

        An order resting on our key that this bot never placed means another
        process is trading the account. No market is safe, and no bind should
        succeed, whatever any per-market bookkeeping says.
        """

        runner, _, _ = self.build_runner()
        await runner.preflight()
        runner._bind_markets(("market-one", "market-two"))
        self._complete_neighbour_frame(runner)
        runner.ledger.apply_order_event(
            "ws", "someone-elses-order",
            OrderState(
                client_order_id="venue:not-ours",
                exchange_order_id="exchange-not-ours",
                market_slug="market-one",
                side="ORDER_SIDE_BUY",
                price=Decimal("0.49"),
                original_qty=Decimal("5"),
                filled_qty=Decimal("0"),
                status="OPEN",
                event_at=runner._now()),
            received_at=runner._now())

        self.assertFalse(runner.ledger.ownership_is_known())
        # No bind may succeed while another process is on our key.
        self.assertFalse(
            runner.gate.session_ready(runner.ledger, now=runner._now()))
        self.assertFalse(runner.gate.ready(runner.ledger, now=runner._now()))
        # Stronger than a blocked frame, and for every market rather than the
        # one the order names: unowned ownership is a terminal safety event.
        for slug in ("market-one", "market-two"):
            with self.assertRaises(RuntimeInvariantViolation):
                runner._frame(runner._now(), slug)
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_a_one_sided_book_does_not_take_the_runner_down(self):
        """It did, on 2026-08-16T19:01Z, after two hours of healthy quoting.

        `_evaluate_rewards` computed `best_offer.price - best_bid.price` with no
        book guard, and those are `min`/`max` over a list -- an empty side
        raises ValueError rather than returning None. That surfaced as
        `runner_failure:ValueError`, and the supervisor then refused to restart
        because shutdown could not prove flat. `Reconciler.plan` already
        declines to quote a one-sided book; this is the same rule one layer up.
        """

        runner, _, _ = self.build_runner()
        await runner.preflight()
        slug = runner.market_slug
        state = runner.state_for(slug)
        # Every bid pulled at once. Nothing else about the market changes.
        state.book = replace(state.book, bids=())

        # Precondition: this is exactly the shape that raised.
        self.assertFalse(state.book.is_two_sided)
        with self.assertRaises(ValueError):
            _ = state.book.best_bid

        # Must not raise.
        runner._evaluate_rewards(runner._now(), slug)

        self.assertEqual(
            state.economic_blocker_by_side,
            {"bid": "book_one_sided", "offer": "book_one_sided"})
        self.assertEqual(state.reward_eligible, set())
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_one_markets_absent_order_does_not_blank_another(self):
        """The reason quotes died two seconds after being placed.

        An order sits in UNKNOWN_ABSENT between leaving the open-order
        snapshot and `retrieve` confirming it terminal -- which happens on
        every cancel. Account-wide, that made every market read as unknown,
        and the runner answers unknown state by quarantining, which cancels
        the quote it just placed. Sampled at the venue: zero orders resting
        while the ledger claimed 27 markets quoted in fifteen minutes.
        """

        runner, _, _ = self.build_runner()
        await runner.preflight()
        runner.config = replace(runner.config, max_markets=8)
        runner._bind_markets(("market-one", "market-two"))
        runner.ledger.apply_order_event(
            "ws", "leaving-the-book",
            OrderState(
                client_order_id="liquidity-absent",
                exchange_order_id="exchange-absent",
                market_slug="market-one",
                side="ORDER_SIDE_BUY",
                price=Decimal("0.49"),
                original_qty=Decimal("5"),
                filled_qty=Decimal("0"),
                status="OPEN",
                event_at=runner._now()),
            received_at=runner._now())
        runner.ledger.apply_order_snapshot(
            "snapshot-without-it", [], as_of=runner._now(),
            observed_at=runner._now(), complete=True)

        self.assertFalse(runner.ledger.risk_state_known("market-one"))
        self.assertTrue(
            runner.ledger.risk_state_known("market-two"),
            "a cancel in one market must not blank its neighbours")
        self.assertFalse(runner.ledger.risk_state_known())
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_unowned_order_still_blanks_every_market(self):
        runner, _, _ = self.build_runner()
        await runner.preflight()
        runner.config = replace(runner.config, max_markets=8)
        runner._bind_markets(("market-one", "market-two"))
        runner.ledger.apply_order_event(
            "ws", "someone-elses",
            OrderState(
                client_order_id="venue:not-ours",
                exchange_order_id="venue-1",
                market_slug="market-one",
                side="ORDER_SIDE_BUY",
                price=Decimal("0.49"),
                original_qty=Decimal("1"),
                filled_qty=Decimal("0"),
                status="OPEN",
                event_at=runner._now()),
            received_at=runner._now())

        # Ownership is never scoped: an order this bot did not place means
        # something else is trading the account, and that stops everything.
        self.assertFalse(runner.ledger.risk_state_known("market-two"))
        self.assertFalse(runner.ledger.risk_state_known())
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_a_ledger_with_past_orders_still_trades(self):
        # "This ledger has ever placed an order" was treated as recovery state.
        # It is true forever after the first order, so every later session
        # became recovery-only and the bot could never trade again without a
        # fresh database. Survivable while recovery exited 0 and stopped; once
        # recovery exited 5 to restart it became an infinite restart loop.
        runner, _, _ = self.build_runner()
        runner.ledger.prepare_intent(
            "old-place", "PLACE", "market-one", "client-old", {},
            created_at=NOW - timedelta(days=1))
        runner.ledger.transition_intent(
            "old-place", "SUBMITTING", updated_at=NOW - timedelta(days=1))
        runner.ledger.transition_intent(
            "old-place", "ACKNOWLEDGED", updated_at=NOW - timedelta(days=1),
            exchange_order_id="old-exchange")
        runner.ledger.transition_intent(
            "old-place", "RECONCILED", updated_at=NOW - timedelta(days=1))

        mode = await runner.preflight()

        # Nothing is open, nothing is unresolved, nothing is held: trade.
        self.assertEqual(mode, "TRADE")
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_a_gate_blocked_side_is_still_quotable_when_advisory(self):
        """The case that mattered live: economics say no, gate is advisory.

        The reconciler only quotes a side whose expected net is strictly
        positive. Storing the raw value here left it negative, so a market was
        selected and then refused to quote -- and because the sign flickered
        between passes, resting quotes were cancelled seconds after being
        placed. Sampled at the venue: zero orders resting, while 27 markets
        had been "quoted" in fifteen minutes.
        """

        runner, client, _ = self.build_runner()
        # A pool this small fails the reward-cost test outright.
        client.reward_pool = 100
        await runner.preflight()
        self.assertFalse(runner.config.enforce_reward_cost_gate)

        runner._evaluate_rewards(NOW, runner.market_slug)

        state = runner.state_for(runner.market_slug)
        self.assertTrue(
            state.expected_net_by_side,
            "an advisory gate must leave the side quotable")
        self.assertTrue(
            all(v > 0 for v in state.expected_net_by_side.values()),
            "expected net must be positive or the reconciler will not quote")
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_default_entry_is_maker_labs_rule_not_the_reward_gate(self):
        """maker-lab's entry rule: 2c spread and risk caps, nothing else.

        It never asked whether a quote was expected to be profitable, and
        collected 201 fills and $70.92. Ours asked that third question and the
        2026-08-14 census measured it rejecting 94% of every side on the
        venue -- 2,500 placements produced 2 fills.
        """

        runner, _, _ = self.build_runner()
        await runner.preflight()
        slug = runner.market_slug
        self.assertFalse(runner.config.enforce_reward_cost_gate)

        runner._evaluate_rewards(NOW, slug)

        state = runner.state_for(slug)
        # The economics are still measured and still recorded -- only the veto
        # is gone, so the gate can be re-armed from evidence later.
        self.assertTrue(state.reward_projection_by_side)
        self.assertNotIn(
            "projected_reward_does_not_cover_probability_weighted_exit_cost",
            state.economic_blocker_by_side.values())
        # And the safety entry checks are untouched: a side may still be
        # blocked for spread or for an exit it could not complete.
        self.assertEqual(sorted(state.expected_net_by_side), ["bid", "offer"])
        # Strictly positive, because the reconciler only quotes a side whose
        # expected net is > 0. Leaving the raw (often negative) value here
        # meant a market was selected and then refused to quote, and the sign
        # flickered between passes so resting quotes died in seconds.
        self.assertTrue(
            all(v > 0 for v in state.expected_net_by_side.values()),
            "an advisory gate must not leave a side unquotable")
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_halved_cost_margin_authorizes_a_pool_that_was_refused(self):
        # The change that made this pass, stated as behaviour rather than as a
        # constant. The gate is projected_reward x (1-P) > multiple x P x
        # unwind; the cost side already carries a Wilson upper-bound P and a
        # stressed-depth unwind, so the 2x padded it a third time. The
        # 2026-08-14 census measured 94% of every side in the universe failing
        # this one test with a best gate_ratio of 0.392.
        runner, client, _ = self.build_runner()
        client.reward_pool = 200
        await runner.preflight()

        runner._evaluate_rewards(NOW)

        self.assertEqual(runner._economic_blocker_by_side, {})
        self.assertEqual(
            sorted(runner._expected_net_by_side), ["bid", "offer"])
        # Still refused at half that pool: this loosened the gate, it did not
        # remove it.
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_a_tight_book_is_priced_back_rather_than_blocked(self):
        """1c in the middle is quoted at 2c, and scored at that same price."""
        runner, _client, _ = self.build_runner()
        await runner.preflight()
        # Depth sits at the backed-off level, so the reward cutoff still
        # reaches it -- see the companion test for the case where it does not.
        runner.book = BookSnapshot(
            "market-one",
            (BookLevel(.49, 1), BookLevel(.48, 100)),
            (BookLevel(.50, 1), BookLevel(.51, 100)),
            "MARKET_STATE_OPEN",
            NOW,
        )
        runner.book_observed_at = NOW

        runner._evaluate_rewards(NOW)
        actions = runner.reconciler.plan(runner._frame(NOW))

        self.assertNotIn(
            "spread_below_entry_minimum",
            set(runner._economic_blocker_by_side.values()))
        places = [a for a in actions if a.kind == ActionKind.PLACE_POST_ONLY]
        self.assertTrue(places)
        prices = {a.side: a.price for a in places}
        # 0.49/0.50 is 1c. The 2c window lands on the grid keeping the bid at
        # the touch, so the offer alone gives up the odd tick.
        self.assertAlmostEqual(prices["bid"], .49, places=6)
        self.assertAlmostEqual(prices["offer"], .51, places=6)
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_backing_off_outside_the_reward_band_blocks_the_side(self):
        """Pricing back to 2c must not buy fill risk for zero reward.

        The reward cutoff is depth-based: it walks levels until the programme's
        target size is met. If all of that depth sits at the touch, an order
        one tick behind earns nothing, and quoting it would be pure downside.
        The reward gate catches this on its own -- this test pins that it does.
        """
        runner, _client, _ = self.build_runner()
        await runner.preflight()
        runner.book = BookSnapshot(
            "market-one",
            (BookLevel(.49, 100000), BookLevel(.48, 100000)),
            (BookLevel(.50, 100000), BookLevel(.51, 100000)),
            "MARKET_STATE_OPEN",
            NOW,
        )
        runner.book_observed_at = NOW

        runner._evaluate_rewards(NOW)
        actions = runner.reconciler.plan(runner._frame(NOW))

        # The bid keeps the touch and stays eligible; the offer is the side
        # that moves, and one tick past a cutoff fed entirely by touch depth
        # earns nothing -- so it is blocked rather than quoted for free risk.
        self.assertEqual(
            runner._economic_blocker_by_side.get("offer"),
            "outside_reward_cutoff")
        self.assertEqual(
            {a.side for a in actions if a.kind == ActionKind.PLACE_POST_ONLY},
            {"bid"})
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_an_overlapping_book_is_blocked_before_new_order(self):
        """Best bids summing past one is stale data, not a tradeable book."""
        runner, client, _ = self.build_runner()
        await runner.preflight()
        runner.book = BookSnapshot(
            "market-one",
            (BookLevel(.60, 100), BookLevel(.59, 100)),
            (BookLevel(.40, 100), BookLevel(.41, 100)),
            "MARKET_STATE_OPEN",
            NOW,
        )
        runner.book_observed_at = NOW

        runner._evaluate_rewards(NOW)
        actions = runner.reconciler.plan(runner._frame(NOW))

        self.assertEqual(
            [a for a in actions if a.kind == ActionKind.PLACE_POST_ONLY], [])
        self.assertEqual(
            set(runner._economic_blocker_by_side.values()),
            {"book_sides_overlap"},
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

    async def test_bot_overall_counts_only_this_bots_era_of_reward(self):
        """The account is older than the bot and carries maker-lab's reward.

        Measured against the venue 2026-08-20: $86.51 PAID predates this bot's
        first order, against $54.44 PAID after it. Counting the earlier figure
        here roughly doubles the bot's apparent result, which has happened
        twice in analysis -- once reporting a reward-to-loss ratio of 3.5x that
        was really about 2x.

        Pending is carried as context and kept OUT of the bottom line, per
        invariant 22: an unsettled reward is not income.
        """
        runner, client, _ = self.build_runner()
        client.earnings_rows = [
            {"date": "2026-08-10", "marketSlug": "market-one",
             "programType": "liquidityProgram", "reward": "86.51",
             "status": "PAID"},                      # maker-lab's, before us
            {"date": "2026-08-14", "marketSlug": "market-one",
             "programType": "liquidityProgram", "reward": "54.44",
             "status": "PAID"},                      # ours, on the boundary
            {"date": "2026-08-18", "marketSlug": "market-one",
             "programType": "liquidityProgram", "reward": "35.73",
             "status": "PENDING"},                   # ours, but not income
        ]

        await runner.preflight()
        await runner._refresh_earnings(NOW)

        self.assertEqual(
            runner._bot_era_rewards.get("PAID"), Decimal("54.44"),
            "reward dated before the bot's first order belongs to maker-lab")
        self.assertEqual(
            runner._bot_era_rewards.get("PENDING"), Decimal("35.73"))

        # And the bottom line takes PAID only. Folding the $35.73 pending in
        # would report an unsettled reward as income, which invariant 22
        # forbids -- pending may be shown as context and never counted.
        runner.ledger.realized_pnl = lambda slug=None: Decimal("-31.96")
        self.assertEqual(runner._bot_overall_pnl(), Decimal("22.48"))
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

    async def test_an_alert_shows_its_severity_without_reading_the_text(self):
        """Recovering and fatal used to look identical -- both plain text.

        Colour and title now carry the severity so it is readable at a glance,
        and the footer says which host and build produced it.
        """
        runner, _client, notifier = self.build_runner()
        await runner.preflight()
        before = len(notifier.payloads)

        for attempt in range(1, 5):          # past the routine announce floor
            await runner._report_runtime_error(
                "strategy_state:market-one", AdapterError("book_stale"),
                attempt)
        await runner._report_runtime_error(
            "strategy_state_terminal",
            RuntimeInvariantViolation("scope", "scope violated"), 1)

        sent = [payload for payload, _c in notifier.payloads[before:]]
        embeds = [p["embeds"][0] for p in sent if "embeds" in p]
        self.assertTrue(embeds, "alerts must be embeds, not plain text")
        colours = {embed["color"] for embed in embeds}
        # Two severities, two colours -- the whole point.
        self.assertIn(RED, colours)
        self.assertIn(GRAY, colours)
        recovering = [e for e in embeds if e["color"] == GRAY][0]
        self.assertIn("Recovering", recovering["title"])
        self.assertIn("market-one", str(recovering["fields"]))
        self.assertTrue(recovering["footer"]["text"].startswith("liquidity-bot"))
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_operational_noise_goes_to_the_errors_webhook(self):
        """The summary channel is for reporting, not for retries.

        Destination used to be tied to severity -- critical went to the errors
        webhook, everything else to the status one -- so downgrading routine
        recoveries out of critical also moved them into the reporting channel.
        They belong in errors either way.
        """
        runner, _client, notifier = self.build_runner()
        await runner.preflight()
        before = len(notifier.messages)

        # Persist past the routine threshold so it announces at all.
        for attempt in range(1, 5):
            await runner._report_runtime_error(
                "strategy_state:market-one", AdapterError("book_stale"),
                attempt)

        announced = notifier.channels[before:]
        self.assertTrue(announced, "a persistent recovery must announce")
        self.assertTrue(all(channel == "errors" for channel in announced))
        # Announced, but never critical -- it is a retry, not a fault.
        self.assertFalse(any(
            critical for _message, critical in notifier.messages[before:]))
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_the_startup_banner_reports_the_real_config(self):
        """The operator's first sight of a run must not be wrong.

        It announced "one market, one contract per quote, $5 soft stop, $15
        hard stop" long after every one of those had changed. Nothing tied the
        message to the numbers, so it rotted silently -- this test is the tie.
        """
        runner, _client, notifier = self.build_runner()
        await runner.preflight()

        banner = next(
            message for message, _critical in notifier.messages
            if "commissioning started" in message)

        self.assertIn(f"{runner.config.max_markets} markets", banner)
        self.assertIn(f"{runner.config.quote_size} contracts", banner)
        self.assertIn(f"${runner.config.max_session_loss_usd} session", banner)
        self.assertIn(f"${runner.config.hard_stop_loss_usd} hard", banner)
        # The gate has been advisory since 2026-08-14; saying "enforced" was
        # one of the four wrong facts.
        self.assertIn(
            "enforced" if runner.config.enforce_reward_cost_gate
            else "advisory", banner)
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_holding_a_position_is_not_a_loss(self):
        """Buying spends cash but is not a loss -- the value is in inventory.

        Cash alone would read the entry cost as a loss the moment we fill, so
        open inventory is added back at its own basis.
        """
        runner, client, _ = self.build_runner()
        await runner.preflight()
        start = client.account.current_balance
        await runner._refresh_balance(NOW)

        # Bought 5 at 0.40: $2 of cash became $2 of inventory.
        client.account.current_balance = start - Decimal("2")
        client.portfolio.rows = {runner.config.market_slug: {
            "netPosition": "5", "netPositionDecimal": "5.0000",
            "updateTime": NOW.isoformat()}}
        await runner._rest_reconcile(runner._now())
        runner.ledger.inventory_basis = lambda slug: SimpleNamespace(
            average_entry_price=Decimal("0.40"), opened_at=NOW)
        await runner._refresh_balance(NOW)

        self.assertEqual(runner._session_realized(), Decimal("0"))
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_the_session_stop_falls_back_when_cash_is_unavailable(self):
        """A stop that raises is worse than one computed the old way."""
        runner, _client, _ = self.build_runner()
        await runner.preflight()
        runner._cash_baseline = None
        runner._realized_baseline = Decimal("0")
        runner.ledger.realized_pnl = lambda slug=None: Decimal("-7")

        self.assertEqual(runner._session_realized(), Decimal("-7"))
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_a_new_et_day_absorbs_every_earlier_loss(self):
        """The stop is a guardrail for today, not a lifetime tally.

        Measured 2026-08-15: once the fill back-fill let the stop see real
        closed round trips, it totalled several days of losses and the runner
        halted 73 seconds after every start. A stop that can never be
        satisfied is not a safety measure, it is an outage. A NEW ET day must
        therefore open with a fresh budget however bad the history is.
        """
        runner, _client, _ = self.build_runner()
        await runner.preflight()
        # Far past the $40 limit, all of it from before today.
        runner.ledger.realized_pnl = lambda slug=None: Decimal("-500")
        runner.ledger.realized_by_market = lambda: {"market-one": Decimal("-500")}

        # Tomorrow: a date with no stored row, so a new baseline is taken.
        tomorrow = NOW + timedelta(days=1)
        runner._now = lambda: tomorrow
        runner._roll_realized_baseline()
        runner.risk_guard.observe_realized(
            runner.ledger.realized_pnl() - runner._realized_baseline)

        self.assertIsNone(runner._stop_reason(tomorrow))

    async def test_a_baseline_is_never_written_without_a_balance(self):
        """A zero opening equity would disable the hard stop until midnight.

        `INSERT OR IGNORE` makes the first write of a risk date permanent, so a
        zero stored here could not be corrected for the rest of the day -- and
        `starting_equity - current_equity` would then be negative, so the
        emergency ceiling could never fire. Skipping is safe because both
        callers fetch the balance immediately beforehand.
        """
        runner, _client, _ = self.build_runner()
        await runner.preflight()
        opening = runner.risk_guard.starting_equity

        # A new day, but nothing to measure it with.
        runner.balance = None
        runner._baseline_day = None
        tomorrow = NOW + timedelta(days=1)
        runner._now = lambda: tomorrow
        runner._roll_realized_baseline()

        stored = runner.ledger.query(
            "SELECT risk_date FROM execution_risk_day_baselines")
        self.assertEqual(
            [r["risk_date"] for r in stored], [risk_day(NOW)])
        self.assertEqual(runner.risk_guard.starting_equity, opening)

    async def test_the_risk_budget_does_not_reset_at_utc_midnight(self):
        """Only midnight America/New_York grants a new daily budget."""

        runner, _client, _ = self.build_runner()
        await runner.preflight()
        original = runner._baseline_day

        # 00:30Z is still 20:30 on the same ET date in August.
        after_utc_midnight = datetime(
            2026, 8, 14, 0, 30, tzinfo=timezone.utc)
        runner._now = lambda: after_utc_midnight
        runner._roll_realized_baseline()
        self.assertEqual(runner._baseline_day, original)
        rows = runner.ledger.query(
            "SELECT risk_date FROM execution_risk_day_baselines")
        self.assertEqual([r["risk_date"] for r in rows], ["2026-08-13"])

        # 04:00Z is midnight EDT, so this is the first instant allowed to
        # establish the next day's marks.
        at_et_midnight = datetime(2026, 8, 14, 4, 0, tzinfo=timezone.utc)
        runner._now = lambda: at_et_midnight
        runner._roll_realized_baseline()
        self.assertEqual(runner._baseline_day.isoformat(), "2026-08-14")
        rows = runner.ledger.query(
            "SELECT risk_date FROM execution_risk_day_baselines "
            "ORDER BY risk_date")
        self.assertEqual(
            [r["risk_date"] for r in rows],
            ["2026-08-13", "2026-08-14"])
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_a_loss_stop_in_the_utc_midnight_gap_uses_the_et_date(self):
        """The pause streak and baseline must never disagree on their day."""

        runner, _client, _ = self.build_runner()
        await runner.preflight()
        # 00:30Z on August 14 is still August 13 in New York. A future refactor
        # that changes only the stop-streak key back to UTC would make the
        # pause persist across the ET baseline reset or corrupt the three-day
        # consecutive-stop bound.
        after_utc_midnight = datetime(
            2026, 8, 14, 0, 30, tzinfo=timezone.utc)
        runner.risk_guard.observe_equity(
            runner.balance.marked_equity, observed_at=after_utc_midnight)
        runner.risk_guard.observe_realized(
            -runner.config.max_session_loss_usd)

        self.assertIsNone(runner._stop_reason(after_utc_midnight))
        rows = runner.ledger.query(
            "SELECT risk_date FROM execution_risk_loss_stop_days")
        self.assertEqual([row["risk_date"] for row in rows], ["2026-08-13"])
        self.assertEqual(runner._loss_pause_pending[0], "2026-08-13")
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_a_restart_inside_the_same_day_inherits_its_drawdown(self):
        """A restart must not hand the day's budget back.

        Both stops used to baseline per PROCESS. Ten sessions ran on
        2026-08-18, so the session budget was returned ten times -- and the
        hard stop was worse, because re-stamping `starting_equity` at the
        lower equity ratchets the floor down and every individual step stays
        inside the limit while the account walks away.

        The two amounts derive from the active stop: each step must sit inside
        the ceiling while their SUM exceeds it. This keeps an operator limit
        change from silently weakening the restart-ratchet regression.
        """
        runner, client, _ = self.build_runner()
        await runner.preflight()
        opening = runner.risk_guard.starting_equity

        ceiling = runner.config.hard_stop_loss_usd
        first_step = ceiling / Decimal("2")
        second_step = ceiling - first_step + Decimal("1")

        # Lose half the ceiling, then die. This is the step that gets forgiven
        # by a per-process baseline, and it is what makes the ratchet possible.
        client.account.current_balance -= first_step
        await runner._refresh_balance(NOW)
        self.assertIsNone(runner._stop_reason(NOW))
        await runner.private_stream.close()
        await runner.market_stream.close()

        # Restart: same ledger, same venue, same ET day.
        restarted, _client2, _ = self.build_runner(
            ledger=runner.ledger, client=client)
        await restarted.preflight()

        # The day's opening equity, NOT the depressed equity it just read.
        self.assertEqual(restarted.risk_guard.starting_equity, opening)

        # Lose the other half plus $1. Measured from the DAY's open this is $1
        # past the ceiling; measured from this process's own start it remains
        # inside the ceiling, which is exactly the historical bug.
        client.account.current_balance -= second_step
        await restarted._refresh_balance(NOW)
        self.assertEqual(
            restarted._stop_reason(NOW), "EMERGENCY_EQUITY_HARD_STOP")
        await restarted.private_stream.close()
        await restarted.market_stream.close()

    async def test_a_loss_taken_this_session_still_stops(self):
        """Re-baselining must not blind the stop to real losses.

        Since 2026-08-22 the loss stop PAUSES rather than ending the session --
        it blocks new risk and clears itself at the next ET day. What this
        test guards is unchanged: the loss must still be SEEN. A re-baseline
        that hid it would leave the bot quoting straight through the budget.
        """
        runner, _client, _ = self.build_runner()
        await runner.preflight()
        runner.ledger.realized_pnl = lambda slug=None: Decimal("0")
        runner.ledger.realized_by_market = lambda: {}
        runner._baseline_day = None
        runner._roll_realized_baseline()

        # Now lose the whole session budget, after the baseline was taken.
        loss = -runner.config.max_session_loss_usd
        runner.ledger.realized_pnl = lambda slug=None: loss
        runner.risk_guard.observe_realized(
            runner.ledger.realized_pnl() - runner._realized_baseline)

        # Seen, and recorded against the day, but not terminal on day one.
        self.assertIsNone(runner._stop_reason(NOW))
        self.assertFalse(runner.risk_guard.can_add_new_risk(now=NOW))
        self.assertIn("loss limit",
                      " ".join(runner.risk_guard.blockers(now=NOW)))
        self.assertEqual(
            runner.ledger.consecutive_loss_stop_days(NOW.date().isoformat()), 1)

    async def test_a_paused_loss_stop_announces_itself_once(self):
        """A pause is silent by construction, so it has to say so.

        The old behaviour ended the process, which was at least loud. Pausing
        leaves the bot up, flat and quiet -- without an alert the first anyone
        would know is a day of missing reward. Once per ET day, because the
        blocker stands all day and `_stop_reason` runs every pass.
        """
        runner, _client, notifier = self.build_runner()
        await runner.preflight()
        loss = -runner.config.max_session_loss_usd
        runner.ledger.realized_pnl = lambda slug=None: loss
        runner.ledger.realized_by_market = lambda: {}
        runner.risk_guard.observe_realized(loss)

        before = len(notifier.messages)
        runner._stop_reason(NOW)
        await runner._announce_loss_stop_pause()
        sent = notifier.messages[before:]

        self.assertEqual(len(sent), 1, "exactly one alert on the first trip")
        body = str(sent[0])
        self.assertIn("PAUSED", body)
        self.assertIn("loss limit", body)
        self.assertIn("next ET midnight", body)

        # Every later pass on the same day is silent.
        runner._stop_reason(NOW + timedelta(seconds=5))
        await runner._announce_loss_stop_pause()
        self.assertEqual(len(notifier.messages[before:]), 1,
                         "the blocker stands all day; do not repeat it")

    async def test_a_loss_stop_becomes_terminal_after_three_days(self):
        """Pausing is convenient and, alone, unbounded.

        The day baseline resets at midnight, so a strategy that is
        systematically wrong rather than unlucky would lose its full budget
        every day forever with nothing forcing anyone to look -- and the $80
        emergency ceiling cannot catch it either, because that re-baselines
        each morning too. One bad day is variance; three in a row is a broken
        model.
        """
        runner, _client, _ = self.build_runner()
        await runner.preflight()
        loss = -runner.config.max_session_loss_usd
        runner.ledger.realized_pnl = lambda slug=None: loss
        runner.ledger.realized_by_market = lambda: {}
        runner.risk_guard.observe_realized(loss)

        # Two prior days already tripped; today is the third.
        today = NOW.date()
        for back in (2, 1):
            runner.ledger.record_loss_stop(
                (today - timedelta(days=back)).isoformat(),
                at=NOW - timedelta(days=back))

        reason = runner._stop_reason(NOW)

        self.assertEqual(
            runner.ledger.consecutive_loss_stop_days(today.isoformat()), 3)
        self.assertIsNotNone(
            reason, "three consecutive loss-stop days must stop the session")
        self.assertIn("loss limit", str(reason))

    async def test_a_gap_day_resets_the_loss_stop_streak(self):
        """Three days in a row, not three days ever.

        Counting cumulatively would turn three unrelated bad days across a
        month into a terminal stop, which is the same 'lifetime counter on a
        churning system' error FINDINGS records for retirement bounds.
        """
        runner, _client, _ = self.build_runner()
        await runner.preflight()
        today = NOW.date()
        # Yesterday clean, the two days before it tripped.
        for back in (3, 2):
            runner.ledger.record_loss_stop(
                (today - timedelta(days=back)).isoformat(), at=NOW)

        self.assertEqual(
            runner.ledger.consecutive_loss_stop_days(today.isoformat()), 0)

    async def test_one_eighty_dollar_threshold_keeps_both_sensors(self):
        runner, client, _ = self.build_runner()
        await runner.preflight()
        start = Decimal("100")

        # Paper movement is NOT the session stop. Open inventory marks against
        # us constantly -- 320 contracts move $38 on twelve cents -- and
        # stopping on that would end sessions that never lost a trade. Paper
        # movement is assetNotional; cash is untouched by it.
        await runner._refresh_balance(NOW)
        client.account.asset_notional = -(
            runner.config.hard_stop_loss_usd - Decimal("1"))
        await runner._refresh_balance(NOW)
        self.assertIsNone(runner._stop_reason(NOW))

        # A drop in settled CASH is a realised loss, and does stop. Cash moves
        # only when a trade executes, which is why the session stop reads it
        # rather than a total rebuilt from fills.
        client.account.asset_notional = Decimal("0")
        client.account.current_balance = (
            start - runner.config.max_session_loss_usd)
        # Keep marked equity $1 inside the equally-sized hard stop. This
        # proves the realized sensor still pauses independently even though
        # both protections now use the same $80 threshold.
        client.account.asset_notional = Decimal("1")
        await runner._refresh_balance(NOW)
        # Since 2026-08-22 a first-day loss stop PAUSES: it blocks new risk and
        # clears at the next ET day rather than ending the session. What must
        # still hold is that the loss is seen and quoting stops.
        self.assertIsNone(runner._stop_reason(NOW))
        self.assertFalse(runner.risk_guard.can_add_new_risk(now=NOW))
        self.assertIn("loss limit",
                      " ".join(runner.risk_guard.blockers(now=NOW)))
        client.account.current_balance = start
        client.account.asset_notional = Decimal("0")

        # The ceiling is the one thing that still reads marked equity, because
        # its job is to catch what the ledger cannot explain.
        runner.ledger.realized_pnl = lambda slug=None: Decimal("0")
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
        # Announced, but not as a critical. Finding leftover state on restart
        # is the healthy recovery path, not a fault -- it fired on roughly
        # fifteen restarts on 2026-08-14, and an alert that cries wolf on a
        # normal state trains the operator to ignore the ones that matter.
        self.assertTrue(any(
            "recovering flat" in message for message, _ in notifier.messages))
        self.assertFalse(any(critical for _, critical in notifier.messages))

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

    async def test_absent_order_resolves_only_when_its_market_settled(self):
        """A settled market is proof; a NotFound alone is not.

        19 rows sat UNKNOWN_ABSENT on 2026-08-17, oldest 18 hours old, because
        their markets had settled and `retrieve` could never answer. Since
        `risk_state_known()` counts absences account-wide, rotation was blocked
        outright and the bot quoted almost nothing while slots sat idle.
        """

        runner, _, _ = self.build_runner()
        slug = runner.market_slug
        ledger = runner.ledger

        def absent(exchange_id):
            ledger.connection.execute(
                "INSERT OR REPLACE INTO execution_orders(client_order_id,"
                "exchange_order_id,market_slug,side,price,original_qty,"
                "filled_qty,status,event_at,received_at,unknown_reason) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (f"c-{exchange_id}", exchange_id, slug, "ORDER_SIDE_BUY",
                 "0.45", "5", "0", "UNKNOWN_ABSENT", NOW.isoformat(),
                 NOW.isoformat(), "missing_from_complete_snapshot"))
            ledger.connection.commit()

        # A SETTLED market resolves the absence and unblocks rotation.
        absent("settled-1")
        self.assertTrue(any(
            r["exchange_order_id"] == "settled-1"
            for r in ledger.absent_orders()))
        self.assertTrue(
            ledger.resolve_absent_order_in_settled_market(
                "settled-1", observed_at=NOW, reason="retrieve_not_found"))
        self.assertFalse(
            any(r["exchange_order_id"] == "settled-1"
                for r in ledger.absent_orders()),
            "a resolved absence must leave the absent set")

        # Idempotent, and never touches a row that is not UNKNOWN_ABSENT.
        self.assertFalse(
            ledger.resolve_absent_order_in_settled_market(
                "settled-1", observed_at=NOW, reason="retrieve_not_found"),
            "resolving twice must report that nothing changed")

    async def test_market_has_settled_fails_closed(self):
        """Anything short of a RESOLVED market must return False."""

        runner, client, _ = self.build_runner()
        for response in ({"market": {"status": "MARKET_STATUS_OPEN"}},
                         {"market": {}}, {}, None, "nonsense"):
            client.markets.retrieve_by_slug = AsyncMock(return_value=response)
            self.assertFalse(
                await runner._market_has_settled("market-one"),
                f"{response!r} must not license resolving an absence")
        client.markets.retrieve_by_slug = AsyncMock(
            side_effect=AdapterError("boom"))
        self.assertFalse(await runner._market_has_settled("market-one"))
        client.markets.retrieve_by_slug = AsyncMock(
            return_value={"market": {"status": "MARKET_STATUS_RESOLVED"}})
        self.assertTrue(await runner._market_has_settled("market-one"))

    async def test_skipped_book_refresh_does_not_clear_the_failure_counter(self):
        """A dead market must still reach MAX_RECOVERY_ATTEMPTS and retire.

        The caller clears `quiet_book_rest_refresh:<slug>` whenever
        `_refresh_quiet_book_if_due` returns without raising -- and it returns
        without raising both when a refresh succeeded and when none was due.
        Counting the second as success reset the counter it could never then
        accumulate.

        Measured live 2026-08-17: 440 failures on one permanently-empty book,
        escalation never passing attempt=3 against MAX_RECOVERY_ATTEMPTS of 12.
        The market held its slot for two hours, top-up never ran once, and the
        bot placed no orders at all while nineteen slots sat free.
        """

        runner, _, _ = self.build_runner()
        slug = runner.market_slug
        state = runner.state_for(slug)
        key = f"quiet_book_rest_refresh:{slug}"

        # Eleven consecutive failures already banked -- one short of retiring.
        runner._runtime_error_counts[key] = MAX_RECOVERY_ATTEMPTS - 1
        # Nothing is due, so the pass does no book work at all.
        state.next_book_refresh = runner._now() + timedelta(seconds=30)
        state.next_contract_refresh = runner._now() + timedelta(seconds=30)

        await runner._service_market_state(slug, runner._now())

        self.assertEqual(
            runner._runtime_error_counts.get(key), MAX_RECOVERY_ATTEMPTS - 1,
            "a not-due book refresh proves nothing and must not clear "
            "accumulated failures -- doing so is what let a permanently dead "
            "market hold a slot for two hours")

    async def _dead_retired_market(self, runner, slug):
        """Retire `slug` for a book fault and put its kickoff in the past."""
        state = runner.state_for(slug)
        state.market = await runner.gateway.fetch_market(slug)
        state.market = replace(
            state.market, game_start=NOW - timedelta(hours=2))
        await runner._retire_market(slug, "unrecoverable_book_stale")
        return state

    async def test_a_settled_flat_retired_market_stops_burning_rest_budget(self):
        """A finished game with an unparseable book refreshed forever.

        Invariant 62 keeps a retired market's book refresh so a retirement
        still holding inventory can be exited, and `_release_retired_if_flat`
        refuses to release a BOOK-fault retirement on flatness because that
        caused the ~45s retire/release churn on chevic-scovla. Both are right;
        composed, a settled flat market fetched a book that could never parse.

        Measured 2026-08-20T10:55Z: two setkamecz markets at attempt=72 and 70,
        144 REST calls in ten minutes, ~6% of the 4 req/s governor each, and
        growing as more games finished.
        """
        runner, client, _ = self.build_runner()
        slug = runner.market_slug
        state = await self._dead_retired_market(runner, slug)
        before = client.markets.book_calls

        state.book_observed_at = None          # maximally "quiet"
        due = NOW - timedelta(seconds=1)       # and overdue
        nxt = await runner._refresh_quiet_book_if_due(due, slug)

        self.assertEqual(client.markets.book_calls, before,
                         "a retired, flat, order-free, past-kickoff market has "
                         "nothing to quote and nothing to exit -- fetching its "
                         "book can only produce another failure")
        self.assertGreaterEqual(
            (nxt - NOW).total_seconds(), runner.DEAD_MARKET_REFRESH_SECONDS - 1)

    async def test_a_market_retired_before_kickoff_keeps_its_book(self):
        """Only a market whose game is OVER is safely dead.

        A book fault before kickoff can be transient -- about one market in
        four publishes a one-sided book, and `RETIREMENT_COOLDOWN_SECONDS`
        exists precisely so such a market can come back rather than being
        barred for the session. Backing its refresh off to five minutes would
        hand it a stale book at the moment it is released.
        """
        runner, client, _ = self.build_runner()
        slug = runner.market_slug
        state = runner.state_for(slug)
        state.market = await runner.gateway.fetch_market(slug)
        # Retired, flat and order-free -- but the game has NOT started.
        state.market = replace(
            state.market, game_start=NOW + timedelta(hours=1))
        await runner._retire_market(slug, "unrecoverable_book_stale")
        before = client.markets.book_calls

        state.book_observed_at = None
        await runner._refresh_quiet_book_if_due(NOW - timedelta(seconds=1), slug)

        self.assertEqual(client.markets.book_calls, before + 1,
                         "a pre-kickoff retirement may still recover and be "
                         "released by its cooldown, so it keeps its book")

    async def test_a_retired_market_holding_a_position_still_refreshes(self):
        """The guard must never strand the exit invariant 62 protects.

        A retirement holding inventory is precisely the case the refresh was
        kept alive for: the position has to be priced against a current book
        before it can be exited.
        """
        runner, client, _ = self.build_runner()
        slug = runner.market_slug
        state = await self._dead_retired_market(runner, slug)
        runner.ledger.position = lambda s=None: Decimal("5")
        before = client.markets.book_calls

        state.book_observed_at = None
        await runner._refresh_quiet_book_if_due(NOW - timedelta(seconds=1), slug)

        self.assertEqual(client.markets.book_calls, before + 1,
                         "a retired market still holding inventory must keep "
                         "its book, or the exit it exists to work is stranded")

    async def test_one_stuck_mutation_retires_its_market_not_the_account(self):
        """The blast radius must match the evidence.

        This stop was terminal and ACCOUNT-WIDE: one overdue intent anywhere
        took all twenty markets down, and because shutdown then could not prove
        flat it exited 3, which the supervisor never restarts. It cost outages
        on 2026-08-17, 2026-08-18 and twice on 2026-08-20 -- the last one with
        nineteen healthy markets and a venue that was already flat.

        An unresolved intent in one market says nothing about the other
        nineteen. Ownership faults stay account-wide; this one does not.
        """
        runner, _, _ = self.build_runner()
        await runner.preflight()
        limit = runner.config.unresolved_mutation_timeout_seconds
        runner.ledger.prepare_intent(
            "stuck", "CANCEL", "market-one", "stuck-client",
            {"marketSlug": "market-one"}, created_at=NOW)

        later = NOW + timedelta(seconds=limit + 30)
        stuck, unattributable = runner._stuck_mutations(later)

        self.assertEqual(sorted(stuck), ["market-one"])
        self.assertIsNone(unattributable)
        # And nothing account-wide has been armed by it.
        self.assertIsNone(runner.risk_guard.critical_reason)
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_a_mutation_inside_the_timeout_retires_nothing(self):
        """Ordinary in-flight mutations must not be mistaken for stuck ones.

        Every cancel is briefly unresolved -- FINDINGS measured the account
        spending 35% of wall-clock time with one outstanding -- so a guard that
        ignored the age would retire a market on every routine mutation and
        walk the whole slate out of the session within minutes.
        """
        runner, _, _ = self.build_runner()
        await runner.preflight()
        limit = runner.config.unresolved_mutation_timeout_seconds
        runner.ledger.prepare_intent(
            "fresh", "CANCEL", "market-one", "fresh-client",
            {"marketSlug": "market-one"}, created_at=NOW)

        # One second short of the limit: still in flight, not stuck.
        stuck, unattributable = runner._stuck_mutations(
            NOW + timedelta(seconds=limit - 1))

        self.assertEqual(stuck, {})
        self.assertIsNone(unattributable)
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_a_stuck_mutation_still_blocks_its_own_market(self):
        """Scoping the stop must not license quoting the market it belongs to.

        The whole point of the guard is that we do not know what happened to
        that mutation. Narrowing its blast radius is only safe while the
        market it names is still barred from building more risk.
        """
        runner, _, _ = self.build_runner()
        await runner.preflight()
        slug = runner.market_slug

        await runner._retire_market(
            slug, "unresolved_mutation_timeout", reset_exit_budget=False)

        self.assertTrue(runner._is_retired(slug))
        self.assertTrue(runner._is_barred_from_quoting(slug))
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_an_unattributable_stuck_mutation_stays_terminal(self):
        """An intent with no market to blame cannot be scoped: fail closed.

        `prepare_intent` refuses an empty slug outright ("intent identity is
        incomplete"), so this branch is defensive against a guard that lives in
        another class. It is driven directly rather than through the ledger for
        exactly that reason -- the alternative is shipping a fail-closed path
        nothing has ever executed.
        """
        runner, _, _ = self.build_runner()
        await runner.preflight()
        limit = runner.config.unresolved_mutation_timeout_seconds
        stale = (NOW - timedelta(seconds=limit + 30)).isoformat()
        runner.ledger.unresolved_intents = lambda slug=None: [
            {"updated_at": stale, "market_slug": ""}]

        stuck, unattributable = runner._stuck_mutations(NOW)

        self.assertEqual(stuck, {})
        self.assertIsNotNone(unattributable)
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_unresolved_mutation_timeout_clears_the_measured_range(self):
        """The terminal stop must not fire inside normal venue latency.

        Hardcoded at 10s, this stopped the live bot twice in forty minutes on
        2026-08-17. Measured that day over 5,023 intents: p50 4.54s, p90 8.22s,
        p95 9.49s, p99 16.36s, max 37.72s -- so the *terminal* stop was armed
        at the p95 of ordinary operation and 4.0% of mutations crossed it as a
        matter of routine. The slow tail is reconciliation queueing behind the
        shared 4 req/s governor, not a stuck venue.
        """

        runner, _, _ = self.build_runner()
        limit = runner.config.unresolved_mutation_timeout_seconds

        # Clear of the whole distribution observed on the day it fired.
        self.assertGreater(limit, 37.72)

        runner.ledger.prepare_intent(
            "slow", "PLACE", "market-one", "slow-client",
            {"marketSlug": "market-one"}, created_at=NOW)
        # p99 of real traffic must be survivable, not terminal.
        self.assertLess(
            runner._oldest_unresolved_age_seconds(NOW + timedelta(seconds=16.4)),
            limit)
        # A mutation that is genuinely never coming back still trips.
        self.assertGreater(
            runner._oldest_unresolved_age_seconds(NOW + timedelta(seconds=300)),
            limit)

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
        moment = [NOW]
        runner, _, _ = self.build_runner(clock=lambda: moment[0])
        await runner.preflight()
        later = NOW + timedelta(seconds=31)
        self.assertFalse(runner.gate.ready(runner.ledger, now=later))
        moment[0] = later
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
        # A transient that clears on its first try is not announced at all.
        # Severity was fixed first (nothing routine is critical); volume is the
        # other half -- 1,517 of these in an hour, and twenty markets each
        # firing book_stale once at startup, is how a real alert gets missed.
        # Every occurrence is still in commissioning.log.
        self.assertFalse(any(
            "recovering" in message for message, _ in notifier.messages))
        self.assertFalse(any(critical for _, critical in notifier.messages))
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
        # A transient that clears on its first try is not announced at all.
        # Severity was fixed first (nothing routine is critical); volume is the
        # other half -- 1,517 of these in an hour, and twenty markets each
        # firing book_stale once at startup, is how a real alert gets missed.
        # Every occurrence is still in commissioning.log.
        self.assertFalse(any(
            "recovering" in message for message, _ in notifier.messages))
        self.assertFalse(any(critical for _, critical in notifier.messages))
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

    async def test_open_position_marked_breach_waits_for_realized_close(self):
        """A losing open mark must not force a shutdown that realizes it."""

        runner, client, notifier = self.build_runner()
        await runner.preflight()
        slug = runner.config.market_slug

        client.portfolio.rows = {slug: {
            "netPosition": "10", "netPositionDecimal": "10.0000",
            "updateTime": NOW.isoformat(),
        }}
        await runner._rest_reconcile(NOW)
        client.account.asset_notional = Decimal("-81")
        await runner._refresh_balance(NOW)

        self.assertIsNone(runner._stop_reason(NOW))
        self.assertEqual(runner._provisional_hard_stop_markets, {slug})
        self.assertFalse(runner.risk_guard.can_add_new_risk(now=NOW))
        await runner._announce_provisional_hard_stop()
        self.assertIn("provisional", str(notifier.messages[-1]).lower())

        # The trade closes and the actual day result is only -$76. A complete
        # venue reconcile followed by a fresh balance is the required proof.
        client.portfolio.rows = {}
        closed_at = NOW + timedelta(seconds=1)
        runner.clock = lambda: closed_at
        await runner._rest_reconcile(closed_at)
        client.account.current_balance = Decimal("24")
        client.account.asset_notional = Decimal("0")
        measured_at = closed_at + timedelta(seconds=1)
        runner.clock = lambda: measured_at
        await runner._refresh_balance(measured_at)

        self.assertIsNone(runner._stop_reason(measured_at))
        self.assertIsNone(runner._provisional_hard_stop_at)
        self.assertEqual(runner._session_realized(), Decimal("-76"))
        await runner._announce_provisional_hard_stop()
        self.assertIn("cleared", str(notifier.messages[-1]).lower())
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_open_position_breach_stops_if_close_confirms_eighty(self):
        runner, client, _ = self.build_runner()
        await runner.preflight()
        slug = runner.config.market_slug
        client.portfolio.rows = {slug: {
            "netPosition": "10", "netPositionDecimal": "10.0000",
            "updateTime": NOW.isoformat(),
        }}
        await runner._rest_reconcile(NOW)
        client.account.asset_notional = Decimal("-81")
        await runner._refresh_balance(NOW)
        self.assertIsNone(runner._stop_reason(NOW))

        client.portfolio.rows = {}
        closed_at = NOW + timedelta(seconds=1)
        runner.clock = lambda: closed_at
        await runner._rest_reconcile(closed_at)
        self.assertIsNone(
            runner._stop_reason(closed_at),
            "the pre-close balance cannot confirm a hard loss")
        client.account.current_balance = Decimal("19")
        client.account.asset_notional = Decimal("0")
        measured_at = closed_at + timedelta(seconds=1)
        runner.clock = lambda: measured_at
        await runner._refresh_balance(measured_at)

        self.assertEqual(
            runner._stop_reason(measured_at), "EMERGENCY_EQUITY_HARD_STOP")
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_realized_stop_can_pause_with_another_position_open(self):
        runner, client, _ = self.build_runner()
        await runner.preflight()
        slug = runner.config.market_slug
        client.portfolio.rows = {slug: {
            "netPosition": "10", "netPositionDecimal": "10.0000",
            "updateTime": NOW.isoformat(),
        }}
        await runner._rest_reconcile(NOW)
        client.account.asset_notional = Decimal("-81")
        await runner._refresh_balance(NOW)
        self.assertIsNone(runner._stop_reason(NOW))

        # Closed trades can independently consume the realized budget even
        # while this unrelated position remains open.
        runner.risk_guard.observe_realized(
            -runner.config.max_session_loss_usd)
        self.assertIsNone(runner._stop_reason(NOW))
        self.assertIn(
            "session loss limit reached", runner.risk_guard.blockers(now=NOW))
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_hard_equity_excludes_proven_nontrading_cash(self):
        runner, client, _ = self.build_runner()
        await runner.preflight()
        runner._nontrading_cash = Decimal("100")
        runner._nontrading_cash_observed_at = NOW
        runner._nontrading_cash_day = runner._baseline_day
        client.account.current_balance = Decimal("120")

        await runner._refresh_balance(NOW)

        self.assertEqual(runner.balance.marked_equity, Decimal("120"))
        self.assertEqual(runner.risk_guard.current_equity, Decimal("20"))
        self.assertTrue(runner.risk_guard.hard_stop_reached(now=NOW))
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_private_recovery_listens_before_cancel_and_drains_proof(self):
        """A reconnect must not create a terminal-event visibility gap."""

        runner, client, _ = self.build_runner(clock=TickingClock())
        await runner.preflight()

        class DrainingPrivateStream(FakePrivateStream):
            def drain_events(self, *, limit=None):
                if limit is None:
                    limit = len(self.events)
                drained = tuple(self.events[:limit])
                del self.events[:limit]
                return drained

        runner.private_stream_factory = DrainingPrivateStream
        slug = runner.market_slug
        runner.ledger.apply_order_event(
            "ws", "open-before-reconnect",
            OrderState(
                client_order_id="liquidity-reconnect",
                exchange_order_id="exchange-reconnect",
                market_slug=slug,
                side="ORDER_SIDE_BUY",
                price=Decimal("0.49"),
                original_qty=Decimal("5"),
                filled_qty=Decimal("0"),
                status="OPEN",
                event_at=runner._now()),
            received_at=runner._now())
        client.orders.rows["exchange-reconnect"] = {
            "id": "exchange-reconnect",
            "clientOrderId": "liquidity-reconnect",
            "marketSlug": slug,
            "side": "ORDER_SIDE_BUY",
            "intent": "ORDER_INTENT_BUY_LONG",
            "price": {"value": "0.49", "currency": "USD"},
            "quantity": "5",
            "cumQuantity": "0",
            "state": "ORDER_STATE_NEW",
            "createTime": runner._now().isoformat(),
        }
        original_cancel = client.orders.cancel

        async def cancel_after_listener_started(order_id, payload):
            self.assertTrue(runner.private_stream.started)
            await original_cancel(order_id, payload)
            runner.private_stream.events.append(PrivateEvent(
                PrivateEventKind.ORDER_UPDATE,
                {"execution": {
                    "id": "cancel-proof-reconnect",
                    "type": "EXECUTION_TYPE_CANCEL",
                    "order": {
                        "id": order_id,
                        "marketSlug": slug,
                        "intent": "ORDER_INTENT_BUY_LONG",
                        "side": "ORDER_SIDE_BUY",
                        "state": "ORDER_STATE_CANCELED",
                        "price": {"value": "0.49", "currency": "USD"},
                        "quantity": "5",
                        "cumQuantity": "0",
                        "updateTime": runner._now().isoformat(),
                    },
                }},
                "terminal-cancel-after-reconnect"))

        client.orders.cancel = cancel_after_listener_started

        async def stop_if_the_ordering_regresses(_seconds):
            runner.request_stop("ordering_regressed")

        runner.sleep = stop_if_the_ordering_regresses

        stop = await runner._recover_private_stream(
            AdapterError("private WebSocket disconnected"))

        self.assertIsNone(stop)
        self.assertEqual(runner.ledger.unresolved_intents(slug), [])
        self.assertEqual(runner.ledger.absent_orders(slug), [])
        self.assertEqual(runner.ledger.working_orders(slug), [])
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_reward_cash_cannot_mask_the_session_loss_stop(self):
        """Non-trading credits must not buy back the trading-loss budget."""

        runner, client, _ = self.build_runner()
        await runner.preflight()
        later = NOW + timedelta(seconds=20)
        runner._now = lambda: later

        # +$100 reward and -$80 trading leaves settled cash +$20. The old
        # sensor read +$20 and left quoting enabled; trading P&L is -$80.
        client.account.current_balance = Decimal("120")
        client.portfolio.cash_activities = [{
            "type": "ACTIVITY_TYPE_TRANSFER",
            "accountBalanceChange": {
                "status": "COMPLETED",
                "amount": {"value": "100", "currency": "USD"},
                "description": "liquidity reward payout",
                "updateTime": later.isoformat(),
            },
        }]

        await runner._refresh_balance(later)

        self.assertEqual(runner.risk_guard.realized_pnl, Decimal("-80"))
        self.assertFalse(runner.risk_guard.can_add_new_risk(now=later))
        self.assertEqual(runner._nontrading_cash, Decimal("100"))
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_unknown_cash_activity_expires_risk_freshness(self):
        """An unclassified credit must fail closed instead of being ignored."""

        runner, client, _ = self.build_runner()
        await runner.preflight()
        later = NOW + timedelta(seconds=20)
        runner._now = lambda: later
        client.portfolio.cash_activities = [{
            "type": "ACTIVITY_TYPE_TRANSFER",
            "accountBalanceChange": {
                "status": "COMPLETED",
                "amount": {"value": "5", "currency": "USD"},
                "description": "unclassified adjustment",
                "updateTime": later.isoformat(),
            },
        }]

        with self.assertRaisesRegex(AdapterError, "unclassified completed"):
            await runner._refresh_balance(later)

        self.assertFalse(runner.risk_guard.can_add_new_risk(now=later))
        self.assertIn(
            "marked account equity is stale",
            runner.risk_guard.blockers(now=later))
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_private_recovery_resumes_other_markets_while_cancel_is_ambiguous(self):
        runner, client, _ = self.build_runner(clock=TickingClock())
        await runner.preflight()
        runner._bind_markets(("market-one", "market-two"))
        self._complete_neighbour_frame(runner)
        runner.ledger.apply_order_event(
            "ws", "owned-before-disconnect",
            OrderState(
                client_order_id="liquidity-before-disconnect",
                exchange_order_id="exchange-before-disconnect",
                market_slug="market-one",
                side="ORDER_SIDE_BUY",
                price=Decimal("0.49"),
                original_qty=Decimal("5"),
                filled_qty=Decimal("0"),
                status="OPEN",
                event_at=NOW,
            ),
            received_at=NOW,
        )
        client.orders.rows["exchange-before-disconnect"] = {
            "id": "exchange-before-disconnect",
            "clientOrderId": "liquidity-before-disconnect",
            "marketSlug": "market-one",
            "side": "ORDER_SIDE_BUY",
            "intent": "ORDER_INTENT_BUY_LONG",
            "price": {"value": "0.49", "currency": "USD"},
            "quantity": "5",
            "cumQuantity": "0",
            "state": "ORDER_STATE_NEW",
            "createTime": NOW.isoformat(),
        }
        hidden: set[str] = set()

        async def accepted_but_not_terminal(order_id, payload):
            client.orders.cancel_calls.append((order_id, payload))
            hidden.add(order_id)

        async def active_orders(_params):
            return {"orders": [
                row for order_id, row in client.orders.rows.items()
                if order_id not in hidden
            ]}

        client.orders.cancel = accepted_but_not_terminal
        client.orders.list = active_orders
        original_retrieve = client.orders.retrieve
        lagged_reads = 0

        async def retrieve_with_consistency_lag(order_id):
            nonlocal lagged_reads
            if order_id in hidden:
                lagged_reads += 1
                raise AdapterError(
                    f"GET /v1/order/{order_id} failed: NotFoundError")
            return await original_retrieve(order_id)

        client.orders.retrieve = retrieve_with_consistency_lag
        runner.executor.gateway.RETRIEVE_NOT_FOUND_ATTEMPTS = 1

        stop = await runner._recover_private_stream(
            AdapterError("private WebSocket disconnected"))
        repeated_stop = await runner._recover_private_stream(
            AdapterError("private WebSocket disconnected again"))

        self.assertIsNone(stop)
        self.assertIsNone(repeated_stop)
        self.assertEqual(len(runner.ledger.unresolved_intents("market-one")), 1)
        self.assertEqual(len(runner.ledger.absent_orders("market-one")), 1)
        self.assertFalse(runner._frame(runner._now(), "market-one").state_known)
        self.assertTrue(runner._frame(runner._now(), "market-two").state_known)
        self.assertTrue(runner.gate.session_ready(
            runner.ledger, now=runner._now()))
        self.assertEqual(
            lagged_reads, 0,
            "private recovery must scope the owner without waiting on its id")
        self.assertEqual(client.orders.create_calls, [])
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_bound_recovery_resumes_other_markets_while_cancel_is_ambiguous(self):
        """A failed active-set bind must not make one absent cancel global.

        This is the 2026-08-24 live failure: both sockets were deliberately
        closed for a top-up, the replacement bind failed while GET-by-id
        lagged, and strict quarantine then held all twenty markets dark.
        """

        runner, client, _ = self.build_runner(clock=TickingClock())
        await runner.preflight()
        runner._bind_markets(("market-one", "market-two"))
        self._complete_neighbour_frame(runner)
        runner.ledger.apply_order_event(
            "ws", "owned-before-failed-bind",
            OrderState(
                client_order_id="liquidity-before-failed-bind",
                exchange_order_id="exchange-before-failed-bind",
                market_slug="market-one",
                side="ORDER_SIDE_BUY",
                price=Decimal("0.49"),
                original_qty=Decimal("5"),
                filled_qty=Decimal("0"),
                status="OPEN",
                event_at=NOW,
            ),
            received_at=NOW,
        )
        client.orders.rows["exchange-before-failed-bind"] = {
            "id": "exchange-before-failed-bind",
            "clientOrderId": "liquidity-before-failed-bind",
            "marketSlug": "market-one",
            "side": "ORDER_SIDE_BUY",
            "intent": "ORDER_INTENT_BUY_LONG",
            "price": {"value": "0.49", "currency": "USD"},
            "quantity": "5",
            "cumQuantity": "0",
            "state": "ORDER_STATE_NEW",
            "createTime": NOW.isoformat(),
        }
        hidden: set[str] = set()

        async def accepted_but_not_terminal(order_id, payload):
            client.orders.cancel_calls.append((order_id, payload))
            hidden.add(order_id)

        async def active_orders(_params):
            return {"orders": [
                row for order_id, row in client.orders.rows.items()
                if order_id not in hidden
            ]}

        client.orders.cancel = accepted_but_not_terminal
        client.orders.list = active_orders
        original_retrieve = client.orders.retrieve
        lagged_reads = 0

        async def retrieve_with_consistency_lag(order_id):
            nonlocal lagged_reads
            if order_id in hidden:
                lagged_reads += 1
                raise AdapterError(
                    f"GET /v1/order/{order_id} failed: NotFoundError")
            return await original_retrieve(order_id)

        client.orders.retrieve = retrieve_with_consistency_lag
        runner.executor.gateway.RETRIEVE_NOT_FOUND_ATTEMPTS = 1

        # Keep the pre-fix infinite retry from hanging the regression test.
        async def stop_after_failed_attempt(_seconds):
            runner.request_stop("regression_retry")

        runner.sleep = stop_after_failed_attempt
        stop = await runner._recover_bound_streams(
            AdapterError("active-set bind failed"))

        self.assertIsNone(stop)
        self.assertFalse(runner._streams_need_rebind)
        self.assertEqual(len(runner.ledger.unresolved_intents("market-one")), 1)
        self.assertEqual(len(runner.ledger.absent_orders("market-one")), 1)
        self.assertFalse(runner._frame(runner._now(), "market-one").state_known)
        self.assertTrue(runner._frame(runner._now(), "market-two").state_known)
        self.assertTrue(runner.gate.session_ready(
            runner.ledger, now=runner._now()))
        self.assertEqual(
            lagged_reads, 0,
            "bound recovery must scope the owner without waiting on its id")
        self.assertEqual(client.orders.create_calls, [])
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_private_reconnect_failures_are_bounded_without_claiming_flat(self):
        runner, client, _ = self.build_runner()
        await runner.preflight()
        runner.private_stream_factory = FailingPrivateStream

        stop = await runner._recover_private_stream(
            AdapterError("private WebSocket disconnected"))

        self.assertEqual(
            stop, "runner_failure:private_stream_recovery_exhausted")
        self.assertEqual(
            runner._runtime_error_counts["private_stream"],
            MAX_PRIVATE_STREAM_RECOVERY_ATTEMPTS,
        )
        self.assertEqual(client.orders.create_calls, [])
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_bound_reconnect_failures_are_bounded_without_claiming_flat(self):
        runner, client, _ = self.build_runner()
        await runner.preflight()
        runner.private_stream_factory = FailingPrivateStream

        # If the bound is removed, fail quickly instead of hanging this test.
        sleeps = 0

        async def reject_an_extra_retry(_seconds):
            nonlocal sleeps
            sleeps += 1
            if sleeps >= MAX_BOUND_STREAM_RECOVERY_ATTEMPTS:
                runner.request_stop("unbounded_bound_stream_recovery")

        runner.sleep = reject_an_extra_retry
        stop = await runner._recover_bound_streams(
            AdapterError("active-set bind failed"))

        self.assertEqual(
            stop, "runner_failure:bound_stream_recovery_exhausted")
        self.assertEqual(
            runner._runtime_error_counts["bound_streams"],
            MAX_BOUND_STREAM_RECOVERY_ATTEMPTS,
        )
        self.assertEqual(
            sleeps, MAX_BOUND_STREAM_RECOVERY_ATTEMPTS - 1)
        self.assertEqual(client.orders.create_calls, [])
        await runner.private_stream.close()
        await runner.market_stream.close()

    def test_maintenance_classifier_is_narrow(self):
        runner, _, _ = self.build_runner()

        class ServerFailure(Exception):
            status_code = 503

        wrapped = AdapterError("GET /v1/orders/open failed")
        wrapped.__cause__ = ServerFailure("service restart")
        self.assertTrue(runner._is_venue_unavailable(wrapped))
        self.assertTrue(runner._is_venue_unavailable(
            AdapterError("GET /v1/orders/open failed: InternalServerError")))
        self.assertTrue(runner._is_venue_unavailable(
            AdapterError("private WebSocket disconnected")))
        self.assertTrue(runner._is_venue_unavailable(AdapterError(
            "private WebSocket disconnected: missing close frame")))
        self.assertTrue(runner._is_venue_unavailable(
            TimeoutError("account read timed out")))
        self.assertFalse(runner._is_venue_unavailable(
            AdapterError("GET /v1/order/id failed: NotFoundError")))
        self.assertFalse(runner._is_venue_unavailable(
            AdapterError("open-order response has no orders list")))
        self.assertFalse(runner._is_venue_unavailable(
            AdapterError("simulated private reconnect failure")))
        self.assertFalse(runner._is_venue_unavailable(
            AdapterError("unresolved_mutation_timeout")))

    async def test_private_maintenance_recovers_without_changing_twenty_slots(self):
        runner, client, notifier = self.build_runner()
        await runner.preflight()
        slugs = tuple(f"market-{index:02d}" for index in range(20))
        runner._bind_markets(slugs)
        factory_calls = 0

        def recovering_factory(fake_client):
            nonlocal factory_calls
            factory_calls += 1
            stream = FakePrivateStream(fake_client)
            if factory_calls <= MAX_PRIVATE_STREAM_RECOVERY_ATTEMPTS:
                async def fail_for_maintenance(_slugs=None):
                    raise AdapterError(
                        "GET /v1/orders/open failed: InternalServerError")
                stream.start = fail_for_maintenance
            return stream

        runner.private_stream_factory = recovering_factory
        fetch_orders = runner.account.fetch_open_orders
        fetch_positions = runner.account.fetch_positions
        fetch_balance = runner.account.fetch_balance
        runner.account.fetch_open_orders = AsyncMock(wraps=fetch_orders)
        runner.account.fetch_positions = AsyncMock(wraps=fetch_positions)
        runner.account.fetch_balance = AsyncMock(wraps=fetch_balance)

        stop = await runner._recover_private_stream(
            AdapterError("private WebSocket disconnected"))

        self.assertIsNone(stop)
        self.assertEqual(factory_calls, MAX_PRIVATE_STREAM_RECOVERY_ATTEMPTS + 1)
        self.assertEqual(runner.active_slugs, slugs)
        self.assertEqual(runner.private_stream.started_with, list(slugs))
        self.assertGreaterEqual(
            runner.account.fetch_open_orders.await_count,
            MAINTENANCE_STABLE_READS)
        self.assertGreaterEqual(
            runner.account.fetch_positions.await_count,
            MAINTENANCE_STABLE_READS)
        self.assertGreaterEqual(
            runner.account.fetch_balance.await_count,
            MAINTENANCE_STABLE_READS)
        self.assertEqual(client.orders.create_calls, [])
        self.assertTrue(any(
            "maintenance" in message.lower()
            for message, _ in notifier.messages))
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_maintenance_pause_honours_operator_stop(self):
        runner, client, _ = self.build_runner()
        await runner.preflight()
        sleeps = 0

        async def stop_on_first_poll(_seconds):
            nonlocal sleeps
            sleeps += 1
            runner.request_stop("operator_requested")

        runner.sleep = stop_on_first_poll
        stop = await runner._wait_for_venue_recovery(
            "private_stream",
            AdapterError("GET /v1/orders/open failed: InternalServerError"),
        )

        self.assertEqual(stop, "operator_requested")
        self.assertEqual(sleeps, 1)
        self.assertEqual(client.orders.create_calls, [])
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_maintenance_health_requires_three_consecutive_complete_reads(self):
        runner, client, _ = self.build_runner()
        await runner.preflight()
        outage = AdapterError(
            "GET /v1/orders/open failed: ServiceUnavailable")
        runner.account.fetch_open_orders = AsyncMock(
            side_effect=[(), outage, (), (), ()])
        runner.account.fetch_positions = AsyncMock(return_value=())
        runner.account.fetch_balance = AsyncMock(
            wraps=runner.account.fetch_balance)

        stop = await runner._wait_for_venue_recovery(
            "private_stream",
            AdapterError("private WebSocket disconnected"),
        )

        self.assertIsNone(stop)
        # One success was discarded by the intervening failure. The gate then
        # required three new complete order/position/balance passes.
        self.assertEqual(runner.account.fetch_open_orders.await_count, 5)
        self.assertEqual(runner.account.fetch_positions.await_count, 4)
        self.assertEqual(runner.account.fetch_balance.await_count, 4)
        self.assertEqual(client.orders.create_calls, [])
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_maintenance_probe_contract_error_is_not_hidden(self):
        runner, client, _ = self.build_runner()
        await runner.preflight()
        runner.account.fetch_open_orders = AsyncMock(side_effect=AdapterError(
            "open-order response has no orders list"))

        with self.assertRaisesRegex(AdapterError, "no orders list"):
            await runner._wait_for_venue_recovery(
                "private_stream",
                AdapterError(
                    "GET /v1/orders/open failed: InternalServerError"),
            )

        self.assertEqual(client.orders.create_calls, [])
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_startup_waits_in_process_for_maintenance(self):
        runner, client, _ = self.build_runner()
        outage = AdapterError(
            "GET /v1/orders/open failed: InternalServerError")
        runner.preflight = AsyncMock(side_effect=[outage, outage, outage, "TRADE"])

        mode = await runner.preflight_until_ready()

        self.assertEqual(mode, "TRADE")
        self.assertEqual(runner.preflight.await_count, 4)
        self.assertEqual(client.orders.create_calls, [])

    async def test_startup_does_not_call_schema_failure_maintenance(self):
        runner, _, _ = self.build_runner()
        invalid = AdapterError("open-order response has no orders list")
        runner.preflight = AsyncMock(side_effect=invalid)
        runner._wait_for_venue_recovery = AsyncMock()

        with self.assertRaisesRegex(AdapterError, "no orders list"):
            await runner.preflight_until_ready()

        runner._wait_for_venue_recovery.assert_not_awaited()

    async def test_market_reconnect_failure_is_bounded(self):
        runner, client, _ = self.build_runner()
        await runner.preflight()
        runner.market_stream_factory = FailingMarketStream

        stop = await runner._recover_market_stream(
            AdapterError("market WebSocket disconnected"))

        self.assertEqual(
            stop, "runner_failure:market_stream_recovery_exhausted")
        self.assertEqual(
            runner._runtime_error_counts["market_stream"],
            MAX_MARKET_STREAM_RECOVERY_ATTEMPTS,
        )
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

    async def test_a_stale_book_replans_on_the_refresh_it_just_fetched(self):
        """The refresh is only worth fetching if we plan while it is fresh."""

        runner, _client, _ = self.build_runner(clock=TickingClock())
        await runner.preflight()
        slug = runner.market_slug
        state = runner.state_for(slug)

        plans = []

        def plan(_frame):
            plans.append(1)
            if len(plans) == 1:
                return [PlanAction(ActionKind.CRITICAL, slug, "book_stale")]
            return []

        runner.reconciler.plan = plan

        async def refresh(_now, _slug=None):
            state.book_observed_at = runner._now()

        runner._refresh_book = refresh
        # Part-way to the permanent retirement this loop used to guarantee.
        runner._runtime_error_counts[f"strategy_state:{slug}"] = 5

        stop, delay = await runner._decide_for_market(slug, runner._now())

        self.assertIsNone(stop)
        # Planned a second time on THIS pass, against the book just fetched.
        self.assertEqual(len(plans), 2)
        # And conceded no backoff. `_recovery_delay` escalates to a 30s cap,
        # which outlives the five-second freshness budget the refresh had just
        # satisfied -- so the next visit reported `book_stale` again and the
        # refresh was wasted. Measured 2026-08-16: 7,535 book_stale events,
        # consecutive ones 34-75s apart on one market.
        self.assertEqual(delay, 0.0)
        # The success path clears the escalation, so a market that recovers
        # stops marching toward MAX_RECOVERY_ATTEMPTS and permanent retirement.
        self.assertNotIn(
            f"strategy_state:{slug}", runner._runtime_error_counts)
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_a_book_that_will_not_refresh_still_backs_off(self):
        """One extra attempt, not a spin: a dead book must still yield."""

        runner, _client, _ = self.build_runner(clock=TickingClock())
        await runner.preflight()
        slug = runner.market_slug
        state = runner.state_for(slug)

        plans = []

        def plan(_frame):
            plans.append(1)
            return [PlanAction(ActionKind.CRITICAL, slug, "book_stale")]

        runner.reconciler.plan = plan

        async def refresh(_now, _slug=None):
            # The refresh runs but the book never actually arrives.
            state.book_observed_at = runner._now() - timedelta(minutes=5)

        runner._refresh_book = refresh

        stop, delay = await runner._decide_for_market(slug, runner._now())

        self.assertIsNone(stop)
        self.assertEqual(len(plans), 1)
        self.assertGreater(delay, 0.0)
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

    async def test_ordinary_attrition_over_a_long_session_does_not_stop(self):
        """Only a burst is systemic. A lifetime total is just longevity."""

        runner, _, _ = self.build_runner(clock=TickingClock())
        await runner.preflight()

        stop = None
        for index in range(runner.config.max_markets + 1):
            slug = f"market-{index}"
            stop = await runner._retire_market(slug, "stuck")
            # Space the faults an hour apart, the way a day's games kick off.
            # Observed 2026-08-16: 31 markets retired across 7h09m, 466 orders
            # sent, account flat -- and the session stopped anyway, because the
            # count it was measured against could only ever grow.
            entry = runner._retired[slug]
            entry.retired_at -= timedelta(
                hours=runner.config.max_markets - index)

        self.assertIsNone(stop)
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_retirements_after_kickoff_are_not_counted_as_faults(self):
        """A started game retiring its market is the market ending, not a bug."""

        runner, _, _ = self.build_runner(clock=TickingClock())
        await runner.preflight()

        kicked_off = runner._now() - timedelta(minutes=5)
        stop = None
        for index in range(runner.config.max_markets + 1):
            slug = f"market-{index}"
            state = MarketState(slug)
            state.market = SimpleNamespace(game_start=kicked_off)
            runner._markets[slug] = state
            # The exact shape of the 2026-08-16 stop: a synchronized morning
            # slate goes book_stale together, exhausts recovery together, and
            # retires together -- a burst, but not a fault.
            stop = await runner._retire_market(slug, "unrecoverable_book_stale")

        self.assertIsNone(stop)
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_a_retirement_stops_barring_the_market_after_its_cooldown(self):
        """A permanent ban starves a fixed slate; only a cooldown does not."""

        runner, _, _ = self.build_runner(clock=TickingClock())
        await runner.preflight()

        await runner._retire_market("market-one", "unrecoverable_book_stale")
        now = runner._now()
        self.assertIn("market-one", runner._barred_slugs(now))

        # The exit machinery lets go once it proves flat; the cooldown is what
        # still bars reselection after that.
        runner._retired.pop("market-one", None)
        self.assertIn("market-one", runner._barred_slugs(now))

        # `book_stale` describes the book at a moment, not forever. Measured
        # 2026-08-16: twenty markets barred inside the first forty minutes and
        # the quoting slate fell 19 -> 6 with candidates still available.
        later = now + timedelta(seconds=RETIREMENT_COOLDOWN_SECONDS + 1)
        self.assertNotIn("market-one", runner._barred_slugs(later))
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_a_book_fault_is_not_released_the_moment_it_is_flat(self):
        """Flatness does not make an unparseable book parseable."""

        runner, _, _ = self.build_runner(clock=TickingClock())
        await runner.preflight()

        await runner._retire_market("market-one", "unrecoverable_book_stale")
        # Releasing on flat alone re-retired the same market about every 45
        # seconds (chevic-scovla, 2026-08-15). Time resolves these, not flatness.
        runner._release_retired_if_flat("market-one")
        self.assertIn("market-one", runner._retired)

        runner._retired_until["market-one"] = (
            runner._now() - timedelta(seconds=1))
        runner._release_retired_if_flat("market-one")
        self.assertNotIn("market-one", runner._retired)
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_resolved_mutation_retirement_clears_only_its_transient_bar(self):
        runner, _, notifier = self.build_runner(clock=TickingClock())
        await runner.preflight()
        slug = runner.market_slug

        await runner._retire_market(
            slug, "unresolved_mutation_timeout", reset_exit_budget=False)
        self.assertIn(slug, runner._retired_until)

        runner._release_retired_if_flat(slug)

        self.assertNotIn(slug, runner._retired)
        self.assertNotIn(slug, runner._retired_until)
        self.assertFalse(runner._is_barred_from_quoting(slug))
        retirement_messages = [
            message for message, _critical in notifier.messages
            if "unresolved_mutation_timeout" in message
        ]
        self.assertTrue(any(
            "resumes automatically" in message
            for message in retirement_messages))
        self.assertFalse(any(
            "will not quote again this session" in message
            for message in retirement_messages))
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_positioned_mutation_retirement_cannot_release_early(self):
        runner, _, _ = self.build_runner(clock=TickingClock())
        await runner.preflight()
        slug = runner.market_slug
        await runner._retire_market(
            slug, "unresolved_mutation_timeout", reset_exit_budget=False)
        runner.ledger.position = lambda market_slug=None: Decimal("10")

        runner._release_retired_if_flat(slug)

        self.assertIn(slug, runner._retired)
        self.assertIn(slug, runner._retired_until)
        self.assertTrue(runner._is_barred_from_quoting(slug))
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

    async def test_ledger_size_counts_the_wal_sidecar_and_never_raises(self):
        """WAL is not a rounding error: the .db alone understates the disk."""

        runner, _, _ = self.build_runner(clock=TickingClock())
        await runner.preflight()
        path = Path(runner.ledger.path)

        # The ledger runs in WAL mode, so a sidecar already exists -- write a
        # known size over it rather than assuming it starts empty.
        Path(f"{path}-wal").write_bytes(b"x" * 4096)
        self.assertEqual(
            runner._ledger_bytes_on_disk(), path.stat().st_size + 4096)
        # And the sidecar is genuinely counted, not merely tolerated.
        self.assertGreater(
            runner._ledger_bytes_on_disk(), path.stat().st_size)

        # A summary that cannot be sent because a stat failed is worse than a
        # size it could not read.
        runner.ledger.path = Path("no", "such", "ledger.db")
        self.assertEqual(runner._ledger_bytes_on_disk(), 0)
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_shutdown_decisions_are_logged_in_a_parseable_line(self):
        """Format is load-bearing: these lines are read back to diagnose."""

        import contextlib

        runner, _, _ = self.build_runner(clock=TickingClock())
        await runner.preflight()

        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            runner._log_shutdown(
                "market-one", "flatten", position=5, side="offer",
                price=0.41, qty=5, attempt=2,
                # None-valued fields are dropped rather than printed as "None",
                # which would make the line harder to read at 3am.
                seconds_to_kickoff=None)
        line = captured.getvalue().strip()

        self.assertTrue(line.startswith("[shutdown] at="))
        self.assertIn("market=market-one", line)
        self.assertIn("decision=flatten", line)
        self.assertIn("position=5", line)
        self.assertIn("price=0.41", line)
        self.assertIn("attempt=2", line)
        self.assertNotIn("seconds_to_kickoff", line)
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_eligibility_changes_are_logged_with_how_long_they_held(self):
        """The measurement three investigations needed and did not have.

        A bound market resting no order left no trace of why: the runner logs
        what it DID, and a side dropped for ineligibility produces no mutation
        and so no `[action]` line. `price_too_extreme` appears zero times in a
        50MB log for that reason. `held` is the load-bearing field -- it is the
        oscillation period that decides between the candidate fixes for the
        `reward_ineligible` churn, where 87% of cancels come back to the
        identical price.
        """

        import contextlib

        runner, _, _ = self.build_runner()
        await runner.preflight()
        state = runner.state_for(runner.market_slug)

        # First observation establishes a baseline and must stay silent -- a
        # "from=none" line per side per bind is noise, not signal.
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            runner._record_eligibility(state, NOW)
        self.assertEqual(captured.getvalue(), "")

        # No change: still silent. Transitions, not levels -- twenty markets
        # serviced once a second would otherwise add ~40 lines/second.
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            runner._record_eligibility(state, NOW + timedelta(seconds=30))
        self.assertEqual(captured.getvalue(), "")

        # Now it genuinely changes, 45 seconds after the state was entered.
        state.reward_eligible = frozenset({"offer"})
        state.economic_blocker_by_side = {"bid": "outside_reward_cutoff"}
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            runner._record_eligibility(state, NOW + timedelta(seconds=45))
        lines = [l for l in captured.getvalue().splitlines() if l.strip()]

        bid = next(l for l in lines if "side=bid" in l)
        self.assertIn("[eligibility] at=", bid)
        self.assertIn(f"market={state.slug}", bid)
        self.assertIn("to=outside_reward_cutoff", bid)
        # 45s, not 15s: duration runs from when the state was ENTERED, not
        # from the previous observation. Measuring the gap between samples
        # would report the service interval instead of the thing being asked
        # about, and every value would come out ~1s.
        self.assertIn("held=45.0s", bid)
        self.assertIn("resting=", bid)
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_rebinding_a_market_forgets_its_old_eligibility(self):
        """Unbound time must never be reported as time spent holding a state.

        A market dropped by rotation and re-selected an hour later would
        otherwise emit `held=3600s`, putting a fabricated hour into the one
        number this log exists to measure.
        """

        import contextlib

        runner, _, _ = self.build_runner()
        await runner.preflight()
        slug = runner.market_slug
        state = runner.state_for(slug)
        runner._record_eligibility(state, NOW)
        self.assertIn((slug, "bid"), runner._eligibility_state)

        runner._bind_markets(("some-other-market",))
        self.assertNotIn((slug, "bid"), runner._eligibility_state)

        # Re-selected: the first observation is a baseline again, so the stale
        # interval can never be printed.
        runner._bind_markets((slug,))
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            runner._record_eligibility(
                runner.state_for(slug), NOW + timedelta(hours=1))
        self.assertEqual(captured.getvalue(), "")
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_every_refusal_to_evaluate_names_itself(self):
        """"We looked and declined" and "we never had the data" are different.

        These paths returned bare, leaving `economic_blocker_by_side` empty --
        which `_idle_market_reasons` then reports as `no_quote_yet`, the same
        label a market gets when it is simply between cycles. Reporting only;
        nothing here changes what is quoted.
        """

        runner, _, _ = self.build_runner()
        await runner.preflight()
        state = runner.state_for(runner.market_slug)

        book = state.book
        state.book = None
        runner._evaluate_rewards(NOW)
        self.assertEqual(
            set(state.economic_blocker_by_side.values()), {"no_book"})

        state.book = book
        state.incentive = None
        runner._evaluate_rewards(NOW)
        self.assertEqual(
            set(state.economic_blocker_by_side.values()), {"no_incentive"})
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
        self.assertIn(f"{'Positions':<19}{1:>10}", rendered)
        # Reported as resting/managed now: managed alone said nothing about
        # whether those markets were earning.
        # Quoting is now reported against slot CAPACITY, not against the
        # number of bound markets: "0/2 bound" hid the two dead slots that
        # "0/8 of capacity" makes obvious.
        self.assertIn(f"{'Quoting':<19}{'0/8':>10}", rendered)
        self.assertIn(f"{'  stuck, look at it':<19}{2:>10}", rendered)
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_shutdown_drains_buffered_terminal_cancel_proof(self):
        """A stopped consumer must still apply proof already on its socket."""

        runner, client, _ = self.build_runner(clock=TickingClock())
        await runner.preflight()
        runner.config = replace(runner.config, max_shutdown_seconds=15)
        runner.sleep = AsyncMock()
        slug = runner.market_slug
        runner.ledger.apply_order_event(
            "ws", "shutdown-open",
            OrderState(
                client_order_id="liquidity-shutdown",
                exchange_order_id="exchange-shutdown",
                market_slug=slug,
                side="ORDER_SIDE_BUY",
                price=Decimal("0.49"),
                original_qty=Decimal("5"),
                filled_qty=Decimal("0"),
                status="OPEN",
                event_at=runner._now()),
            received_at=runner._now())
        runner.ledger.prepare_intent(
            "shutdown-cancel", "CANCEL", slug, "liquidity-shutdown",
            {"orderId": "exchange-shutdown"}, created_at=runner._now())
        runner.ledger.transition_intent(
            "shutdown-cancel", "SUBMITTING", updated_at=runner._now())
        runner.ledger.transition_intent(
            "shutdown-cancel", "ACKNOWLEDGED", updated_at=runner._now(),
            exchange_order_id="exchange-shutdown")
        client.orders.rows["exchange-shutdown"] = {
            "id": "exchange-shutdown",
            "clientOrderId": "liquidity-shutdown",
            "marketSlug": slug,
            "side": "ORDER_SIDE_BUY",
            "intent": "ORDER_INTENT_BUY_LONG",
            "price": {"value": "0.49", "currency": "USD"},
            "quantity": "5",
            "cumQuantity": "0",
            "state": "ORDER_STATE_CANCELED",
            "createTime": runner._now().isoformat(),
        }

        async def lagging_retrieve(_order_id):
            raise AdapterError(
                "GET /v1/order/exchange-shutdown failed: NotFoundError")

        client.orders.retrieve = lagging_retrieve
        buffered = [PrivateEvent(
            PrivateEventKind.ORDER_UPDATE,
            {"execution": {
                "id": "shutdown-cancel-proof",
                "type": "EXECUTION_TYPE_CANCEL",
                "order": {
                    "id": "exchange-shutdown",
                    "marketSlug": slug,
                    "intent": "ORDER_INTENT_BUY_LONG",
                    "side": "ORDER_SIDE_BUY",
                    "state": "ORDER_STATE_CANCELED",
                    "price": {"value": "0.49", "currency": "USD"},
                    "quantity": "5",
                    "cumQuantity": "0",
                    "updateTime": runner._now().isoformat(),
                },
            }},
            "buffered-shutdown-cancel")]

        def drain_events():
            drained = tuple(buffered)
            buffered.clear()
            return drained

        runner.private_stream.drain_events = drain_events

        await runner._shutdown("operator_sigterm")

        self.assertTrue(runner._shutdown_proved_flat)
        self.assertEqual(runner.ledger.unresolved_intents(slug), [])
        self.assertEqual(runner.ledger.working_orders(slug), [])
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_flat_abandoned_market_stops_refreshing_before_kickoff(self):
        """Round ten cannot plan again, so a flat slot cannot use a book."""

        runner, client, _ = self.build_runner(clock=TickingClock())
        await runner.preflight()
        slug = runner.market_slug
        runner.state_for(slug).market = SimpleNamespace(
            game_start=runner._now() + timedelta(hours=2))
        for _ in range(MAX_RETIREMENT_ROUNDS):
            await runner._retire_market(slug, "unrecoverable_book_stale")
        runner._retired[slug].rounds = MAX_RETIREMENT_ROUNDS

        self.assertTrue(runner._book_refresh_would_be_pointless(
            slug, runner._now()))
        before = client.markets.book_calls
        due = runner._now() - timedelta(seconds=1)
        next_due = await runner._refresh_quiet_book_if_due(due, slug)
        self.assertEqual(client.markets.book_calls, before)
        self.assertGreater(next_due, runner._now())
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_ambiguous_abandoned_market_keeps_its_book_refresh(self):
        runner, _, _ = self.build_runner(clock=TickingClock())
        await runner.preflight()
        slug = runner.market_slug
        runner.state_for(slug).market = SimpleNamespace(
            game_start=runner._now() + timedelta(hours=2))
        for _ in range(MAX_RETIREMENT_ROUNDS):
            await runner._retire_market(slug, "unrecoverable_book_stale")
        runner._retired[slug].rounds = MAX_RETIREMENT_ROUNDS
        runner.ledger.prepare_intent(
            "abandoned-ambiguous", "CANCEL", slug,
            "liquidity-ambiguous", {}, created_at=runner._now())

        self.assertFalse(runner._book_refresh_would_be_pointless(
            slug, runner._now()))
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_daily_score_write_failure_cannot_break_summary_or_shutdown(self):
        runner, _, notifier = self.build_runner(clock=TickingClock())
        await runner.preflight()
        now = runner._now()
        runner._score_by_side = {"bid": 2.0}
        runner._score_observed_at = now - timedelta(seconds=10)

        with patch.object(
                runner.ledger, "add_score_seconds",
                side_effect=RuntimeError("score disk write failed")):
            # Must not raise: shutdown calls the same method after proving the
            # account flat, so telemetry failure cannot strand the process.
            await runner._send_summary(now)

        self.assertTrue(notifier.payloads)
        self.assertAlmostEqual(
            runner._daily_score_pending[risk_day(now)], 20.0)
        self.assertEqual(
            runner._runtime_error_counts["daily_score_persistence"], 1)
        slots = next(
            field["value"]
            for field in notifier.payloads[-1][0]["embeds"][0]["fields"]
            if "Slots" in field["name"])
        self.assertIn("Resting side-hours", slots)
        self.assertIn("0.00", slots)
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_live_summary_uses_seven_day_table_resting_hours(self):
        runner, _, notifier = self.build_runner(clock=TickingClock())
        await runner.preflight()

        with patch(
                "liquidity_bot.live_commissioning.resting_side_hours_by_day",
                return_value=({risk_day(runner._now()): 416.65}, {})):
            await runner._send_summary(runner._now())

        slots = next(
            field["value"]
            for field in notifier.payloads[-1][0]["embeds"][0]["fields"]
            if "Slots" in field["name"])
        self.assertIn("Resting side-hours", slots)
        self.assertIn("416.65", slots)
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_resting_hours_read_failure_cannot_break_summary(self):
        runner, _, notifier = self.build_runner(clock=TickingClock())
        await runner.preflight()

        with patch(
                "liquidity_bot.live_commissioning.resting_side_hours_by_day",
                side_effect=RuntimeError("read-only resting report failed")):
            await runner._send_summary(runner._now())

        self.assertEqual(
            runner._runtime_error_counts["resting_time_summary"], 1)
        slots = next(
            field["value"]
            for field in notifier.payloads[-1][0]["embeds"][0]["fields"]
            if "Slots" in field["name"])
        self.assertIn("Resting side-hours", slots)
        self.assertIn("0.00", slots)
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_summary_names_retired_markets(self):
        runner, _, notifier = self.build_runner(clock=TickingClock())
        await runner.preflight()
        slug = runner.market_slug
        await runner._retire_market(slug, "hard_exit_attempt_limit_exceeded")

        await runner._send_summary(runner._now())

        # A silently retired slot looks identical to an idle one otherwise.
        # This moved out of the footer and into the rewards block, where the
        # idle markets are broken down by reason -- the footer had grown to one
        # unreadable line and nobody was reading it.
        rewards = [f for f in notifier.payloads[-1][0]["embeds"][0]["fields"]
                   if "reward" in f["name"].lower()][0]["value"]
        # A retired slot is a BROKEN slot: bound, resting nothing, and not
        # releasable to another market. It is counted, and the reason is
        # named underneath, so "are we at capacity or is something stuck?"
        # is answerable from the message.
        self.assertIn("stuck, look at it", rewards)
        self.assertIn("retired", rewards)
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_the_summary_survives_a_market_it_cannot_inspect(self):
        """A reporting metric must never keep a flat process from stopping.

        `_send_summary` is called from the shutdown loop, where an exception
        reads as a failure to prove flat -- which exits 3, the one code the
        supervisor never restarts. `_idle_market_reasons` has guarded each
        market for this reason since it was written; `_awaiting_rotation` was
        added on 2026-08-18 without the same guard, which would have made a
        single unreadable market able to strand the bot.
        """

        runner, _, notifier = self.build_runner()
        await runner.preflight()
        runner._bind_markets(("market-one", "market-two"))

        boom = RuntimeError("market state is unreadable")
        original = runner._is_retired

        def explode(slug):
            if slug == "market-two":
                raise boom
            return original(slug)

        runner._is_retired = explode

        # Must not raise, and must still report the market it could read.
        await runner._send_summary(runner._now())

        self.assertTrue(notifier.payloads, "the summary must still be sent")
        slots = [f for f in notifier.payloads[-1][0]["embeds"][0]["fields"]
                 if "Slots" in f["name"]][0]["value"]
        self.assertIn("stuck, look at it", slots)
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_recovery_spans_every_market_that_holds_state(self):
        """Observed 2026-08-14T23:03Z: down until an operator noticed.

        Leftover state in more than one market raised CommissioningRefused,
        which exits 2 and is never restarted. Written when the runner held one
        market at a time; with eight quoting, state in several markets after a
        hard stop is the expected case.
        """

        runner, _, _ = self.build_runner(clock=TickingClock())
        await runner.preflight()
        runner.config = replace(runner.config, max_markets=8)
        for index, slug in enumerate(("market-one", "market-two")):
            runner.ledger.apply_order_event(
                "ws", f"left-behind-{index}",
                OrderState(
                    client_order_id=f"liquidity-left-{index}",
                    exchange_order_id=f"exchange-left-{index}",
                    market_slug=slug,
                    side="ORDER_SIDE_BUY",
                    price=Decimal("0.49"),
                    original_qty=Decimal("5"),
                    filled_qty=Decimal("0"),
                    status="OPEN",
                    event_at=runner._now()),
                received_at=runner._now())

        found = runner._identified_recovery_markets()

        self.assertEqual(found, ("market-one", "market-two"))
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_recovery_still_refuses_more_markets_than_authorized(self):
        runner, _, _ = self.build_runner(clock=TickingClock())
        await runner.preflight()
        for index in range(runner.config.max_markets + 1):
            runner.ledger.apply_order_event(
                "ws", f"too-many-{index}",
                OrderState(
                    client_order_id=f"liquidity-many-{index}",
                    exchange_order_id=f"exchange-many-{index}",
                    market_slug=f"market-{index}",
                    side="ORDER_SIDE_BUY",
                    price=Decimal("0.49"),
                    original_qty=Decimal("5"),
                    filled_qty=Decimal("0"),
                    status="OPEN",
                    event_at=runner._now()),
                received_at=runner._now())

        # State this session could not have created is not ours to retire.
        with self.assertRaisesRegex(CommissioningRefused, "above the authorized"):
            runner._identified_recovery_markets()
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

    async def test_losing_a_race_with_our_own_placement_cancels_nothing(self):
        """The loop the operator watched: place, cancel 1.5 seconds later.

        A just-sent order is not yet visible as working, so the next pass
        tries to place again; place() refuses because the market has an
        unresolved intent; and quarantining in response cancels the very order
        that caused the refusal. Measured live: PLACE 2597 against CANCEL
        2596, with no planner ever asking for a cancel.
        """

        runner, client, _ = self.build_runner()
        await runner.preflight()
        slug = runner.market_slug
        runner.ledger.apply_order_event(
            "ws", "just-placed",
            OrderState(
                client_order_id="liquidity-fresh",
                exchange_order_id="exchange-fresh",
                market_slug=slug,
                side="ORDER_SIDE_BUY",
                price=Decimal("0.49"),
                original_qty=Decimal("5"),
                filled_qty=Decimal("0"),
                status="OPEN",
                event_at=runner._now()),
            received_at=runner._now())
        before = len(client.orders.cancel_calls)

        stop = await runner._handle_action_failure(
            OrderOutcomeUncertain("market has an unresolved order intent"),
            slug)

        self.assertIsNone(stop)
        self.assertEqual(
            len(client.orders.cancel_calls), before,
            "racing our own placement must not cancel the resting quote")

    async def test_a_real_adapter_failure_still_quarantines(self):
        runner, client, _ = self.build_runner()
        await runner.preflight()

        stop = await runner._handle_action_failure(
            AdapterError("order gateway is unreachable"), runner.market_slug)

        # Genuine transport failure still retires quotes before resuming.
        self.assertIsNone(stop)

    async def test_a_stale_book_does_not_cancel_a_resting_quote(self):
        """What the operator watched live: place, then cancel two seconds on.

        book_stale is recoverable, and recovery refreshed the book and then
        quarantined anyway -- and quarantine cancels every working order.
        Measured 2026-08-14: 226 book_stale events in six minutes against 45
        placements, so nearly every quote died before it could rest, and
        reward is paid for resting time.

        A stale book means we cannot make a NEW decision. It does not make an
        already-resting post-only order unsafe.
        """

        runner, client, _ = self.build_runner()
        await runner.preflight()
        slug = runner.market_slug
        runner.ledger.apply_order_event(
            "ws", "resting-quote",
            OrderState(
                client_order_id="liquidity-resting",
                exchange_order_id="exchange-resting",
                market_slug=slug,
                side="ORDER_SIDE_BUY",
                price=Decimal("0.49"),
                original_qty=Decimal("5"),
                filled_qty=Decimal("0"),
                status="OPEN",
                event_at=runner._now()),
            received_at=runner._now())
        before = len(client.orders.cancel_calls)

        await runner._recover_strategy_state("book_stale", slug)

        self.assertEqual(
            len(client.orders.cancel_calls), before,
            "a stale book must not cancel a quote that is already resting")

    async def test_a_condition_that_never_recovers_retires_the_market(self):
        """Measured 2026-08-14: 371 consecutive book_stale recoveries.

        Each failed on `book is not two-sided` -- 3,416 across the session.
        Thin books here are routinely one-sided (FINDINGS 5 records bid 23,388
        against ask 1.0) and parse_book_response rejects them, so the market
        could never refresh, never quoted, and held a slot while paging
        Discord on every attempt. A "recoverable" condition that never
        recovers is not recoverable.
        """

        runner, _, _ = self.build_runner(clock=TickingClock())
        await runner.preflight()
        slug = runner.market_slug
        runner._runtime_error_counts[f"strategy_state:{slug}"] = (
            MAX_RECOVERY_ATTEMPTS)

        stop = await runner._handle_strategy_critical("book_stale", slug)

        self.assertIsNone(stop, "one bad book must not end the session")
        self.assertTrue(runner._is_retired(slug))
        self.assertEqual(
            runner._retired[slug].reason, "unrecoverable_book_stale")
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_a_recoverable_condition_still_gets_its_retries(self):
        runner, _, _ = self.build_runner(clock=TickingClock())
        await runner.preflight()
        slug = runner.market_slug
        runner._runtime_error_counts[f"strategy_state:{slug}"] = 1

        self.assertIsNone(
            await runner._handle_strategy_critical("book_stale", slug))
        self.assertFalse(
            runner._is_retired(slug),
            "a transient stale book must recover, not retire on first sight")
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
        # Shutdown only crosses out inside the kickoff window now;
        # outside it a position is parked on its resting exit.
        self._force_kickoff_window(runner)
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

    def _force_kickoff_window(self, runner):
        """Put the bound market inside the pre-kickoff flatten window."""
        runner.market = replace(
            runner.market, game_start=NOW + timedelta(seconds=60))

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

    async def _parking_runner(self, exit_side, extra_orders=()):
        """A runner holding a short of 5 with orders resting, far from kickoff."""
        runner, client, _ = self.build_runner(clock=TickingClock())
        await runner.preflight()
        slug = runner.config.market_slug
        client.portfolio.rows = {slug: {
            "netPosition": "-5",
            "netPositionDecimal": "-5.0000",
            "updateTime": NOW.isoformat(),
        }}
        rows = [("resting-exit", exit_side)] + list(extra_orders)
        for order_id, side in rows:
            client.orders.rows[order_id] = {
                "id": order_id, "marketSlug": slug, "side": side,
                "intent": ("ORDER_INTENT_BUY_LONG" if side == "ORDER_SIDE_BUY"
                           else "ORDER_INTENT_BUY_SHORT"),
                "price": {"value": "0.65", "currency": "USD"},
                "quantity": "5", "cumQuantity": "0",
                "state": "ORDER_STATE_NEW", "createTime": NOW.isoformat(),
            }
        # The flat shortcuts would return before the per-market branch runs.
        runner._venue_proves_flat = lambda: False
        runner._whole_account_safe_to_stop = lambda: False
        runner.config = replace(runner.config, max_shutdown_seconds=30)
        return runner, client, slug

    async def test_a_repeated_book_fault_does_not_escalate_to_abandonment(self):
        """The round counter bounds exit attempts, not book-parse failures.

        The book-refresh error counter never resets, so every later failure
        re-entered _retire_market and bumped the round -- 1, 2, 4, 8, then
        abandonment -- for a market already retired and quoting nothing.
        Measured 2026-08-15: 50 markets retired and 30 abandoned in a single
        session, past the max_markets limit that stops the session outright.
        """
        runner, _client, _ = self.build_runner(clock=TickingClock())
        await runner.preflight()
        slug = runner.config.market_slug

        for _ in range(12):
            await runner._retire_market(slug, "unrecoverable_book_stale")

        self.assertEqual(runner._retired[slug].rounds, 1)
        self.assertNotIn("abandoned", str(runner._retired[slug].reason))
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_repeated_exit_failures_do_still_escalate(self):
        """Exit retirements must keep counting -- that bound is the point."""
        runner, _client, _ = self.build_runner(clock=TickingClock())
        await runner.preflight()
        slug = runner.config.market_slug

        for _ in range(3):
            await runner._retire_market(slug, "hard_exit_retry_window_exceeded")

        self.assertEqual(runner._retired[slug].rounds, 3)
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_an_unquotable_market_is_not_released_just_for_being_flat(self):
        """Being flat does not fix a book that will not parse.

        Observed 2026-08-15 on chevic-scovla: retired, released=flat, retired
        again, every ~45 seconds. Releasing hands the slot back to the same
        fault, which immediately retires it.
        """
        runner, _client, _ = self.build_runner(clock=TickingClock())
        await runner.preflight()
        slug = runner.config.market_slug

        await runner._retire_market(slug, "unrecoverable_book_stale")
        self.assertIn(slug, runner._retired)

        # Flat, no orders, no intents -- the release conditions are all met.
        runner._release_retired_if_flat(slug)

        # Still retired. _is_barred_from_quoting reads _retired_ever, which is
        # never cleared, so asserting on it would pass either way.
        self.assertIn(slug, runner._retired)
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_an_exit_retirement_is_still_released_when_flat(self):
        """The release exists for retirements that being flat does resolve."""
        runner, _client, _ = self.build_runner(clock=TickingClock())
        await runner.preflight()
        slug = runner.config.market_slug

        await runner._retire_market(slug, "hard_exit_retry_window_exceeded")
        runner._release_retired_if_flat(slug)

        self.assertNotIn(slug, runner._retired)
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_a_book_that_never_parses_retires_its_market(self):
        """An unquotable market must not hold a slot forever.

        `book is not two-sided` was the most common error in the log at 2,121
        occurrences across 21 of 77 markets, two of them retried about 1,100
        times each at three-second intervals. The market can never quote, so
        the retries buy nothing and spend REST budget.
        """
        runner, client, _ = self.build_runner(clock=TickingClock())
        await runner.preflight()
        slug = runner.config.market_slug

        async def unparseable(*_args, **_kwargs):
            raise AdapterError("book is not two-sided")

        runner._refresh_quiet_book_if_due = unparseable

        for _ in range(MAX_RECOVERY_ATTEMPTS + 1):
            await runner._service_market_state(slug, runner._now())

        self.assertTrue(runner._is_barred_from_quoting(slug))

    async def test_a_fill_only_the_venue_saw_still_reaches_the_stop(self):
        """A closing fill lost by the WebSocket must not hide a loss.

        Measured 2026-08-15: three closing fills were never written, because a
        shutdown IOC returns before its match completes and the fill then
        arrives on a socket shutdown is closing. realized_pnl read a $1.55 loss
        as a $3.26 gain, and the $40 stop cannot fire on what it cannot see.
        """
        runner, client, _ = self.build_runner(clock=TickingClock())
        await runner.preflight()
        slug = runner.config.market_slug

        def execution(exec_id, side, intent, px, qty):
            return {
                "id": exec_id, "tradeId": exec_id, "aggressor": True,
                "type": "EXECUTION_TYPE_FILL",
                "lastPx": {"value": px, "currency": "USD"},
                "lastShares": qty,
                "commissionNotionalCollected": {
                    "value": "0.0000", "currency": "USD"},
                "transactTime": NOW.isoformat(),
                "order": {
                    "id": f"order-{exec_id}", "marketSlug": slug,
                    "side": side, "intent": intent,
                    "price": {"value": px, "currency": "USD"},
                    "quantity": qty, "cumQuantity": qty,
                    "state": "ORDER_STATE_FILLED",
                    "createTime": NOW.isoformat(),
                },
            }

        # Claim both orders as ours, the way an acknowledged intent does.
        for oid, price in (("e1", "0.65"), ("e2", "0.96")):
            runner.ledger.prepare_intent(
                f"intent-{oid}", "PLACE", slug, f"client-{oid}",
                {"marketSlug": slug, "price": {"value": price},
                 "quantity": 5}, created_at=NOW)
            runner.ledger.transition_intent(
                f"intent-{oid}", "SUBMITTING", updated_at=NOW)
            runner.ledger.transition_intent(
                f"intent-{oid}", "ACKNOWLEDGED", updated_at=NOW,
                exchange_order_id=f"order-{oid}")
            client.orders.rows[f"order-{oid}"] = {
                "id": f"order-{oid}", "marketSlug": slug,
                "side": "ORDER_SIDE_BUY",
                "intent": "ORDER_INTENT_BUY_LONG",
                "price": {"value": price, "currency": "USD"},
                "quantity": "5", "cumQuantity": "5",
                "state": "ORDER_STATE_FILLED",
                "createTime": NOW.isoformat(),
            }

        # Sold 5 at 0.65, bought back at 0.96. Only the venue ever saw either.
        client.portfolio.trades = [
            {"id": "t1", "isAggressor": True, "aggressorExecution": execution(
                "e1", "ORDER_SIDE_SELL", "ORDER_INTENT_BUY_SHORT", "0.65", "5")},
            {"id": "t2", "isAggressor": True, "aggressorExecution": execution(
                "e2", "ORDER_SIDE_BUY", "ORDER_INTENT_BUY_LONG", "0.96", "5")},
        ]

        await runner._rest_reconcile(runner._now())

        # Both intents are PURCHASES: 5 x 0.65 + 5 x 0.96 = 8.05 paid for five
        # pairs that settle at 5.00, so -3.05. The old expectation of -1.55
        # came from reading BUY_SHORT as a sale, which is the defect -- the
        # venue's ORDER_SIDE_SELL label says which outcome is taken, not which
        # way our cash moves.
        self.assertAlmostEqual(
            float(runner.ledger.realized_pnl(slug)), -3.05, places=2)
        # Replaying is idempotent -- dedupe is on execution_id.
        await runner._rest_reconcile(runner._now())
        self.assertAlmostEqual(
            float(runner.ledger.realized_pnl(slug)), -3.05, places=2)

    async def test_replaying_a_fill_after_a_schema_change_is_not_a_conflict(self):
        """Adding a field to Execution must not break the back-fill.

        _accept_event dedupes by hashing the whole payload, so adding `outcome`
        made a replay of an already-recorded fill look like a conflicting
        rewrite. That aborted the reconcile sweep, left the position
        unprovable, and the runner exited non-flat and refused to restart.
        Observed 2026-08-15T21:24Z. The execution id is the identity -- if we
        hold it, we hold it.
        """
        runner, _client, _ = self.build_runner(clock=TickingClock())
        await runner.preflight()
        slug = runner.config.market_slug
        recorded = Execution(
            execution_id="dupe", market_slug=slug, side="ORDER_SIDE_BUY",
            price=Decimal("0.40"), qty=Decimal("5"), fee=Decimal("0"),
            event_at=NOW, intent="ORDER_INTENT_BUY_LONG")
        runner.ledger.apply_execution(recorded, received_at=NOW)

        # The same execution carrying a field it did not have before, followed
        # by a genuinely new one. The new one proves the sweep CONTINUED --
        # aborting on the conflict is what left the position unprovable.
        fresh = replace(recorded, execution_id="fresh", price=Decimal("0.41"))

        async def replay(*, observed_at):
            return (replace(recorded, outcome="Some Outcome"), fresh)

        runner.account.fetch_executions = replay

        await runner._rest_reconcile(runner._now())

        recorded_ids = {
            row["execution_id"] for row in
            runner.ledger.query("SELECT execution_id FROM execution_fills")}
        self.assertEqual(recorded_ids, {"dupe", "fresh"})
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_only_our_leg_of_a_trade_is_recorded(self):
        """A trade has two legs and the venue reports both. `isAggressor` says
        which is ours.

        Recording both made every trade appear twice -- once as our fill and
        once as the counterparty's. Observed 2026-08-15 on aec-valorant-ff-ep:
        SELL 5 @ 0.39 and BUY 5 @ 0.39 at the same instant against a real
        position of +5. Offsetting phantom fills make realised P&L read flat on
        a market holding risk, which is the number the $40 stop trusts.

        Ownership is NOT decided by whether we recognise the order id. That was
        the old rule, and it discarded our own fills whenever we held no order
        record -- an operator's manual close, or an order whose record was lost.
        """
        runner, client, _ = self.build_runner(clock=TickingClock())
        await runner.preflight()
        slug = runner.config.market_slug
        # We are the PASSIVE side here, which is the normal case for a market
        # maker and the case the old parser could not see at all: it looked for
        # `makerExecutions`, a key the venue does not publish.
        client.portfolio.trades = [{
            "id": "t1",
            "isAggressor": False,
            "aggressorExecution": {
                "id": "theirs", "tradeId": "t1",
                "type": "EXECUTION_TYPE_FILL",
                "lastPx": {"value": "0.39", "currency": "USD"},
                "lastShares": "5",
                "commissionNotionalCollected": {
                    "value": "0.0000", "currency": "USD"},
                "transactTime": NOW.isoformat(),
                "order": {
                    "id": "their-order", "marketSlug": slug,
                    "side": "ORDER_SIDE_BUY",
                    "intent": "ORDER_INTENT_BUY_LONG",
                    "price": {"value": "0.39", "currency": "USD"},
                    "quantity": "5", "cumQuantity": "5",
                    "state": "ORDER_STATE_FILLED",
                    "createTime": NOW.isoformat()},
            },
            "passiveExecution": {
                "id": "ours", "tradeId": "t1",
                "type": "EXECUTION_TYPE_FILL",
                "lastPx": {"value": "0.61", "currency": "USD"},
                "lastShares": "5",
                "commissionNotionalCollected": {
                    "value": "0.0000", "currency": "USD"},
                "transactTime": NOW.isoformat(),
                "order": {
                    "id": "our-order", "marketSlug": slug,
                    "side": "ORDER_SIDE_BUY",
                    "intent": "ORDER_INTENT_BUY_SHORT",
                    "price": {"value": "0.61", "currency": "USD"},
                    "quantity": "5", "cumQuantity": "5",
                    "state": "ORDER_STATE_FILLED",
                    "createTime": NOW.isoformat()},
            },
        }]

        await runner._rest_reconcile(runner._now())

        fills = runner.ledger.query("SELECT * FROM execution_fills")
        self.assertEqual(len(fills), 1, "exactly one leg of a trade is ours")
        self.assertEqual(fills[0]["execution_id"], "ours")
        self.assertNotIn("theirs", {row["execution_id"] for row in fills})

    async def test_a_position_outside_the_selected_set_is_still_serviced(self):
        """A position must not lose its manager when selection moves on.

        active_slugs answered two different questions -- what to quote and what
        to manage -- so a market dropped by rotation was never visited again.
        The passive reducer, the inventory timer and the pre-kickoff flatten
        all iterate the serviced set, so none of them ran, and no exit order
        was ever placed. Rotation also cancelled the exit already resting,
        leaving position kept and exit gone.

        Observed 2026-08-15: aec-wta-kamrak-marsak held 5 contracts from the
        PREVIOUS DAY, through many restarts, with no exit and no attempt at
        one. Nothing refused it; nothing looked at it.
        """
        runner, client, _ = self.build_runner(
            auto=True, reward_selector=FakeRewardSelector(
                fake_selection("market-one")), clock=TickingClock())
        await runner.preflight()
        runner._bind_markets(["market-one"])

        # A position in a market selection did NOT choose.
        client.portfolio.rows = {"market-two": {
            "netPosition": "5", "netPositionDecimal": "5.0000",
            "updateTime": NOW.isoformat()}}
        await runner._rest_reconcile(runner._now())

        self.assertNotIn("market-two", runner.active_slugs)
        self.assertIn("market-two", runner.managed_slugs())
        # Serviced, but never quotable -- we are leaving, not building.
        self.assertTrue(runner._is_barred_from_quoting("market-two"))
        self.assertFalse(runner._is_barred_from_quoting("market-one"))
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_shutdown_parks_a_position_on_its_resting_exit(self):
        """A redeploy must not liquidate a position at the market.

        Measured 2026-08-15: short 5 at 0.65 with a break-even reducer resting.
        Shutdown cancelled every order, then crossed out at 0.96 because that
        path has no price cap anywhere -- -$1.55 on a position that cost
        nothing to hold, with kickoff hours away and the next process ready to
        pick the exit straight back up.
        """
        # Short, so the exit is a BUY. Nothing resting can add to the position.
        runner, client, _slug = await self._parking_runner("ORDER_SIDE_BUY")

        await runner._shutdown("operator_sigterm")

        self.assertEqual(client.orders.create_calls, [])
        self.assertEqual(client.orders.cancel_calls, [])
        self.assertTrue(runner._shutdown_proved_flat)

    async def test_parking_one_market_does_not_excuse_another(self):
        """Only the market holding the position is left alone.

        Every other market must still be flat, orderless and resolved before
        shutdown will report itself done. A second market still holding risk
        keeps the shutdown loop working rather than walking away from it.
        """
        runner, client, slug = await self._parking_runner("ORDER_SIDE_BUY")
        runner._bind_markets([slug, "market-two"])
        client.portfolio.rows["market-two"] = {
            "netPosition": "-5",
            "netPositionDecimal": "-5.0000",
            "updateTime": NOW.isoformat(),
        }
        await runner._rest_reconcile(runner._now())

        self.assertNotEqual(runner.ledger.position("market-two"), 0)
        self.assertFalse(runner._only_parked_positions_remain([slug]))
        # And the parked one alone is still a safe place to stop.
        self.assertTrue(
            runner._only_parked_positions_remain([slug, "market-two"]))

    async def test_a_position_with_no_resting_exit_is_not_treated_as_parked(self):
        """Parking means the exit is there. With none, it is stranded.

        Observed 2026-08-15 on aec-valorant-ff-ep: +5 held with no order,
        shutdown reported it parked, exited non-flat, and the supervisor
        refused to restart -- leaving the position with no way out and the bot
        down. Fall through to the flatten instead.
        """
        runner, client, _slug = await self._parking_runner("ORDER_SIDE_BUY")
        # Same position, but nothing resting on it.
        client.orders.rows.clear()
        await runner._rest_reconcile(runner._now())

        await runner._shutdown("operator_sigterm")

        # It attempted an exit rather than walking away calling it parked.
        self.assertTrue(client.orders.create_calls)
        self.assertEqual(
            client.orders.create_calls[0]["tif"],
            "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL")

    async def test_shutdown_still_pulls_an_order_that_could_add(self):
        """Parking protects the exit, never a quote that grows the position."""
        # Short, so a SELL would add to it. That must not be left resting.
        runner, client, _slug = await self._parking_runner(
            "ORDER_SIDE_BUY", extra_orders=[("builder", "ORDER_SIDE_SELL")])

        await runner._shutdown("operator_sigterm")

        # Not parked: an add-side order means this market is not in a safe
        # state to walk away from, so shutdown must not report itself done.
        self.assertFalse(runner._shutdown_proved_flat)

    async def test_shutdown_flattens_on_its_own_budget_after_runtime_ceiling(self):
        runner, client, _ = self.build_runner(clock=TickingClock())
        await runner.preflight()
        # Shutdown only crosses out inside the kickoff window now;
        # outside it a position is parked on its resting exit.
        self._force_kickoff_window(runner)
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
        # Shutdown only crosses out inside the kickoff window now;
        # outside it a position is parked on its resting exit.
        self._force_kickoff_window(runner)
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
        # Shutdown only crosses out inside the kickoff window now;
        # outside it a position is parked on its resting exit.
        self._force_kickoff_window(runner)
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

    def test_an_unresolved_mutation_stop_restarts_but_nothing_else_does(self):
        """2026-08-18: three hours down for a 131-second venue stall.

        The venue accepted a place, returned an exchange id, then served no
        state for it for over two minutes. The intent cannot resolve in that
        window by design -- absence is never cancellation -- so the 60s
        critical stop fired on a flat, healthy account. It exited 0, which the
        supervisor reads as a clean operator stop, so nothing restarted it.

        This one stop is a venue-latency artefact and restarts. Every other
        risk stop is a statement about the account and must not.
        """

        unresolved = ("risk_stop:critical failure: order mutation unresolved "
                      "for more than 60 seconds")
        self.assertEqual(self._exit_code_for(unresolved), 5)

        # Still never restarts while inventory is unproven.
        self.assertEqual(self._exit_code_for(unresolved, flat=False), 3)

        # A loss stop in the same breath makes it terminal again. `blockers()`
        # are comma-joined, so a comma means something else tripped too, and
        # restarting into a loss stop is the one thing this must never do.
        self.assertEqual(
            self._exit_code_for(unresolved + ",session loss limit reached"), 0)

        # Neighbouring risk stops are untouched.
        for reason in ("risk_stop:session loss limit reached",
                       "risk_stop:market loss limit reached",
                       "risk_stop:session duration limit reached",
                       "risk_stop:critical failure: something else entirely"):
            self.assertEqual(
                self._exit_code_for(reason), 0,
                f"{reason} must not restart")

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
            "--quote-size", "7",
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
        self.assertEqual(args.quote_size, 7)

    def test_cli_rejects_quote_size_outside_the_reviewed_range(self):
        argv = [
            "live_commissioning",
            "--auto-select-rewards",
            "--quote-size", "4",
        ]
        with patch("sys.argv", argv), self.assertRaises(SystemExit) as error:
            main()
        self.assertEqual(error.exception.code, 2)

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
            quote_size=5,
            lock_file=Path("unused.lock"),
            database=Path("unused.db"),
        )
        with patch("liquidity_bot.live_commissioning.load_runtime_secrets") as load:
            with self.assertRaisesRegex(CommissioningRefused, "arming"):
                await _run(args)
        load.assert_not_called()


if __name__ == "__main__":
    unittest.main()


class MarketTopUpTests(unittest.IsolatedAsyncioTestCase):
    """Reclaim slots held by kicked-off markets without disturbing the rest."""

    build_runner = LiveCommissioningTests.build_runner

    async def test_a_kicked_off_flat_market_is_releasable(self):
        runner, _, _ = self.build_runner(clock=TickingClock())
        await runner.preflight()
        slug = runner.market_slug
        state = runner.state_for(slug)
        state.market = SimpleNamespace(
            game_start=runner._now() - timedelta(minutes=45))

        # Exactly the 2026-08-16 state: four markets 45 minutes past kickoff,
        # still serviced every ~70s, never quotable again.
        self.assertTrue(runner._expired_and_flat(slug, runner._now()))
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_a_kicked_off_market_holding_a_position_is_never_released(self):
        """Releasing it would strand the position with nothing working its exit."""

        from liquidity_bot.execution_ledger import PositionState

        runner, _, _ = self.build_runner(clock=TickingClock())
        await runner.preflight()
        slug = runner.market_slug
        state = runner.state_for(slug)
        state.market = SimpleNamespace(
            game_start=runner._now() - timedelta(minutes=45))
        runner.ledger.apply_position_event(
            "ws", "held-past-kickoff",
            PositionState(slug, Decimal("5"), runner._now()),
            received_at=runner._now())

        self.assertFalse(runner._expired_and_flat(slug, runner._now()))
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_a_market_before_kickoff_is_never_released(self):
        runner, _, _ = self.build_runner(clock=TickingClock())
        await runner.preflight()
        slug = runner.market_slug
        runner.state_for(slug).market = SimpleNamespace(
            game_start=runner._now() + timedelta(hours=2))

        self.assertFalse(runner._expired_and_flat(slug, runner._now()))
        await runner.private_stream.close()
        await runner.market_stream.close()


class TopUpSafetyTests(unittest.IsolatedAsyncioTestCase):
    """The two ways the top-up could take the session down."""

    build_runner = LiveCommissioningTests.build_runner

    async def armed(self, replacements=()):
        """A runner with auto-select armed -- the fixture ships it disabled,
        which silently made the first version of these tests vacuous."""

        runner, _, _ = self.build_runner(clock=TickingClock())
        await runner.preflight()
        runner.config = replace(runner.config, auto_select_reward_markets=True)

        class Selector:
            async def select(self, *, now):
                return SimpleNamespace(
                    opportunities=(), selected=None,
                    top_viable=lambda *a, **k: tuple(
                        SimpleNamespace(market=SimpleNamespace(slug=s))
                        for s in replacements))

        runner.reward_selector = Selector()
        return runner

    def mark_reward_ended(self, runner, slug):
        """Record the proof `_refresh_contracts` owns in production."""

        now = runner._now()
        state = runner.state_for(slug)
        state.market = SimpleNamespace(
            game_start=now + timedelta(hours=2))
        state.reward_program_id = f"reward-{slug}"
        state.reward_program_ends_at = now - timedelta(seconds=1)
        state.reward_period_ended_at = now - timedelta(seconds=1)
        state.reward_period_end_confirmed_at = now
        return state

    def metadata(self, slug, now):
        return MarketMetadata(
            slug=slug,
            game_start=now + timedelta(hours=2),
            tick_size=0.01,
            active=True,
            closed=False,
            status="MARKET_STATUS_OPEN",
            minimum_trade_qty=1,
            sports_market_type_v2="SPORTS_MARKET_TYPE_MONEYLINE",
        )

    def ended_incentive(self, slug, now, *, program_id=None):
        return MarketIncentive(slug=slug, periods=(IncentivePeriod(
            program_id=program_id or f"reward-{slug}",
            period="day_of",
            status="active",
            reward_pool=100,
            discount_factor=0.5,
            target_size=10,
            starts_at=now - timedelta(hours=1),
            ends_at=now - timedelta(seconds=1),
        ),))

    async def test_confirmed_reward_end_replaces_only_that_slot_at_exact_cap(self):
        runner = await self.armed(replacements=(
            "market-three", "market-four"))
        runner.config = replace(runner.config, max_markets=2)
        runner._bind_markets(("market-one", "market-two"))
        runner.state_for("market-one").market = SimpleNamespace(
            game_start=runner._now() + timedelta(hours=2))
        self.mark_reward_ended(runner, "market-two")

        self.assertTrue(runner._active_set_needs_top_up(runner._now()))
        self.assertTrue(await runner._top_up_active_markets(runner._now()))

        self.assertEqual(runner.active_slugs,
                         ("market-one", "market-three"))
        self.assertEqual(len(runner.active_slugs), 2)
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_reward_end_requests_top_up_before_normal_scan_deadline(self):
        runner = await self.armed()
        slug = runner.market_slug
        self.mark_reward_ended(runner, slug)
        now = runner._now()
        next_selection = now + timedelta(hours=1)

        self.assertFalse(runner._incremental_top_up_due(
            now, next_selection))
        runner._reward_end_top_up_requested = True
        self.assertTrue(runner._incremental_top_up_due(
            now, next_selection))
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_normal_safe_scan_is_left_for_full_rotation(self):
        """The lifecycle fix must not divert the existing rotation path."""

        runner = await self.armed()
        slug = runner.market_slug
        self.mark_reward_ended(runner, slug)
        now = runner._now()
        self.assertTrue(runner._safe_to_scan())

        self.assertFalse(runner._incremental_top_up_due(
            now, now - timedelta(seconds=1)))
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_missing_incentive_row_does_not_confirm_reward_end(self):
        runner = await self.armed()
        slug = runner.market_slug
        state = runner.state_for(slug)
        state.reward_program_id = f"reward-{slug}"
        state.reward_program_ends_at = runner._now() + timedelta(minutes=5)
        runner.gateway.fetch_market = AsyncMock(
            return_value=self.metadata(slug, runner._now()))
        runner.gateway.fetch_incentives = AsyncMock(return_value={})

        with self.assertRaisesRegex(
                CommissioningUnavailable, "no liquidityProgram"):
            await runner._refresh_contracts(runner._now(), slug)

        self.assertIsNone(state.reward_period_end_confirmed_at)
        self.assertFalse(runner._reward_end_top_up_requested)
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_exact_elapsed_program_confirms_reward_end_only_once(self):
        runner = await self.armed()
        slug = runner.market_slug
        now = runner._now()
        state = runner.state_for(slug)
        state.reward_program_id = f"reward-{slug}"
        state.reward_program_ends_at = now + timedelta(minutes=5)
        runner.gateway.fetch_market = AsyncMock(
            return_value=self.metadata(slug, now))
        runner.gateway.fetch_incentives = AsyncMock(return_value={
            slug: self.ended_incentive(slug, now),
        })

        await runner._refresh_contracts(now, slug)

        self.assertIsNotNone(state.reward_period_end_confirmed_at)
        self.assertTrue(runner._reward_end_top_up_requested)
        runner._reward_end_top_up_requested = False
        await runner._refresh_contracts(now, slug)
        self.assertFalse(runner._reward_end_top_up_requested)
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_rescheduled_exact_program_releases_an_unpaid_slot(self):
        """The venue may move kickoff back outside the six-hour window."""

        runner = await self.armed()
        slug = runner.market_slug
        now = runner._now()
        state = runner.state_for(slug)
        state.reward_program_id = f"reward-{slug}"
        runner.gateway.fetch_market = AsyncMock(return_value=MarketMetadata(
            slug=slug,
            game_start=now + timedelta(hours=8),
            tick_size=0.01,
            active=True,
            closed=False,
            status="MARKET_STATUS_OPEN",
            minimum_trade_qty=1,
            sports_market_type_v2="SPORTS_MARKET_TYPE_MONEYLINE",
        ))
        runner.gateway.fetch_incentives = AsyncMock(return_value={
            slug: MarketIncentive(slug=slug, periods=(IncentivePeriod(
                program_id=f"reward-{slug}",
                period="day_of",
                status="active",
                reward_pool=100,
                discount_factor=0.5,
                target_size=10,
                starts_at=None,
                ends_at=None,
            ),)),
        })

        await runner._refresh_contracts(now, slug)

        self.assertEqual(
            state.reward_authorization_loss_reason,
            "no_longer_applicable")
        self.assertIsNotNone(
            state.reward_authorization_loss_confirmed_at)
        self.assertIsNone(state.incentive)
        self.assertTrue(runner._reward_authorization_lost_and_flat(
            slug, runner._now()))
        self.assertTrue(runner._reward_end_top_up_requested)
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_active_refresh_records_the_exact_authorizing_program(self):
        runner = await self.armed()
        slug = runner.market_slug
        now = runner._now()
        end = now + timedelta(minutes=5)
        reward = MarketIncentive(slug=slug, periods=(IncentivePeriod(
            program_id="authorizing-program",
            period="day_of",
            status="active",
            reward_pool=100,
            discount_factor=0.5,
            target_size=10,
            starts_at=now - timedelta(minutes=5),
            ends_at=end,
        ),))
        runner.gateway.fetch_market = AsyncMock(
            return_value=self.metadata(slug, now))
        runner.gateway.fetch_incentives = AsyncMock(
            return_value={slug: reward})

        await runner._refresh_contracts(now, slug)

        state = runner.state_for(slug)
        self.assertEqual(state.reward_program_id, "authorizing-program")
        self.assertEqual(state.reward_program_ends_at, end)
        self.assertIsNone(state.reward_period_end_confirmed_at)
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_different_elapsed_program_cannot_release_current_owner(self):
        runner = await self.armed()
        slug = runner.market_slug
        now = runner._now()
        state = runner.state_for(slug)
        state.reward_program_id = f"reward-{slug}"
        runner.gateway.fetch_market = AsyncMock(
            return_value=self.metadata(slug, now))
        runner.gateway.fetch_incentives = AsyncMock(return_value={
            slug: self.ended_incentive(
                slug, now, program_id="some-other-program"),
        })

        with self.assertRaisesRegex(
                CommissioningUnavailable, "no applicable pregame"):
            await runner._refresh_contracts(now, slug)

        self.assertIsNone(state.reward_period_end_confirmed_at)
        self.assertFalse(runner._reward_end_top_up_requested)
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_working_order_blocks_reward_ended_slot_release(self):
        runner = await self.armed()
        slug = runner.market_slug
        self.mark_reward_ended(runner, slug)
        runner.ledger.apply_order_event(
            "ws", "reward-ended-open-order",
            OrderState(
                client_order_id="reward-ended-client",
                exchange_order_id="reward-ended-exchange",
                market_slug=slug,
                side="ORDER_SIDE_BUY",
                price=Decimal("0.49"),
                original_qty=Decimal("1"),
                filled_qty=Decimal("0"),
                status="OPEN",
                event_at=runner._now()),
            received_at=runner._now())

        self.assertFalse(runner._reward_ended_and_flat(
            slug, runner._now()))
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_position_blocks_reward_ended_slot_release(self):
        from liquidity_bot.execution_ledger import PositionState

        runner = await self.armed()
        slug = runner.market_slug
        self.mark_reward_ended(runner, slug)
        runner.ledger.apply_position_event(
            "ws", "reward-ended-position",
            PositionState(slug, Decimal("1"), runner._now()),
            received_at=runner._now())

        self.assertFalse(runner._reward_ended_and_flat(
            slug, runner._now()))
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_unresolved_intent_blocks_reward_ended_slot_release(self):
        runner = await self.armed()
        slug = runner.market_slug
        self.mark_reward_ended(runner, slug)
        runner.ledger.prepare_intent(
            "reward-ended-intent", "CANCEL", slug,
            "reward-ended-client", {}, created_at=runner._now())

        self.assertFalse(runner._reward_ended_and_flat(
            slug, runner._now()))
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_absent_order_blocks_reward_slot_release(self):
        runner = await self.armed()
        slug = runner.market_slug
        self.mark_reward_ended(runner, slug)
        runner.ledger.apply_order_event(
            "ws", "reward-ended-absent",
            OrderState(
                client_order_id="reward-ended-client",
                exchange_order_id="reward-ended-exchange",
                market_slug=slug,
                side="ORDER_SIDE_BUY",
                price=Decimal("0.49"),
                original_qty=Decimal("1"),
                filled_qty=Decimal("0"),
                status="OPEN",
                event_at=runner._now()),
            received_at=runner._now())
        runner.ledger.connection.execute(
            "UPDATE execution_orders SET status='UNKNOWN_ABSENT', "
            "unknown_reason='missing_from_complete_snapshot' "
            "WHERE exchange_order_id='reward-ended-exchange'")
        runner.ledger.connection.commit()

        self.assertFalse(runner._reward_ended_and_flat(
            slug, runner._now()))
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_top_up_never_binds_an_empty_active_set(self):
        """`_bind_markets(())` leaves `market_slug` raising and kills the loop."""

        runner = await self.armed(replacements=())
        before = runner.active_slugs
        for slug in before:
            runner.state_for(slug).market = SimpleNamespace(
                game_start=runner._now() - timedelta(minutes=45))
        # Precondition: without this the test proves nothing.
        self.assertTrue(all(runner._expired_and_flat(s, runner._now())
                            for s in before))

        self.assertFalse(await runner._top_up_active_markets(runner._now()))
        # Holding dead markets beats holding none.
        self.assertEqual(runner.active_slugs, before)
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def _one_dead_slot(self, runner):
        """Bind one pregame market and one that is past kickoff and flat."""

        held, dead = "market-one", "market-two"
        runner._bind_markets((held, dead))
        runner.state_for(held).market = SimpleNamespace(
            game_start=runner._now() + timedelta(hours=2))
        runner.state_for(dead).market = SimpleNamespace(
            game_start=runner._now() - timedelta(minutes=45))
        # Preconditions: exactly one slot is reclaimable, and no position is
        # near its deadline -- so only the bind can decide the outcome.
        self.assertFalse(runner._expired_and_flat(held, runner._now()))
        self.assertTrue(runner._expired_and_flat(dead, runner._now()))
        self.assertFalse(
            runner._position_near_its_flatten_deadline(runner._now()))
        return held, dead

    async def test_a_failed_bind_marks_the_streams_for_rebind(self):
        """The 2026-08-16 hang: a failed top-up bind left the runner alive
        holding two closed streams and a disconnected gate, and nothing ever
        retried. Every market reported `execution_state_unknown` for 24
        minutes. The flag is what lets the run loop notice."""

        runner = await self.armed(replacements=("market-three",))
        await self._one_dead_slot(runner)

        async def timed_out():
            raise TimeoutError()

        runner._wait_for_initial_private_state = timed_out
        # Precondition: nothing is asking for a rebind before the failure.
        self.assertFalse(runner._streams_need_rebind)

        with self.assertRaises(TimeoutError):
            await runner._top_up_active_markets(runner._now())

        # The gate is down, which is correct and is why no market can quote.
        self.assertFalse(runner.gate.connected)
        # ...and this is the part that was missing: a standing request to
        # rebind, so the loop does not sit in the quarantine loop forever.
        self.assertTrue(runner._streams_need_rebind)

    async def test_top_up_proves_state_before_changing_scope_or_streams(self):
        """A known order ambiguity must abort while the old session is live.

        Live 2026-08-25: top-up committed its replacement and closed both
        streams, then strict GET-by-ID returned NotFound. Recovery consequently
        cancelled the whole slate and retired eighteen otherwise healthy
        owners. The strict read belongs before that irreversible boundary.
        """

        runner = await self.armed(replacements=("market-three",))
        await self._one_dead_slot(runner)
        before_slugs = runner.active_slugs
        before_private = runner.private_stream
        before_market = runner.market_stream

        with patch.object(
                runner, "_rest_reconcile", new=AsyncMock(
                    side_effect=AdapterError(
                        "GET /v1/order/exchange-lagged failed: NotFoundError"))):
            with self.assertRaisesRegex(AdapterError, "NotFoundError"):
                await runner._top_up_active_markets(runner._now())

        self.assertEqual(runner.active_slugs, before_slugs)
        self.assertIs(runner.private_stream, before_private)
        self.assertIs(runner.market_stream, before_market)
        self.assertFalse(before_private.closed)
        self.assertFalse(before_market.closed)
        self.assertTrue(runner.gate.connected)
        self.assertFalse(runner._streams_need_rebind)
        self.assertEqual(runner.client.orders.cancel_calls, [])
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_top_up_scopes_only_the_post_preflight_bind_read(self):
        """Strict reads cover releases, never unrelated retained owners."""

        runner = await self.armed(replacements=("market-three",))
        await self._one_dead_slot(runner)
        original_reconcile = runner._rest_reconcile

        with patch.object(
                runner, "_rest_reconcile",
                new=AsyncMock(wraps=original_reconcile)) as reconcile:
            self.assertTrue(
                await runner._top_up_active_markets(runner._now()))

        scopes = [call.kwargs.get("strict_order_markets")
                  for call in reconcile.await_args_list]
        self.assertEqual(scopes, [frozenset({"market-two"}), frozenset()])
        self.assertEqual(
            runner.active_slugs, ("market-one", "market-three"))
        self.assertTrue(runner.gate.connected)
        self.assertFalse(runner._streams_need_rebind)
        self.assertEqual(runner.client.orders.cancel_calls, [])
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_unrelated_not_found_does_not_block_a_slot_top_up(self):
        """One retained owner's lagging GET must not freeze all 20 slots."""

        runner = await self.armed(replacements=("market-three",))
        held, _dead = await self._one_dead_slot(runner)
        runner.ledger.apply_order_event(
            "ws", "held-open",
            OrderState(
                client_order_id="liquidity-held",
                exchange_order_id="exchange-held",
                market_slug=held,
                side="ORDER_SIDE_BUY",
                price=Decimal("0.49"),
                original_qty=Decimal("5"),
                filled_qty=Decimal("0"),
                status="OPEN",
                event_at=runner._now()),
            received_at=runner._now())
        runner.client.orders.rows["exchange-held"] = {
            "id": "exchange-held",
            "clientOrderId": "liquidity-held",
            "marketSlug": held,
            "side": "ORDER_SIDE_BUY",
            "intent": "ORDER_INTENT_BUY_LONG",
            "price": {"value": "0.49", "currency": "USD"},
            "quantity": "5",
            "cumQuantity": "0",
            "state": "ORDER_STATE_NEW",
            "createTime": runner._now().isoformat(),
        }
        original_retrieve = runner.client.orders.retrieve

        async def unrelated_not_found(order_id):
            if order_id == "exchange-held":
                raise AdapterError(
                    "GET /v1/order/exchange-held failed: NotFoundError")
            return await original_retrieve(order_id)

        runner.client.orders.retrieve = unrelated_not_found

        self.assertTrue(await runner._top_up_active_markets(runner._now()))
        self.assertEqual(
            runner.active_slugs, ("market-one", "market-three"))
        self.assertEqual(runner.client.orders.cancel_calls, [])
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_a_successful_bind_clears_the_rebind_request(self):
        """Otherwise the loop would rebind on every single pass."""

        runner = await self.armed(replacements=("market-three",))
        runner._streams_need_rebind = True

        original_reconcile = runner._rest_reconcile
        with patch.object(
                runner, "_rest_reconcile",
                new=AsyncMock(wraps=original_reconcile)) as reconcile:
            await runner._open_streams_for_active_market()

        self.assertFalse(runner._streams_need_rebind)
        self.assertTrue(runner.gate.connected)
        self.assertIsNone(
            reconcile.await_args.kwargs["strict_order_markets"],
            "ordinary active-set binds must retain account-wide strict reads",
        )
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_free_startup_capacity_is_filled_without_releasing_markets(self):
        """Rejected startup candidates used to leave 18/20 forever."""

        runner = await self.armed(replacements=(
            "market-three", "market-four", "market-five"))
        runner.config = replace(runner.config, max_markets=3)
        runner._bind_markets(("market-one", "market-two"))
        for slug in runner.active_slugs:
            runner.state_for(slug).market = SimpleNamespace(
                game_start=runner._now() + timedelta(hours=2))

        before = runner.active_slugs
        self.assertTrue(runner._active_set_needs_top_up(runner._now()))
        self.assertTrue(await runner._top_up_active_markets(runner._now()))

        self.assertEqual(runner.active_slugs,
                         before + ("market-three",))
        self.assertEqual(len(runner.active_slugs), 3)
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_free_capacity_with_no_replacement_keeps_streams_intact(self):
        runner = await self.armed(replacements=())
        runner.config = replace(runner.config, max_markets=3)
        runner._bind_markets(("market-one", "market-two"))
        for slug in runner.active_slugs:
            runner.state_for(slug).market = SimpleNamespace(
                game_start=runner._now() + timedelta(hours=2))
        before_private = runner.private_stream
        before_market = runner.market_stream

        self.assertFalse(await runner._top_up_active_markets(runner._now()))

        self.assertEqual(runner.active_slugs,
                         ("market-one", "market-two"))
        self.assertIs(runner.private_stream, before_private)
        self.assertIs(runner.market_stream, before_market)
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_full_capacity_without_expiry_does_not_scan(self):
        runner = await self.armed(replacements=("market-three",))
        runner.config = replace(runner.config, max_markets=2)
        runner._bind_markets(("market-one", "market-two"))
        for slug in runner.active_slugs:
            runner.state_for(slug).market = SimpleNamespace(
                game_start=runner._now() + timedelta(hours=2))
        runner.reward_selector.select = AsyncMock(
            side_effect=AssertionError("full capacity must not scan"))

        self.assertFalse(runner._active_set_needs_top_up(runner._now()))
        self.assertFalse(await runner._top_up_active_markets(runner._now()))
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_underfilled_startup_schedules_immediate_top_up(self):
        runner = await self.armed(replacements=())
        runner.config = replace(runner.config, max_markets=2)
        now = runner._now()

        self.assertEqual(runner._initial_market_selection_due(now), now)

        runner.config = replace(runner.config, max_markets=1)
        self.assertEqual(
            runner._initial_market_selection_due(now),
            now + timedelta(
                seconds=runner.config.market_selection_refresh_seconds))
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_recovery_rebinds_both_streams_and_resumes(self):
        """`_recover_bound_streams` must fix BOTH sides. The two existing
        recoveries each repair one and assume the other is healthy, which is
        exactly the assumption a failed bind breaks."""

        runner = await self.armed(replacements=("market-three",))
        await self._one_dead_slot(runner)

        failures = []

        async def timed_out():
            failures.append(1)
            raise TimeoutError()

        original = runner._wait_for_initial_private_state
        runner._wait_for_initial_private_state = timed_out
        with self.assertRaises(TimeoutError):
            await runner._top_up_active_markets(runner._now())
        self.assertTrue(runner._streams_need_rebind)

        # The venue comes back.
        runner._wait_for_initial_private_state = original
        stop = await runner._recover_bound_streams(RuntimeError("bind failed"))

        self.assertIsNone(stop)
        self.assertFalse(runner._streams_need_rebind)
        self.assertTrue(runner.gate.connected)
        # Fresh objects, not the closed ones it was holding.
        self.assertTrue(runner.private_stream.started)
        self.assertFalse(runner.private_stream.closed)
        self.assertTrue(runner.market_stream.started)
        self.assertFalse(runner.market_stream.closed)
        self.assertEqual(len(failures), 1)
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_top_up_defers_while_inventory_nears_its_flatten_deadline(self):
        """A scan is deferrable; a missed pre-kickoff flatten is not."""

        from liquidity_bot.execution_ledger import PositionState

        runner = await self.armed(replacements=("market-three",))
        held, dead = "market-one", "market-two"
        runner._bind_markets((held, dead))
        runner.state_for(held).market = SimpleNamespace(
            game_start=runner._now() + timedelta(seconds=120))
        runner.state_for(dead).market = SimpleNamespace(
            game_start=runner._now() - timedelta(minutes=45))
        runner.ledger.apply_position_event(
            "ws", "near-kickoff-position",
            PositionState(held, Decimal("5"), runner._now()),
            received_at=runner._now())

        # Preconditions: there IS work to do, so only the deadline guard can
        # be what stops it.
        self.assertTrue(runner._expired_and_flat(dead, runner._now()))
        self.assertTrue(
            runner._position_near_its_flatten_deadline(runner._now()))

        self.assertFalse(await runner._top_up_active_markets(runner._now()))
        self.assertEqual(set(runner.active_slugs), {held, dead})
        await runner.private_stream.close()
        await runner.market_stream.close()

    async def test_a_distant_position_does_not_defer_the_top_up(self):
        from liquidity_bot.execution_ledger import PositionState

        runner = await self.armed()
        slug = runner.market_slug
        runner.state_for(slug).market = SimpleNamespace(
            game_start=runner._now() + timedelta(hours=4))
        runner.ledger.apply_position_event(
            "ws", "distant-position",
            PositionState(slug, Decimal("5"), runner._now()),
            received_at=runner._now())

        self.assertFalse(
            runner._position_near_its_flatten_deadline(runner._now()))
        await runner.private_stream.close()
        await runner.market_stream.close()
