"""Durable, decimal-safe Phase 3 execution state.

The ledger owns no network client and cannot place an order. A future
execution adapter must commit an intent here before it performs a remote
mutation, then reconcile every ambiguous result against venue state.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Mapping


class LedgerConflict(RuntimeError):
    """Persisted truth conflicts with a proposed transition."""


INTENT_OPERATIONS = {"PLACE", "MODIFY", "CANCEL"}
UNRESOLVED_INTENTS = {"PREPARED", "SUBMITTING", "ACKNOWLEDGED", "UNCERTAIN"}
TERMINAL_ORDER_STATES = {"FILLED", "CANCELED", "CANCELLED", "REJECTED", "EXPIRED"}
ACTIVE_ORDER_STATES = {"OPEN", "NEW", "PARTIAL", "PENDING", "SUBMITTED"}


SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS execution_intents (
    intent_id TEXT PRIMARY KEY,
    operation TEXT NOT NULL,
    market_slug TEXT NOT NULL,
    client_order_id TEXT NOT NULL,
    exchange_order_id TEXT,
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    state TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    error TEXT,
    quote_observation_required INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_execution_intents_state
    ON execution_intents(state, market_slug);
CREATE TABLE IF NOT EXISTS execution_events (
    source TEXT NOT NULL,
    event_id TEXT NOT NULL,
    received_at TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    applied INTEGER NOT NULL,
    note TEXT,
    PRIMARY KEY (source, event_id)
);
CREATE TABLE IF NOT EXISTS execution_orders (
    client_order_id TEXT PRIMARY KEY,
    exchange_order_id TEXT UNIQUE,
    market_slug TEXT NOT NULL,
    side TEXT,
    price TEXT,
    original_qty TEXT,
    filled_qty TEXT NOT NULL,
    status TEXT NOT NULL,
    event_at TEXT NOT NULL,
    received_at TEXT NOT NULL,
    unknown_reason TEXT,
    reject_reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_execution_orders_market_status
    ON execution_orders(market_slug, status);
CREATE TABLE IF NOT EXISTS execution_fills (
    execution_id TEXT PRIMARY KEY,
    client_order_id TEXT,
    exchange_order_id TEXT,
    market_slug TEXT NOT NULL,
    side TEXT NOT NULL,
    price TEXT NOT NULL,
    qty TEXT NOT NULL,
    fee TEXT NOT NULL,
    liquidity TEXT,
    intent TEXT NOT NULL,
    event_at TEXT NOT NULL,
    received_at TEXT NOT NULL
,
    outcome TEXT);
CREATE TABLE IF NOT EXISTS execution_positions (
    market_slug TEXT PRIMARY KEY,
    net_qty TEXT NOT NULL,
    event_at TEXT NOT NULL,
    received_at TEXT NOT NULL,
    source TEXT NOT NULL,
    authoritative_through TEXT
,
    cost TEXT,
    first_seen_at TEXT);
CREATE TABLE IF NOT EXISTS execution_position_reconciliations (
    id INTEGER PRIMARY KEY,
    snapshot_id TEXT NOT NULL,
    market_slug TEXT NOT NULL,
    prior_qty TEXT NOT NULL,
    venue_qty TEXT NOT NULL,
    difference TEXT NOT NULL,
    as_of TEXT NOT NULL,
    observed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS execution_reward_earnings (
    date TEXT NOT NULL,
    market_slug TEXT NOT NULL,
    program_type TEXT NOT NULL,
    status TEXT NOT NULL,
    reward TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    PRIMARY KEY (date, market_slug, program_type, status, observed_at)
);
CREATE INDEX IF NOT EXISTS idx_execution_reward_market_observed
    ON execution_reward_earnings(market_slug, observed_at);
CREATE TABLE IF NOT EXISTS execution_loss_stop_days (
    utc_date TEXT PRIMARY KEY,
    first_tripped_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS execution_day_baselines (
    utc_date TEXT PRIMARY KEY,
    opening_equity TEXT NOT NULL,
    opening_realized TEXT NOT NULL,
    opening_cash TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS execution_quote_observations (
    intent_id TEXT PRIMARY KEY,
    client_order_id TEXT NOT NULL UNIQUE,
    market_slug TEXT NOT NULL,
    side TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    book_observed_at TEXT NOT NULL,
    game_start TEXT,
    quote_price TEXT NOT NULL,
    quantity TEXT NOT NULL,
    fill_probability REAL,
    fill_risk_driver TEXT,
    model_queue_ahead REAL,
    observed_queue_ahead REAL,
    target_size REAL,
    cutoff_price REAL,
    touch_changes_30s INTEGER NOT NULL,
    touch_changes_120s INTEGER NOT NULL,
    seconds_since_touch_change REAL,
    projected_reward REAL,
    expected_net REAL,
    stressed_exit_pnl REAL,
    trade_volume_30s REAL,
    trade_volume_120s REAL,
    observation_json TEXT NOT NULL,
    observation_hash TEXT NOT NULL,
    FOREIGN KEY(intent_id) REFERENCES execution_intents(intent_id)
);
CREATE INDEX IF NOT EXISTS idx_execution_quote_observations_market_at
    ON execution_quote_observations(market_slug, observed_at);
CREATE INDEX IF NOT EXISTS idx_execution_quote_observations_observed_at
    ON execution_quote_observations(observed_at);
CREATE TABLE IF NOT EXISTS execution_queue_measurements (
    client_order_id TEXT PRIMARY KEY,
    intent_id TEXT NOT NULL UNIQUE,
    market_slug TEXT NOT NULL,
    side TEXT NOT NULL,
    quote_price TEXT NOT NULL,
    quantity TEXT NOT NULL,
    started_at TEXT NOT NULL,
    initial_queue_ahead REAL NOT NULL,
    initial_better_depth REAL NOT NULL,
    initial_same_price_depth REAL NOT NULL,
    queue_lower_bound REAL NOT NULL,
    queue_upper_bound REAL NOT NULL,
    better_depth REAL NOT NULL,
    same_price_lower_bound REAL NOT NULL,
    same_price_upper_bound REAL NOT NULL,
    last_same_price_depth REAL NOT NULL,
    baseline_book_seen INTEGER NOT NULL DEFAULT 0,
    baseline_book_at TEXT,
    baseline_same_price_depth REAL,
    trade_at_price_qty REAL NOT NULL DEFAULT 0,
    trade_better_price_qty REAL NOT NULL DEFAULT 0,
    trade_through_qty REAL NOT NULL DEFAULT 0,
    ambiguous_removed_qty REAL NOT NULL DEFAULT 0,
    same_price_added_qty REAL NOT NULL DEFAULT 0,
    relevant_book_events INTEGER NOT NULL DEFAULT 0,
    relevant_trade_events INTEGER NOT NULL DEFAULT 0,
    coverage_gaps INTEGER NOT NULL DEFAULT 0,
    queue_band TEXT NOT NULL,
    last_filled_qty REAL NOT NULL DEFAULT 0,
    last_book_at TEXT,
    last_trade_at TEXT,
    last_persisted_at TEXT NOT NULL,
    would_reset_at TEXT,
    would_reset_queue_lower_bound REAL,
    would_reset_queue_upper_bound REAL,
    status TEXT NOT NULL,
    terminal_status TEXT,
    closed_at TEXT,
    close_queue_lower_bound REAL,
    close_queue_upper_bound REAL,
    measurement_version INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY(intent_id) REFERENCES execution_intents(intent_id)
);
CREATE INDEX IF NOT EXISTS idx_execution_queue_measurements_market_status
    ON execution_queue_measurements(market_slug, status, started_at);
CREATE TABLE IF NOT EXISTS execution_queue_measurement_events (
    id INTEGER PRIMARY KEY,
    client_order_id TEXT NOT NULL,
    event_at TEXT NOT NULL,
    event_kind TEXT NOT NULL,
    queue_lower_bound REAL NOT NULL,
    queue_upper_bound REAL NOT NULL,
    better_depth REAL NOT NULL,
    same_price_lower_bound REAL NOT NULL,
    same_price_upper_bound REAL NOT NULL,
    event_price REAL,
    event_quantity REAL,
    queue_band TEXT NOT NULL,
    details_json TEXT,
    FOREIGN KEY(client_order_id)
        REFERENCES execution_queue_measurements(client_order_id)
);
CREATE INDEX IF NOT EXISTS idx_execution_queue_events_order_at
    ON execution_queue_measurement_events(client_order_id, event_at);
CREATE TABLE IF NOT EXISTS execution_queue_stream_sessions (
    id INTEGER PRIMARY KEY,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    scope_json TEXT NOT NULL,
    scope_size INTEGER NOT NULL,
    status TEXT NOT NULL,
    trades_received INTEGER NOT NULL DEFAULT 0,
    dropped_trades INTEGER NOT NULL DEFAULT 0,
    decode_failures INTEGER NOT NULL DEFAULT 0,
    last_trade_at TEXT,
    last_updated_at TEXT NOT NULL,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_execution_queue_stream_started
    ON execution_queue_stream_sessions(started_at);
"""


