"""Post-fill markouts for offline calibration. Measurement only.

After each owned fill, record the market's mid price 30 s, 2 min and 10 min
later from the book the runner already holds (zero API requests). Positive
markout means the market moved in our favour; persistent negative markout is
adverse selection. Output goes only to the optional market-quality archive as
kind ``fill_markout`` and is consumed by ``scripts/calibration_report.py``.
Nothing here can place, cancel, delay or gate an order.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Callable

HORIZONS_SECONDS = (30, 120, 600)
POLL_SECONDS = 2.0
MAX_PENDING = 600
MAX_BOOK_AGE_SECONDS = 5.0
LATE_TOLERANCE_SECONDS = 60.0
ENTRY_INTENTS = {"ORDER_INTENT_BUY_LONG", "ORDER_INTENT_BUY_SHORT"}

BookLookup = Callable[[str], "tuple[Any, datetime | None] | None"]
Emit = Callable[[dict], None]


def _stamp(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


class FillMarkoutSampler:
    """Bounded queue of (fill, horizon) samples due in the future.

    The first poll starts at the ledger's current newest fill, so history is
    never backfilled with misleading late prices. Samples more than a minute
    past due, or without a fresh book, are still recorded with ``mid=None``
    so coverage gaps stay visible.
    """

    def __init__(self, *, horizons=HORIZONS_SECONDS, max_pending=MAX_PENDING):
        self.horizons = tuple(horizons)
        self.max_pending = max_pending
        self.last_rowid: int | None = None
        self.next_poll_at: datetime | None = None
        self.pending: list[tuple[datetime, int, dict]] = []
        self.dropped = 0

    def poll(self, query, now: datetime, book_for: BookLookup, emit: Emit) -> None:
        if self.next_poll_at is not None and now < self.next_poll_at:
            return
        self.next_poll_at = now + timedelta(seconds=POLL_SECONDS)
        if self.last_rowid is None:
            row = query("SELECT COALESCE(MAX(rowid), 0) FROM execution_fills")[0]
            self.last_rowid = int(row[0])
        for row in query(
                "SELECT rowid, execution_id, client_order_id, market_slug, side, price,"
                " qty, liquidity, intent, event_at FROM execution_fills"
                " WHERE rowid>? AND reversed=0 ORDER BY rowid LIMIT 200",
                (self.last_rowid,)):
            self.last_rowid = int(row[0])
            fill = dict(execution_id=row[1], client_order_id=row[2], market=row[3],
                        side=row[4], price=str(row[5]), qty=str(row[6]),
                        liquidity=row[7], intent=row[8], fill_at=str(row[9]),
                        role="entry" if row[8] in ENTRY_INTENTS else "exit")
            filled_at = _stamp(fill["fill_at"])
            for horizon in self.horizons:
                self.pending.append((filled_at + timedelta(seconds=horizon), horizon, fill))
        if len(self.pending) > self.max_pending:
            self.pending.sort(key=lambda item: item[0])
            self.dropped += len(self.pending) - self.max_pending
            self.pending = self.pending[-self.max_pending:]
        due = [item for item in self.pending if item[0] <= now]
        if not due:
            return
        self.pending = [item for item in self.pending if item[0] > now]
        for due_at, horizon, fill in due:
            record = dict(fill, horizon_seconds=horizon, sampled_at=now.isoformat(),
                          late_seconds=round((now - due_at).total_seconds(), 3),
                          mid=None, best_bid=None, best_offer=None, book_age_seconds=None,
                          dropped_before=self.dropped)
            found = book_for(fill["market"]) if (now - due_at).total_seconds() <= LATE_TOLERANCE_SECONDS else None
            if found is not None:
                book, observed_at = found
                if book is not None and observed_at is not None and book.bids and book.offers:
                    age = (now - observed_at).total_seconds()
                    record["book_age_seconds"] = round(age, 3)
                    if 0 <= age <= MAX_BOOK_AGE_SECONDS:
                        bid, offer = book.best_bid.price, book.best_offer.price
                        record.update(best_bid=bid, best_offer=offer, mid=(bid + offer) / 2)
            emit(record)
