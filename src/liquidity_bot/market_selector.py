"""Read-only automatic selection across the liquidity-reward universe.

Discovery and execution deliberately remain separate.  This module can list,
validate, and rank reward markets, but it has no order client and cannot mutate
venue state.  The live runner may bind its existing one-market executor to the
selected slug only after it has proved the account flat and order-free.
"""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Iterable

from .adapters import AsyncReadOnlyGateway
from .errors import AdapterError, ApiAdmissionDeferred
from .fill_hazard import (
    MondayFillHazardModel,
    historical_reward_yield_prior,
)
from .live_orders import OPEN_MARKET_STATUSES
from .models import BookSnapshot, MarketIncentive, MarketMetadata
from .rewards import (
    AmbiguousIncentiveError,
    IncentivePeriod,
    RewardProjection,
    evaluate_order_score,
    project_order_reward,
    select_applicable_period,
)
from .risk import OpportunityEconomics
from .shadow_math import cross_exit
from .entry_quality import EntryQualityPolicy
from .selection_books import book_scope


SUPPORTED_EVENT_PERIODS = frozenset({
    "early",
    "pre_day",
    "pre-day",
    "day_of",
    "day-of",
    "dayof",
    "daily_event",
    "daily-event",
    "live",
})

# Event and nested market clocks can temporarily disagree when a sporting
# event is postponed.  Discovery uses a one-day superset on both sides of the
# strategy horizon, then the existing market-level game_start gate below
# remains authoritative.  A 2026-09-02 live comparison found seven otherwise
# executable delayed markets outside the exact event-time window and none
# outside this buffered window.
EVENT_DISCOVERY_CLOCK_SKEW_SECONDS = 24 * 60 * 60
EVENT_DISCOVERY_PAGE_SIZE = 500


@dataclass(frozen=True)
class RewardSelectionPolicy:
    """Hard-bounded read-only selection policy for commissioning."""

    quote_size: float = 1.0
    projection_horizon_seconds: float = 30.0
    projection_haircut: float = 0.5
    exit_depth_haircut: float = 0.5
    exit_cost_safety_multiple: float = 2.0
    max_book_candidates: int = 25
    minimum_entry_spread: float = 0.02
    minimum_entry_price: float = 0.10
    # Daily-per-event is an explicit fallback only. The established day-of
    # program keeps priority whenever both are present, and the runner exposes
    # a restart-scoped disable switch for fast rollback.
    allow_daily_event_rewards: bool = True
    # Mirrors CommissioningConfig.enforce_reward_cost_gate. The same test
    # existed here and in the runner's per-market pass; disabling it in one
    # place only did nothing, because a market that never becomes viable here
    # is never bound for the runner to evaluate at all.
    enforce_reward_cost_gate: bool = False
    # How far ahead of kickoff a market may be and still be selected.
    #
    # **Restored to the full six hours on 2026-08-23, operator decision, after
    # the evidence for narrowing it did not survive re-checking.** It was 7200
    # from 2026-08-17T02:27Z (`0b04c2b`) to then. The reasoning is in FINDINGS,
    # "Testing the horizon change against realised reward"; the short form:
    #
    # 1. `projected_reward` -- the quantity the narrowing was argued from --
    #    is `constant x (pool x share)`. Its only time term is
    #    `min(horizon, remaining)` with horizon 30s and remaining always above
    #    300s, so it is ALWAYS 30. It cannot vary with distance from kickoff,
    #    so the buckets compared different markets, not different times.
    # 2. Polymarket's own docs give no distance penalty: day_of runs six hours
    #    and "every second of the time period is equally weighted regardless
    #    of how much liquidity is on the book". Pool, target and discount are
    #    identical at T-8h and T-0h in the live payload.
    # 3. Supply, measured: executable markets fell 113 -> ~50 and median
    #    viable 36 -> 2-10 against twenty slots. At 7200 the bot cannot fill
    #    its book, and an empty slot earns exactly nothing.
    # 4. Realised reward moved the wrong way at constant quote size: share of
    #    bound markets earning anything 68% -> 47%, median per-market reward
    #    0.085 -> 0.030, reward per resting hour 0.128 -> 0.093.
    #
    # **What is still unmeasured, and is the reason to watch this.** The
    # narrowing was also argued on fill risk in thin distant books, and clean
    # trading data begins 2026-08-18 -- after the change -- so that half has
    # never been compared either way. Trading runs about -$0.045/pair and more
    # resting hours means more pairs. Judge this on NET, not on reward.
    #
    # 21600 is also the ceiling `__post_init__` allows, so this cannot widen
    # further without that bound moving too. Set to 7200 to go back.
    selection_horizon_seconds: float = 21600.0
    entry_quality_policy: EntryQualityPolicy = EntryQualityPolicy()

    def __post_init__(self) -> None:
        if type(self.allow_daily_event_rewards) is not bool:
            raise ValueError("allow_daily_event_rewards must be boolean")
        if self.quote_size <= 0 or self.projection_horizon_seconds <= 0:
            raise ValueError("selection size and horizon must be positive")
        if not 0 < self.selection_horizon_seconds <= 21600:
            raise ValueError(
                "selection horizon must be inside the authorized six hours")
        if not 0 < self.projection_haircut <= 1:
            raise ValueError("selection reward haircut must be in (0, 1]")
        if not 0 < self.exit_depth_haircut <= 1:
            raise ValueError("selection exit haircut must be in (0, 1]")
        if self.exit_cost_safety_multiple < 1:
            raise ValueError("selection exit-cost multiple must be at least one")
        if not 1 <= self.max_book_candidates <= 100:
            raise ValueError("selection book-candidate cap must be in [1, 100]")
        if not 0 < self.minimum_entry_spread < 1:
            raise ValueError("selection entry spread must be in (0, 1)")
        if not 0 < self.minimum_entry_price < .5:
            raise ValueError("selection entry price must be in (0, .5)")


