"""Observation-only price-time queue bounds for live resting orders.

The venue publishes aggregate books and public trades, not order-level queue
rank.  This module therefore records a defensible interval:

* public trades at our price definitely consume quantity ahead;
* better-priced displayed quantity is always ahead;
* same-price cancellations are ambiguous and can only widen the interval;
* newly displayed same-price quantity arrived behind an already-resting order.

Nothing here returns a strategy decision or exposes an order-mutation method.
Telemetry failure is handled by the runner and never changes desired orders.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from .execution_ledger import ExecutionLedger
from .models import BookSnapshot, TradePrint


TERMINAL_MEASUREMENT_STATES = frozenset({
    "FILLED", "CANCELED", "CANCELLED", "REJECTED", "EXPIRED",
})
SHADOW_RESTING_STATES = frozenset({"OPEN", "PARTIAL"})
def _utc(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("queue measurement time must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat()


def _finite_nonnegative(value: Any, field: str) -> float:
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"{field} must be finite and nonnegative")
    return result


def _queue_band(upper: float, quantity: float) -> str:
    """Stable coarse bands keep the event table bounded.

    Exact current bounds stay in the one-row summary.  Append-only history is
    written only when the conservative upper bound crosses one of these bands,
    which is enough to replay candidate almost-front thresholds without one
    database row per full-book message.
    """

    if upper <= 0:
        return "front"
    ratio = upper / quantity
    for limit, label in (
        (.25, "lte_0.25x"),
        (.5, "lte_0.5x"),
        (1, "lte_1x"),
        (2, "lte_2x"),
        (5, "lte_5x"),
        (10, "lte_10x"),
    ):
        if ratio <= limit:
            return label
    return "gt_10x"


@dataclass
class _TrackedOrder:
    client_order_id: str
    intent_id: str
    market_slug: str
    side: str
    quote_price: float
    quantity: float
    started_at: datetime
    initial_queue: float
    initial_better: float
    initial_same: float
    better_depth: float
    same_lower: float
    same_upper: float
    last_same_depth: float
    baseline_book_seen: bool = False
    baseline_book_at: datetime | None = None
    baseline_same_depth: float | None = None
    trade_at: float = 0
    trade_better: float = 0
    trade_through: float = 0
    ambiguous_removed: float = 0
    same_added: float = 0
    book_events: int = 0
    trade_events: int = 0
    coverage_gaps: int = 0
    last_filled: float = 0
    last_book_at: datetime | None = None
    last_trade_at: datetime | None = None
    last_persisted_at: datetime | None = None
    would_reset_at: datetime | None = None
    shadow_eligible: bool = False
    band: str = ""

    @property
    def lower(self) -> float:
        return max(0.0, self.better_depth + self.same_lower)

    @property
    def upper(self) -> float:
        return max(self.lower, self.better_depth + self.same_upper)


class QueueMeasurementTracker:
    """Persist compact queue intervals for exact building client order IDs."""

    SUMMARY_PERSIST_SECONDS = 300.0
    SHADOW_MINIMUM_DWELL_SECONDS = 60.0
    SHADOW_QUEUE_UPPER_QUANTITY_MULTIPLE = 1.0

    def __init__(
        self, ledger: ExecutionLedger, *, collection_started_at: datetime | None = None,
    ):
        self.ledger = ledger
        self.collection_started_at = collection_started_at or datetime.now(timezone.utc)
        _utc(self.collection_started_at)
        self._active: dict[str, _TrackedOrder] = {}
        self._load_active()

    @property
    def active_count(self) -> int:
        return len(self._active)

    def _load_active(self) -> None:
        rows = self.ledger.query(
            "SELECT * FROM execution_queue_measurements WHERE status='ACTIVE'")
        for row in rows:
            started = datetime.fromisoformat(str(row["started_at"]))
            last_persisted = datetime.fromisoformat(str(row["last_persisted_at"]))
            last_book = (datetime.fromisoformat(str(row["last_book_at"]))
                         if row["last_book_at"] else None)
            last_trade = (datetime.fromisoformat(str(row["last_trade_at"]))
                          if row["last_trade_at"] else None)
            would_reset = (datetime.fromisoformat(str(row["would_reset_at"]))
                           if row["would_reset_at"] else None)
            baseline_book_at = (
                datetime.fromisoformat(str(row["baseline_book_at"]))
                if row["baseline_book_at"] else None)
            state = _TrackedOrder(
                client_order_id=str(row["client_order_id"]),
                intent_id=str(row["intent_id"]),
                market_slug=str(row["market_slug"]),
                side=str(row["side"]),
                quote_price=float(row["quote_price"]),
                quantity=float(row["quantity"]),
                started_at=started,
                initial_queue=float(row["initial_queue_ahead"]),
                initial_better=float(row["initial_better_depth"]),
                initial_same=float(row["initial_same_price_depth"]),
                better_depth=float(row["better_depth"]),
                same_lower=float(row["same_price_lower_bound"]),
                same_upper=float(row["same_price_upper_bound"]),
                last_same_depth=float(row["last_same_price_depth"]),
                baseline_book_seen=bool(row["baseline_book_seen"]),
                baseline_book_at=baseline_book_at,
                baseline_same_depth=(
                    float(row["baseline_same_price_depth"])
                    if row["baseline_same_price_depth"] is not None else None),
                trade_at=float(row["trade_at_price_qty"]),
                trade_better=float(row["trade_better_price_qty"]),
                trade_through=float(row["trade_through_qty"]),
                ambiguous_removed=float(row["ambiguous_removed_qty"]),
                same_added=float(row["same_price_added_qty"]),
                book_events=int(row["relevant_book_events"]),
                trade_events=int(row["relevant_trade_events"]),
                coverage_gaps=int(row["coverage_gaps"]),
                band=str(row["queue_band"]),
                last_filled=float(row["last_filled_qty"]),
                last_book_at=last_book,
                last_trade_at=last_trade,
                last_persisted_at=last_persisted,
                would_reset_at=would_reset,
            )
            self._active[state.client_order_id] = state

    @staticmethod
    def _depth(book: BookSnapshot, state: _TrackedOrder) -> tuple[float, float]:
        levels = book.levels(state.side)
        if state.side == "bid":
            better = sum(size for price, size in levels
                         if price > state.quote_price + 1e-9)
        else:
            better = sum(size for price, size in levels
                         if price < state.quote_price - 1e-9)
        same = sum(size for price, size in levels
                   if abs(price - state.quote_price) < 1e-9)
        return max(0.0, better), max(0.0, same)

    def _insert_event(
        self,
        state: _TrackedOrder,
        at: datetime,
        kind: str,
        *,
        price: float | None = None,
        quantity: float | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.ledger.connection.execute(
            "INSERT INTO execution_queue_measurement_events("
            "client_order_id,event_at,event_kind,queue_lower_bound,"
            "queue_upper_bound,better_depth,same_price_lower_bound,"
            "same_price_upper_bound,event_price,event_quantity,queue_band,"
            "details_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                state.client_order_id, _utc(at), kind, state.lower, state.upper,
                state.better_depth, state.same_lower, state.same_upper,
                price, quantity, state.band,
                (json.dumps(details, sort_keys=True, separators=(",", ":"))
                 if details else None),
            ),
        )

    def _persist(self, state: _TrackedOrder, at: datetime, *, force: bool = False) -> None:
        if not force and state.last_persisted_at is not None:
            age = (at - state.last_persisted_at).total_seconds()
            if age < self.SUMMARY_PERSIST_SECONDS:
                return
        self.ledger.connection.execute(
            "UPDATE execution_queue_measurements SET "
            "queue_lower_bound=?,queue_upper_bound=?,better_depth=?,"
            "same_price_lower_bound=?,same_price_upper_bound=?,"
            "last_same_price_depth=?,baseline_book_seen=?,baseline_book_at=?,"
            "baseline_same_price_depth=?,trade_at_price_qty=?,"
            "trade_better_price_qty=?,trade_through_qty=?,"
            "ambiguous_removed_qty=?,same_price_added_qty=?,"
            "relevant_book_events=?,relevant_trade_events=?,coverage_gaps=?,"
            "queue_band=?,last_filled_qty=?,last_book_at=?,last_trade_at=?,"
            "last_persisted_at=? WHERE client_order_id=?",
            (
                state.lower, state.upper, state.better_depth,
                state.same_lower, state.same_upper, state.last_same_depth,
                int(state.baseline_book_seen),
                _utc(state.baseline_book_at) if state.baseline_book_at else None,
                state.baseline_same_depth,
                state.trade_at, state.trade_better, state.trade_through,
                state.ambiguous_removed, state.same_added, state.book_events,
                state.trade_events, state.coverage_gaps, state.band,
                state.last_filled,
                _utc(state.last_book_at) if state.last_book_at else None,
                _utc(state.last_trade_at) if state.last_trade_at else None,
                _utc(at), state.client_order_id,
            ),
        )
        state.last_persisted_at = at

    def _maybe_shadow_reset(self, state: _TrackedOrder, at: datetime) -> bool:
        """Record a candidate signal; deliberately return no trading action."""

        if (state.would_reset_at is not None or state.coverage_gaps
                or not state.baseline_book_seen or not state.shadow_eligible):
            return False
        dwell = (at - state.started_at).total_seconds()
        threshold = state.quantity * self.SHADOW_QUEUE_UPPER_QUANTITY_MULTIPLE
        if dwell < self.SHADOW_MINIMUM_DWELL_SECONDS or state.upper > threshold:
            return False
        state.would_reset_at = at
        self.ledger.connection.execute(
            "UPDATE execution_queue_measurements SET would_reset_at=?,"
            "would_reset_queue_lower_bound=?,would_reset_queue_upper_bound=? "
            "WHERE client_order_id=?",
            (_utc(at), state.lower, state.upper, state.client_order_id),
        )
        self._insert_event(
            state, at, "WOULD_QUEUE_RESET",
            details={
                "minimum_dwell_seconds": self.SHADOW_MINIMUM_DWELL_SECONDS,
                "upper_quantity_multiple": (
                    self.SHADOW_QUEUE_UPPER_QUANTITY_MULTIPLE),
            })
        return True

    def _start(self, row: Any, at: datetime) -> None:
        document = json.loads(str(row["observation_json"]))
        quantity = _finite_nonnegative(row["quantity"], "quantity")
        if quantity <= 0:
            raise ValueError("queue measurement quantity must be positive")
        initial_better = _finite_nonnegative(
            document.get("depth_better_than_quote", 0), "initial better depth")
        initial_same = _finite_nonnegative(
            document.get("depth_at_quote", 0), "initial same-price depth")
        initial_queue = _finite_nonnegative(
            row["observed_queue_ahead"], "initial queue")
        # The JSON arithmetic is the durable decision-time source. Refuse to
        # silently manufacture an interval if it was corrupted.
        if abs(initial_queue - (initial_better + initial_same)) > 1e-6:
            raise ValueError("initial queue components do not add up")
        # Queue tracking starts only after the create was acknowledged. The
        # decision-time book remains the initial queue source, but a public
        # trade during POST latency happened before our order could rest and
        # must not consume its queue.
        started_at = at
        state = _TrackedOrder(
            client_order_id=str(row["client_order_id"]),
            intent_id=str(row["intent_id"]),
            market_slug=str(row["market_slug"]),
            side=str(row["side"]),
            quote_price=float(Decimal(str(row["quote_price"]))),
            quantity=quantity,
            started_at=started_at,
            initial_queue=initial_queue,
            initial_better=initial_better,
            initial_same=initial_same,
            better_depth=initial_better,
            same_lower=initial_same,
            same_upper=initial_same,
            last_same_depth=initial_same,
            last_persisted_at=at,
        )
        state.band = _queue_band(state.upper, state.quantity)
        with self.ledger.connection:
            self.ledger.connection.execute(
                "INSERT OR IGNORE INTO execution_queue_measurements("
                "client_order_id,intent_id,market_slug,side,quote_price,quantity,"
                "started_at,initial_queue_ahead,initial_better_depth,"
                "initial_same_price_depth,queue_lower_bound,queue_upper_bound,"
                "better_depth,same_price_lower_bound,same_price_upper_bound,"
                "last_same_price_depth,queue_band,last_persisted_at,status) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    state.client_order_id, state.intent_id, state.market_slug,
                    state.side, str(row["quote_price"]), str(row["quantity"]),
                    _utc(started_at), initial_queue, initial_better, initial_same,
                    state.lower, state.upper, state.better_depth,
                    state.same_lower, state.same_upper, state.last_same_depth,
                    state.band, _utc(at), "ACTIVE",
                ),
            )
            inserted = self.ledger.connection.execute(
                "SELECT changes() changed").fetchone()["changed"]
            if inserted:
                self._insert_event(state, at, "START")
        # Existing ACTIVE rows were loaded in __init__. Existing CLOSED rows
        # must never be reopened by a replayed ACKNOWLEDGED intent.
        persisted = self.ledger.connection.execute(
            "SELECT status FROM execution_queue_measurements "
            "WHERE client_order_id=?", (state.client_order_id,)).fetchone()
        if persisted is not None and str(persisted["status"]) == "ACTIVE":
            self._active.setdefault(state.client_order_id, state)

    def _close(self, state: _TrackedOrder, status: str, at: datetime) -> None:
        with self.ledger.connection:
            self._persist(state, at, force=True)
            self._insert_event(
                state, at, "CLOSE", details={"terminal_status": status})
            self.ledger.connection.execute(
                "UPDATE execution_queue_measurements SET status='CLOSED',"
                "terminal_status=?,closed_at=?,close_queue_lower_bound=?,"
                "close_queue_upper_bound=? WHERE client_order_id=?",
                (status, _utc(at), state.lower, state.upper,
                 state.client_order_id),
            )
        self._active.pop(state.client_order_id, None)

    def sync_orders(self, *, observed_at: datetime) -> None:
        """Start/close trackers from durable order truth; never mutate orders."""

        rows = self.ledger.query(
            "SELECT q.*,i.state intent_state,o.status order_status,"
            "o.filled_qty,o.original_qty,qm.status measurement_status,"
            "EXISTS(SELECT 1 FROM execution_intents c "
            "WHERE c.client_order_id=q.client_order_id "
            "AND c.operation='CANCEL' AND c.state IN "
            "('PREPARED','SUBMITTING','ACKNOWLEDGED','UNCERTAIN')) cancel_pending "
            "FROM execution_quote_observations q "
            "JOIN execution_intents i ON i.intent_id=q.intent_id "
            "LEFT JOIN execution_orders o "
            "ON o.client_order_id=q.client_order_id "
            "LEFT JOIN execution_queue_measurements qm "
            "ON qm.client_order_id=q.client_order_id "
            "WHERE qm.status='ACTIVE' OR (qm.client_order_id IS NULL AND ("
            "q.observed_at>=? OR o.status IN (?,?,?,?,?,?)))",
            (_utc(self.collection_started_at),
             "OPEN", "NEW", "PARTIAL", "PENDING", "SUBMITTED",
             "UNKNOWN_ABSENT"))
        for row in rows:
            client_id = str(row["client_order_id"])
            intent_state = str(row["intent_state"] or "").upper()
            order_status = str(row["order_status"] or "").upper()
            state = self._active.get(client_id)
            if state is None:
                if intent_state in {"ACKNOWLEDGED", "RECONCILED"}:
                    self._start(row, observed_at)
                    state = self._active.get(client_id)
                if state is None:
                    continue
            state.shadow_eligible = (
                order_status in SHADOW_RESTING_STATES
                and not bool(row["cancel_pending"]))
            filled = _finite_nonnegative(row["filled_qty"] or 0, "filled quantity")
            if filled > state.last_filled + 1e-9:
                delta = filled - state.last_filled
                state.last_filled = filled
                # An actual maker fill is venue proof that queue ahead reached
                # zero, stronger than either aggregate-book bound.
                state.better_depth = 0
                state.same_lower = 0
                state.same_upper = 0
                old_band = state.band
                state.band = _queue_band(state.upper, state.quantity)
                with self.ledger.connection:
                    self._insert_event(
                        state, observed_at, "OWN_FILL", quantity=delta,
                        details={"prior_band": old_band})
                    self._persist(state, observed_at, force=True)
            if order_status in TERMINAL_MEASUREMENT_STATES:
                self._close(state, order_status, observed_at)
            elif intent_state == "FAILED" and not order_status:
                self._close(state, "PLACE_FAILED", observed_at)

    def on_book(self, book: BookSnapshot, *, observed_at: datetime) -> None:
        for state in tuple(self._active.values()):
            if (state.market_slug != book.slug
                    or observed_at < state.started_at):
                continue
            better, same = self._depth(book, state)
            previous_band = state.band
            if not state.baseline_book_seen:
                # Decision-time depth is descriptive, not a safe upper bound
                # after POST latency: same-price orders can arrive before our
                # acknowledgement and therefore be ahead. The first full book
                # received after ACK establishes a conservative baseline. It
                # includes our own quantity and any newer behind-us quantity,
                # both of which only overstate queue ahead.
                state.baseline_book_seen = True
                state.baseline_book_at = observed_at
                state.baseline_same_depth = same
                state.better_depth = better
                state.same_lower = 0
                state.same_upper = same
                state.last_same_depth = same
                state.book_events += 1
                state.last_book_at = observed_at
                state.band = _queue_band(state.upper, state.quantity)
                with self.ledger.connection:
                    self._insert_event(
                        state, observed_at, "QUEUE_BASELINE",
                        details={"prior_band": previous_band})
                    self._maybe_shadow_reset(state, observed_at)
                    self._persist(state, observed_at, force=True)
                continue
            changed = (abs(better - state.better_depth) > 1e-9
                       or abs(same - state.last_same_depth) > 1e-9)
            if not changed:
                state.last_book_at = observed_at
                with self.ledger.connection:
                    signaled = self._maybe_shadow_reset(state, observed_at)
                    self._persist(state, observed_at, force=signaled)
                continue
            if same < state.last_same_depth:
                removed = state.last_same_depth - same
                state.ambiguous_removed += removed
                # The vanished quantity may have been ahead or behind. Only
                # the lower bound can assume the favourable all-ahead case.
                state.same_lower = max(0.0, state.same_lower - removed)
            elif same > state.last_same_depth:
                state.same_added += same - state.last_same_depth
                # It arrived after our resting order and is behind us.
            # Whatever is actually displayed is an upper cap on same-price
            # quantity ahead. It may include our own order and newer orders,
            # which only makes the cap conservatively high.
            # Books and trades use separate sockets and can arrive in either
            # order. This nonincreasing full-book cap cannot double-subtract a
            # trade the book already reflects. Same-price additions after the
            # acknowledged baseline are behind us and cannot raise it.
            state.same_upper = min(state.same_upper, same)
            state.same_lower = min(state.same_lower, state.same_upper)
            state.better_depth = better
            state.last_same_depth = same
            state.book_events += 1
            state.last_book_at = observed_at
            state.band = _queue_band(state.upper, state.quantity)
            with self.ledger.connection:
                signaled = self._maybe_shadow_reset(state, observed_at)
                if state.band != previous_band:
                    self._insert_event(
                        state, observed_at, "QUEUE_BAND",
                        details={"prior_band": previous_band})
                    self._persist(state, observed_at, force=True)
                else:
                    self._persist(state, observed_at, force=signaled)

    @staticmethod
    def _trade_side(trade: TradePrint) -> str:
        return "bid" if trade.maker_side == "ORDER_SIDE_BUY" else "offer"

    def on_trade(self, trade: TradePrint, *, observed_at: datetime) -> None:
        consumed_side = self._trade_side(trade)
        for state in tuple(self._active.values()):
            if (state.market_slug != trade.slug or state.side != consumed_side
                    or trade.trade_time < state.started_at):
                continue
            same = abs(trade.price - state.quote_price) < 1e-9
            better = (trade.price > state.quote_price
                      if state.side == "bid"
                      else trade.price < state.quote_price)
            through = (trade.price < state.quote_price
                       if state.side == "bid"
                       else trade.price > state.quote_price)
            if not (same or better or through):
                continue
            previous_band = state.band
            quantity = _finite_nonnegative(trade.quantity, "trade quantity")
            if same:
                state.trade_at += quantity
                kind = "TRADE_AT_PRICE"
            elif better:
                state.trade_better += quantity
                # The latest full book remains the conservative boundary.
                # Subtracting here could double count when that book already
                # reflected this trade on the independently ordered socket.
                kind = "TRADE_BETTER_PRICE"
            else:
                # A print through our level proves the entire displayed path
                # was consumed. It is a measurement signal, not fill proof;
                # only the private order state labels our actual fill.
                state.trade_through += quantity
                if state.baseline_book_seen:
                    state.better_depth = 0
                    state.same_lower = 0
                    state.same_upper = 0
                kind = "TRADE_THROUGH"
            state.trade_events += 1
            state.last_trade_at = observed_at
            state.band = _queue_band(state.upper, state.quantity)
            with self.ledger.connection:
                self._insert_event(
                    state, observed_at, kind, price=trade.price,
                    quantity=quantity,
                    details={"trade_time": _utc(trade.trade_time),
                             "request_id": trade.request_id,
                             "prior_band": previous_band})
                self._persist(state, observed_at, force=True)

    def mark_coverage_gap(
        self, *, observed_at: datetime, dropped: int, reason: str,
        only_clean: bool = False,
    ) -> None:
        count = max(1, int(dropped))
        for state in tuple(self._active.values()):
            if only_clean and state.coverage_gaps:
                continue
            state.coverage_gaps += count
            with self.ledger.connection:
                self._insert_event(
                    state, observed_at, "COVERAGE_GAP", quantity=float(count),
                    details={"reason": str(reason)[:200]})
                self._persist(state, observed_at, force=True)

    def flush(self, *, observed_at: datetime) -> None:
        with self.ledger.connection:
            for state in tuple(self._active.values()):
                self._persist(state, observed_at, force=True)

    def start_stream_session(
        self, *, observed_at: datetime, slugs: tuple[str, ...],
    ) -> int:
        with self.ledger.connection:
            cursor = self.ledger.connection.execute(
                "INSERT INTO execution_queue_stream_sessions("
                "started_at,scope_json,scope_size,status,last_updated_at) "
                "VALUES (?,?,?,?,?)",
                (_utc(observed_at), json.dumps(list(slugs), separators=(",", ":")),
                 len(slugs), "COLLECTING", _utc(observed_at)))
        return int(cursor.lastrowid)

    def update_stream_session(
        self,
        session_id: int,
        *,
        observed_at: datetime,
        trades_received: int,
        dropped_trades: int,
        decode_failures: int,
        last_trade_at: datetime | None,
    ) -> None:
        with self.ledger.connection:
            self.ledger.connection.execute(
                "UPDATE execution_queue_stream_sessions SET "
                "trades_received=?,dropped_trades=?,decode_failures=?,"
                "last_trade_at=?,last_updated_at=? WHERE id=?",
                (max(0, int(trades_received)), max(0, int(dropped_trades)),
                 max(0, int(decode_failures)),
                 _utc(last_trade_at) if last_trade_at else None,
                 _utc(observed_at), int(session_id)))

    def finish_stream_session(
        self,
        session_id: int,
        *,
        observed_at: datetime,
        status: str,
        trades_received: int,
        dropped_trades: int,
        decode_failures: int,
        last_trade_at: datetime | None,
        error: str | None = None,
    ) -> None:
        clean_status = str(status).strip().upper()
        if clean_status not in {"REBOUND", "ERROR", "STOPPED"}:
            raise ValueError("unsupported queue stream terminal status")
        with self.ledger.connection:
            self.ledger.connection.execute(
                "UPDATE execution_queue_stream_sessions SET ended_at=?,status=?,"
                "trades_received=?,dropped_trades=?,decode_failures=?,"
                "last_trade_at=?,last_updated_at=?,error=? WHERE id=?",
                (_utc(observed_at), clean_status, max(0, int(trades_received)),
                 max(0, int(dropped_trades)), max(0, int(decode_failures)),
                 _utc(last_trade_at) if last_trade_at else None,
                 _utc(observed_at), str(error)[:300] if error else None,
                 int(session_id)))
