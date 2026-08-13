"""Normalized, immutable values returned by read-only adapters."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from .rewards import IncentivePeriod


@dataclass(frozen=True)
class MarketIncentive:
    slug: str
    periods: tuple[IncentivePeriod, ...]
    reported_event_start: datetime | None = None


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
        # Official account docs define currentBalance as fiat and
        # assetNotional as the value of securities positions.
        return self.current_balance + self.asset_notional
