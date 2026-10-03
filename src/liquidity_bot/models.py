"""Normalized, immutable values returned by read-only adapters."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from .rewards import IncentivePeriod


SPORTS_MONEYLINE_V2 = "SPORTS_MARKET_TYPE_MONEYLINE"


@dataclass(frozen=True)
class MarketIncentive:
    slug: str
    periods: tuple[IncentivePeriod, ...]
    reported_event_start: datetime | None = None
    # Conservative start of the complete fetch, never a cache-read timestamp.
    observed_at: datetime | None = None


@dataclass(frozen=True)
class RewardEarning:
    date: str
    market_slug: str
    program_type: str
    reward: float
    status: str

    @property
    def is_paid(self) -> bool:
        return self.status == "PAID"


@dataclass(frozen=True)
class MarketMetadata:
    slug: str
    game_start: datetime | None
    tick_size: float | None
    active: bool
    closed: bool
    status: str | None = None
    sports_market_type: str | None = None
    question: str | None = None
    minimum_trade_qty: float | None = None
    sports_market_type_v2: str | None = None
    catalog_best_bid: float | None = None
    catalog_best_offer: float | None = None
    catalog_volume_usd: float | None = None
    catalog_volume_24h_usd: float | None = None
    catalog_liquidity_usd: float | None = None
    # Execution gateways require fresh parent-event evidence independently
    # of the scheduled clock. Library/report metadata remains descriptive.
    event_guard_required: bool = False
    event_slug: str | None = None
    event_checked_at: datetime | None = None
    event_not_started: bool = False
    event_started_at: datetime | None = None
    # BC-2026-10-03-A: fresh parent evidence that the game is being played and
    # has not ended. Only the live strategy reads it.
    event_in_play: bool = False

    def event_allows_live_entry(self, now: datetime) -> bool:
        """Live strategy: positively started, not ended, evidence under 30 s."""
        if not self.event_guard_required:
            return self.game_start is not None and self.game_start <= now
        return (
            self.event_started_at is not None
            and self.event_in_play
            and self.event_checked_at is not None
            and 0 <= (now - self.event_checked_at).total_seconds() < 30
        )

    def event_allows_entry(self, now: datetime) -> bool:
        if not self.event_guard_required:
            return True
        return (
            self.event_started_at is None
            and self.event_not_started
            and self.event_checked_at is not None
            and 0 <= (now - self.event_checked_at).total_seconds() < 30
        )

    def event_entry_block_reason(self, now: datetime) -> str:
        """Name the failed `event_allows_entry` condition, for logs only."""
        if self.event_started_at is not None:
            return "event_started"
        if self.event_checked_at is None:
            return "no_event_evidence"
        if not self.event_not_started:
            return "not_proven_pregame"
        return f"evidence_age={(now - self.event_checked_at).total_seconds():.1f}s"

    @property
    def is_supported_moneyline(self) -> bool:
        return (self.sports_market_type_v2 or "").strip().upper() == (
            SPORTS_MONEYLINE_V2)


@dataclass(frozen=True)
class BookLevel:
    price: float
    size: float


@dataclass(frozen=True)
class BookSnapshot:
    slug: str
    bids: tuple[BookLevel, ...]
    offers: tuple[BookLevel, ...]
    state: str | None = None
    transact_time: datetime | None = None
    # Local socket receipt, distinct from the exchange transaction clock.
    received_at: datetime | None = None
    # Optional documented market statistics. They are measurement metadata and
    # never participate in order planning or account reconciliation.
    shares_traded: float | None = None
    open_interest: float | None = None
    notional_traded_usd: float | None = None

    def has_side(self, side: str) -> bool:
        """Whether this book can price that side at all.

        A one-sided book is common -- about one market in four -- and is
        perfectly usable for the side it does carry. Quoting needs both;
        exiting needs only the side being sold into.
        """

        if side == "bid":
            return bool(self.bids)
        if side == "offer":
            return bool(self.offers)
        raise ValueError("side must be 'bid' or 'offer'")

    @property
    def is_two_sided(self) -> bool:
        return bool(self.bids) and bool(self.offers)

    @property
    def best_bid(self) -> BookLevel:
        return max(self.bids, key=lambda level: level.price)

    @property
    def best_offer(self) -> BookLevel:
        return min(self.offers, key=lambda level: level.price)

    @property
    def bid_depth(self) -> float:
        return sum(level.size for level in self.bids)

    @property
    def offer_depth(self) -> float:
        return sum(level.size for level in self.offers)

    def levels(self, side: str) -> list[tuple[float, float]]:
        if side == "bid":
            return [(level.price, level.size) for level in self.bids]
        if side == "offer":
            return [(level.price, level.size) for level in self.offers]
        raise ValueError("side must be 'bid' or 'offer'")


@dataclass(frozen=True)
class TradePrint:
    slug: str
    price: float
    quantity: float
    maker_side: str
    trade_time: datetime
    request_id: str | None = None
    maker_intent: str | None = None
    taker_side: str | None = None
    taker_intent: str | None = None


@dataclass(frozen=True)
class AccountBalance:
    current_balance: Decimal
    asset_notional: Decimal
    buying_power: Decimal
    observed_at: datetime

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("balance observation must be timezone-aware")
        if not all(value.is_finite() for value in (
                self.current_balance, self.asset_notional, self.buying_power)):
            raise ValueError("balance values must be finite")

    @property
    def marked_equity(self) -> Decimal:
        # Retained for compatibility with raw balance-row diagnostics only.
        # Production Retail evidence showed assetNotional=0 with nonzero open
        # positions, so risk/reporting consumers must use AccountEquitySnapshot
        # (cash + per-position cashValue), never this shortcut.
        return self.current_balance + self.asset_notional