@dataclass(frozen=True)
class SideOpportunity:
    side: str
    price: float
    reward_score_eligible: bool
    projection: RewardProjection
    stressed_exit_complete: bool
    stressed_exit_pnl: float
    expected_net_value: float | None
    blocker: str | None
    fill_probability: float
    fill_risk_driver: str

    @property
    def viable(self) -> bool:
        return self.expected_net_value is not None and self.expected_net_value > 0


@dataclass(frozen=True)
class RewardMarketOpportunity:
    market: MarketMetadata
    incentive: MarketIncentive
    period: IncentivePeriod
    book: BookSnapshot
    sides: tuple[SideOpportunity, ...]

    @property
    def viable_sides(self) -> tuple[str, ...]:
        return tuple(item.side for item in self.sides if item.viable)

    @property
    def best_expected_net_value(self) -> float | None:
        values = [item.expected_net_value for item in self.sides
                  if item.expected_net_value is not None]
        return max(values) if values else None

    @property
    def maximum_remaining_reward(self) -> float:
        return max(
            (item.projection.maximum_remaining_reward for item in self.sides),
            default=0.0,
        )

    @property
    def projected_reward_total(self) -> float:
        """What this market is actually expected to pay us.

        Folds in pool size, our SHARE of it, and the time remaining -- share
        being order_score / total_score, where total_score is everyone else's
        depth inside the reward band. So a small denominator, meaning little
        competition, is what makes a market worth a slot.

        Only twenty slots exist, so which twenty is the whole game. Measured
        2026-08-15: the selected twenty overlapped the best twenty by this
        number on only SEVEN, and a slot worth 0.000014 was held while one
        worth 0.010063 went unquoted -- a 700x spread inside the same slate.
        """

        return sum(
            item.projection.projected_reward for item in self.sides
            if item.projection is not None and item.projection.eligible)

    @property
    def reward_density(self) -> float:
        return self.period.reward_pool / self.period.target_size

    @property
    def spread(self) -> float:
        return self.book.best_offer.price - self.book.best_bid.price

    @property
    def entry_spread_eligible(self) -> bool:
        return bool(self.sides) and all(
            side.blocker != "spread_below_entry_minimum"
            for side in self.sides
        )

    @property
    def midpoint_distance(self) -> float:
        midpoint = (self.book.best_bid.price + self.book.best_offer.price) / 2
        return abs(midpoint - 0.5)

    @property
    def historical_reward_yield_prior(self) -> float:
        """Shrunk paid yield used only to rank otherwise valid markets."""

        return historical_reward_yield_prior(self.market.slug)


