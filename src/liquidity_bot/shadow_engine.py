"""Stateful, non-trading evaluator for reward and inventory policies."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Iterable

from .models import BookSnapshot, MarketMetadata, TradePrint
from .rewards import (
    AmbiguousIncentiveError,
    IncentivePeriod,
    evaluate_order_score,
    select_applicable_period,
)
from .shadow_math import cross_exit, passive_close_pnl, split_reward_day_interval
from .shadow_store import ShadowStore


@dataclass(frozen=True)
class ShadowPolicy:
    quote_size: float = 2.0
    exit_horizons_seconds: tuple[int, ...] = (15, 30, 60, 120)
    exit_depth_haircut: float = 0.5
    max_exit_loss_per_market: float = 5.0
    taker_theta: float = 0.06
    pregame_only: bool = True

    def __post_init__(self) -> None:
        if self.quote_size <= 0:
            raise ValueError("quote_size must be positive")
        horizons = tuple(sorted(set(int(item) for item in self.exit_horizons_seconds)))
        if not horizons or horizons[0] <= 0:
            raise ValueError("exit horizons must be positive")
        if horizons != self.exit_horizons_seconds:
            raise ValueError("exit horizons must be unique and increasing")
        if not 0 < self.exit_depth_haircut <= 1:
            raise ValueError("exit_depth_haircut must be in (0, 1]")
        if self.max_exit_loss_per_market <= 0 or self.taker_theta < 0:
            raise ValueError("risk and fee settings are invalid")


@dataclass
class _QuoteState:
    side: str
    price: float
    size: float
    candidate: bool
    score: float
    queue_ahead: float


@dataclass
class _PolicyState:
    horizon: int
    remaining: float
    passive_qty: float = 0.0
    passive_notional: float = 0.0
    passive_pnl: float = 0.0
    resolved: bool = False


@dataclass
class _Episode:
    fill_signal_id: int
    entry_side: str
    entry_price: float
    qty: float
    entry_at: datetime
    passive_side: str
    passive_price: float
    passive_queue: float
    policies: dict[int, _PolicyState]


@dataclass
class _MarketState:
    market: MarketMetadata
    periods: tuple[IncentivePeriod, ...]
    quotes: dict[str, _QuoteState] = field(default_factory=dict)
    last_book: BookSnapshot | None = None
    last_at: datetime | None = None
    episode: _Episode | None = None
    enabled: bool = True


class ShadowEngine:
    """Measure hypothetical behavior without sending exchange requests.

    Public trades create *fill signals*, not executions. The back-of-queue
    model is deliberately conservative and every result retains that label in
    SQLite so it cannot later be confused with account truth.
    """

    def __init__(self, run_id: str, store: ShadowStore, policy: ShadowPolicy,
                 *, started_at: datetime):
        if started_at.tzinfo is None:
            raise ValueError("started_at must be timezone-aware")
        self.run_id = run_id
        self.store = store
        self.policy = policy
        self.states: dict[str, _MarketState] = {}
        self.store.start_run(run_id, started_at, asdict(policy))

    def register_market(self, market: MarketMetadata,
                        periods: Iterable[IncentivePeriod], *,
                        observed_at: datetime) -> None:
        if market.game_start is None or market.tick_size is None:
            raise ValueError("shadow markets require a start time and tick size")
        normalized = tuple(periods)
        prior = self.states.get(market.slug)
        if prior is None:
            self.states[market.slug] = _MarketState(market, normalized)
        else:
            prior.market = market
            prior.periods = normalized
            prior.enabled = True
        self.store.register_market(
            self.run_id, market, normalized, captured_at=observed_at)

    def deactivate_market(self, slug: str, *, observed_at: datetime) -> None:
        state = self.states.get(slug)
        if state is None:
            return
        self._accrue(state, observed_at)
        state.quotes.clear()
        state.enabled = False

    def has_open_episode(self, slug: str) -> bool:
        state = self.states.get(slug)
        return state is not None and state.episode is not None

    def oldest_open_episode_age(self, observed_at: datetime) -> float:
        ages = [
            (observed_at - state.episode.entry_at).total_seconds()
            for state in self.states.values() if state.episode is not None
        ]
        if any(age < 0 for age in ages):
            raise ValueError("shadow episode clock moved backwards")
        return max(ages, default=0.0)

    @staticmethod
    def _queue_at(book: BookSnapshot, side: str, price: float) -> float:
        return sum(size for level_price, size in book.levels(side)
                   if abs(level_price - price) < 1e-9)

    def _accrue(self, state: _MarketState, until: datetime) -> None:
        if state.last_at is None:
            state.last_at = until
            return
        if until < state.last_at:
            raise ValueError("shadow observations must be chronological")
        for quote in state.quotes.values():
            for day_et, seconds in split_reward_day_interval(state.last_at, until):
                self.store.add_score(
                    self.run_id, day_et, state.market.slug, quote.side,
                    seconds if quote.candidate else 0.0,
                    seconds * quote.score if quote.candidate else 0.0)
        state.last_at = until

    def _candidate(self, state: _MarketState, book: BookSnapshot, side: str,
                   observed_at: datetime) -> tuple[_QuoteState, dict]:
        price = book.best_bid.price if side == "bid" else book.best_offer.price
        queue = self._queue_at(book, side, price)
        details = {
            "ticks": None, "cutoff": None, "target": None,
            "reason": "unknown", "score": 0.0,
        }
        if not state.enabled:
            details["reason"] = "not_selected"
            return _QuoteState(side, price, self.policy.quote_size, False, 0, queue), details
        if not state.market.active or state.market.closed or not book.state in (
                None, "MARKET_STATE_OPEN"):
            details["reason"] = "market_not_open"
            return _QuoteState(side, price, self.policy.quote_size, False, 0, queue), details
        if self.policy.pregame_only and observed_at >= state.market.game_start:
            details["reason"] = "in_play"
            return _QuoteState(side, price, self.policy.quote_size, False, 0, queue), details
        try:
            period = select_applicable_period(
                state.periods, state.market.game_start, observed_at)
        except AmbiguousIncentiveError:
            details["reason"] = "ambiguous_incentive"
            return _QuoteState(side, price, self.policy.quote_size, False, 0, queue), details
        if period is None:
            details["reason"] = "no_applicable_incentive"
            return _QuoteState(side, price, self.policy.quote_size, False, 0, queue), details
        result = evaluate_order_score(
            side=side, price=price, size=self.policy.quote_size,
            levels=book.levels(side), incentive=period,
            tick_size=state.market.tick_size)
        details.update(ticks=result.ticks_from_best, cutoff=result.cutoff_price,
                       target=period.target_size, reason=result.reason,
                       score=result.score)
        if not result.eligible:
            return _QuoteState(side, price, self.policy.quote_size, False, 0, queue), details

        # Do not count the entry touch as future exit liquidity. A fill through
        # our quote necessarily consumed the queue that had been ahead of us.
        exit_test = cross_exit(
            book, entry_side=side, entry_price=price,
            qty=self.policy.quote_size,
            depth_haircut=self.policy.exit_depth_haircut,
            taker_theta=self.policy.taker_theta,
            exclude_entry_level=True)
        if not exit_test.complete:
            details["reason"] = "insufficient_stressed_exit_depth"
            return _QuoteState(side, price, self.policy.quote_size, False, 0, queue), details
        if exit_test.pnl < -self.policy.max_exit_loss_per_market:
            details["reason"] = "exit_loss_budget"
            return _QuoteState(side, price, self.policy.quote_size, False, 0, queue), details
        details["reason"] = "candidate"
        return _QuoteState(
            side, price, self.policy.quote_size, True, result.score, queue), details

    def on_book(self, book: BookSnapshot, *, observed_at: datetime) -> None:
        state = self.states.get(book.slug)
        if state is None:
            raise KeyError(f"unregistered shadow market {book.slug}")
        self._accrue(state, observed_at)
        self.store.record_book(self.run_id, book, observed_at)
        state.last_book = book

        if state.episode is not None:
            self._evaluate_due_exits(state, book, observed_at)
            if state.episode is not None:
                self._reprice_passive(state.episode, book)
                state.quotes.clear()
                return

        quotes: dict[str, _QuoteState] = {}
        for side in ("bid", "offer"):
            quote, details = self._candidate(state, book, side, observed_at)
            quotes[side] = quote
            self.store.record_quote(
                self.run_id, book.slug, side, observed_at,
                price=quote.price, size=quote.size, candidate=quote.candidate,
                score=details["score"], ticks=details["ticks"],
                cutoff=details["cutoff"], target=details["target"],
                queue_ahead=quote.queue_ahead, reason=details["reason"], book=book)
        state.quotes = quotes

    @staticmethod
    def _consumed(quote_side: str, quote_price: float,
                  trade: TradePrint, queue_ahead: float,
                  remaining: float) -> tuple[float, float, str | None]:
        consumed_side = ("bid" if trade.maker_side == "ORDER_SIDE_BUY" else "offer")
        if consumed_side != quote_side:
            return 0.0, queue_ahead, None
        same = abs(trade.price - quote_price) < 1e-9
        swept = (trade.price < quote_price if quote_side == "bid"
                 else trade.price > quote_price)
        if same:
            new_queue = max(0.0, queue_ahead - trade.quantity)
            fill = min(remaining, max(0.0, trade.quantity - queue_ahead))
            return fill, new_queue, "at_price_queue" if fill > 0 else None
        if swept:
            return min(remaining, trade.quantity), 0.0, "trade_through"
        return 0.0, queue_ahead, None

    def on_trade(self, trade: TradePrint, *, observed_at: datetime | None = None) -> None:
        state = self.states.get(trade.slug)
        if state is None:
            return
        at = observed_at or trade.trade_time
        self._accrue(state, at)
        self.store.record_trade(self.run_id, trade, at)
        if state.episode is not None:
            self._apply_passive_trade(state, trade, at)
            return
        side = "bid" if trade.maker_side == "ORDER_SIDE_BUY" else "offer"
        quote = state.quotes.get(side)
        if quote is None or not quote.candidate:
            return
        queue_before = quote.queue_ahead
        fill, new_queue, signal = self._consumed(
            side, quote.price, trade, quote.queue_ahead, quote.size)
        quote.queue_ahead = new_queue
        if fill <= 0 or signal is None:
            return
        fill_id = self.store.record_fill_signal(
            self.run_id, trade.slug, "entry", side, at, quote.price, fill,
            signal, queue_before, trade.request_id)
        opposite = "offer" if side == "bid" else "bid"
        existing_exit = state.quotes.get(opposite)
        if existing_exit is not None:
            passive_price = existing_exit.price
            passive_queue = existing_exit.queue_ahead
        elif state.last_book is not None:
            passive_price = (state.last_book.best_offer.price if opposite == "offer"
                             else state.last_book.best_bid.price)
            passive_queue = self._queue_at(state.last_book, opposite, passive_price)
        else:
            raise RuntimeError("a fill signal cannot exist without a book")
        state.episode = _Episode(
            fill_signal_id=fill_id,
            entry_side=side,
            entry_price=quote.price,
            qty=fill,
            entry_at=at,
            passive_side=opposite,
            passive_price=passive_price,
            passive_queue=passive_queue,
            policies={horizon: _PolicyState(horizon, fill)
                      for horizon in self.policy.exit_horizons_seconds},
        )
        state.quotes.clear()

    def _reprice_passive(self, episode: _Episode, book: BookSnapshot) -> None:
        target = (book.best_offer.price if episode.passive_side == "offer"
                  else book.best_bid.price)
        if abs(target - episode.passive_price) > 1e-9:
            episode.passive_price = target
            episode.passive_queue = self._queue_at(
                book, episode.passive_side, target)

    def _apply_passive_trade(self, state: _MarketState, trade: TradePrint,
                             at: datetime) -> None:
        episode = state.episode
        if episode is None:
            return
        max_remaining = max(
            (item.remaining for item in episode.policies.values() if not item.resolved),
            default=0.0)
        fill, new_queue, signal = self._consumed(
            episode.passive_side, episode.passive_price, trade,
            episode.passive_queue, max_remaining)
        queue_before = episode.passive_queue
        episode.passive_queue = new_queue
        if fill <= 0 or signal is None:
            return
        self.store.record_fill_signal(
            self.run_id, trade.slug, "passive_exit", episode.passive_side,
            at, episode.passive_price, fill, signal,
            queue_before, trade.request_id)
        for item in episode.policies.values():
            if item.resolved or item.remaining <= 0:
                continue
            closed = min(fill, item.remaining)
            item.remaining -= closed
            item.passive_qty += closed
            item.passive_notional += closed * episode.passive_price
            item.passive_pnl += passive_close_pnl(
                episode.entry_side, episode.entry_price,
                episode.passive_price, closed)
            if item.remaining <= 1e-9:
                average = item.passive_notional / item.passive_qty
                due = episode.entry_at + timedelta(seconds=item.horizon)
                self.store.record_exit(
                    self.run_id, episode.fill_signal_id, item.horizon, at, due,
                    (at - due).total_seconds(), "passive", episode.qty,
                    item.passive_qty, 0.0, True, average, 0.0,
                    item.passive_pnl,
                    item.passive_pnl >= -self.policy.max_exit_loss_per_market)
                item.resolved = True
        if all(item.resolved for item in episode.policies.values()):
            state.episode = None

    def _evaluate_due_exits(self, state: _MarketState, book: BookSnapshot,
                            observed_at: datetime) -> None:
        episode = state.episode
        if episode is None:
            return
        for item in episode.policies.values():
            if item.resolved:
                continue
            due = episode.entry_at + timedelta(seconds=item.horizon)
            if observed_at < due:
                continue
            result = cross_exit(
                book, entry_side=episode.entry_side,
                entry_price=episode.entry_price, qty=item.remaining,
                depth_haircut=self.policy.exit_depth_haircut,
                taker_theta=self.policy.taker_theta)
            closed_total = item.passive_qty + result.closed_qty
            notional = item.passive_notional
            if result.average_price is not None:
                notional += result.average_price * result.closed_qty
            average = notional / closed_total if closed_total else None
            pnl = item.passive_pnl + result.pnl
            remaining = result.remaining_qty
            complete = remaining <= 1e-9
            method = "mixed" if item.passive_qty > 0 else "hard_ioc"
            self.store.record_exit(
                self.run_id, episode.fill_signal_id, item.horizon,
                observed_at, due, (observed_at - due).total_seconds(), method,
                episode.qty, closed_total, remaining, complete, average,
                result.taker_fee, pnl,
                complete and pnl >= -self.policy.max_exit_loss_per_market)
            item.resolved = True
        if all(item.resolved for item in episode.policies.values()):
            state.episode = None

    def finish(self, ended_at: datetime) -> None:
        for state in self.states.values():
            self._accrue(state, ended_at)
        self.store.end_run(self.run_id, ended_at)