def _time(value: datetime, field: str) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat()


def _decimal(value: Decimal | str | int | float, field: str) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field} is not a decimal") from exc
    if not result.is_finite():
        raise ValueError(f"{field} must be finite")
    return result


def _number(value: Decimal | str | int | float, field: str) -> str:
    return format(_decimal(value, field), "f")


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class OrderState:
    client_order_id: str
    market_slug: str
    side: str
    price: Decimal
    original_qty: Decimal
    filled_qty: Decimal
    status: str
    event_at: datetime
    exchange_order_id: str | None = None
    # Why the venue refused it. 203 rejections were unattributable because this
    # was thrown away: a rejected order never rests, and reward pays only for
    # resting time, so telling an ordinary post-only collision from a real
    # defect matters.
    reject_reason: str | None = None

    def __post_init__(self) -> None:
        if not self.client_order_id or not self.market_slug:
            raise ValueError("order identity is required")
        if self.side.upper() not in {
                "BUY", "SELL", "BID", "OFFER",
                "ORDER_SIDE_BUY", "ORDER_SIDE_SELL"}:
            raise ValueError("order side is invalid")
        price = _decimal(self.price, "price")
        original = _decimal(self.original_qty, "original_qty")
        filled = _decimal(self.filled_qty, "filled_qty")
        if not 0 < price < 1 or original <= 0 or filled < 0 or filled > original:
            raise ValueError("order quantities or price are invalid")
        _time(self.event_at, "event_at")


@dataclass(frozen=True)
class Execution:
    execution_id: str
    market_slug: str
    side: str
    price: Decimal
    qty: Decimal
    fee: Decimal
    event_at: datetime
    client_order_id: str | None = None
    exchange_order_id: str | None = None
    liquidity: str | None = None
    intent: str | None = None
    # WHICH outcome this price was quoted against. The venue prices an
    # execution on one specific outcome, and 0.67 on one side is 0.33 on the
    # other -- so a price without its outcome cannot be interpreted. Dropping
    # it made realized_pnl compute +$1.34 on a trade that lost $0.30
    # (aec-valorant-ff-ep, 2026-08-15), and realized_pnl is what the $40 stop
    # reads. Recorded now so the frame is recoverable; see task 24.
    outcome: str | None = None

    def __post_init__(self) -> None:
        if not self.execution_id or not self.market_slug:
            raise ValueError("execution identity is required")
        if self.side.upper() not in {
                "BUY", "SELL", "BID", "OFFER",
                "ORDER_SIDE_BUY", "ORDER_SIDE_SELL"}:
            raise ValueError("execution side is invalid")
        intent = str(self.intent or "").upper()
        if intent not in {
                "ORDER_INTENT_BUY_LONG", "ORDER_INTENT_SELL_LONG",
                "ORDER_INTENT_BUY_SHORT", "ORDER_INTENT_SELL_SHORT"}:
            raise ValueError("execution intent is required for economic direction")
        price = _decimal(self.price, "price")
        qty = _decimal(self.qty, "qty")
        fee = _decimal(self.fee, "fee")
        if not 0 < price < 1 or qty <= 0:
            raise ValueError("execution values are invalid")
        _time(self.event_at, "event_at")


@dataclass(frozen=True)
class PositionState:
    market_slug: str
    net_qty: Decimal
    event_at: datetime
    # What the venue says this position cost. Our own fills are the primary
    # source of basis, but a fill that never arrives leaves the basis
    # unknowable -- and no basis means no exit can be priced, which deadlocks:
    # _inventory_plan returns inventory_basis_or_clock_unknown, the market is
    # retired, and a retired market's exit still needs a basis. Observed
    # 2026-08-15 on demciva-hryole and ff-ep. The venue already knows the cost,
    # so stop rebuilding it -- the same move that fixed the loss stop.
    cost: Decimal | None = None

    def __post_init__(self) -> None:
        if not self.market_slug:
            raise ValueError("position market is required")
        _decimal(self.net_qty, "net_qty")
        _time(self.event_at, "event_at")
        if self.cost is not None:
            _decimal(self.cost, "cost")


@dataclass(frozen=True)
class InventoryBasis:
    net_qty: Decimal
    average_entry_price: Decimal
    opened_at: datetime


@dataclass(frozen=True)
class QuoteObservation:
    """Decision-time facts for one building post-only order.

    This record is deliberately descriptive.  No value in it is read by the
    reconciler or order policy.  The exact client order id is attached only
    when the durable PLACE intent is prepared, which lets later order and fill
    rows label this snapshot without market/time guessing.
    """

    market_slug: str
    side: str
    observed_at: datetime
    book_observed_at: datetime
    game_start: datetime | None
    quote_price: Decimal
    quantity: Decimal
    decision_reason: str
    best_bid: float
    best_bid_size: float
    best_offer: float
    best_offer_size: float
    spread: float
    bid_depth: float
    offer_depth: float
    model_queue_ahead: float
    observed_queue_ahead: float
    depth_better_than_quote: float
    depth_at_quote: float
    depth_through_quote: float
    touch_changes_30s: int
    touch_changes_120s: int
    seconds_since_touch_change: float | None
    seconds_to_event: float | None
    program_id: str
    reward_period: str
    reward_pool: float
    discount_factor: float
    target_size: float
    cutoff_price: float | None
    depth_to_reward_cutoff: float
    ticks_from_best: int | None
    order_score: float
    total_score: float
    snapshot_share: float
    projected_reward: float
    maximum_remaining_reward: float
    fill_probability: float
    fill_risk_driver: str
    stressed_exit_complete: bool
    stressed_exit_closed_qty: float
    stressed_exit_average_price: float | None
    stressed_exit_worst_price: float | None
    stressed_exit_fee: float
    stressed_exit_pnl: float
    expected_net: float
    reward_cost_safety_multiple: float
    # The live commissioning market stream currently subscribes to books, not
    # public trades.  Keep these explicitly NULL rather than inventing volume
    # from our own fills; a future trade subscription can populate them.
    trade_volume_30s: float | None = None
    trade_volume_120s: float | None = None

    def __post_init__(self) -> None:
        if not self.market_slug or self.side not in {"bid", "offer"}:
            raise ValueError("quote observation identity is incomplete")
        _time(self.observed_at, "observed_at")
        _time(self.book_observed_at, "book_observed_at")
        if self.game_start is not None:
            _time(self.game_start, "game_start")
        price = _decimal(self.quote_price, "quote_price")
        quantity = _decimal(self.quantity, "quantity")
        if not 0 < price < 1 or quantity <= 0:
            raise ValueError("quote observation order values are invalid")
        for name, value in asdict(self).items():
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError(f"{name} must be finite")
        if min(self.touch_changes_30s, self.touch_changes_120s) < 0:
            raise ValueError("touch change counts cannot be negative")