@dataclass(frozen=True)
class RewardMarketSelection:
    selected: RewardMarketOpportunity | None
    opportunities: tuple[RewardMarketOpportunity, ...]
    rewarded_executable_markets: int
    catalog_markets: int
    executable_catalog_markets: int
    unsupported_programs: int
    eligible_pregame_markets: int
    books_evaluated: int
    deferred_markets: tuple[str, ...]
    failures: tuple[str, ...]
    catalog_spread_filtered_markets: int = 0
    event_pages_used: int = 0

    def top_viable(
        self, limit: int, *, exclude: frozenset[str] | set[str] = frozenset(),
    ) -> tuple[RewardMarketOpportunity, ...]:
        """Return the best economically viable markets, ranked.

        ``opportunities`` is already ordered by ``_opportunity_key``, which
        sorts viable markets first. Monitoring-only rows are excluded here for
        the same reason ``selected`` excludes them: binding a market that
        cannot be quoted spends a rotation and earns nothing.

        ``exclude`` drops slugs the caller has barred, and does so *before*
        the limit so a barred market cannot consume a slot. The runner passes
        markets it retired earlier in the session.
        """

        if limit <= 0:
            raise ValueError("selection limit must be positive")
        return tuple(
            item for item in self.opportunities
            if item.viable_sides and item.market.slug not in exclude)[:limit]

    @property
    def viable_markets(self) -> int:
        return sum(bool(item.viable_sides) for item in self.opportunities)


