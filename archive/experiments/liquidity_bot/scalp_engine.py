"""Conservative two-sided maker-pair measurement; never an order strategy."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_HALF_EVEN

from .models import BookSnapshot, TradePrint
from .scalp_store import ScalpStore


@dataclass(frozen=True)
class ScalpPolicy:
    quantity: float = 10.0
    no_fill_timeout_seconds: float = 300.0
    minimum_spread: float = 0.005
    maker_theta: float = -0.0125

    def __post_init__(self) -> None:
        if self.quantity <= 0 or self.no_fill_timeout_seconds <= 0:
            raise ValueError("scalp quantity and timeout must be positive")
        if not 0 <= self.minimum_spread < 1:
            raise ValueError("minimum spread must be within [0, 1)")
        if self.maker_theta > 0:
            raise ValueError("maker theta must be zero or negative")


@dataclass
class _Episode:
    id: int
    slug: str
    cohort: str
    started_at: datetime
    qty: float
    bid_price: float
    offer_price: float
    bid_queue: float
    offer_queue: float
    bid_filled: float = 0.0
    offer_filled: float = 0.0
    bid_rebate: float = 0.0
    offer_rebate: float = 0.0
    first_fill_at: datetime | None = None


def maker_rebate(price: float, qty: float, theta: float = -0.0125) -> float:
    """Forecast the signed Retail maker fee, rounded per simulated fill."""

    if not 0 < price < 1 or qty <= 0 or theta > 0:
        raise ValueError("maker rebate inputs are invalid")
    exact = Decimal(str(theta)) * Decimal(str(qty)) * Decimal(str(price)) * (
        Decimal("1") - Decimal(str(price)))
    signed_fee = exact.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
    return float(-signed_fee)


class ScalpMeasurementEngine:
    """Track fixed, back-of-queue orders until both original sides fully fill."""

    def __init__(self, run_id: str, store: ScalpStore, policy: ScalpPolicy):
        self.run_id = run_id
        self.store = store
        self.policy = policy
        self.cohorts_by_slug: dict[str, set[str]] = {}
        self.episodes: dict[tuple[str, str], _Episode] = {}

    def restore_open(self) -> int:
        """Restore durable first-leg state after an unclean process restart."""

        rows = self.store.query(
            "SELECT * FROM scalp_episodes WHERE run_id=? AND status='OPEN'",
            (self.run_id,))
        for row in rows:
            first_fill = (datetime.fromisoformat(row["first_fill_at"])
                          if row["first_fill_at"] else None)
            episode = _Episode(
                id=int(row["id"]), slug=str(row["market_slug"]),
                cohort=str(row["cohort"]),
                started_at=datetime.fromisoformat(row["started_at"]),
                qty=float(row["requested_qty"]),
                bid_price=float(row["bid_price"]),
                offer_price=float(row["offer_price"]),
                bid_queue=float(row["bid_queue_remaining"]),
                offer_queue=float(row["offer_queue_remaining"]),
                bid_filled=float(row["bid_filled_qty"]),
                offer_filled=float(row["offer_filled_qty"]),
                bid_rebate=float(row["bid_rebate_usd"]),
                offer_rebate=float(row["offer_rebate_usd"]),
                first_fill_at=first_fill,
            )
            self.episodes[(episode.slug, episode.cohort)] = episode
            self.cohorts_by_slug.setdefault(episode.slug, set()).add(episode.cohort)
        return len(rows)

    @property
    def required_slugs(self) -> set[str]:
        """Selected markets plus retained first-leg exposures."""

        return set(self.cohorts_by_slug)

    def has_exposure(self, slug: str) -> bool:
        return any(episode.slug == slug and episode.first_fill_at is not None
                   for episode in self.episodes.values())

    def settle_market(self, slug: str, *, at: datetime) -> None:
        for key, episode in tuple(self.episodes.items()):
            if episode.slug == slug:
                self.store.close_episode(episode.id, at, "SETTLED")
                del self.episodes[key]
        self.cohorts_by_slug.pop(slug, None)

    def set_cohorts(self, cohorts: dict[str, set[str]], *, at: datetime) -> None:
        desired: dict[str, set[str]] = {}
        for cohort, slugs in cohorts.items():
            for slug in slugs:
                desired.setdefault(slug, set()).add(cohort)
        for key, episode in tuple(self.episodes.items()):
            if episode.cohort not in desired.get(episode.slug, set()):
                # A first-leg exposure stays observable after rotation. A
                # never-filled quote is safely censored at selection removal.
                if episode.first_fill_at is None:
                    self.store.close_episode(episode.id, at, "SELECTION_REMOVED")
                    del self.episodes[key]
                else:
                    desired.setdefault(episode.slug, set()).add(episode.cohort)
        self.cohorts_by_slug = desired

    @staticmethod
    def _queue_at(book: BookSnapshot, side: str, price: float) -> float:
        return sum(size for level_price, size in book.levels(side)
                   if abs(level_price - price) < 1e-9)

    def _start(self, book: BookSnapshot, cohort: str, at: datetime) -> None:
        if not book.is_two_sided or book.state not in (None, "MARKET_STATE_OPEN"):
            return
        bid, offer = book.best_bid.price, book.best_offer.price
        spread = offer - bid
        if spread + 1e-9 < self.policy.minimum_spread:
            return
        bid_queue = self._queue_at(book, "bid", bid)
        offer_queue = self._queue_at(book, "offer", offer)
        episode_id = self.store.start_episode(
            self.run_id, book.slug, cohort, at, qty=self.policy.quantity,
            bid_price=bid, offer_price=offer, bid_queue=bid_queue,
            offer_queue=offer_queue)
        self.episodes[(book.slug, cohort)] = _Episode(
            episode_id, book.slug, cohort, at, self.policy.quantity, bid, offer,
            bid_queue, offer_queue)

    def on_book(self, book: BookSnapshot, *, observed_at: datetime) -> None:
        self.store.record_book(self.run_id, book, observed_at)
        cohorts = self.cohorts_by_slug.get(book.slug, set())
        for cohort in tuple(cohorts):
            key = (book.slug, cohort)
            episode = self.episodes.get(key)
            if (episode is not None and episode.first_fill_at is None
                    and (observed_at - episode.started_at).total_seconds()
                    >= self.policy.no_fill_timeout_seconds):
                self.store.close_episode(episode.id, observed_at, "NO_FILL_TIMEOUT")
                del self.episodes[key]
                episode = None
            if episode is None:
                self._start(book, cohort, observed_at)

    @staticmethod
    def _consume(side: str, quote_price: float, queue: float, remaining: float,
                 trade: TradePrint) -> tuple[float, float, str | None]:
        consumed_side = "bid" if trade.maker_side == "ORDER_SIDE_BUY" else "offer"
        if consumed_side != side or remaining <= 1e-9:
            return 0.0, queue, None
        same = abs(trade.price - quote_price) < 1e-9
        through = (trade.price < quote_price if side == "bid"
                   else trade.price > quote_price)
        if same:
            new_queue = max(0.0, queue - trade.quantity)
            fill = min(remaining, max(0.0, trade.quantity - queue))
            return fill, new_queue, "AT_PRICE_AFTER_QUEUE" if fill > 0 else None
        if through:
            # Deliberately conservative for an aggregated public print: count
            # only its reported quantity, even though reaching a worse price
            # implies our hypothetical better-priced order was encountered.
            return min(remaining, trade.quantity), 0.0, "TRADE_THROUGH"
        return 0.0, queue, None

    def on_trade(self, trade: TradePrint, *, observed_at: datetime) -> None:
        trade_id = self.store.record_trade(self.run_id, trade, observed_at)
        for key, episode in tuple(self.episodes.items()):
            if episode.slug != trade.slug:
                continue
            side = "bid" if trade.maker_side == "ORDER_SIDE_BUY" else "offer"
            if side == "bid":
                remaining = episode.qty - episode.bid_filled
                queue_before = episode.bid_queue
                fill, episode.bid_queue, signal = self._consume(
                    side, episode.bid_price, episode.bid_queue, remaining, trade)
                economic_price = episode.bid_price
            else:
                remaining = episode.qty - episode.offer_filled
                queue_before = episode.offer_queue
                fill, episode.offer_queue, signal = self._consume(
                    side, episode.offer_price, episode.offer_queue, remaining, trade)
                economic_price = 1.0 - episode.offer_price
            if fill <= 0 or signal is None:
                self.store.update_episode(
                    episode.id, bid_queue=episode.bid_queue,
                    offer_queue=episode.offer_queue, bid_filled=episode.bid_filled,
                    offer_filled=episode.offer_filled,
                    bid_rebate=episode.bid_rebate,
                    offer_rebate=episode.offer_rebate,
                    first_fill_at=episode.first_fill_at)
                continue
            rebate = maker_rebate(economic_price, fill, self.policy.maker_theta)
            if side == "bid":
                episode.bid_filled += fill
                episode.bid_rebate += rebate
                price = episode.bid_price
            else:
                episode.offer_filled += fill
                episode.offer_rebate += rebate
                price = episode.offer_price
            if episode.first_fill_at is None:
                episode.first_fill_at = observed_at
            self.store.record_episode_fill(
                episode.id, observed_at, side, price, fill, signal,
                queue_before, rebate, trade_id)
            completed = (episode.bid_filled >= episode.qty - 1e-9
                         and episode.offer_filled >= episode.qty - 1e-9)
            self.store.update_episode(
                episode.id, bid_queue=episode.bid_queue,
                offer_queue=episode.offer_queue, bid_filled=episode.bid_filled,
                offer_filled=episode.offer_filled, bid_rebate=episode.bid_rebate,
                offer_rebate=episode.offer_rebate,
                first_fill_at=episode.first_fill_at,
                completed_at=observed_at if completed else None)
            if completed:
                del self.episodes[key]

    def finish(self, at: datetime) -> None:
        for episode in tuple(self.episodes.values()):
            self.store.close_episode(episode.id, at, "RUN_ENDED_CENSORED")
        self.episodes.clear()
