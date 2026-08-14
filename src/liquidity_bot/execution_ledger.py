"""Durable, decimal-safe Phase 3 execution state.

The ledger owns no network client and cannot place an order. A future
execution adapter must commit an intent here before it performs a remote
mutation, then reconcile every ambiguous result against venue state.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
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
    error TEXT
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
    unknown_reason TEXT
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
);
CREATE TABLE IF NOT EXISTS execution_positions (
    market_slug TEXT PRIMARY KEY,
    net_qty TEXT NOT NULL,
    event_at TEXT NOT NULL,
    received_at TEXT NOT NULL,
    source TEXT NOT NULL,
    authoritative_through TEXT
);
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

    def __post_init__(self) -> None:
        if not self.market_slug:
            raise ValueError("position market is required")
        _decimal(self.net_qty, "net_qty")
        _time(self.event_at, "event_at")


@dataclass(frozen=True)
class InventoryBasis:
    net_qty: Decimal
    average_entry_price: Decimal
    opened_at: datetime


class ExecutionLedger:
    """SQLite system of record for intents and observed execution state."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.executescript(SCHEMA)
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        return list(self.connection.execute(sql, params).fetchall())

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
    ) -> bool:
        operation = operation.upper()
        if operation not in INTENT_OPERATIONS:
            raise ValueError("unsupported intent operation")
        if not intent_id or not market_slug or not client_order_id:
            raise ValueError("intent identity is incomplete")
        at = _time(created_at, "created_at")
        body = _json(dict(payload))
        digest = _hash(dict(payload))
        with self.connection:
            row = self.connection.execute(
                "SELECT operation,market_slug,client_order_id,payload_hash "
                "FROM execution_intents WHERE intent_id=?", (intent_id,)).fetchone()
            if row is not None:
                existing = tuple(row)
                proposed = (operation, market_slug, client_order_id, digest)
                if existing != proposed:
                    raise LedgerConflict("intent id was reused with different content")
                return False
            self.connection.execute(
                "INSERT INTO execution_intents(intent_id,operation,market_slug,"
                "client_order_id,payload_json,payload_hash,state,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (intent_id, operation, market_slug, client_order_id, body,
                 digest, "PREPARED", at, at))
        return True

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

    def unresolved_intents(self) -> list[sqlite3.Row]:
        placeholders = ",".join("?" for _ in UNRESOLVED_INTENTS)
        return self.query(
            f"SELECT * FROM execution_intents WHERE state IN ({placeholders}) "
            "ORDER BY created_at", tuple(sorted(UNRESOLVED_INTENTS)))

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
            return None
        return InventoryBasis(authoritative, average, opened_at)

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
            "SELECT payload_hash FROM execution_events WHERE source=? AND event_id=?",
            (source, event_id)).fetchone()
        if prior is None or prior["payload_hash"] != digest:
            raise LedgerConflict("event id was reused with different content")
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
            "received_at,unknown_reason) VALUES (?,?,?,?,?,?,?,?,?,?,NULL) "
            "ON CONFLICT(client_order_id) DO UPDATE SET "
            "exchange_order_id=COALESCE(excluded.exchange_order_id,exchange_order_id),"
            "price=excluded.price,filled_qty=excluded.filled_qty,status=excluded.status,"
            "event_at=excluded.event_at,received_at=excluded.received_at,"
            "unknown_reason=NULL",
            (order.client_order_id, order.exchange_order_id, order.market_slug,
             side, price, _number(original, "original_qty"),
             _number(filled, "filled_qty"), status, event_at, received))
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
                "intent,event_at,received_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (execution.execution_id, execution.client_order_id,
                 execution.exchange_order_id, execution.market_slug,
                 execution.side.upper(), _number(execution.price, "price"),
                 _number(qty, "qty"), _number(execution.fee, "fee"),
                 execution.liquidity, execution.intent, event_at, received))
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
                self.connection.execute(
                    "INSERT INTO execution_positions(market_slug,net_qty,event_at,"
                    "received_at,source,authoritative_through) VALUES (?,?,?,?,?,?) "
                    "ON CONFLICT(market_slug) "
                    "DO UPDATE SET net_qty=excluded.net_qty,event_at=excluded.event_at,"
                    "received_at=excluded.received_at,source=excluded.source,"
                    "authoritative_through=excluded.authoritative_through",
                    (slug, _number(venue_qty, "venue_qty"), snapshot_at,
                     received, "position_snapshot", snapshot_at))
            self._finish_event("position_snapshot", snapshot_id, True)
        return True

    def risk_state_known(self) -> bool:
        if self.unresolved_intents():
            return False
        unknown_orders = self.connection.execute(
            "SELECT COUNT(*) FROM execution_orders WHERE status='UNKNOWN_ABSENT'"
        ).fetchone()[0]
        unowned_working = self.connection.execute(
            "SELECT COUNT(*) FROM execution_orders WHERE client_order_id LIKE "
            "'venue:%' AND status IN "
            "('OPEN','NEW','PARTIAL','PENDING','SUBMITTED')"
        ).fetchone()[0]
        return unknown_orders == 0 and unowned_working == 0