def evaluate_market_opportunity(
    market: MarketMetadata,
    incentive: MarketIncentive,
    book: BookSnapshot,
    *,
    period: IncentivePeriod,
    now: datetime,
    policy: RewardSelectionPolicy,
    fill_hazard_model: MondayFillHazardModel | None = None,
) -> RewardMarketOpportunity:
    """Apply the exact live one-contract economic gate to both book sides."""

    if market.game_start is None or market.tick_size is None:
        raise ValueError("market clock and tick are required for selection")
    fill_hazard_model = fill_hazard_model or MondayFillHazardModel()
    spread = book.best_offer.price - book.best_bid.price
    seconds_to_event = max(0.0, (market.game_start - now).total_seconds())
    sides: list[SideOpportunity] = []
    for side, price in (("bid", book.best_bid.price),
                        ("offer", book.best_offer.price)):
        score = evaluate_order_score(
            side=side,
            price=price,
            size=policy.quote_size,
            levels=book.levels(side),
            incentive=period,
            tick_size=market.tick_size,
        )
        projection = project_order_reward(
            side=side,
            price=price,
            size=policy.quote_size,
            levels=book.levels(side),
            incentive=period,
            tick_size=market.tick_size,
            event_start=market.game_start,
            now=now,
            horizon_seconds=policy.projection_horizon_seconds,
            haircut=policy.projection_haircut,
            # The venue's $1 threshold applies to aggregate reward settlement,
            # not to every market row.  Per-order economics must therefore use
            # the actual sub-dollar projection.
            minimum_payout=0.0,
        )
        queue_ahead = (
            book.best_bid.size if side == "bid" else book.best_offer.size)
        fill_hazard = fill_hazard_model.estimate(
            slug=market.slug,
            side=side,
            price=price,
            spread=spread,
            queue_ahead=queue_ahead,
            seconds_to_event=seconds_to_event,
            horizon_seconds=policy.projection_horizon_seconds,
        )
        blocker: str | None = None
        exit_complete = False
        exit_pnl = 0.0
        expected: float | None = None
        if min(price, 1.0 - price) < policy.minimum_entry_price - 1e-9:
            blocker = "price_too_extreme"
        elif spread + 1e-9 < policy.minimum_entry_spread:
            blocker = "spread_below_entry_minimum"
        elif not projection.eligible:
            blocker = projection.reason
        else:
            exit_result = cross_exit(
                book,
                entry_side=side,
                entry_price=price,
                qty=policy.quote_size,
                depth_haircut=policy.exit_depth_haircut,
                exclude_entry_level=True,
            )
            exit_complete = exit_result.complete
            exit_pnl = exit_result.pnl
            if not exit_result.complete:
                blocker = "stressed_exit_incomplete"
            elif (quality_blocker := policy.entry_quality_policy.blocker(exit_result)):
                blocker = quality_blocker
            else:
                unwind_cost = max(0.0, -exit_result.pnl)
                economics = OpportunityEconomics(
                    # A fill can stop score accrual before the horizon.  Count
                    # the full projected reward only in the no-fill branch;
                    # the fill branch contributes zero reward here.
                    conservative_reward=(
                        projection.projected_reward
                        * (1.0 - fill_hazard.probability)),
                    maker_rebate=0.0,
                    spread_capture=0.0,
                    fill_probability=fill_hazard.probability,
                    unwind_loss_if_filled=unwind_cost,
                    operational_buffer=(
                        fill_hazard.probability * unwind_cost
                        * (policy.exit_cost_safety_multiple - 1.0)),
                )
                if economics.should_quote or not policy.enforce_reward_cost_gate:
                    expected = economics.expected_net_value
                    if not economics.should_quote:
                        # Measured and carried, but not vetoed: maker-lab's
                        # entry rule is 2c spread plus risk caps, nothing more.
                        expected = max(expected, 1e-9)
                else:
                    blocker = (
                        "projected_reward_does_not_cover_probability_"
                        "weighted_exit_cost")
        sides.append(SideOpportunity(
            side=side,
            price=price,
            reward_score_eligible=score.eligible,
            projection=projection,
            stressed_exit_complete=exit_complete,
            stressed_exit_pnl=exit_pnl,
            expected_net_value=expected,
            blocker=blocker,
            fill_probability=fill_hazard.probability,
            fill_risk_driver=fill_hazard.driver,
        ))
    return RewardMarketOpportunity(
        market=market,
        incentive=incentive,
        period=period,
        book=book,
        sides=tuple(sides),
    )


def _candidate_key(
    item: tuple[MarketMetadata, MarketIncentive, IncentivePeriod],
) -> tuple[int, float, float, float, datetime, str]:
    market, _, period = item
    # Preserve the established event-lifecycle strategy. Daily-per-event can
    # fill otherwise empty capacity, but never displaces a valid day-of row.
    phase = 0 if str(period.period).strip().lower() in {
        "day_of", "day-of", "dayof",
    } else 1
    density = period.reward_pool / period.target_size
    midpoint_distance = 1.0
    if (market.catalog_best_bid is not None
            and market.catalog_best_offer is not None):
        midpoint_distance = abs(
            (market.catalog_best_bid + market.catalog_best_offer) / 2 - 0.5)
    return (
        phase,
        -historical_reward_yield_prior(market.slug),
        -density,
        midpoint_distance,
        market.game_start or datetime.max,
        market.slug,
    )


