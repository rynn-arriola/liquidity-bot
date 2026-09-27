"""Reward eligibility evaluation and market rotation for the commissioning runner.

Moved verbatim from ``live_commissioning.CommissioningRunner`` (pure
relocation, no behavior change). Methods run on the runner instance via
inheritance, so every ``self`` attribute and override resolves as before.
"""

from __future__ import annotations

from .api_diagnostics import api_workflow
import time
from datetime import datetime
from .errors import AdapterError, RuntimeInvariantViolation
from .market_state import MarketState
from .reconciler import entry_quote_price
from .rewards import AmbiguousIncentiveError, evaluate_order_score, project_order_reward, select_applicable_period
from .risk import OpportunityEconomics
from .shadow_math import cross_exit
from .runner_shared import CONTRACT_REFRESH_LOOKAHEAD_SECONDS, CONTRACT_REFRESH_MAX_BACKOFF_SECONDS


class RewardRotationMixin:

    def _evaluate_rewards(
        self, now: datetime, slug: str | None = None,
    ) -> None:
        """Evaluate this market's economics, then record any CHANGE in them.

        The evaluation itself is `_evaluate_rewards_inner`; this wrapper exists
        only to emit `[eligibility]`. Three separate investigations in one
        session (2026-08-22/23) failed on the same missing fact -- a bound
        market resting no order leaves no trace of why, because the runner logs
        what it DID and a side dropped for ineligibility produces no mutation
        and therefore no `[action]` line. `price_too_extreme` appears zero
        times in a 50MB log for exactly this reason.

        Transitions rather than levels, so the volume is bounded: markets are
        serviced roughly once a second across twenty slots, and logging a level
        would add ~40 lines/second to a file that is already 12MB a session.
        A transition also carries the quantity a level cannot -- `held`, how
        long the previous state lasted, which is the oscillation period that
        decides between the three candidate fixes recorded in FINDINGS for the
        `reward_ineligible` churn.
        """

        state = self.state_for(slug or self.market_slug)
        try:
            self._evaluate_rewards_inner(now, slug)
        except Exception:
            # A failed evaluation interrupts continuous economic proof.  It
            # must not leave an old refusal clock aging through unknown state.
            state.economically_ineligible_since = None
            state.economic_ineligibility_reasons = ()
            raise
        self._record_economic_ineligibility(state, self._now())
        self._record_eligibility(state, self._now())

    THIN_BOOK_LOG_SECONDS = 300.0

    def _log_thin_book_skip(self, slug: str, side: str, spread: float,
                            queue_ahead: float, now: datetime) -> None:
        """Evidence for BC-2026-09-27-A, at most once per market side per 5 min."""
        logged = self.__dict__.setdefault("_thin_book_logged_at", {})
        last = logged.get((slug, side))
        if last is not None and (now - last).total_seconds() < self.THIN_BOOK_LOG_SECONDS:
            return
        logged[(slug, side)] = now
        print(f"[thin-book-skip] at={now.isoformat()} market={slug} side={side} "
              f"spread={spread:.4f} queue_ahead={queue_ahead:g}", flush=True)

    def _record_economic_ineligibility(
        self, state: MarketState, now: datetime,
    ) -> None:
        """Maintain a continuous dwell only for a flat, proven economic no.

        The timer is intentionally stricter than quote eligibility.  Both
        sides must have completed evaluation with a replaceable economic
        blocker, and the owner must already be position/order/ambiguity free.
        Any authorized side or execution state resets the dwell immediately.
        """

        reasons = tuple(sorted(set(state.economic_blocker_by_side.values())))
        replaceable = (
            set(state.economic_blocker_by_side) == {"bid", "offer"}
            and bool(reasons)
            and set(reasons).issubset(self.REPLACEABLE_ECONOMIC_BLOCKERS)
            and not any(
                value > 0 for value in state.expected_net_by_side.values())
            and self._owner_is_flat_and_clear(state.slug)
        )
        if not replaceable:
            state.economically_ineligible_since = None
            state.economic_ineligibility_reasons = ()
            return
        if state.economically_ineligible_since is None:
            state.economically_ineligible_since = now
        state.economic_ineligibility_reasons = reasons

    def _record_eligibility(self, state: MarketState, now: datetime) -> None:
        """Log a side whose reason-to-quote changed, with how long it held.

        Never raises. This is reporting, and `_evaluate_rewards` is reached
        from the shutdown path where an exception reads as a failure to prove
        flat -- which exits 3, which the supervisor never restarts. Same
        argument `_idle_market_reasons` and `_awaiting_rotation` already carry.
        """

        try:
            for side in ("bid", "offer"):
                label = (
                    "eligible" if side in state.reward_eligible
                    else state.economic_blocker_by_side.get(
                        side, "not_evaluated"))
                key = (state.slug, side)
                previous = self._eligibility_state.get(key)
                if previous is not None and previous[0] == label:
                    continue
                self._eligibility_state[key] = (label, now)
                if previous is None:
                    # First observation of this side carries no duration, and
                    # a bare "from=none" line per market per bind is noise.
                    continue
                resting = any(
                    order.side == side
                    for order in self._working_orders(state.slug))
                print(
                    f"[eligibility] at={now.isoformat()} "
                    f"market={state.slug} side={side} "
                    f"from={previous[0]} to={label} "
                    f"held={(now - previous[1]).total_seconds():.1f}s "
                    f"resting={'yes' if resting else 'no'}",
                    flush=True,
                )
        except Exception:
            return

    def _evaluate_rewards_inner(
        self, now: datetime, slug: str | None = None,
    ) -> None:
        self._accrue_score(now)
        state = self.state_for(slug or self.market_slug)
        state.clear_economics()
        if self._is_barred_from_quoting(state.slug):
            # A retired market is never re-authorized. Retirement clears
            # economics once; without this the very next evaluation would
            # recompute them from a healthy-looking book and quote it again.
            # The reconciler still plans its exit, which needs no economics.
            state.economic_blocker_by_side = {
                "bid": "market_retired", "offer": "market_retired"}
            return
        if state.market is None or state.book is None or state.incentive is None:
            # Named rather than bare: these three are the difference between
            # "we looked and declined" and "we never got the data", and they
            # were indistinguishable in the log. Reporting only.
            missing = ("no_market_metadata" if state.market is None
                       else "no_book" if state.book is None
                       else "no_incentive")
            state.economic_blocker_by_side = {"bid": missing, "offer": missing}
            return
        if not state.market.is_supported_moneyline:
            state.economic_blocker_by_side = {
                "bid": "unsupported_market_structure",
                "offer": "unsupported_market_structure",
            }
            return
        try:
            period = select_applicable_period(
                state.incentive.periods, state.market.game_start, now,
                allow_daily_event=self.config.allow_daily_event_rewards,
                daily_event_horizon_seconds=self.config.daily_event_horizon_seconds,
                daily_event_observed_at=state.incentive.observed_at)
        except AmbiguousIncentiveError:
            state.economic_blocker_by_side = {
                "bid": "ambiguous_incentive", "offer": "ambiguous_incentive"}
            return
        if period is None or period.period == "live" or state.market.tick_size is None:
            # Invariant 39: only approved pregame reward clocks qualify. Which of the
            # three it is matters -- "no applicable period" is a slate that has
            # not opened yet, "live" is a market we may never quote, and a
            # missing tick is bad metadata. Reporting only.
            why = ("no_applicable_period" if period is None
                   else "period_is_live" if period.period == "live"
                   else "no_tick_size")
            state.economic_blocker_by_side = {"bid": why, "offer": why}
            return
        if not state.book.is_two_sided:
            # `best_bid`/`best_offer` are `max`/`min` over a list, so an empty
            # side raises ValueError rather than returning None -- and this ran
            # outside any book guard. A single one-sided book took the whole
            # runner down with `runner_failure:ValueError` after two hours of
            # healthy quoting, and the supervisor then refused to restart
            # because shutdown could not prove flat. Observed 2026-08-16T19:01Z.
            #
            # Quoting needs BOTH touches -- the two-cent rule is defined
            # between them -- so there is genuinely nothing to evaluate here.
            # `Reconciler.plan` already returns no actions for exactly this
            # case; this is the same rule, applied one layer earlier. Exits are
            # unaffected: they are planned by the reconciler, which needs only
            # the side being sold into and no economics at all.
            state.economic_blocker_by_side = {
                "bid": "book_one_sided", "offer": "book_one_sided"}
            return
        eligible: set[str] = set()
        working_orders = self._working_orders(state.slug)
        working_sides = {order.side for order in working_orders}
        resting_price_by_side = {
            side: self._furthest_resting_price(working_orders, side)
            for side in ("bid", "offer")}
        spread = state.book.best_offer.price - state.book.best_bid.price
        for side in ("bid", "offer"):
            # Score the price we will actually rest at. An exact one-tick book
            # joins both touches; a locked book is priced back to a two-cent
            # gap. The economics and planner must use the same price.
            price = entry_quote_price(
                state.book, side,
                float(self.config.minimum_entry_spread),
                state.market.tick_size)
            if price is None:
                state.economic_blocker_by_side[side] = "book_sides_overlap"
                continue
            if min(price, 1.0 - price) < (
                    float(self.config.minimum_entry_price) - 1e-9):
                # The reconciler already refuses the same candidate.  Record
                # it here as economics as well so a flat market that drifts to
                # an untradeable extreme cannot silently own a slot forever.
                state.economic_blocker_by_side[side] = "price_too_extreme"
                continue
            already_resting = side in working_sides
            result = evaluate_order_score(
                side=side, price=price, size=float(self.config.quote_size),
                levels=state.book.levels(side), incentive=period,
                tick_size=state.market.tick_size,
                order_already_resting=already_resting)
            state.reward_score_by_side[side] = result
            if result.eligible:
                eligible.add(side)
            else:
                # Record why. This drop is what removes the side from
                # reward_eligible, and it used to write nothing at all -- so a
                # burst of cancels across every market logged `blockers={}`
                # with both nets present and could not be attributed for two
                # days. A later projection failure overwrites this with its own
                # more specific reason; nothing here changes behaviour.
                state.economic_blocker_by_side[side] = (
                    result.reason or "score_ineligible")
            projection = project_order_reward(
                side=side,
                price=price,
                size=float(self.config.quote_size),
                levels=state.book.levels(side),
                incentive=period,
                tick_size=state.market.tick_size,
                event_start=state.market.game_start,
                now=now,
                horizon_seconds=self.config.reward_projection_horizon_seconds,
                haircut=float(self.config.reward_projection_haircut),
                # Preserve live economics while the currently stated
                # per-market/period $1 payout floor is measured separately.
                minimum_payout=0.0,
                order_already_resting=already_resting,
            )
            state.reward_projection_by_side[side] = projection
            queue_ahead = (
                state.book.best_bid.size
                if side == "bid"
                else state.book.best_offer.size)
            # Preserve the exact approximation fed to the current model.  A
            # backed-off quote can have several better levels ahead of it, so
            # the durable observation also records actual cumulative queue
            # separately.  Measurement only: do not silently recalibrate the
            # live model while collecting its replacement data.
            state.model_queue_ahead_by_side[side] = queue_ahead
            seconds_to_event = max(
                0.0, (state.market.game_start - now).total_seconds())
            fill_hazard = self.fill_hazard_model.estimate(
                slug=state.market.slug,
                side=side,
                price=price,
                spread=spread,
                queue_ahead=queue_ahead,
                seconds_to_event=seconds_to_event,
                horizon_seconds=(
                    self.config.reward_projection_horizon_seconds),
            )
            state.fill_probability_by_side[side] = fill_hazard.probability
            state.fill_risk_driver_by_side[side] = fill_hazard.driver
            thin_blocker = self.config.thin_book_policy.blocker(
                slug=state.slug, spread=spread, queue_ahead=queue_ahead,
                already_resting=already_resting)
            if thin_blocker:
                # Entry-only (BC-2026-09-27-A): a resting side is never
                # cancelled by this rule and exits never consult it. A zero
                # expected net makes the reconciler drop the new entry.
                state.economic_blocker_by_side[side] = thin_blocker
                state.expected_net_by_side[side] = 0.0
                self._log_thin_book_skip(state.slug, side, spread, queue_ahead, now)
                continue
            if not projection.eligible:
                state.economic_blocker_by_side[side] = projection.reason
                continue
            exit_result = cross_exit(
                state.book,
                entry_side=side,
                entry_price=price,
                qty=float(self.config.quote_size),
                depth_haircut=.5,
                # If this quote fills, liquidity ahead at its entry level was
                # consumed. Never count that same level as guaranteed exit
                # depth, even while our order is already visible in the book.
                exclude_entry_level=True,
            )
            state.stressed_exit_by_side[side] = exit_result
            if not exit_result.complete:
                state.economic_blocker_by_side[side] = (
                    "stressed_exit_incomplete")
                continue
            quality_blocker = self.config.entry_quality_policy.blocker(exit_result)
            if quality_blocker:
                state.economic_blocker_by_side[side] = quality_blocker
                state.expected_net_by_side[side] = 0.0
                continue
            unwind_cost = max(0.0, -exit_result.pnl)
            economics = OpportunityEconomics(
                # Lower-bound score accrual by assigning the full horizon's
                # reward only to the no-fill branch.
                conservative_reward=(
                    projection.projected_reward
                    * (1.0 - fill_hazard.probability)),
                maker_rebate=0.0,
                # Pair-completion profit remains zero until live evidence can
                # estimate it without optimism.
                spread_capture=0.0,
                fill_probability=fill_hazard.probability,
                unwind_loss_if_filled=unwind_cost,
                operational_buffer=(
                    fill_hazard.probability * unwind_cost
                    * (float(self.config.reward_cost_safety_multiple) - 1.0)),
            )
            state.opportunity_economics_by_side[side] = economics
            if economics.should_quote or not self.config.enforce_reward_cost_gate:
                # The reconciler only quotes a side whose expected net is
                # strictly positive. With the gate advisory this value is
                # frequently negative, so a market would be selected and then
                # refuse to quote -- and because the sign flickers between
                # passes, a resting quote got cancelled seconds after being
                # placed. Measured: zero orders resting at the venue across
                # repeated samples, while 27 markets were "quoted" in 15
                # minutes. Reward is paid for resting, so that earned nothing.
                #
                # Floored the same way the selector already floors it. The
                # true value stays in reward_projection_by_side for the census
                # and the summary, so nothing measurable is lost.
                expected = economics.expected_net_value
                if not economics.should_quote:
                    expected = max(expected, 1e-9)
                state.expected_net_by_side[side] = expected
                # Live score-seconds represent actual attributable resting
                # exposure, not a hypothetical candidate. A newly authorized
                # side begins accruing only after its owned order is observed
                # working; a newly blocked side stops at this evaluation.
                if already_resting:
                    state.score_by_side[side] = self._resting_score(
                        state=state,
                        side=side,
                        period=period,
                        touch_price=price,
                        touch_score=result.score,
                        resting_price=resting_price_by_side[side])
            else:
                state.economic_blocker_by_side[side] = (
                    "projected_reward_does_not_cover_probability_"
                    "weighted_exit_cost")
                # Record the refusal as a value, not as an absent key.
                #
                # The reconciler treats a MISSING entry as "not evaluated this
                # pass" and leaves the eligibility dwell to decide, so absence
                # must not also mean "refused" -- that would let an enforced
                # refusal place the order it forbade. `should_quote` is
                # `expected_net_value > 0`, so this is at or below zero and the
                # reconciler drops the side naming `expected_net_not_positive`.
                state.expected_net_by_side[side] = min(
                    economics.expected_net_value, 0.0)
        if not self.config.enforce_reward_cost_gate:
            # Keep the measurement, drop the veto: the blocker above is still
            # computed and logged so the gate can be re-armed from evidence.
            state.economic_blocker_by_side = {
                side: reason
                for side, reason in state.economic_blocker_by_side.items()
                if not reason.startswith("projected_reward_does_not_cover")}
        state.reward_eligible = frozenset(eligible)

    def _awaiting_rotation(self) -> int:
        """Idle slots held only because their market has already started.

        Never raises, for the same reason `_idle_market_reasons` does not:
        this is reporting, and the summary is sent from the shutdown loop
        where an exception reads as a failure to prove flat -- which exits 3,
        which the supervisor never restarts. A reporting metric must not be
        able to keep a flat process from stopping cleanly.

        Counted per market rather than in one comprehension so that one
        market that cannot be inspected costs only its own row. Undercounting
        here is the safe direction: it moves a slot into `SLOTS NOT WORKING`,
        which over-reports a fault rather than hiding one.
        """

        waiting = 0
        for slug in self.active_slugs:
            try:
                if (not self.ledger.working_orders(slug)
                        and not self._is_retired(slug)
                        and self._has_kicked_off(slug)):
                    waiting += 1
            except Exception:
                continue
        return waiting

    def _rotation_blockers(self) -> tuple[str, ...]:
        """Require whole-account flat, owned, resolved state before rebinding."""

        blockers: list[str] = []
        if self.ledger.working_orders():
            blockers.append("working orders remain")
        if self.ledger.unresolved_intents():
            blockers.append("order intents remain unresolved")
        if self._has_open_position():
            blockers.append("a nonzero position remains")
        if not self.ledger.risk_state_known():
            blockers.append("whole-account execution state is not known")
        return tuple(blockers)

    def _reward_authorization_lost_and_flat(
        self, slug: str, now: datetime,
    ) -> bool:
        """True only after fresh metadata proves this slot is no longer paid.

        Missing reward rows are not proof of authorization loss. The separate
        optional idle replacement path never claims a program ended and may
        release only a freshly proved empty owner for a viable replacement.
        `_refresh_contracts` records confirmation only when a later complete
        response still contains the exact program that previously authorized
        entry and that row no longer applies to the freshly read event clock.
        This covers both an explicit elapsed ``ends_at`` and a postponed event
        moving back outside the six-hour day-of phase.
        """

        state = self._markets.get(slug)
        if state is None:
            return False
        confirmed_at = (state.reward_authorization_loss_confirmed_at
                        or state.reward_period_end_confirmed_at)
        lost_at = (state.reward_authorization_lost_at
                   or state.reward_period_ended_at)
        if (confirmed_at is None or lost_at is None or lost_at > now):
            return False
        market = state.market
        if (market is None or market.game_start is None
                or market.game_start <= now):
            # Past kickoff belongs to `_expired_and_flat`, whose separate
            # lifecycle rule is already covered and logged.
            return False
        return self._owner_is_flat_and_clear(slug)

    def _reward_ended_and_flat(self, slug: str, now: datetime) -> bool:
        """Compatibility name for the broader authorization-loss predicate."""

        return self._reward_authorization_lost_and_flat(slug, now)

    def _reward_idle_and_flat(self, slug: str, now: datetime) -> bool:
        """Repeated unavailable rewards can replace an empty slot, not risk."""

        state = self._markets.get(slug)
        if state is None:
            return False
        if (self._is_barred_from_quoting(slug)
                or not self._owner_is_flat_and_clear(slug)):
            state.clear_reward_unavailability()
            return False
        since = state.reward_unavailable_since
        observed = state.reward_unavailable_observed_at
        if since is None or observed is None:
            return False
        if not 0 <= (now - observed).total_seconds() <= (
                CONTRACT_REFRESH_MAX_BACKOFF_SECONDS
                + CONTRACT_REFRESH_LOOKAHEAD_SECONDS):
            state.clear_reward_unavailability()
            return False
        return (
            state.incentive is None
            and state.market is not None
            and state.market.game_start is not None
            and state.market.game_start > now
            and state.market.event_allows_entry(now)
            and not self._market_metadata_proves_not_open(state.market)
            and (observed - since).total_seconds()
            >= self.config.economic_idle_replacement_seconds
        )

    @api_workflow("market_rotation")
    async def _rotate_reward_market(self, now: datetime) -> bool:
        transition_started = time.monotonic()
        try:
            return await self._rotate_reward_market_inner(now)
        finally:
            self._rotation_transition_seconds_last = (
                time.monotonic() - transition_started)

    async def _rotate_reward_market_inner(self, now: datetime) -> bool:
        """Select from executable rewards and switch only from proven-flat state.

        The caller must cancel its outstanding ``next_*`` tasks first.  The
        underlying current streams may remain connected while discovery reads
        the shared, rate-governed REST API because all current maker orders
        have already been quarantined.
        """

        if not self.config.auto_select_reward_markets:
            return False
        await self._quarantine_working_orders()
        await self._rest_reconcile(self._now())
        blockers = self._rotation_blockers()
        if blockers:
            raise AdapterError(
                "reward-market rotation blocked: " + "; ".join(blockers))
        if self.reward_selector is None:
            raise RuntimeError("automatic reward selector is unavailable")
        scan_started = time.monotonic()
        try:
            selection = await self.reward_selector.select(now=self._now())
        finally:
            self._selection_scan_seconds_last = (
                time.monotonic() - scan_started)
        self._log_selection_scan(selection)
        self._market_selection = selection
        self._market_selection_observed_at = self._now()
        if selection.selected is None:
            # A zero-viable scan is a normal idle result, never permission to
            # bind a different monitoring-only market. Keep the proven stream
            # scope and refresh it after the potentially long discovery pass.
            #
            # The bound market can expire while the scan runs, most commonly by
            # reaching its own start. That is still an idle result rather than a
            # discovery failure, so it must not escape into the caller's fast
            # error-retry cadence: doing so re-scans the whole universe every 60
            # seconds and starves the shared REST budget that book and account
            # freshness depend on. Clear the incentive so no side can quote, and
            # stay dormant on the normal selection interval instead.
            try:
                await self._refresh_contracts(self._now())
                await self._refresh_book(self._now())
                self._runtime_error_counts.pop("idle_market_refresh", None)
            except Exception as exc:
                self.incentive = None
                self._reward_eligible = frozenset()
                await self._report_runtime_error("idle_market_refresh", exc)
            await self._rest_reconcile(self._now())
            return False
        # Markets retired earlier this session are never re-entered. The
        # exclusion happens inside top_viable so a barred market cannot take a
        # slot that a quotable one could have used.
        chosen = tuple(
            item.market.slug
            for item in selection.top_viable(
                self.config.max_markets, exclude=self._barred_slugs()))
        previous = tuple(self.active_slugs)
        if chosen == previous:
            for slug in previous:
                await self._refresh_contracts(self._now(), slug)
                await self._refresh_book(self._now(), slug)
            await self._rest_reconcile(self._now())
            return False

        old_private = self.private_stream
        old_market = self.market_stream
        await old_private.close()
        await old_market.close()
        self.gate.on_disconnect()
        # Per-slot replacement: _bind_markets discards only the entries being
        # dropped, so a retained market keeps its identity rather than being
        # torn down because a different slot changed. Its *contracts* are still
        # forgotten below -- both streams were just closed, so every book is
        # potentially stale and must be re-proven before it can authorize a
        # quote. Accrual is unaffected either way: score-seconds live in
        # _score_seconds_by_market, not in MarketState.
        self._bind_markets(chosen)
        for slug in chosen:
            self.state_for(slug).forget_contracts()
        self._reset_selected_market_observations(self._now())
        try:
            for slug in chosen:
                await self._refresh_contracts(self._now(), slug)
                await self._refresh_book(self._now(), slug)
            await self._open_streams_for_active_market()
            await self._refresh_earnings(self._now())
        except Exception as selection_error:
            # No mutation occurred after the flat proof. Restore the prior
            # scope so a transient discovery/bind failure leaves the process
            # safely monitoring instead of silently streamless.
            self._bind_markets(previous)
            for slug in previous:
                self.state_for(slug).forget_contracts()
            try:
                for slug in previous:
                    await self._refresh_contracts(self._now(), slug)
                    await self._refresh_book(self._now(), slug)
                await self._open_streams_for_active_market()
                await self._refresh_earnings(self._now())
            except Exception as restore_error:
                raise RuntimeInvariantViolation(
                    "market_rotation_restore_failed",
                    "automatic market rotation could not restore a safe "
                    "stream scope",
                ) from restore_error
            raise AdapterError(
                "selected reward markets could not be safely bound: "
                f"{type(selection_error).__name__}") from selection_error
        await self.notifier.send_async(
            "🔄 Liquidity Bot rotated while flat from "
            f"[{', '.join(previous) or 'none'}] to "
            f"[{', '.join(chosen)}]. "
            f"Joined {selection.catalog_markets} buffered event-market rows to "
            f"{selection.rewarded_executable_markets} executable reward programs, "
            f"evaluated {selection.books_evaluated} books, and "
            f"found {selection.viable_markets} economically viable markets; "
            f"selection scan {self._selection_scan_seconds_last:.1f}s; "
            f"{selection.unsupported_programs} unsupported lifecycle programs "
            "remained read-only.")
        return True