class ExecutionLedger:
    """SQLite system of record for intents and observed execution state."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.executescript(SCHEMA)
        self._add_missing_columns()
        self.connection.commit()

    # Additive-only migrations. `CREATE TABLE IF NOT EXISTS` silently leaves an
    # existing table alone, so a new column never reaches a database that has
    # already been created -- and this ledger is never rebuilt, because it is
    # what proves what we own at the venue.
    ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
        ("execution_intents", "quote_observation_required",
         "INTEGER NOT NULL DEFAULT 0"),
        ("execution_orders", "reject_reason", "TEXT"),
        ("execution_fills", "outcome", "TEXT"),
        ("execution_positions", "cost", "TEXT"),
        ("execution_positions", "first_seen_at", "TEXT"),
    )

    def _add_missing_columns(self) -> None:
        for table, column, kind in self.ADDED_COLUMNS:
            existing = {
                str(row["name"]) for row in
                self.connection.execute(f"PRAGMA table_info({table})")}
            if not existing:
                continue
            if column not in existing:
                self.connection.execute(
                    f"ALTER TABLE {table} ADD COLUMN {column} {kind}")

    def close(self) -> None:
        self.connection.close()

    def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        return list(self.connection.execute(sql, params).fetchall())

    def record_loss_stop(self, utc_date: str, *, at: datetime) -> None:
        """Note that a loss stop tripped on this UTC day. Idempotent.

        Kept in its own table rather than a column on `execution_day_baselines`
        so a loss stop can never disturb the equity marks the stops themselves
        read. Only the FIRST trip of a day is stored: the guard re-evaluates
        every pass while tripped, and what matters is the day, not the count.
        """

        with self.connection:
            self.connection.execute(
                "INSERT OR IGNORE INTO execution_loss_stop_days"
                "(utc_date,first_tripped_at) VALUES (?,?)",
                (str(utc_date), _time(at, "at")))

    def consecutive_loss_stop_days(self, utc_date: str) -> int:
        """How many days in a row, ending at ``utc_date``, hit a loss stop.

        A loss stop that pauses until the next day is convenient and, on its
        own, unbounded: the day baseline resets at midnight, so a strategy that
        is systematically wrong rather than merely unlucky would lose its full
        budget every day forever with nothing forcing anyone to look. The
        emergency equity ceiling does not catch it either, because that
        re-baselines each morning too.

        One bad day is variance. Several in a row is a broken model, and the
        caller escalates to a terminal stop at that point.
        """

        rows = {str(row["utc_date"]) for row in self.connection.execute(
            "SELECT utc_date FROM execution_loss_stop_days")}
        try:
            day = date.fromisoformat(str(utc_date))
        except ValueError:
            return 0
        streak = 0
        while day.isoformat() in rows:
            streak += 1
            day -= timedelta(days=1)
        return streak

    def day_baseline(
        self,
        utc_date: str,
        *,
        opening_equity: Decimal,
        opening_realized: Decimal,
        opening_cash: Decimal | None,
        created_at: datetime,
    ) -> tuple[Decimal, Decimal, Decimal | None]:
        """Return the UTC day's opening marks, writing them only if absent.

        The loss stops used to baseline per PROCESS. Ten sessions started on
        2026-08-18, so the day's $40 budget was handed back ten times, and the
        $45 hard stop was worse: it re-stamped `starting_equity` at whatever
        equity happened to be, so a $30 loss followed by a crash was forgiven
        and it took another $45 from the LOWER floor to fire. A bad day could
        step down indefinitely with every individual step inside the limit.

        `INSERT OR IGNORE` is the whole mechanism: the first process of the
        day writes the marks and every later one reads them back, so a restart
        inherits the day's drawdown instead of being handed a fresh budget. A
        new UTC date has no row and therefore does get a fresh one, which is
        the intended reset.

        Returns the stored values, which are the caller's baseline whether or
        not this call is the one that wrote them.
        """

        date_key = str(utc_date)
        with self.connection:
            self.connection.execute(
                "INSERT OR IGNORE INTO execution_day_baselines"
                "(utc_date,opening_equity,opening_realized,opening_cash,"
                "created_at) VALUES (?,?,?,?,?)",
                (date_key, str(opening_equity), str(opening_realized),
                 None if opening_cash is None else str(opening_cash),
                 _time(created_at, "created_at")))
        row = self.connection.execute(
            "SELECT opening_equity,opening_realized,opening_cash "
            "FROM execution_day_baselines WHERE utc_date=?",
            (date_key,)).fetchone()
        if row is None:  # pragma: no cover - the insert above guarantees it
            return opening_equity, opening_realized, opening_cash
        stored_cash = row["opening_cash"]
        return (
            Decimal(str(row["opening_equity"])),
            Decimal(str(row["opening_realized"])),
            None if stored_cash is None else Decimal(str(stored_cash)))

    def record_reward_earnings(
        self, earnings: Iterable[Any], *, observed_at: datetime,
    ) -> None:
        """Append authenticated reward observations without calling them P&L."""

        at = _time(observed_at, "observed_at")
        rows = []
        for item in earnings:
            date = str(getattr(item, "date", "") or "").strip()
            slug = str(getattr(item, "market_slug", "") or "").strip()
            program_type = str(
                getattr(item, "program_type", "") or "").strip()
            status = str(getattr(item, "status", "") or "").strip().upper()
            reward = _number(getattr(item, "reward", None), "reward")
            if not date or not slug or not program_type or not status:
                raise ValueError("reward earning identity is incomplete")
            if _decimal(reward, "reward") < 0:
                raise ValueError("reward earning cannot be negative")
            rows.append((date, slug, program_type, status, reward, at))
        with self.connection:
            self.connection.executemany(
                "INSERT OR IGNORE INTO execution_reward_earnings"
                "(date,market_slug,program_type,status,reward,observed_at) "
                "VALUES (?,?,?,?,?,?)",
                rows,
            )

    def prepare_intent(
        self,
        intent_id: str,
        operation: str,
        market_slug: str,
        client_order_id: str,
        payload: Mapping[str, Any],
        *,
        created_at: datetime,
        quote_observation: QuoteObservation | None = None,
        quote_observation_required: bool = False,
    ) -> bool:
        operation = operation.upper()
        if operation not in INTENT_OPERATIONS:
            raise ValueError("unsupported intent operation")
        if not intent_id or not market_slug or not client_order_id:
            raise ValueError("intent identity is incomplete")
        at = _time(created_at, "created_at")
        body = _json(dict(payload))
        digest = _hash(dict(payload))
        observation_required = int(bool(quote_observation_required))
        if quote_observation is not None:
            if operation != "PLACE":
                raise ValueError("quote observations belong only to PLACE intents")
            if quote_observation.market_slug != market_slug:
                raise ValueError("quote observation market does not match intent")
        with self.connection:
            row = self.connection.execute(
                "SELECT operation,market_slug,client_order_id,payload_hash,"
                "quote_observation_required "
                "FROM execution_intents WHERE intent_id=?", (intent_id,)).fetchone()
            if row is not None:
                existing = tuple(row)
                proposed = (operation, market_slug, client_order_id, digest,
                            observation_required)
                if existing != proposed:
                    raise LedgerConflict("intent id was reused with different content")
                if quote_observation is not None:
                    self._record_quote_observation(
                        intent_id, client_order_id, quote_observation)
                return False
            self.connection.execute(
                "INSERT INTO execution_intents(intent_id,operation,market_slug,"
                "client_order_id,payload_json,payload_hash,state,created_at,updated_at,"
                "quote_observation_required) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (intent_id, operation, market_slug, client_order_id, body,
                 digest, "PREPARED", at, at, observation_required))
            if quote_observation is not None:
                self._record_quote_observation(
                    intent_id, client_order_id, quote_observation)
        return True

    def _record_quote_observation(
        self,
        intent_id: str,
        client_order_id: str,
        observation: QuoteObservation,
    ) -> None:
        """Insert one immutable observation inside the intent transaction."""

        document = asdict(observation)
        body = _json(document)
        digest = _hash(document)
        existing = self.connection.execute(
            "SELECT observation_hash FROM execution_quote_observations "
            "WHERE intent_id=?", (intent_id,)).fetchone()
        if existing is not None:
            if str(existing["observation_hash"]) != digest:
                raise LedgerConflict(
                    "quote observation intent was reused with different content")
            return
        self.connection.execute(
            "INSERT INTO execution_quote_observations("
            "intent_id,client_order_id,market_slug,side,observed_at,"
            "book_observed_at,game_start,quote_price,quantity,fill_probability,"
            "fill_risk_driver,model_queue_ahead,observed_queue_ahead,target_size,"
            "cutoff_price,touch_changes_30s,touch_changes_120s,"
            "seconds_since_touch_change,projected_reward,expected_net,"
            "stressed_exit_pnl,trade_volume_30s,trade_volume_120s,"
            "observation_json,observation_hash) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                intent_id,
                client_order_id,
                observation.market_slug,
                observation.side,
                _time(observation.observed_at, "observed_at"),
                _time(observation.book_observed_at, "book_observed_at"),
                (_time(observation.game_start, "game_start")
                 if observation.game_start is not None else None),
                _number(observation.quote_price, "quote_price"),
                _number(observation.quantity, "quantity"),
                observation.fill_probability,
                observation.fill_risk_driver,
                observation.model_queue_ahead,
                observation.observed_queue_ahead,
                observation.target_size,
                observation.cutoff_price,
                observation.touch_changes_30s,
                observation.touch_changes_120s,
                observation.seconds_since_touch_change,
                observation.projected_reward,
                observation.expected_net,
                observation.stressed_exit_pnl,
                observation.trade_volume_30s,
                observation.trade_volume_120s,
                body,
                digest,
            ),
        )

    def transition_intent(
        self,
        intent_id: str,
        state: str,
        *,
        updated_at: datetime,
        exchange_order_id: str | None = None,
        error: str | None = None,
    ) -> None:
        state = state.upper()
        allowed = {
            "PREPARED": {"SUBMITTING", "FAILED"},
            "SUBMITTING": {"ACKNOWLEDGED", "UNCERTAIN", "FAILED"},
            "UNCERTAIN": {"RECONCILED", "FAILED"},
            "ACKNOWLEDGED": {"RECONCILED"},
            "RECONCILED": set(),
            "FAILED": set(),
        }
        with self.connection:
            row = self.connection.execute(
                "SELECT state FROM execution_intents WHERE intent_id=?",
                (intent_id,)).fetchone()
            if row is None:
                raise KeyError(intent_id)
            current = str(row["state"])
            if state == current:
                return
            if state not in allowed.get(current, set()):
                raise LedgerConflict(f"invalid intent transition {current} -> {state}")
            self.connection.execute(
                "UPDATE execution_intents SET state=?,updated_at=?,"
                "exchange_order_id=COALESCE(?,exchange_order_id),error=? "
                "WHERE intent_id=?",
                (state, _time(updated_at, "updated_at"), exchange_order_id,
                 error, intent_id))

    def unresolved_intents(
        self, market_slug: str | None = None,
    ) -> list[sqlite3.Row]:
        """Intents whose venue outcome is not yet proven.

        ``market_slug`` narrows to one market. Callers deciding whether the
        whole account is safe to stop or rotate must keep passing nothing;
        only per-market planning may scope it.
        """

        placeholders = ",".join("?" for _ in UNRESOLVED_INTENTS)
        sql = (f"SELECT * FROM execution_intents WHERE state IN ({placeholders})")
        params: list[Any] = list(sorted(UNRESOLVED_INTENTS))
        if market_slug is not None:
            sql += " AND market_slug=?"
            params.append(market_slug)
        return self.query(sql + " ORDER BY created_at", tuple(params))

    @staticmethod
    def _intent_matches_raw_order(payload: Mapping[str, Any],
                                  raw: Mapping[str, Any]) -> bool:
        metadata = raw.get("marketMetadata") or raw.get("market_metadata") or {}
        raw_slug = (raw.get("marketSlug") or raw.get("market_slug")
                    or (metadata.get("slug")
                        if isinstance(metadata, Mapping) else None))
        raw_price = raw.get("price")
        if isinstance(raw_price, Mapping):
            raw_price = raw_price.get("value")
        payload_price = payload.get("price")
        if isinstance(payload_price, Mapping):
            payload_price = payload_price.get("value")
        try:
            same_price = (_decimal(raw_price, "order price")
                          == _decimal(payload_price, "intent price"))
            same_qty = (_decimal(raw.get("quantity"), "order quantity")
                        == _decimal(payload.get("quantity"), "intent quantity"))
        except ValueError:
            return False
        return (
            str(raw_slug or "") == str(payload.get("marketSlug") or "")
            and str(raw.get("intent") or "") == str(payload.get("intent") or "")
            and same_price and same_qty)

    def resolve_client_order_id(
        self, exchange_order_id: str,
        raw_order: Mapping[str, Any] | None = None,
    ) -> str | None:
        """Resolve the bot-owned identity associated with an exchange order.

        Retail order entry does not expose a client-order-id field. The
        durable intent is therefore the ownership anchor and the exchange ID
        returned by create is bound to it before private events are applied.
        """

        clean = str(exchange_order_id or "").strip()
        if not clean:
            return None
        row = self.connection.execute(
            "SELECT client_order_id FROM execution_intents "
            "WHERE exchange_order_id=? ORDER BY created_at LIMIT 1",
            (clean,)).fetchone()
        if row is None:
            row = self.connection.execute(
                "SELECT client_order_id FROM execution_orders "
                "WHERE exchange_order_id=?", (clean,)).fetchone()
        if row is not None and not str(row["client_order_id"]).startswith("venue:"):
            return str(row["client_order_id"])
        if raw_order is None:
            return str(row["client_order_id"]) if row is not None else None

        candidates = self.connection.execute(
            "SELECT intent_id,client_order_id,payload_json FROM execution_intents "
            "WHERE operation='PLACE' AND exchange_order_id IS NULL AND state IN "
            "('SUBMITTING','UNCERTAIN') ORDER BY created_at"
        ).fetchall()
        matches = []
        for candidate in candidates:
            try:
                payload = json.loads(candidate["payload_json"])
            except (TypeError, json.JSONDecodeError):
                continue
            if (isinstance(payload, Mapping)
                    and self._intent_matches_raw_order(payload, raw_order)):
                matches.append(candidate)
        if len(matches) != 1:
            return str(row["client_order_id"]) if row is not None else None
        match = matches[0]
        client_id = str(match["client_order_id"])
        with self.connection:
            self.connection.execute(
                "UPDATE execution_intents SET exchange_order_id=?,updated_at=? "
                "WHERE intent_id=? AND exchange_order_id IS NULL",
                (clean, datetime.now(timezone.utc).isoformat(), match["intent_id"]))
            if row is not None and str(row["client_order_id"]).startswith("venue:"):
                old_id = str(row["client_order_id"])
                collision = self.connection.execute(
                    "SELECT 1 FROM execution_orders WHERE client_order_id=?",
                    (client_id,)).fetchone()
                if collision is not None:
                    raise LedgerConflict(
                        "cannot adopt exchange order: client identity already exists")
                self.connection.execute(
                    "UPDATE execution_orders SET client_order_id=? "
                    "WHERE client_order_id=?", (client_id, old_id))
                self.connection.execute(
                    "UPDATE execution_fills SET client_order_id=? "
                    "WHERE client_order_id=?", (client_id, old_id))
        return client_id

    def order_is_active(self, exchange_order_id: str) -> bool:
        """Does the venue's latest word on this order say it is still live?

        Positive evidence only. `UNKNOWN_ABSENT` is not active and neither is
        a missing row -- both mean we do not know, and the one caller of this
        re-issues a cancel on the strength of the answer, so "we do not know"
        must never read as "still there".
        """

        placeholders = ",".join("?" for _ in ACTIVE_ORDER_STATES)
        params = (exchange_order_id, *tuple(sorted(ACTIVE_ORDER_STATES)))
        return self.connection.execute(
            f"SELECT 1 FROM execution_orders WHERE exchange_order_id=? "
            f"AND status IN ({placeholders}) LIMIT 1", params).fetchone() is not None

    def market_has_unresolved_intent(self, market_slug: str) -> bool:
        placeholders = ",".join("?" for _ in UNRESOLVED_INTENTS)
        params = (market_slug, *tuple(sorted(UNRESOLVED_INTENTS)))
        return self.connection.execute(
            f"SELECT 1 FROM execution_intents WHERE market_slug=? "
            f"AND state IN ({placeholders}) LIMIT 1", params).fetchone() is not None

    def working_orders(self, market_slug: str | None = None,
                       *, owned_only: bool = False) -> list[sqlite3.Row]:
        sql = ("SELECT * FROM execution_orders WHERE status IN "
               "('OPEN','NEW','PARTIAL','PENDING','SUBMITTED')")
        params: list[Any] = []
        if market_slug is not None:
            sql += " AND market_slug=?"
            params.append(market_slug)
        if owned_only:
            sql += " AND client_order_id NOT LIKE 'venue:%'"
        return self.query(sql + " ORDER BY event_at", tuple(params))

    def absent_orders(self, market_slug: str | None = None) -> list[sqlite3.Row]:
        """Orders we lost sight of, so they can be asked about again.

        `UNKNOWN_ABSENT` is deliberately not a working state -- it is our
        failure to see an order, not a venue transition. But that also kept it
        out of `working_orders`, which is the set `_rest_reconcile` re-reads
        from the venue, and its intent is already RECONCILED, so it fell out of
        the other re-read set too. Nothing asked about it ever again and it sat
        absent for the rest of the session.

        That is not cosmetic: `risk_state_known(None)` counts absences
        account-wide, so one stuck row made the account-wide gate false
        forever, and every stream bind timed out waiting for a readiness that
        could no longer arrive. See BUGFIXES 2026-08-16.
        """

        sql = "SELECT * FROM execution_orders WHERE status='UNKNOWN_ABSENT'"
        params: list[Any] = []
        if market_slug is not None:
            sql += " AND market_slug=?"
            params.append(market_slug)
        return self.query(sql + " ORDER BY event_at", tuple(params))

    def resolve_absent_order_in_settled_market(
        self, exchange_order_id: str, *, observed_at: datetime, reason: str,
    ) -> bool:
        """Close out an absence the venue can no longer answer.

        `absent_orders` exists to be re-asked, and normally the venue answers.
        It cannot once the market has **settled**: `retrieve` returns NotFound
        forever, the row stays `UNKNOWN_ABSENT`, and because
        `risk_state_known()` counts absences account-wide, market rotation is
        blocked for the rest of the session. Measured 2026-08-17: 19 such rows,
        the oldest 18 hours old, `_rotation_blockers()` returning
        "whole-account execution state is not known" on every pass, and the bot
        pinned to one market while nineteen slots sat idle. It degrades
        monotonically and survives restarts, because the state is durable here.

        **This is not reading absence as cancellation.** That rule exists
        because an unseen order might still be working, and acting on that
        guess risks trading against ourselves. A settled market removes the
        guess: no order can be live in a market the venue has resolved. The
        caller must prove that settlement first -- this method only writes.

        Deliberately narrow: `UNKNOWN_ABSENT` only, never a working or already
        terminal row, and it reports whether it changed anything so a caller
        cannot mistake a no-op for a resolution.
        """

        received = _time(observed_at, "observed_at")
        with self.connection:
            cursor = self.connection.execute(
                "UPDATE execution_orders SET status='EXPIRED',"
                " unknown_reason=?, received_at=?"
                " WHERE exchange_order_id=? AND status='UNKNOWN_ABSENT'",
                (f"settled_market:{reason}"[:200], received,
                 str(exchange_order_id)))
            return cursor.rowcount > 0

    def position(self, market_slug: str) -> Decimal:
        row = self.connection.execute(
            "SELECT net_qty FROM execution_positions WHERE market_slug=?",
            (market_slug,)).fetchone()
        return (_decimal(row["net_qty"], "net_qty")
                if row is not None else Decimal("0"))

    def inventory_basis(self, market_slug: str) -> InventoryBasis | None:
        """Rebuild the current run's open basis from immutable fills.

        Startup requires a flat account, so any nonzero live position must be
        explainable entirely by fills in this ledger. A mismatch with the
        venue-authoritative exact position returns unknown rather than guessing.
        """

        rows = self.connection.execute(
            "SELECT qty,price,intent,event_at FROM execution_fills "
            "WHERE market_slug=? ORDER BY event_at,received_at,execution_id",
            (market_slug,)).fetchall()
        net = Decimal("0")
        average: Decimal | None = None
        opened_at: datetime | None = None
        for row in rows:
            qty = self._signed_qty(
                str(row["intent"]), _decimal(row["qty"], "fill qty"))
            price = _decimal(row["price"], "fill price")
            at = datetime.fromisoformat(str(row["event_at"]))
            if net == 0:
                net, average, opened_at = qty, price, at
                continue
            if (net > 0) == (qty > 0):
                combined = abs(net) + abs(qty)
                average = ((average * abs(net) + price * abs(qty)) / combined
                           if average is not None else price)
                net += qty
                continue
            prior_sign = 1 if net > 0 else -1
            net += qty
            if net == 0:
                average, opened_at = None, None
            elif (1 if net > 0 else -1) != prior_sign:
                # The closing fill crossed through flat. Its residual opens
                # the new direction at this fill's price and time.
                average, opened_at = price, at
        authoritative = self.position(market_slug)
        if authoritative == 0:
            return None
        if (net != authoritative or average is None or opened_at is None
                or opened_at.tzinfo is None or opened_at.utcoffset() is None):
            # Fills cannot explain the position -- most often because the fill
            # that opened it never arrived. Fall back to what the VENUE says it
            # cost. Without this there is no basis, so no exit can be priced,
            # and _inventory_plan returns inventory_basis_or_clock_unknown --
            # which retires the market, whose exit then still needs a basis.
            # That deadlock stranded demciva-hryole and ff-ep on 2026-08-15.
            #
            # The clock uses first_seen_at, not a fill time we do not have.
            # It can only make the deadline LATER, never skip it, which is the
            # safe direction.
            return self._venue_basis(market_slug, authoritative)
        return InventoryBasis(authoritative, average, opened_at)

    def _venue_basis(
        self, market_slug: str, authoritative: Decimal,
    ) -> InventoryBasis | None:
        row = self.connection.execute(
            "SELECT cost,first_seen_at FROM execution_positions "
            "WHERE market_slug=?", (market_slug,)).fetchone()
        if row is None or row["cost"] is None or row["first_seen_at"] is None:
            return None
        try:
            cost = _decimal(str(row["cost"]), "position cost")
            seen = datetime.fromisoformat(str(row["first_seen_at"]))
        except Exception:
            return None
        if not cost or seen.tzinfo is None or seen.utcoffset() is None:
            return None
        # Cost is what the position cost in total; basis is per contract.
        return InventoryBasis(
            authoritative, abs(cost) / abs(authoritative), seen)

    def _accept_event(
        self, source: str, event_id: str, received_at: str,
        payload: Any, *, note: str | None = None,
    ) -> bool:
        digest = _hash(payload)
        cursor = self.connection.execute(
            "INSERT OR IGNORE INTO execution_events(source,event_id,received_at,"
            "payload_hash,applied,note) VALUES (?,?,?,?,0,?)",
            (source, event_id, received_at, digest, note))
        if cursor.rowcount == 1:
            return True
        prior = self.connection.execute(
            "SELECT payload_hash,applied FROM execution_events "
            "WHERE source=? AND event_id=?",
            (source, event_id)).fetchone()
        if prior is None or prior["payload_hash"] != digest:
            raise LedgerConflict("event id was reused with different content")
        # An event that was recorded but never applied may be retried. Dedup
        # exists to stop the same effect landing twice; a rejected event had no
        # effect, and refusing to reprocess it makes the rejection permanent.
        # A venue order id embeds a fixed lastTransactTime, so a cancel that
        # was rejected once could never be re-offered under a new id -- the
        # order stayed unreconcilable for the life of the ledger, and no code
        # fix could heal it because the retry never reached the fixed logic.
        if not int(prior["applied"]):
            return True
        return False

    def _finish_event(self, source: str, event_id: str, applied: bool,
                      note: str | None = None) -> None:
        self.connection.execute(
            "UPDATE execution_events SET applied=?,note=COALESCE(?,note) "
            "WHERE source=? AND event_id=?",
            (int(applied), note, source, event_id))

    def _upsert_order(self, order: OrderState, received: str) -> bool:
        event_at = _time(order.event_at, "event_at")
        status = order.status.upper()
        side = order.side.upper()
        price = _number(order.price, "price")
        original = _decimal(order.original_qty, "original_qty")
        filled = _decimal(order.filled_qty, "filled_qty")
        row = self.connection.execute(
            "SELECT * FROM execution_orders WHERE client_order_id=?",
            (order.client_order_id,)).fetchone()
        if row is not None:
            current_status = str(row["status"])
            # UNKNOWN_ABSENT records our failure to see an order, not a venue
            # transition, so it must never outrank the venue's own terminal
            # record. It was stamped with local wall clock; a cancel confirmed
            # microseconds earlier then looked "stale" and was dropped, leaving
            # the order stuck absent and its CANCEL intent unreconciled for the
            # rest of the session. That trips the 10-second unresolved-mutation
            # guard, and every quote cycle ends in a cancel.
            resolves_absence = (
                current_status == "UNKNOWN_ABSENT"
                and status in TERMINAL_ORDER_STATES)
            if event_at < row["event_at"] and not resolves_absence:
                return False
            if current_status in TERMINAL_ORDER_STATES and status not in TERMINAL_ORDER_STATES:
                return False
            stored_filled = _decimal(row["filled_qty"], "stored filled_qty")
            if filled < stored_filled:
                filled = stored_filled
            stored_original = _decimal(row["original_qty"], "stored original_qty")
            if original != stored_original:
                raise LedgerConflict("order original quantity changed")
            if row["market_slug"] != order.market_slug or row["side"] != side:
                raise LedgerConflict("order identity changed")
        known_fills = self.connection.execute(
            "SELECT qty FROM execution_fills WHERE client_order_id=? OR "
            "(? IS NOT NULL AND exchange_order_id=?)",
            (order.client_order_id, order.exchange_order_id,
             order.exchange_order_id)).fetchall()
        execution_total = sum(
            (_decimal(item["qty"], "fill qty") for item in known_fills),
            Decimal("0"))
        filled = max(filled, execution_total)
        if filled > original:
            raise LedgerConflict("known fills exceed order quantity")
        if filled >= original:
            status = "FILLED"
        elif filled > 0 and status in ACTIVE_ORDER_STATES:
            status = "PARTIAL"
        self.connection.execute(
            "INSERT INTO execution_orders(client_order_id,exchange_order_id,"
            "market_slug,side,price,original_qty,filled_qty,status,event_at,"
            "received_at,unknown_reason,reject_reason) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,NULL,?) "
            "ON CONFLICT(client_order_id) DO UPDATE SET "
            "exchange_order_id=COALESCE(excluded.exchange_order_id,exchange_order_id),"
            "price=excluded.price,filled_qty=excluded.filled_qty,status=excluded.status,"
            "event_at=excluded.event_at,received_at=excluded.received_at,"
            "unknown_reason=NULL,"
            # Keep a reason once seen. A later snapshot of the same rejected
            # order carries no reason, and overwriting with NULL would erase
            # the only explanation we ever get.
            "reject_reason=COALESCE(excluded.reject_reason,reject_reason)",
            (order.client_order_id, order.exchange_order_id, order.market_slug,
             side, price, _number(original, "original_qty"),
             _number(filled, "filled_qty"), status, event_at, received,
             order.reject_reason))
        if order.exchange_order_id:
            # A create is proven once any exchange state for its returned ID
            # arrives. Cancel/modify are proven only by a terminal state; an
            # accepted HTTP response is not cancellation confirmation.
            self.connection.execute(
                "UPDATE execution_intents SET state='RECONCILED',updated_at=? "
                "WHERE exchange_order_id=? AND state IN "
                "('SUBMITTING','ACKNOWLEDGED','UNCERTAIN') AND (operation='PLACE' OR "
                "(operation IN ('CANCEL','MODIFY') AND ? IN "
                "('FILLED','CANCELED','CANCELLED','REJECTED','EXPIRED')))",
                (received, order.exchange_order_id, status))
        return True

    def apply_order_event(
        self, source: str, event_id: str, order: OrderState,
        *, received_at: datetime,
    ) -> bool:
        received = _time(received_at, "received_at")
        payload = asdict(order)
        with self.connection:
            if not self._accept_event(source, event_id, received, payload):
                return False
            applied = self._upsert_order(order, received)
            self._finish_event(source, event_id, applied,
                               None if applied else "stale_order_event")
        return applied

    def apply_order_snapshot(
        self,
        snapshot_id: str,
        orders: Iterable[OrderState],
        *,
        as_of: datetime,
        observed_at: datetime,
        complete: bool,
    ) -> bool:
        rows = tuple(orders)
        received = _time(observed_at, "observed_at")
        snapshot_at = _time(as_of, "as_of")
        payload = {"as_of": snapshot_at, "complete": complete,
                   "orders": [asdict(row) for row in rows]}
        with self.connection:
            if not self._accept_event("order_snapshot", snapshot_id, received, payload):
                return False
            seen: set[str] = set()
            applied_any = False
            for order in rows:
                seen.add(order.client_order_id)
                applied_any = self._upsert_order(order, received) or applied_any
            if complete:
                active = self.connection.execute(
                    "SELECT client_order_id,event_at FROM execution_orders WHERE status "
                    "IN ('OPEN','NEW','PARTIAL','PENDING','SUBMITTED')").fetchall()
                for item in active:
                    if item["client_order_id"] in seen or snapshot_at < item["event_at"]:
                        continue
                    # Deliberately does not advance event_at. Absence is not
                    # venue evidence and must not take a position on the
                    # venue's event timeline ahead of a real terminal state.
                    self.connection.execute(
                        "UPDATE execution_orders SET status='UNKNOWN_ABSENT',"
                        "unknown_reason='missing_from_complete_snapshot',"
                        "received_at=? WHERE client_order_id=?",
                        (received, item["client_order_id"]))
                    applied_any = True
            self._finish_event("order_snapshot", snapshot_id, True,
                               "snapshot_applied" if applied_any else "snapshot_no_change")
        return True

    @staticmethod
    def _signed_qty(intent: str, qty: Decimal) -> Decimal:
        normalized = intent.upper()
        if normalized in {"ORDER_INTENT_BUY_LONG", "ORDER_INTENT_SELL_SHORT"}:
            return qty
        if normalized in {"ORDER_INTENT_BUY_SHORT", "ORDER_INTENT_SELL_LONG"}:
            return -qty
        raise ValueError("execution intent is required for economic direction")

    def apply_execution(
        self, execution: Execution, *, received_at: datetime,
    ) -> bool:
        received = _time(received_at, "received_at")
        event_at = _time(execution.event_at, "event_at")
        qty = _decimal(execution.qty, "qty")
        payload = asdict(execution)
        with self.connection:
            if not self._accept_event(
                    "execution", execution.execution_id, received, payload):
                return False
            self.connection.execute(
                "INSERT INTO execution_fills(execution_id,client_order_id,"
                "exchange_order_id,market_slug,side,price,qty,fee,liquidity,"
                "intent,event_at,received_at,outcome) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (execution.execution_id, execution.client_order_id,
                 execution.exchange_order_id, execution.market_slug,
                 execution.side.upper(), _number(execution.price, "price"),
                 _number(qty, "qty"), _number(execution.fee, "fee"),
                 execution.liquidity, execution.intent, event_at, received,
                 execution.outcome))
            position = self.connection.execute(
                "SELECT net_qty,event_at,source,authoritative_through "
                "FROM execution_positions WHERE market_slug=?",
                (execution.market_slug,)).fetchone()
            prior = (_decimal(position["net_qty"], "net_qty")
                     if position is not None else Decimal("0"))
            # A cumulative position update/snapshot at or after this execution
            # already includes it. Persist the immutable fill, but do not add
            # it to venue-authoritative quantity a second time.
            included_by_position = (
                position is not None
                and position["authoritative_through"] is not None
                and event_at <= position["authoritative_through"])
            net = (prior if included_by_position
                   else prior + self._signed_qty(str(execution.intent), qty))
            position_at = max(event_at, position["event_at"] if position else event_at)
            if not included_by_position:
                self.connection.execute(
                    "INSERT INTO execution_positions(market_slug,net_qty,event_at,"
                    "received_at,source,authoritative_through) VALUES (?,?,?,?,?,?) "
                    "ON CONFLICT(market_slug) "
                    "DO UPDATE SET net_qty=excluded.net_qty,event_at=excluded.event_at,"
                    "received_at=excluded.received_at,source=excluded.source,"
                    "authoritative_through=COALESCE(authoritative_through,"
                    "excluded.authoritative_through)",
                    (execution.market_slug, _number(net, "net_qty"), position_at,
                     received, "execution",
                     position["authoritative_through"] if position else None))
            if execution.client_order_id:
                order = self.connection.execute(
                    "SELECT filled_qty,original_qty,status FROM execution_orders "
                    "WHERE client_order_id=?", (execution.client_order_id,)).fetchone()
                if order is not None:
                    observed_total = sum(
                        (_decimal(item["qty"], "fill qty") for item in
                         self.connection.execute(
                             "SELECT qty FROM execution_fills WHERE client_order_id=?",
                             (execution.client_order_id,)).fetchall()),
                        Decimal("0"))
                    # Order updates carry a cumulative filled quantity while
                    # execution messages carry increments. Take their maximum;
                    # adding both would double count the same trade.
                    filled = max(
                        _decimal(order["filled_qty"], "filled_qty"), observed_total)
                    original = _decimal(order["original_qty"], "original_qty")
                    if filled > original:
                        raise LedgerConflict("fills exceed order quantity")
                    status = "FILLED" if filled == original else "PARTIAL"
                    self.connection.execute(
                        "UPDATE execution_orders SET filled_qty=?,status=?,"
                        "event_at=?,received_at=? WHERE client_order_id=?",
                        (_number(filled, "filled_qty"), status,
                         max(event_at, self.connection.execute(
                             "SELECT event_at FROM execution_orders WHERE client_order_id=?",
                             (execution.client_order_id,)).fetchone()[0]),
                         received, execution.client_order_id))
            self._finish_event("execution", execution.execution_id, True)
        return True

    def apply_position_event(
        self, source: str, event_id: str, position: PositionState,
        *, received_at: datetime,
    ) -> bool:
        received = _time(received_at, "received_at")
        event_at = _time(position.event_at, "event_at")
        with self.connection:
            if not self._accept_event(source, event_id, received, asdict(position)):
                return False
            prior = self.connection.execute(
                "SELECT event_at FROM execution_positions WHERE market_slug=?",
                (position.market_slug,)).fetchone()
            applied = prior is None or event_at >= prior["event_at"]
            if applied:
                self.connection.execute(
                    "INSERT INTO execution_positions(market_slug,net_qty,event_at,"
                    "received_at,source,authoritative_through) VALUES (?,?,?,?,?,?) "
                    "ON CONFLICT(market_slug) "
                    "DO UPDATE SET net_qty=excluded.net_qty,event_at=excluded.event_at,"
                    "received_at=excluded.received_at,source=excluded.source,"
                    "authoritative_through=excluded.authoritative_through",
                    (position.market_slug, _number(position.net_qty, "net_qty"),
                     event_at, received, source, event_at))
            self._finish_event(source, event_id, applied,
                               None if applied else "stale_position_event")
        return applied

    def apply_position_snapshot(
        self,
        snapshot_id: str,
        positions: Iterable[PositionState],
        *,
        as_of: datetime,
        observed_at: datetime,
        complete_account: bool,
    ) -> bool:
        rows = tuple(positions)
        snapshot_at = _time(as_of, "as_of")
        received = _time(observed_at, "observed_at")
        payload = {"as_of": snapshot_at, "complete_account": complete_account,
                   "positions": [asdict(row) for row in rows]}
        with self.connection:
            if not self._accept_event("position_snapshot", snapshot_id,
                                      received, payload):
                return False
            venue = {row.market_slug: _decimal(row.net_qty, "net_qty") for row in rows}
            # The venue's own cost per market, used only as a basis fallback.
            costs = {row.market_slug: getattr(row, "cost", None) for row in rows}
            if complete_account:
                for item in self.connection.execute(
                        "SELECT market_slug FROM execution_positions").fetchall():
                    venue.setdefault(item["market_slug"], Decimal("0"))
            for slug, venue_qty in venue.items():
                prior = self.connection.execute(
                    "SELECT net_qty,event_at FROM execution_positions WHERE market_slug=?",
                    (slug,)).fetchone()
                if prior is not None and snapshot_at < prior["event_at"]:
                    continue
                prior_qty = (_decimal(prior["net_qty"], "prior_qty")
                             if prior is not None else Decimal("0"))
                if prior_qty != venue_qty:
                    self.connection.execute(
                        "INSERT INTO execution_position_reconciliations(snapshot_id,"
                        "market_slug,prior_qty,venue_qty,difference,as_of,observed_at) "
                        "VALUES (?,?,?,?,?,?,?)",
                        (snapshot_id, slug, _number(prior_qty, "prior_qty"),
                         _number(venue_qty, "venue_qty"),
                         _number(venue_qty - prior_qty, "difference"),
                         snapshot_at, received))
                cost = costs.get(slug)
                self.connection.execute(
                    "INSERT INTO execution_positions(market_slug,net_qty,event_at,"
                    "received_at,source,authoritative_through,cost,first_seen_at) "
                    "VALUES (?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(market_slug) "
                    "DO UPDATE SET net_qty=excluded.net_qty,event_at=excluded.event_at,"
                    "received_at=excluded.received_at,source=excluded.source,"
                    "authoritative_through=excluded.authoritative_through,"
                    "cost=COALESCE(excluded.cost,cost),"
                    # first_seen_at is when this position became nonzero and is
                    # NOT refreshed while it stays open -- it is the clock the
                    # exit deadline runs on when no fill time is available.
                    # Cleared once flat so the next position starts its own.
                    "first_seen_at=CASE WHEN CAST(excluded.net_qty AS REAL)=0 "
                    "THEN NULL ELSE COALESCE(first_seen_at,excluded.first_seen_at) END",
                    (slug, _number(venue_qty, "venue_qty"), snapshot_at,
                     received, "position_snapshot", snapshot_at,
                     None if cost is None else _number(cost, "cost"),
                     None if venue_qty == 0 else received))
            self._finish_event("position_snapshot", snapshot_id, True)
        return True

    def realized_pnl(self, market_slug: str | None = None, *,
                     since: datetime | None = None) -> Decimal:
        """Realised trading P&L: closed round trips only, net of fees.

        Open inventory is excluded by valuing it at its own average entry, so
        an unrealised mark never reaches a loss stop. That distinction matters
        once size grows: 320 open contracts move $38 on a twelve-cent mark, and
        a stop reading marked equity would fire on paper movement with nothing
        traded.

            realised = cash from fills + position x average entry

        A long has paid `q x basis` that is not yet a loss, so it is added
        back; a short has received it, so it is subtracted. Both fall out of
        the signed position.
        """

        # `since` bounds the window. Without it this is lifetime P&L, and the
        # loss stop is a *session* limit -- every past loss would count against
        # today's budget forever, so once the ledger had ever lost $40 the bot
        # could never start again. Observed 2026-08-15: the stop fired on
        # startup from several days of accumulated test losses.
        clauses: list[str] = []
        params: list[Any] = []
        if market_slug is not None:
            clauses.append("market_slug=?")
            params.append(market_slug)
        if since is not None:
            clauses.append("event_at>=?")
            params.append(_time(since, "since").isoformat())
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        cash = Decimal("0")
        long_qty = Decimal("0")
        short_qty = Decimal("0")
        for row in self.query(
                "SELECT side,price,qty,fee,intent FROM execution_fills" + where,
                tuple(params)):
            qty = _decimal(row["qty"], "fill qty")
            price = _decimal(str(row["price"]), "fill price")
            fee = _decimal(str(row["fee"] or 0), "fill fee")
            intent = str(row["intent"] or "").upper()
            if intent.endswith("BUY_SHORT"):
                # A PURCHASE, despite the venue labelling it ORDER_SIDE_SELL.
                # That label says which OUTCOME is taken, not which way our
                # cash moves -- both intents we send are BUY intents with the
                # price unchanged (live_orders._intent, validated by maker-lab
                # over 201 fills). Taking the sign from the label booked cash
                # IN on a purchase and turned a $0.30 loss into a $1.34 gain
                # on aec-valorant-ff-ep.
                #
                # Buying the opposite outcome nets the position out, and the
                # pair settles at exactly 1.00 -- credited below.
                cash -= qty * price + fee
                short_qty += qty
            elif intent.endswith("BUY_LONG"):
                cash -= qty * price + fee
                long_qty += qty
            else:
                # Legacy SELL_LONG / SELL_SHORT fills, and any fill recorded
                # before intent was captured. Those used the other mapping,
                # where the venue read the price in the opposite frame, so the
                # side label is the only signal available.
                signed = qty if "SELL" in str(row["side"]).upper() else -qty
                cash += signed * price - fee
        # Each MATCHED pair of outcomes settles at exactly one dollar, so a
        # completed round trip realises that dollar against what both legs
        # cost. An unmatched leg is still open inventory and is valued at its
        # own basis below instead.
        cash += min(long_qty, short_qty)
        slugs = ([market_slug] if market_slug is not None
                 else [str(r["market_slug"]) for r in self.query(
                     "SELECT DISTINCT market_slug FROM execution_fills")])
        # Open inventory is added back at basis so an unrealised mark never
        # reaches the stop. That is window-independent: a position held from
        # before `since` is still open now, and excluding it would report its
        # entry cost as a loss.
        for slug in slugs:
            position = self.position(slug)
            if not position:
                continue
            basis = self.inventory_basis(slug)
            if basis is None:
                continue
            cash += position * basis.average_entry_price
        return cash

    def realized_by_market(
        self, *, since: datetime | None = None,
    ) -> dict[str, Decimal]:
        """Realised P&L per market, for the per-market loss stop."""

        return {
            str(row["market_slug"]): self.realized_pnl(
                str(row["market_slug"]), since=since)
            for row in self.query(
                "SELECT DISTINCT market_slug FROM execution_fills")
        }

    def risk_state_known(self, market_slug: str | None = None) -> bool:
        """True when execution state is provable enough to build new risk.

        ``market_slug`` scopes the *intent* test to one market. Ownership tests
        stay account-wide whatever is passed: a ``venue:`` order or an order
        stuck absent anywhere means something is true of the account that we
        cannot explain, and that must stop every market.

        Scoping matters at eight markets. An in-flight cancel leaves its intent
        unresolved and its order briefly absent from the open-order snapshot,
        and both conditions are part of the ordinary cancel lifecycle. Measured
        2026-08-14: the two together held account-wide state "unknown" for 35%
        of wall-clock time, and every market quarantined its resting quote for
        each of those windows. What we know about market B does not actually
        change because a cancel is in flight in market A -- the durable-intent
        ledger is per order and per market.
        """

        if self.unresolved_intents(market_slug):
            return False
        # Scoped like the intent test above, and for the same reason. An order
        # is marked UNKNOWN_ABSENT for the moment between leaving the
        # open-order snapshot and `retrieve` confirming its terminal state --
        # which happens on every cancel. Left account-wide, one market's
        # ordinary cancel made every market read as unknown, and the runner
        # responded by quarantining, which cancels the quote it had just
        # placed. Measured 2026-08-14: orders cancelled about two seconds
        # after placement, nothing ever resting at the venue, nothing earning.
        #
        # Ownership below stays account-wide: a `venue:` order is one this bot
        # never placed, which is an account-level fact and must stop everything.
        absent_sql = ("SELECT COUNT(*) FROM execution_orders "
                      "WHERE status='UNKNOWN_ABSENT'")
        absent_params: tuple[Any, ...] = ()
        if market_slug is not None:
            absent_sql += " AND market_slug=?"
            absent_params = (market_slug,)
        unknown_orders = self.connection.execute(
            absent_sql, absent_params).fetchone()[0]
        return unknown_orders == 0 and self.ownership_is_known()

    def ownership_is_known(self) -> bool:
        """No order resting on our key that this bot never placed.

        The one test here that is genuinely account-wide, and the only one that
        should ever stop every market at once: a `venue:` order means something
        is true of the account we cannot explain -- another process on the same
        key -- and no market is safe while that holds.

        Split out from `risk_state_known` because the rest of that predicate is
        per-market bookkeeping. Callers that are asking "is the account sane"
        want this; callers asking "may this market quote" want the scoped
        predicate. Conflating them is what let one market's stuck row stop the
        whole account. See BUGFIXES 2026-08-16.
        """

        return self.connection.execute(
            "SELECT COUNT(*) FROM execution_orders WHERE client_order_id LIKE "
            "'venue:%' AND status IN "
            "('OPEN','NEW','PARTIAL','PENDING','SUBMITTED')"
        ).fetchone()[0] == 0
