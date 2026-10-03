"""Paper trading for the Trade Scorer on markets the bot does not trade.

Operator 2026-10-03: "While doing this can we also shadow other markets?",
"it does not need to be reward market as long its passing the 60 percent",
"We should test as much as we can". Read-only: no order is ever placed.

For each scored market side the shadow rests ONE pretend entry at the exact
price the bot would quote, decides whether it would have filled from public
trades and book crossings (strict: queue ahead must be consumed first, no
credit for cancellations), then follows the bot's exit rules: a passive
reducer one tick inside the touch while not losing (``exit_quote_price``) and
the forced taker exit after 15 minutes or 5 minutes before the start
(``cross_exit`` with the live 0.5 depth haircut and taker fee). Every side that
passes the bot's own non-reward checks is paper-traded whatever its score, so
the score can be judged on both sides of 60%.

Known simplifications (stated in every report): the pretend entry is not
repriced while it rests; maker fees/rebates are zero; partial fills are kept.
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta

from .models import BookSnapshot, TradePrint
from .reconciler import exit_quote_price
from .shadow_math import cross_exit

ENTRY_LIFETIME_SECONDS = 600          # config/entry_expiry_policy.json
MAX_INVENTORY_AGE_SECONDS = 900       # CommissioningConfig.max_inventory_age_seconds
FORCE_EXIT_BEFORE_START_SECONDS = 300
EXIT_DEPTH_HAIRCUT = 0.5
TAKER_THETA = 0.06
EPS = 1e-9


def _buy(side: str) -> bool:
    return side == "bid"


def depth_at_and_better(book: BookSnapshot, side: str, price: float) -> tuple[float, float]:
    """(size strictly better than ``price``, size at ``price``) on ``side``."""
    better = at = 0.0
    for level_price, size in book.levels(side):
        if (side == "bid" and level_price > price + EPS) or (side == "offer" and level_price < price - EPS):
            better += size
        elif abs(level_price - price) <= EPS:
            at += size
    return better, at


def ticks_from_best(book: BookSnapshot, side: str, price: float, tick: float) -> int:
    best = book.best_bid.price if side == "bid" else book.best_offer.price
    distance = best - price if side == "bid" else price - best
    return max(0, math.ceil(distance / tick - EPS))


@dataclass
class RestingQuote:
    """A pretend passive order: ``side`` is the book side it rests on."""
    side: str
    price: float
    qty: float
    queue_ahead: float
    filled: float = 0.0

    @property
    def remaining(self) -> float:
        return max(0.0, self.qty - self.filled)

    def on_trade(self, trade: TradePrint) -> float:
        """Quantity of this quote filled by one public print."""
        maker_buy = trade.maker_side == "ORDER_SIDE_BUY"
        if maker_buy != _buy(self.side) or self.remaining <= EPS:
            return 0.0
        through = (trade.price < self.price - EPS) if _buy(self.side) else (trade.price > self.price + EPS)
        if through:
            got = self.remaining
        elif abs(trade.price - self.price) <= EPS:
            used = min(self.queue_ahead, trade.quantity)
            self.queue_ahead -= used
            got = min(self.remaining, trade.quantity - used)
        else:
            return 0.0
        self.filled += got
        return got

    def on_book(self, book: BookSnapshot) -> float:
        """A resting opposite order at or through our price would have matched us."""
        if self.remaining <= EPS or not book.is_two_sided:
            return 0.0
        crossed = (book.best_offer.price <= self.price + EPS) if _buy(self.side) \
            else (book.best_bid.price >= self.price - EPS)
        if not crossed:
            return 0.0
        got = self.remaining
        self.filled += got
        return got


@dataclass
class PaperTrade:
    slug: str
    league: str
    side: str                      # entry side: "bid" = long, "offer" = short
    price: float
    qty: float
    win_probability: float | None
    model: str | None
    traits: dict
    placed_at: datetime
    game_start: datetime
    tick: float
    entry: RestingQuote
    status: str = "resting"        # resting -> holding -> closed | expired
    filled_at: datetime | None = None
    cash: float = 0.0              # signed trading cash (buys negative)
    position: float = 0.0          # contracts held (always >= 0)
    reducer: RestingQuote | None = None
    forced_at: datetime | None = None
    deferred: bool = False
    closed_at: datetime | None = None
    close_kind: str | None = None
    fees: float = 0.0
    trade_id: int | None = None
    events: list = field(default_factory=list)

    # --- lifecycle -----------------------------------------------------------------
    @property
    def expires_at(self) -> datetime:
        return min(self.placed_at + timedelta(seconds=ENTRY_LIFETIME_SECONDS),
                   self.game_start - timedelta(seconds=FORCE_EXIT_BEFORE_START_SECONDS))

    @property
    def reducing_side(self) -> str:
        return "offer" if _buy(self.side) else "bid"

    @property
    def pnl(self) -> float | None:
        return None if self.status != "closed" else self.cash - self.fees

    def _entry_fill(self, qty: float, now: datetime) -> None:
        if qty <= EPS:
            return
        self.position += qty
        self.cash += -qty * self.price if _buy(self.side) else qty * self.price
        if self.filled_at is None:
            self.filled_at = now
            self.forced_at = min(now + timedelta(seconds=MAX_INVENTORY_AGE_SECONDS),
                                 self.game_start - timedelta(seconds=FORCE_EXIT_BEFORE_START_SECONDS))
        if self.status == "resting" and self.entry.remaining <= EPS:
            self.status = "holding"

    def _exit_fill(self, qty: float, price: float, now: datetime) -> None:
        qty = min(qty, self.position)
        if qty <= EPS:
            return
        self.position -= qty
        self.cash += qty * price if _buy(self.side) else -qty * price
        if self.position <= EPS and self.status == "holding":
            self._close(now, "passive")

    def _close(self, now: datetime, kind: str) -> None:
        self.position = 0.0
        self.status = "closed"
        self.closed_at = now
        self.close_kind = kind
        self.reducer = None

    def on_trade(self, trade: TradePrint, now: datetime) -> None:
        if self.status == "resting":
            self._entry_fill(self.entry.on_trade(trade), now)
        if self.status == "holding" and self.reducer is not None:
            self._exit_fill(self.reducer.on_trade(trade), self.reducer.price, now)

    def on_book(self, book: BookSnapshot, now: datetime) -> None:
        if self.status == "resting":
            self._entry_fill(self.entry.on_book(book), now)
        if self.status == "holding":
            self._refresh_reducer(book)
            if self.reducer is not None:
                self._exit_fill(self.reducer.on_book(book), self.reducer.price, now)

    def _refresh_reducer(self, book: BookSnapshot) -> None:
        if not (book.has_side("bid") or book.has_side("offer")) or self.position <= EPS:
            return
        try:
            price = exit_quote_price(book, self.reducing_side, self.tick, entry_price=self.price)
        except Exception:
            return
        if self.reducer is not None and abs(self.reducer.price - price) <= EPS:
            self.reducer.qty = self.reducer.filled + self.position
            return
        better, at = depth_at_and_better(book, self.reducing_side, price)
        self.reducer = RestingQuote(self.reducing_side, price, self.position, better + at)

    def step(self, book: BookSnapshot | None, now: datetime, *, max_early_exit_loss: float) -> None:
        """Clock-driven transitions: entry expiry and the forced taker exit."""
        if self.status == "resting" and now >= self.expires_at:
            if self.position > EPS:
                self.status = "holding"           # keep the partial fill
            else:
                self.status = "expired"
                self.closed_at = now
                self.close_kind = "not_filled"
                return
        if self.status != "holding" or self.forced_at is None or now < self.forced_at:
            return
        if book is None or not (book.has_side("bid") if _buy(self.side) else book.has_side("offer")):
            return                                 # nothing to exit into yet; retry next step
        result = cross_exit(book, entry_side=self.side, entry_price=self.price, qty=self.position,
                            depth_haircut=EXIT_DEPTH_HAIRCUT, taker_theta=TAKER_THETA,
                            exclude_entry_level=False)
        hard = self.game_start - timedelta(seconds=FORCE_EXIT_BEFORE_START_SECONDS)
        if now < hard and (not result.complete or result.pnl < -max_early_exit_loss):
            # The live bot defers an expensive early cross and keeps the passive reducer.
            self.deferred = True
            self.forced_at = hard
            return
        closed = result.closed_qty
        if closed > EPS:
            avg = result.average_price
            self.cash += closed * avg if _buy(self.side) else -closed * avg
            self.fees += result.taker_fee
        rest = self.position - closed
        if rest > EPS:
            # Thin book even at full depth: value the remainder at the worst level reached.
            worst = result.worst_price if result.worst_price is not None else (0.01 if _buy(self.side) else 0.99)
            self.cash += rest * worst if _buy(self.side) else -rest * worst
            self.fees += TAKER_THETA * rest * worst * (1 - worst)
        self._close(now, "forced_deferred" if self.deferred else "forced")

    def row(self) -> dict:
        return {
            "slug": self.slug, "league": self.league, "side": self.side, "price": self.price,
            "qty": self.qty, "win_probability": self.win_probability, "model": self.model,
            "traits_json": json.dumps(self.traits, separators=(",", ":"), default=str),
            "placed_at": self.placed_at.isoformat(), "game_start": self.game_start.isoformat(),
            "status": self.status, "filled_qty": self.entry.filled,
            "filled_at": self.filled_at.isoformat() if self.filled_at else None,
            "closed_at": self.closed_at.isoformat() if self.closed_at else None,
            "close_kind": self.close_kind, "cash": self.cash, "fees": self.fees,
            "pnl": self.pnl,
        }


# --- reporting ---------------------------------------------------------------------

def auc(scores: list[float], labels: list[int]) -> float | None:
    pos = [s for s, y in zip(scores, labels) if y]
    neg = [s for s, y in zip(scores, labels) if not y]
    if not pos or not neg:
        return None
    ranked = sorted((s, y) for s, y in zip(scores, labels))
    rank_sum, i = 0.0, 0
    while i < len(ranked):
        j = i
        while j < len(ranked) and ranked[j][0] == ranked[i][0]:
            j += 1
        avg_rank = (i + 1 + j) / 2
        rank_sum += avg_rank * sum(y for _, y in ranked[i:j])
        i = j
    return (rank_sum - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg))


def _group(rows: list[dict]) -> dict:
    placed = len(rows)
    traded = [r for r in rows if r["status"] == "closed"]
    pnl = sum(r["pnl"] for r in traded)
    wins = sum(1 for r in traded if r["pnl"] > 1e-9)
    return {"placed": placed, "filled": len(traded),
            "fill_rate": len(traded) / placed if placed else None,
            "win_rate": wins / len(traded) if traded else None,
            "pnl": round(pnl, 2), "pnl_per_trade": round(pnl / len(traded), 4) if traded else None,
            "forced_exits": sum(1 for r in traded if str(r["close_kind"]).startswith("forced"))}


def summarize(rows: list[dict], *, threshold: float, model_leagues: set[str]) -> dict:
    """Finished paper trades (closed or expired) grouped the ways the operator asked about."""
    done = [r for r in rows if r["status"] in ("closed", "expired") and r["win_probability"] is not None]
    hi = [r for r in done if r["win_probability"] >= threshold]
    lo = [r for r in done if r["win_probability"] < threshold]
    traded = [r for r in done if r["status"] == "closed"]
    out = {
        "threshold": threshold,
        "all": _group(done), "allowed": _group(hi), "skipped": _group(lo),
        "auc_closed": auc([r["win_probability"] for r in traded], [1 if r["pnl"] > 1e-9 else 0 for r in traded]),
        "allowed_known_leagues": _group([r for r in hi if r["league"] in model_leagues]),
        "allowed_new_leagues": _group([r for r in hi if r["league"] not in model_leagues]),
        "by_league": {}, "calibration": [],
    }
    leagues: dict[str, list] = {}
    for r in hi:
        leagues.setdefault(r["league"], []).append(r)
    out["by_league"] = {lg: _group(rs) for lg, rs in sorted(leagues.items(), key=lambda kv: -len(kv[1]))}
    for lo_p, hi_p in ((0, .2), (.2, .4), (.4, .5), (.5, .6), (.6, .8), (.8, 1.01)):
        bucket = [r for r in traded if lo_p <= r["win_probability"] < hi_p]
        if bucket:
            out["calibration"].append({
                "bucket": f"{int(lo_p*100)}-{min(100, int(hi_p*100))}%", "trades": len(bucket),
                "predicted": round(sum(r["win_probability"] for r in bucket) / len(bucket), 3),
                "actual": round(sum(1 for r in bucket if r["pnl"] > 1e-9) / len(bucket), 3),
                "pnl": round(sum(r["pnl"] for r in bucket), 2)})
    return out