def _opportunity_key(
    item: RewardMarketOpportunity,
) -> tuple[int, int, float, float, float, float, float, float, datetime, str]:
    best = item.best_expected_net_value
    return (
        0 if item.viable_sides else 1,
        0 if str(item.period.period).strip().lower() in {
            "day_of", "day-of", "dayof",
        } else 1,
        # Projected reward first, because it is the quantity we are paid on.
        #
        # This was `-best_expected_net_value`, which is floored to 1e-09 for
        # every side whenever enforce_reward_cost_gate is False -- advisory
        # since 2026-08-14. So every market TIED on the primary key and the
        # ranking fell through to tiebreakers that do not predict earnings.
        # One field served as both the economic filter and the sort key, so
        # switching the gate off silently switched the ranking off too, and
        # nothing failed loudly.
        #
        # The old key is kept immediately below rather than deleted: when the
        # gate is re-armed it becomes meaningful again and resumes as a
        # tiebreak. The remaining keys stay in order because it has not been
        # established that they were not protecting against something real.
        -item.projected_reward_total,
        -(best if best is not None else 0.0),
        -item.historical_reward_yield_prior,
        item.midpoint_distance,
        -item.maximum_remaining_reward,
        -item.reward_density,
        item.market.game_start or datetime.max,
        item.market.slug,
    )


class RewardMarketSelector:
    """Join bounded sports-event markets to exact rewards and score them."""

    def __init__(
        self,
        gateway: AsyncReadOnlyGateway,
        policy: RewardSelectionPolicy | None = None,
        fill_hazard_model: MondayFillHazardModel | None = None,
        *, clock: Callable[[], datetime] | None = None,
    ):
        self.gateway = gateway
        self.policy = policy or RewardSelectionPolicy()
        self.fill_hazard_model = fill_hazard_model or MondayFillHazardModel()
        self._book_cursor = 0
        self._last_selected_slug: str | None = None
        self.clock = clock

    async def select(self, *, now: datetime) -> RewardMarketSelection:
        scope = getattr(self.gateway, "traffic_class", None)
        with scope("background") if callable(scope) else nullcontext():
            return await self._select(now=now)

    async def _select(self, *, now: datetime) -> RewardMarketSelection:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("selection clock must be timezone-aware")
        # Discover only active sports events near the six-hour strategy
        # horizon, filter their nested markets by the unchanged market-level
        # execution gates, then request rewards only for that exact set.  The
        # old catalog-first path walked every active market merely to discard
        # the non-sports and non-moneyline majority.  On 2026-09-02 the venue
        # crossed its fixed 50,000-row safety bound; each failed top-up spent
        # 100 reads and never reached replacement selection.  Enumerating all
        # nominally active rewards also failed because the venue returned more
        # than 20,000 programs despite the documented active-status filter.
        #
        # Event clocks are discovery hints only.  The one-day superset retains
        # delayed/rescheduled events; market.game_start remains the execution
        # authority, so no horizon or lifecycle policy changes here.
        window_end = now + timedelta(
            seconds=self.policy.selection_horizon_seconds)
        clock_skew = timedelta(seconds=EVENT_DISCOVERY_CLOCK_SKEW_SECONDS)
        catalog = await self.gateway.list_upcoming_sports_markets(
            start=now - clock_skew,
            end=window_end + clock_skew,
            limit=EVENT_DISCOVERY_PAGE_SIZE,
            require_complete=True,
        )
        now = self.clock() if self.clock is not None else now
        event_pages_used = int(getattr(
            self.gateway, "last_upcoming_event_pages", 0))
        executable_catalog = {
            market.slug: market
            for market in catalog
            if self._catalog_market_is_executable(
                market, now=now, window_end=window_end)
        }
        incentives = await self.gateway.fetch_incentives(
            sorted(executable_catalog))
        now = self.clock() if self.clock is not None else now
        rewarded_executable = {
            slug: incentives[slug]
            for slug in executable_catalog
            if slug in incentives
        }
        candidates: list[
            tuple[MarketMetadata, MarketIncentive, IncentivePeriod]
        ] = []
        failures: list[str] = []
        unsupported = 0
        catalog_spread_filtered = 0
        for slug in sorted(rewarded_executable):
            incentive = rewarded_executable[slug]
            if not any(
                    str(period.period).strip().lower()
                    in SUPPORTED_EVENT_PERIODS
                    for period in incentive.periods):
                # The venue currently emits values not implemented by the
                # pregame executor, including generic ``daily`` and observed
                # ``tournament_with_cutoff``. They remain visible in universe
                # counts but cannot authorize REST fan-out or risk.
                unsupported += 1
                continue
            try:
                market = executable_catalog[slug]
                period = self._applicable_period(market, incentive, now)
                if period is None:
                    continue
                if not self._catalog_spread_can_enter(market):
                    catalog_spread_filtered += 1
                    continue
                candidates.append((market, incentive, period))
            except (KeyError, AmbiguousIncentiveError, ValueError) as exc:
                failures.append(f"{slug}:{type(exc).__name__}")

        ranked = sorted(candidates, key=_candidate_key)
        detailed, deferred = self._detailed_candidates(ranked)
        opportunities: list[RewardMarketOpportunity] = []
        async with book_scope(self.gateway, [m.slug for m, _, _ in detailed]):
            for market, incentive, period in detailed:
                try:
                    book = await self.gateway.fetch_book(market.slug)
                    opportunities.append(evaluate_market_opportunity(
                        market, incentive, book, period=period, now=now,
                        policy=self.policy, fill_hazard_model=self.fill_hazard_model))
                except ApiAdmissionDeferred:
                    raise
                except (AdapterError, ValueError) as exc:
                    if isinstance(exc, AdapterError) and exc.status_code == 429:
                        raise
                    failures.append(f"{market.slug}:book:{type(exc).__name__}")

        books_evaluated = len(opportunities)
        # A full background scan can outlive the execution guard's 30-second
        # lifecycle receipt. Keep positive started evidence and current
        # schedule/reward clocks in ranking, but do not reinterpret an expired
        # receipt as proof that every candidate is economically nonviable.
        # Binding/action paths fetch genuine fresh lifecycle evidence again.
        opportunities = self._refresh_opportunity_times(
            opportunities, now, require_event_freshness=False)
        opportunities.sort(key=_opportunity_key)
        result = RewardMarketSelection(
            selected=next(
                (item for item in opportunities
                 if item.viable_sides),
                None,
            ),
            opportunities=tuple(opportunities),
            rewarded_executable_markets=len(rewarded_executable),
            catalog_markets=len(catalog),
            executable_catalog_markets=len(executable_catalog),
            unsupported_programs=unsupported,
            eligible_pregame_markets=len(candidates),
            books_evaluated=books_evaluated,
            deferred_markets=deferred,
            failures=tuple(failures),
            catalog_spread_filtered_markets=catalog_spread_filtered,
            event_pages_used=event_pages_used,
        )
        if result.selected is not None:
            self._last_selected_slug = result.selected.market.slug
        return result

    async def revalidate_viable(
        self, slugs: Iterable[str], *, now: datetime,
    ) -> tuple[RewardMarketOpportunity, ...]:
        slugs = tuple(dict.fromkeys(
            str(slug).strip() for slug in slugs if str(slug).strip()))
        if len(slugs) > self.policy.max_book_candidates:
            raise ValueError("cached revalidation exceeds the selector book budget")
        scope = getattr(self.gateway, "traffic_class", None)
        # This small cached-candidate refresh exists to restore an empty slot
        # promptly after flatness. Keep it on the independent entry lane rather
        # than queueing behind a full-universe background scan.
        with scope("entry") if callable(scope) else nullcontext():
            async with book_scope(self.gateway, slugs):
                return await self._revalidate_viable(slugs, now=now)

    async def _revalidate_viable(
        self, slugs: Iterable[str], *, now: datetime,
    ) -> tuple[RewardMarketOpportunity, ...]:
        """Freshly re-prove a small cached replacement set.

        Incremental top-up may already have a recent full-universe selection
        when an urgent position becomes flat. Re-running the entire bounded
        catalog scan delays the refill, but trusting its old rows would let a
        closed market, ended incentive or changed book enter the active set.

        This path is deliberately small and read-only: one bulk market read,
        one batched incentive read and one fresh full-book read per candidate.
        Every normal selector gate is applied again and only currently viable
        opportunities are returned. Callers fall back to ``select`` when this
        bounded refresh is unavailable or does not supply enough replacements.
        """

        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("selection clock must be timezone-aware")
        clean = tuple(dict.fromkeys(
            str(slug).strip() for slug in slugs if str(slug).strip()))
        if not clean:
            return ()
        if len(clean) > self.policy.max_book_candidates:
            raise ValueError(
                "cached revalidation exceeds the selector book budget")

        markets = await self.gateway.fetch_markets_by_slugs(clean)
        incentives = await self.gateway.fetch_incentives(clean)
        now = self.clock() if self.clock is not None else now
        window_end = now + timedelta(
            seconds=self.policy.selection_horizon_seconds)
        opportunities: list[RewardMarketOpportunity] = []
        for slug in clean:
            market = markets.get(slug)
            incentive = incentives.get(slug)
            if market is None or incentive is None:
                continue
            if not self._catalog_market_is_executable(
                    market, now=now, window_end=window_end):
                continue
            if not self._catalog_spread_can_enter(market):
                continue
            try:
                period = self._applicable_period(market, incentive, now)
                if period is None:
                    continue
                opportunity = evaluate_market_opportunity(
                    market,
                    incentive,
                    await self.gateway.fetch_book(slug),
                    period=period,
                    now=now,
                    policy=self.policy,
                    fill_hazard_model=self.fill_hazard_model,
                )
            except ApiAdmissionDeferred:
                # A local admission decline says nothing about this market's
                # viability. Preserve the cached selection and let the runner
                # retry after the shared cooldown instead of silently treating
                # this and every later candidate as nonviable.
                raise
            except (AdapterError, AmbiguousIncentiveError, ValueError) as exc:
                if isinstance(exc, AdapterError) and exc.status_code == 429:
                    raise
                continue
            if opportunity.viable_sides:
                opportunities.append(opportunity)
        opportunities = self._refresh_opportunity_times(opportunities, now)
        opportunities.sort(key=_opportunity_key)
        return tuple(item for item in opportunities if item.viable_sides)

    def _refresh_opportunity_times(
        self, opportunities: list[RewardMarketOpportunity], now: datetime, *,
        require_event_freshness: bool = True,
    ) -> list[RewardMarketOpportunity]:
        """A paced scan ranks at completion, not at its pre-await clock.

        These remain selection observations, never fresh executable books.
        Startup/replacement/decision validation still acquires current proof.
        """
        if self.clock is None:
            return opportunities
        now = self.clock()
        refreshed = []
        for item in opportunities:
            period = self._applicable_period(
                item.market, item.incentive, now,
                require_event_freshness=require_event_freshness)
            if period is not None:
                refreshed.append(evaluate_market_opportunity(
                    item.market, item.incentive, item.book, period=period,
                    now=now, policy=self.policy,
                    fill_hazard_model=self.fill_hazard_model))
        return refreshed

    def _catalog_market_is_executable(
        self,
        market: MarketMetadata,
        *,
        now: datetime,
        window_end: datetime,
    ) -> bool:
        """Apply metadata-only gates before any incentive or book fan-out."""

        return (
            market.active
            and not market.closed
            and market.status in OPEN_MARKET_STATUSES
            and market.game_start is not None
            and now < market.game_start <= window_end
            and market.event_allows_entry(now)
            and market.tick_size is not None
            and market.minimum_trade_qty is not None
            # The venue minimum must fit inside one quote, so this scales
            # with quote size rather than assuming a single contract.
            and market.minimum_trade_qty <= self.policy.quote_size
            and market.is_supported_moneyline
        )

    def _catalog_spread_can_enter(self, market: MarketMetadata) -> bool:
        """Use catalog BBO only to skip known-tight rows before book reads.

        Missing catalog quotes remain candidates. A fresh full book makes the
        authoritative entry decision later, so a stale wide catalog row can
        never authorize risk.
        """

        if (market.catalog_best_bid is None
                or market.catalog_best_offer is None):
            return True
        spread = market.catalog_best_offer - market.catalog_best_bid
        return spread + 1e-9 >= self.policy.minimum_entry_spread

    def _detailed_candidates(
        self,
        ranked: list[
            tuple[MarketMetadata, MarketIncentive, IncentivePeriod]
        ],
    ) -> tuple[
        list[tuple[MarketMetadata, MarketIncentive, IncentivePeriod]],
        tuple[str, ...],
    ]:
        """Keep the strongest head while rotating lower-pool options.

        Every currently executable catalog market is checked for rewards on
        every pass. The book budget always retains a small economically ranked
        head and the current selection, then advances a round-robin window
        through the remainder. Low-pool programs are therefore not permanently
        hidden by the API cap.
        """

        budget = self.policy.max_book_candidates
        if len(ranked) <= budget:
            return list(ranked), ()
        all_ranked = ranked
        day_of = [item for item in ranked if _candidate_key(item)[0] == 0]
        # Scan priority must survive the rotating tail too. Daily candidates
        # get only leftover book capacity; a previously selected daily market
        # cannot evict an unexamined day-of candidate from this scan.
        if len(day_of) >= budget:
            ranked = day_of
        head_count = min(budget, max(
            min(5, max(1, budget // 5)),
            len(day_of) if len(day_of) < budget else 0))
        chosen = list(ranked[:head_count])
        chosen_slugs = {item[0].slug for item in chosen}
        if self._last_selected_slug and len(chosen) < budget:
            current = next(
                (item for item in ranked
                 if item[0].slug == self._last_selected_slug),
                None,
            )
            if current is not None and current[0].slug not in chosen_slugs:
                chosen.append(current)
                chosen_slugs.add(current[0].slug)
        tail = [item for item in ranked if item[0].slug not in chosen_slugs]
        slots = budget - len(chosen)
        if tail and slots > 0:
            start = self._book_cursor % len(tail)
            count = min(slots, len(tail))
            chosen.extend(tail[(start + index) % len(tail)]
                          for index in range(count))
            self._book_cursor = (start + count) % len(tail)
        selected_slugs = {item[0].slug for item in chosen}
        deferred = tuple(
            item[0].slug for item in all_ranked
            if item[0].slug not in selected_slugs)
        return chosen, deferred

    def _applicable_period(
        self,
        market: MarketMetadata,
        incentive: MarketIncentive,
        now: datetime, *, require_event_freshness: bool = True,
    ) -> IncentivePeriod | None:
        if (not market.active or market.closed
                or market.status not in OPEN_MARKET_STATUSES
                or market.game_start is None or market.game_start <= now
                or market.event_started_at is not None
                or (require_event_freshness and not market.event_allows_entry(now))
                or market.tick_size is None
                or market.minimum_trade_qty is None
                or market.minimum_trade_qty > self.policy.quote_size
                or not market.is_supported_moneyline):
            return None
        period = select_applicable_period(
            incentive.periods, market.game_start, now,
            allow_daily_event=self.policy.allow_daily_event_rewards)
        if period is None or period.period == "live":
            return None
        return period


def reward_market_slugs(
    selection: RewardMarketSelection,
) -> tuple[str, ...]:
    """Stable helper for operator summaries and tests."""

    return tuple(item.market.slug for item in selection.opportunities)
