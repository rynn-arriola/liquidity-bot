"""Per-market decisions, order placement/cancel handling, sweeps and exit service for the commissioning runner.

Moved verbatim from ``live_commissioning.CommissioningRunner`` (pure
relocation, no behavior change). Methods run on the runner instance via
inheritance, so every ``self`` attribute and override resolves as before.
"""

from __future__ import annotations

from . import read_backoff
from .api_diagnostics import api_workflow
import asyncio
import json
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from .errors import AdapterError, ApiAdmissionDeferred, EmptyBookError, MarketStreamUnavailable, RuntimeInvariantViolation
from .execution_ledger import LedgerConflict, QuoteObservation
from .reducer_diagnostics import capture_reducer_decision, completed_reducer_decision
from .live_orders import OrderMutationRejected, OrderOutcomeUncertain
from .market_state import MarketState
from .models import AccountBalance
from .reconciler import ActionKind, PlanAction, WorkingOrder
from .rewards import select_applicable_period
from .shadow_math import cross_exit
from .runner_shared import CONTRACT_REFRESH_SECONDS, DECISION_STATE_REFRESH_MARGIN_SECONDS, MAX_RECOVERY_ATTEMPTS, MAX_RETIREMENT_ROUNDS, _ConfirmedMarketNotOpen


class DecisionMixin:

    def _unresolved_places_without_exchange_id(self) -> tuple[Any, ...]:
        """Return creates that have no safe order-specific recovery path."""

        return tuple(
            row for row in self.ledger.unresolved_intents()
            if str(row["operation"]).upper() == "PLACE"
            and str(row["state"]).upper() == "UNCERTAIN"
            and not str(row["exchange_order_id"] or "").strip()
        )

    def _take_entry_pause_cancel_slugs(self) -> tuple[str, ...]:
        """Return exact flat owners whose known quotes need prompt withdrawal."""

        if not self._entry_pause_cancel_requested:
            return ()
        self._entry_pause_cancel_requested = False
        working = {
            str(row["market_slug"]) for row in self.ledger.working_orders()
            if row["market_slug"]
        }
        return tuple(
            slug for slug in self.active_slugs
            if (slug in working
                and slug in self._markets
                and self.ledger.position(slug) == 0)
        )

    def _record_scheduler_decision(
        self, slug: str, *, finished_at: datetime, action_at: datetime | None,
    ) -> None:
        started_at = self._dirty_market_since.pop(slug, None)
        if started_at is not None:
            latency = (finished_at - started_at).total_seconds()
            if latency >= 0:
                self._scheduler_decision_latencies.append(latency)
        if action_at is not None:
            input_at = self._market_last_input_at.get(slug)
            if input_at is not None:
                latency = (action_at - input_at).total_seconds()
                if latency >= 0:
                    self._scheduler_action_latencies.append(latency)
        # A later timer-driven action must not be attributed to an old input
        # that was already evaluated. A backoff path never reaches this method,
        # so it deliberately keeps the triggering input until its retry.
        self._market_last_input_at.pop(slug, None)
        for values in (
                self._scheduler_decision_latencies,
                self._scheduler_action_latencies):
            if len(values) > 512:
                del values[:-512]

    async def _retry_uncertain_cancels(
        self, now: datetime, *, markets: frozenset[str] | None = None,
    ) -> None:
        """Re-issue a cancel the venue has just confirmed did not land.

        `cancel` moves its intent to `UNCERTAIN` when the request fails and to
        `ACKNOWLEDGED` when the request returns, while either CANCEL intent
        resolves only on a terminal order state -- correctly, since even an
        accepted HTTP response is not cancellation confirmation. Re-issue
        either form only after a later positive OPEN read proves that terminal
        cancellation still has not happened.

        Measured 2026-08-18T15:16:52Z: `POST /v1/order/BYRYM21VADK0/cancel`
        returned InternalServerError, the venue answered `OPEN` for the next
        sixteen minutes, the 60s critical stop fired, and the same intent then
        made its own market's flatten unprovable -- `kill_switch.status` counts
        unresolved intents as a blocker -- so shutdown exited 3, which the
        supervisor never restarts. One 500 cost about three hours.

        Retried **only** while the order's freshly re-read state is active.
        That is positive evidence the cancel did not take effect, so re-sending
        it assumes nothing -- the exact opposite of reading absence as
        cancellation. An absent, unknown or terminal order is never retried.

        Each attempt opens a new intent, because the state machine does not
        allow `UNCERTAIN -> SUBMITTING` and reusing the id would misreport what
        happened. That is harmless: `_upsert_order` resolves every intent
        matching the exchange id in one statement, so the stuck one and its
        retries all clear together on the terminal state.
        """

        latest_by_exchange_id: dict[str, Any] = {}
        for row in self.ledger.unresolved_intents():
            if str(row["operation"]).upper() != "CANCEL":
                continue
            if str(row["state"]).upper() not in {
                    "ACKNOWLEDGED", "UNCERTAIN"}:
                continue
            exchange_id = str(row["exchange_order_id"] or "")
            if not exchange_id:
                continue
            prior = latest_by_exchange_id.get(exchange_id)
            if (prior is None
                    or str(row["updated_at"]) > str(prior["updated_at"])):
                latest_by_exchange_id[exchange_id] = row

        seen = set(latest_by_exchange_id)
        for exchange_id, row in latest_by_exchange_id.items():
            if (markets is not None
                    and str(row["market_slug"]) not in markets):
                continue
            if not self.ledger.order_is_active(exchange_id):
                continue
            # A successful cancel response can be followed briefly by an
            # authoritative-but-stale OPEN read. Give the venue one bounded
            # publication window before treating that OPEN as proof the
            # cancel did not land. This also makes the throttle survive an
            # in-process retry-map miss because the intent time is durable.
            intent_at = datetime.fromisoformat(str(row["updated_at"]))
            last = max(
                (value for value in (
                    intent_at, self._cancel_retried_at.get(exchange_id))
                 if value is not None),
            )
            if (now - last).total_seconds() < self.CANCEL_RETRY_SECONDS:
                continue
            self._cancel_retried_at[exchange_id] = now
            try:
                await self.executor.cancel(
                    client_order_id=str(row["client_order_id"]),
                    exchange_order_id=exchange_id,
                    market_slug=str(row["market_slug"]),
                    now=now)
                self._runtime_error_counts.pop("uncertain_cancel_retry", None)
            except Exception as exc:
                # Best-effort, like the absent sweep. A cancel the venue keeps
                # refusing is already the failure being repaired, and letting
                # it abort the reconcile would take every other market down
                # with it. The critical stop remains the backstop.
                await self._report_runtime_error("uncertain_cancel_retry", exc)
        # Forget orders that are no longer stuck, so the throttle cannot grow
        # without bound across a long session.
        self._cancel_retried_at = {
            key: at for key, at in self._cancel_retried_at.items()
            if key in seen}

    async def _read_owned_order_states(
        self, exchange_ids: list[str], now: datetime,
    ) -> list[tuple[str, str]]:
        failures: list[tuple[str, str]] = []
        for exchange_id in exchange_ids:
            try:
                await self.executor.reconcile_order(exchange_id, observed_at=now)
            except RuntimeInvariantViolation:
                raise
            except Exception as exc:
                failures.append((exchange_id, f"{type(exc).__name__}: {exc}"))
                self._note_order_read_failure(exchange_id, exc)
        return failures

    def _unresolved_cancel_owners(self) -> dict[str, str]:
        """Map unresolved CANCEL intents to their positively-known owner."""

        return {
            intent_id: detail[1]
            for intent_id, detail in self._unresolved_intent_owners().items()
            if detail[0] == "CANCEL"
        }

    def _record_resolved_cancel_owners(
        self, unresolved_cancels: dict[str, str],
    ) -> None:
        """Prioritize owners whose CANCEL gained terminal proof."""

        self._record_resolved_intent_owners({
            intent_id: ("CANCEL", slug, "")
            for intent_id, slug in unresolved_cancels.items()
        })

    def _record_reducer_decision(self, record, *, result=None, error_type=None):
        # A failed measurement must never turn a successful order into an
        # execution failure. One bounded row per attempted position action,
        # no API/ledger query and no raw exception/payload/credential logging.
        if record is None:
            return
        try:
            row = completed_reducer_decision(
                record, at=self._now(), result=result, error_type=error_type)
            print("[reducer-decision] " + json.dumps(row, sort_keys=True, allow_nan=False),
                  flush=True)
        except Exception:
            pass

    def _working_orders(
        self, slug: str | None = None, *, include_absent: bool = False,
    ) -> tuple[WorkingOrder, ...]:
        result: list[WorkingOrder] = []
        side_by_intent = {
            "ORDER_INTENT_BUY_LONG": "bid",
            "ORDER_INTENT_SELL_SHORT": "bid",
            "ORDER_INTENT_BUY_SHORT": "offer",
            "ORDER_INTENT_SELL_LONG": "offer",
        }
        rows = self.ledger.working_orders(slug or self.market_slug)
        if include_absent:
            rows += self.ledger.absent_orders(slug or self.market_slug)
        for row in rows:
            intent_row = self.ledger.query(
                "SELECT payload_json FROM execution_intents "
                "WHERE operation='PLACE' AND client_order_id=? "
                "ORDER BY created_at DESC LIMIT 1", (row["client_order_id"],))
            if not intent_row:
                raise RuntimeInvariantViolation(
                    "working_order_without_owned_intent",
                    "working order has no owned place intent")
            payload = json.loads(intent_row[0]["payload_json"])
            strategy_side = side_by_intent.get(str(payload.get("intent") or ""))
            if strategy_side is None:
                raise RuntimeInvariantViolation(
                    "working_order_intent_unmapped",
                    "working order intent cannot be mapped")
            remaining = Decimal(row["original_qty"]) - Decimal(row["filled_qty"])
            if remaining > 0:
                result.append(WorkingOrder(
                    row["exchange_order_id"], strategy_side,
                    float(Decimal(row["price"])), remaining))
        return tuple(result)

    def _quote_observation_for_action(
        self,
        state: MarketState,
        action: PlanAction,
        now: datetime,
        position: Decimal,
    ) -> QuoteObservation | None:
        """Describe a building PLACE without influencing whether it is sent.

        A failure here is reported and the order follows the unchanged action
        path.  Coverage is independently measurable because the durable PLACE
        intent is marked ``quote_observation_required`` even when this returns
        ``None``.
        """

        if (action.kind != ActionKind.PLACE_POST_ONLY
                or action.side not in {"bid", "offer"}
                or action.price is None or action.qty is None
                or position != 0):
            return None
        try:
            if (state.market is None or state.book is None
                    or state.book_observed_at is None
                    or state.incentive is None):
                raise RuntimeError("market observation state is incomplete")
            if (state.book.slug != action.market_slug
                    or state.market.slug != action.market_slug):
                raise RuntimeError("market observation identity is inconsistent")
            period = select_applicable_period(
                state.incentive.periods, state.market.game_start, now,
                allow_daily_event=self.config.allow_daily_event_rewards,
                daily_event_horizon_seconds=self.config.daily_event_horizon_seconds,
                daily_event_observed_at=state.incentive.observed_at)
            if period is None:
                raise RuntimeError("applicable reward period is unavailable")
            side = action.side
            score = state.reward_score_by_side[side]
            projection = state.reward_projection_by_side[side]
            hazard = state.fill_probability_by_side[side]
            driver = state.fill_risk_driver_by_side[side]
            # The cached economics may describe an earlier touch. Bind these
            # telemetry fields to this exact action and current local book.
            exit_result = cross_exit(
                state.book, entry_side=side, entry_price=float(action.price),
                qty=float(action.qty), depth_haircut=.5,
                exclude_entry_level=True)
            economics = state.opportunity_economics_by_side[side]
            model_queue = state.model_queue_ahead_by_side[side]

            entry_stake = exit_result.entry_cost
            retained_value = (
                entry_stake + exit_result.pnl
                if exit_result.complete and entry_stake > 0 else None)
            retained_ratio = (
                retained_value / entry_stake
                if retained_value is not None else None)
            replacement = self._retained_value_replacement_snapshot(
                now, current_projected_reward=projection.projected_reward)

            quote_price = float(action.price)
            better = at_quote = 0.0
            for level_price, level_size in state.book.levels(side):
                if (side == "bid" and level_price > quote_price + 1e-9
                        or side == "offer" and level_price < quote_price - 1e-9):
                    better += level_size
                elif abs(level_price - quote_price) <= 1e-9:
                    at_quote += level_size
            observed_queue = better + at_quote
            seconds_to_event = (
                max(0.0, (state.market.game_start - now).total_seconds())
                if state.market.game_start is not None else None)
            provenance = self._selection_provenance_for_market(now, state.slug)
            from .fill_hazard import CALIBRATION_ID
            return QuoteObservation(
                market_slug=state.slug,
                side=side,
                observed_at=now,
                book_observed_at=state.book_observed_at,
                game_start=state.market.game_start,
                quote_price=Decimal(str(action.price)),
                quantity=action.qty,
                decision_reason=action.reason,
                best_bid=state.book.best_bid.price,
                best_bid_size=state.book.best_bid.size,
                best_offer=state.book.best_offer.price,
                best_offer_size=state.book.best_offer.size,
                spread=state.book.best_offer.price - state.book.best_bid.price,
                bid_depth=state.book.bid_depth,
                offer_depth=state.book.offer_depth,
                model_queue_ahead=model_queue,
                observed_queue_ahead=observed_queue,
                depth_better_than_quote=better,
                depth_at_quote=at_quote,
                depth_through_quote=observed_queue,
                touch_changes_30s=state.touch_changes(now, seconds=30),
                touch_changes_120s=state.touch_changes(now, seconds=120),
                seconds_since_touch_change=(
                    state.seconds_since_touch_change(now)),
                seconds_to_event=seconds_to_event,
                program_id=period.program_id,
                reward_period=period.period,
                reward_pool=period.reward_pool,
                discount_factor=period.discount_factor,
                target_size=period.target_size,
                cutoff_price=score.cutoff_price,
                depth_to_reward_cutoff=score.total_depth,
                ticks_from_best=score.ticks_from_best,
                order_score=score.score,
                total_score=projection.total_score,
                snapshot_share=projection.snapshot_share,
                projected_reward=projection.projected_reward,
                maximum_remaining_reward=(
                    projection.maximum_remaining_reward),
                fill_probability=hazard,
                fill_risk_driver=driver,
                stressed_exit_complete=exit_result.complete,
                stressed_exit_closed_qty=exit_result.closed_qty,
                stressed_exit_average_price=exit_result.average_price,
                stressed_exit_worst_price=exit_result.worst_price,
                stressed_exit_fee=exit_result.taker_fee,
                stressed_exit_pnl=exit_result.pnl,
                expected_net=economics.expected_net_value,
                reward_cost_safety_multiple=float(
                    self.config.reward_cost_safety_multiple),
                retained_value_ratio=retained_ratio,
                stressed_exit_value=retained_value,
                would_refuse_retained_60=(
                    retained_ratio < .60
                    if retained_ratio is not None else None),
                would_refuse_retained_70=(
                    retained_ratio < .70
                    if retained_ratio is not None else None),
                would_refuse_retained_80=(
                    retained_ratio < .80
                    if retained_ratio is not None else None),
                replacement_market_slug=replacement[0],
                replacement_projected_reward=replacement[1],
                replacement_expected_net=replacement[2],
                replacement_reward_delta=replacement[3],
                replacement_selection_age_seconds=replacement[4],
                replacement_reason=replacement[5],
                slot_would_go_dark=replacement[6],
                selection_rank=provenance.get("current", {}).get("rank"),
                selection_provenance=provenance,
                fill_model_horizon_seconds=self.config.reward_projection_horizon_seconds,
                fill_model_calibration_id=CALIBRATION_ID,
            )
        except Exception as exc:
            # Observation is reporting.  It must never turn a valid action
            # into a strategy refusal or make shutdown unable to prove flat.
            print(
                f"[quote-observation] at={now.isoformat()} "
                f"market={state.slug} side={action.side} "
                f"missing={type(exc).__name__}:{exc}",
                flush=True,
            )
            return None

    def _effective_entry_order_cap(self, observed_at: datetime) -> int:
        """Return the tighter of the durable loss and clock-based ceilings."""

        if not self._window_entry_ready(observed_at):
            return 0
        policy_cap = min(
            self._loss_entry_order_cap,
            self.config.entry_order_cap_for_time(observed_at),
        )
        ceiling = self.PENDING_ENTRY_ORDER_CEILING
        return policy_cap if ceiling is None else min(policy_cap, ceiling)

    def _refresh_overnight_entry_order_cap(
        self, observed_at: datetime,
    ) -> None:
        """Record midnight/09:00 ET transitions without changing loss state."""

        time_cap = self.config.entry_order_cap_for_time(observed_at)
        active = self.config.entry_order_cap_is_overnight(observed_at)
        previous = self._overnight_entry_order_cap_active
        self._overnight_entry_order_cap_active = active
        if previous is active:
            return
        # An initial daytime observation merely establishes state. Starting
        # inside the overnight window is material and should be announced.
        if previous is None and not active:
            return

        effective = self._effective_entry_order_cap(observed_at)
        print(
            f"[overnight-entry-order-cap] at={observed_at.isoformat()} "
            "window=00:00-09:00_ET "
            f"active={str(active).lower()} time_cap={time_cap} "
            f"loss_cap={self._loss_entry_order_cap} effective={effective} "
            "positions_and_reducers=excluded",
            flush=True,
        )
        self._overnight_entry_order_cap_notice = (
            active, effective, self._loss_entry_order_cap, time_cap)
        self._dirty_market_slugs.update(
            slug for slug in self.active_slugs
            if self.ledger.position(slug) == 0)

    def _flat_entry_orders_and_reservations(
        self,
    ) -> tuple[tuple[Any, ...], int]:
        """Return working flat entry orders and unmatched PLACE reservations."""

        positioned = {
            str(row["market_slug"])
            for row in self.ledger.query(
                "SELECT market_slug FROM execution_positions "
                "WHERE CAST(net_qty AS REAL)!=0")
        }
        working = tuple(
            row for row in self.ledger.working_orders(owned_only=True)
            if str(row["market_slug"]) not in positioned)
        active_clients = {str(row["client_order_id"]) for row in working}
        reservations = {
            str(row["client_order_id"])
            for row in self.ledger.unresolved_intents()
            if str(row["operation"]) == "PLACE"
            and str(row["market_slug"]) not in positioned
            and str(row["client_order_id"]) not in active_clients
        }
        reservations.update(
            str(row["client_order_id"]) for row in self.ledger.absent_orders()
            if str(row["market_slug"]) not in positioned
            and str(row["client_order_id"]) not in active_clients)
        return working, len(reservations)

    async def _refresh_execution_state_for_decision(
        self, now: datetime,
    ) -> None:
        """Reconcile before the private/session gate expires mid-sweep."""

        observations = tuple(
            at for at in (
                self.gate.last_message_at,
                self.gate.last_reconcile_at,
            ) if at is not None)
        if not observations:
            await self._rest_reconcile(
                now, strict_order_markets=frozenset())
            return
        age = (now - max(observations)).total_seconds()
        refresh_at = max(
            0.0,
            self.gate.max_silence_seconds
            - DECISION_STATE_REFRESH_MARGIN_SECONDS,
        )
        if age < 0 or age >= refresh_at:
            await self._rest_reconcile(
                now, strict_order_markets=frozenset())

    async def _announce_overnight_entry_order_cap(self) -> None:
        """Notify on each midnight/09:00 ET ceiling transition."""

        notice, self._overnight_entry_order_cap_notice = (
            self._overnight_entry_order_cap_notice, None)
        if notice is None:
            return
        active, effective, loss_cap, time_cap = notice
        if active:
            detail = (
                "is active until 9:00 AM ET; pending flat-entry orders are "
                f"limited to {effective}"
            )
        else:
            detail = (
                f"ended at 9:00 AM ET; the daytime cap is {time_cap} and "
                f"the effective cap is {effective} after the remaining "
                f"loss-based cap of {loss_cap}"
            )
        await self.notifier.send_async(
            f"ℹ️ Liquidity Bot midnight-9:00 AM ET order cap {detail}. "
            "Open positions, reducers, and exits are excluded.")

    @api_workflow("unresolved_order_repair")
    async def _repair_unresolved_orders(
        self, now: datetime, *, markets: frozenset[str],
    ) -> None:
        """Seek positive terminal proof without repeating account snapshots.

        The normal ten-second reconcile already refreshes complete open-order
        and position snapshots. Repeating those account reads once per owner
        made a private disconnect's twenty cancel owners take several minutes
        to repair. This focused path asks only for the unresolved ids in a
        bounded owner batch. Each read is independent and concurrent so one
        venue NotFound retry ladder cannot hide the other answers; the shared
        REST limiter remains the authority on request rate.

        No absence is interpreted here. An intent resolves only if
        ``reconcile_order`` applies an affirmative venue state.
        """

        started_at = self._now()
        unresolved_rows = self.ledger.unresolved_intents()
        scoped_intent_ids = {
            str(row["intent_id"])
            for row in unresolved_rows
            if str(row["market_slug"] or "").strip() in markets
        }
        unresolved_owners = self._unresolved_intent_owners()
        exchange_ids = sorted({
            str(row["exchange_order_id"] or "").strip()
            for row in unresolved_rows
            if str(row["market_slug"] or "").strip() in markets
            and str(row["exchange_order_id"] or "").strip()
        })

        async def repair(exchange_id: str) -> tuple[str, str] | None:
            try:
                await self.executor.reconcile_order(
                    exchange_id, observed_at=now)
            except RuntimeInvariantViolation:
                raise
            except Exception as exc:
                return exchange_id, f"{type(exc).__name__}: {exc}"
            return None

        results = await asyncio.gather(*(repair(item) for item in exchange_ids))
        # A positive OPEN read may license retrying an uncertain cancel. This
        # still uses the normal per-owner throttle and mutation state machine.
        await self._retry_uncertain_cancels(now, markets=markets)
        self._record_resolved_intent_owners(unresolved_owners)
        failures = [result for result in results if result is not None]
        remaining_intent_ids = {
            str(row["intent_id"])
            for row in self.ledger.unresolved_intents()
            if str(row["market_slug"] or "").strip() in markets
        }
        finished_at = self._now()
        print(
            f"[unresolved-repair] at={finished_at.isoformat()} "
            f"owners={len(markets)} ids={len(exchange_ids)} "
            f"resolved={len(scoped_intent_ids - remaining_intent_ids)} "
            f"remaining={len(remaining_intent_ids)} failures={len(failures)} "
            f"elapsed={max(0.0, (finished_at - started_at).total_seconds()):.1f} "
            f"markets={','.join(sorted(markets))}",
            flush=True,
        )
        if failures:
            raise AdapterError(
                "could not re-read "
                f"{len(failures)} unresolved order(s): "
                + "; ".join(
                    f"{exchange_id}: {detail}"
                    for exchange_id, detail in failures))

    @staticmethod
    def _is_mutation_action(action: PlanAction) -> bool:
        return action.kind in {
            ActionKind.CANCEL,
            ActionKind.PLACE_POST_ONLY,
            ActionKind.PLACE_IOC,
        }

    async def _prepare_market_decision(self, slug: str) -> bool:
        """Source-specific scheduling checkpoint, never mutation authority."""
        return True

    async def _decide_for_market(
        self, slug: str, now: datetime, *, replanned: bool = False,
    ) -> tuple[str | None, float]:
        """Plan and apply at most one mutation for one market.

        Returns ``(stop_reason, backoff_seconds)``. A stop reason is terminal
        for the whole session; a backoff defers only this market's next pass.
        Recoverable conditions are handled here so one market's stale book or
        pending exit cannot stall the other nineteen.

        The clock is read per market rather than once per sweep: issuing twenty
        mutations through a 4 requests/second governor takes real time, and a
        market whose book aged meanwhile must report ``book_stale`` and be
        skipped rather than quote on data that is no longer fresh.
        """

        state = self.state_for(slug)
        if self._maintenance_owner_is_idle(slug, now):
            return None, 0.0
        retired = self._retired.get(slug)
        if (retired is not None and retired.rounds >= MAX_RETIREMENT_ROUNDS
                and self.ledger.position(slug) == 0
                and not self.ledger.working_orders(slug)
                and not self.ledger.absent_orders(slug)
                and not self.ledger.unresolved_intents(slug)):
            # Only positively empty local owners may become dormant. Held or
            # ambiguous owners keep their normal proof and slow exit service.
            return None, 0.0
        if (retired is not None and retired.next_retry_at is not None
                and now < retired.next_retry_at):
            # Inside the cooldown the position simply rests. Its reducing quote
            # is already working and the pre-kickoff flatten is unaffected;
            # replanning here would re-fire the same condition immediately.
            return None, 0.0
        try:
            await self._refresh_event_guard()
            if self._stop_requested:
                return None, 0.0
            if not await self._prepare_market_decision(slug):
                return None, 1.0
            if self._stop_requested:
                return None, 0.0
            now = self._now()
            frame = self._frame(now, slug)
            overflow_cancel = self._loss_cap_cancel_action(frame)
            actions = (
                [overflow_cancel] if overflow_cancel is not None
                else self.reconciler.plan(frame))
            if (actions
                    and actions[0].kind == ActionKind.PLACE_POST_ONLY
                    and frame.position == 0
                    and self._loss_cap_blocks_new_entry(frame.now)):
                # A cap is a ceiling, not a target. Existing normal cancels
                # still flow through the reconciler; only a building PLACE is
                # suppressed. The next ledger/private input dirties the owner
                # and retries after real capacity exists.
                self._note_entry_suppressed(
                    slug, actions[0], frame.now, "pending_entry_cap")
                actions = []
            if (actions and actions[0].kind == ActionKind.PLACE_POST_ONLY
                    and frame.position == 0 and self._entry_observation_pending()):
                # Known proof waits are scheduling, not failed durable creates.
                # Keep every cancellation and the final admission fence intact.
                return None, 1.0
        except LedgerConflict:
            raise
        except RuntimeInvariantViolation as invariant:
            # A violated runtime invariant is about the process, not this
            # market. It stays terminal.
            return await self._handle_strategy_plan_failure(invariant), 0.0
        except Exception as exc:
            # Planning raised on this market's own state. Ending the session
            # would take seven healthy markets down with it and stop all
            # accrual; retiring keeps them quoting while this one is worked out
            # and never quoted again. Retirement is bounded -- ten rounds per
            # market, and a full slate of retirements still stops the session,
            # so a systemic planning fault is not silently absorbed.
            await self._report_runtime_error(f"strategy_plan:{slug}", exc)
            stop = await self._retire_market(
                slug, f"plan_failed_{type(exc).__name__}")
            return stop, 0.0
        if actions and actions[0].kind == ActionKind.CRITICAL:
            reason = actions[0].reason
            if reason == "hard_exit_sent_but_inventory_remains":
                try:
                    outcome = await self._reconcile_pending_hard_exit(slug)
                    print(
                        "[runtime-recovery] "
                        f"at={self._now().isoformat()} market={slug} "
                        f"component=hard_exit_confirmation outcome={outcome}",
                        flush=True,
                    )
                    if (outcome == "pending"
                            and self.reconciler.hard_exit_confirmation_expired(
                                slug, now=self._now())):
                        stop = await self._retire_market(
                            slug, "hard_exit_confirmation_timeout",
                            reset_exit_budget=False)
                        return stop, 0.0
                    self._runtime_error_counts.pop(
                        f"hard_exit_confirmation:{slug}", None)
                    if outcome == "flat":
                        return None, 0.0
                    return None, self._hard_exit_retry_delay(slug)
                except Exception as exc:
                    await self._report_runtime_error(
                        f"hard_exit_confirmation:{slug}", exc)
                    if self.reconciler.hard_exit_confirmation_expired(
                            slug, now=self._now()):
                        stop = await self._retire_market(
                            slug, "hard_exit_confirmation_timeout",
                            reset_exit_budget=False)
                        return stop, 0.0
                    return None, self._recovery_delay(
                        f"hard_exit_confirmation:{slug}")
            stop = await self._handle_strategy_critical(reason, slug)
            if stop:
                return stop, 0.0
            # A refreshed book is only worth fetching if we plan on it while it
            # is still fresh. Returning straight to `_recovery_delay` -- which
            # escalates to a 30s cap -- outlives the five-second freshness
            # budget the refresh just satisfied, so the next visit reports
            # `book_stale` again and the refresh was wasted. That loop feeds
            # itself: measured 2026-08-16, 7,535 book_stale events with
            # consecutive ones 34-75s apart on the same market, and 30 of the
            # session's 31 retirements were `unrecoverable_book_stale`.
            #
            # It also selects against exactly the markets we want. Quiet books
            # are the ones with the least competition for reward share, and
            # reward is paid for RESTING, not for trading -- a market with
            # nothing to send is not a market that is broken.
            #
            # Replanning once here also lets the success path below clear
            # `strategy_state:{slug}`, so a market that recovers stops marching
            # toward MAX_RECOVERY_ATTEMPTS. `replanned` bounds this to a single
            # extra attempt per sweep, so a book that will not refresh still
            # backs off instead of spinning.
            if reason == "book_stale":
                # Diagnostic only, no behaviour change. Measured 2026-08-17:
                # seven markets held slots for 54-130 minutes and never placed
                # a single order, every visit reporting `book_stale` at
                # attempt=1 -- while their books fetched cleanly from REST,
                # two-sided and open, and no `quiet_book_rest_refresh` error
                # was ever logged for them. So the refresh does not fail; the
                # planner just never sees a fresh book, and nothing recorded
                # enough to say why. These three numbers separate the
                # candidates: a refresh that never fires (age keeps growing,
                # `since_refresh` large), one that fires but lands stale
                # (`age` already over budget right after a refresh), and a
                # replan that is never reached (`fresh_enough` false).
                state = self.state_for(slug)
                now_at = self._now()
                age = state.book_age_seconds(now_at)
                due = state.next_book_refresh
                fresh_enough = self._book_is_fresh_enough_to_plan(slug)
                if not fresh_enough:
                    print(
                        f"[book-stale-why] at={now_at.isoformat()} market={slug} "
                        f"age={'none' if age is None else round(age, 1)} "
                        f"refresh_due_in="
                        f"{'none' if due is None else round((due - now_at).total_seconds(), 1)} "
                        f"fresh_enough={fresh_enough} replanned={replanned}",
                        flush=True,
                    )
            if (not replanned and reason == "book_stale"
                    and fresh_enough):
                return await self._decide_for_market(
                    slug, self._now(), replanned=True)
            return None, self._recovery_delay(f"strategy_state:{slug}")
        # Per market. A shared counter let one market's repeated failure
        # inflate every market's backoff toward the 30s cap, and let any one
        # market's success reset a different market's escalation.
        for key in ("runtime_state_gate", "runtime_state_quarantine",
                    "strategy_plan", "strategy_plan_recovery"):
            self._runtime_error_counts.pop(key, None)
        for key in ("strategy_state", "strategy_state_recovery",
                    "action_driver", "action_driver_recovery"):
            self._runtime_error_counts.pop(f"{key}:{slug}", None)
        if self.driver is None or state.market is None:
            raise RuntimeError("commissioning driver is unavailable")
        # One snapshot is reused by rejection/buying-power protection,
        # observation classification and the action driver. A nonzero position
        # makes PLACE_POST_ONLY reducing; no entry rejection cooldown or buying
        # power gate may delay that exit path.
        position = self.ledger.position(slug)
        # Log the mutation and why the planner asked for it. Without this the
        # ledger records that an order was cancelled but never the reason, so
        # the 2026-08-14 place/cancel churn could not be diagnosed from the
        # data it left behind -- only guessed at.
        # An empty plan is the normal healthy case -- the desired state already
        # matches, so there is nothing to do and nothing to log. Indexing it
        # unconditionally crashed the runner every time the planner was
        # satisfied, which is most passes.
        if actions:
            chosen = actions[0]
            print(
                f"[action] at={now.isoformat()} market={slug} "
                f"kind={chosen.kind.name} reason={chosen.reason} "
                f"side={chosen.side} price={chosen.price} "
                f"working={len(self._working_orders(slug))} "
                f"pending={len(actions)} "
                f"blockers={state.economic_blocker_by_side or '{}'} "
                f"net={ {k: round(v, 6) for k, v in
                         state.expected_net_by_side.items()} }",
                flush=True,
            )
            mutation_delay = self._mutation_access_delay(
                actions, now, position=position)
            if mutation_delay > 0:
                print(
                    f"[mutation-access-backoff] at={now.isoformat()} "
                    f"market={slug} kind={chosen.kind.name} "
                    f"seconds={mutation_delay:.1f}",
                    flush=True,
                )
                return None, mutation_delay
            if (chosen.kind == ActionKind.PLACE_POST_ONLY and position == 0):
                if not state.market.event_allows_entry(self._now()):
                    return None, 1.0
                if self.config.entry_quality_policy.enabled:
                    # Recheck the actual action after planning; do not consume
                    # cached shadow labels or add any venue request. Safety
                    # actions bypass this entry-only boundary completely.
                    book_age = state.book_age_seconds(self._now())
                    if (state.book is None or state.book.slug != slug
                            or book_age is None or not 0 <= book_age <= 5
                            or chosen.side not in {"bid", "offer"}
                            or chosen.price is None or chosen.qty is None):
                        return None, 1.0
                    entry_exit = cross_exit(
                        state.book, entry_side=chosen.side,
                        entry_price=float(chosen.price), qty=float(chosen.qty),
                        depth_haircut=.5, exclude_entry_level=True)
                    quality_blocker = self.config.entry_quality_policy.blocker(entry_exit)
                    if quality_blocker:
                        print(f"[entry-quality] at={now.isoformat()} market={slug} "
                              f"side={chosen.side} reason={quality_blocker}", flush=True)
                        return None, 1.0
                rejection_delay = self._recent_rejection_delay(slug, now)
                if rejection_delay > 0:
                    latest = self.ledger.latest_rejection(slug)
                    print(
                        f"[order-rejection-backoff] at={now.isoformat()} "
                        f"market={slug} reason="
                        f"{latest['reject_reason'] if latest else 'unknown'} "
                        f"seconds={rejection_delay:.1f}",
                        flush=True,
                    )
                    return None, rejection_delay
        try:
            # Reuse one ledger snapshot for both observation classification
            # and the action driver.  Observation must not add another query
            # to each placement or see a different position than the driver.
            power = self._entry_buying_power(actions[0], position=position) \
                if actions else None
            if power is not None and power[1] < power[0]:
                print(
                    f"[buying-power-block] at={now.isoformat()} market={slug} "
                    f"required={power[0]} available={power[1]}",
                    flush=True,
                )
                return None, self.BUYING_POWER_RECHECK_SECONDS
            quote_observation = (
                self._quote_observation_for_action(
                    state, actions[0], self._now(), position)
                if actions else None)
            reducer_record = None
            try:
                if actions:
                    reducer_record = capture_reducer_decision(
                        frame, actions[0],
                        source=getattr(self, "account_state_mode", "websocket"),
                        apply_position=position)
            except Exception:
                pass  # Missing evidence never changes an execution decision.
            try:
                result = await self.driver.apply_one(
                    actions, market=state.market,
                    position=position, now=now,
                    quote_observation=quote_observation)
            except Exception as exc:
                self._record_reducer_decision(reducer_record, error_type=type(exc).__name__)
                raise
            self._record_reducer_decision(reducer_record, result=result)
            if actions and result is not None:
                self._record_execution_timing("action", action=actions[0],
                                              result=result, position=position)
            if (actions and result is not None
                    and getattr(result, "cancel_intent_id", None) is not None
                    and actions[0].reason
                    == "hard_timeout_cancel_confirm_first"):
                # The grace belongs to the exact request that crossed the
                # mutation boundary, not to a plan that local admission may
                # have deferred. The reconciler will wait for terminal proof
                # of this order before selecting the next hard-exit cancel.
                self.reconciler.note_hard_exit_cancel_submitted(
                    slug,
                    actions[0].order_id or "",
                    submitted_at=self._now(),
                )
            if actions and result is not None:
                self._clear_mutation_access_denial(
                    actions[0], position=position)
            # The venue balance refresh is periodic. Reserve successful new
            # quote notional locally so later slots in this same sweep cannot
            # all spend the same stale buying-power observation. A rejection or
            # cancel is restored by the next authenticated five-second read.
            if (power is not None and result is not None
                    and result.submitted is not None
                    and self.balance is not None):
                self.balance = AccountBalance(
                    current_balance=self.balance.current_balance,
                    asset_notional=self.balance.asset_notional,
                    buying_power=max(Decimal("0"), power[1] - power[0]),
                    observed_at=self.balance.observed_at,
                )
        except Exception as exc:
            stop = await self._handle_action_failure(
                exc,
                slug,
                risk_reducing=(
                    bool(actions)
                    and self._is_risk_reducing_mutation(
                        actions[0], position)),
            )
            if stop:
                return stop, 0.0
            if isinstance(exc, ApiAdmissionDeferred):
                delay = self._api_admission_delay(exc)
                self._log_api_backpressure(
                    f"action_driver:{slug}", delay, cause=exc.error_code)
                return None, delay
            return None, self._recovery_delay(f"action_driver:{slug}")
        if actions:
            # Stamp a successful mutation, not an empty healthy plan.
            self._last_action_at[slug] = self._now()
            self._market_action_generation[slug] = (
                self._market_action_generation.get(slug, 0) + 1)
        return None, 0.0

    def _hard_exit_retry_delay(self, slug: str) -> float:
        """Back off a confirmed terminal IOC before its bounded retry."""

        attempts = self.reconciler.hard_exit_attempts(slug)
        return float(min(2 ** max(attempts - 1, 0), 5))

    async def _service_urgent_positions(self) -> str | None:
        """Optional source-specific exit pass before unrelated periodic work."""
        return None

    @api_workflow("decision_sweep")
    async def _decision_sweep(
        self, slugs: tuple[str, ...] | None = None,
        *, position_only: bool = False, service_books: bool = True,
    ) -> str | None:
        """Plan every due market without cross-slot intent/backoff blocking."""

        state_refresh_failed = False
        equity_refresh_failed = False
        planned_slugs = self.active_slugs if slugs is None else slugs
        pending_slugs = list(planned_slugs)
        priority_seen: dict[str, tuple[Any, ...]] = {}
        priority_visits: dict[str, int] = {}
        priority_dispatches: set[str] = set()
        while pending_slugs:
            slug = pending_slugs.pop(0)
            priority_dispatch = slug in priority_dispatches
            priority_dispatches.discard(slug)
            # SIGTERM is delivered by an event-loop callback.  The outer run
            # loop cannot see it while this twenty-market sweep is already in
            # progress, so without this boundary the remaining slots can place
            # fresh risk after an operator has asked the bot to stop.  Observed
            # live 2026-08-25.  Return to the shutdown path; it alone owns the
            # cancellation and flattening work that follows a stop.
            if self._stop_requested:
                return self._stop_requested
            if self._streams_need_rebind:
                return None
            if slug not in self._markets:
                continue
            if position_only and self.ledger.position(slug) == 0:
                continue
            if self.ledger.market_has_unresolved_intent(slug):
                # The exact intent-resolution path re-dirties this owner. Do
                # not spend every outer pass rediscovering the same deliberate
                # quarantine while its venue outcome is still unknown.
                self._dirty_market_slugs.discard(slug)
                self._dirty_market_since.pop(slug, None)
                continue
            if slug in self._isolated_unexecutable_slugs():
                now = self._now()
                if self._can_wait_unexecutable(slug, now):
                    # No valid mutation path exists. The owner is still
                    # serviced by complete account snapshots, its private
                    # position subscription and one bounded recovery timer;
                    # planning would only manufacture a recurring failure.
                    self._dirty_market_slugs.discard(slug)
                    self._dirty_market_since.pop(slug, None)
                    self._scheduler_markets_skipped += 1
                    continue
                self._leave_settlement_wait(
                    slug, now, "isolation_preconditions_changed")
                self._leave_inactive_market_wait(
                    slug, now, "isolation_preconditions_changed")
            now = self._now()
            due_at = self._next_decision_at.get(slug)
            if due_at is not None and now < due_at:
                # Preserve the deadline rather than the dirty bit. Any newer
                # input is still represented by ``_market_last_input_at`` for
                # telemetry, while the reviewed market-local backoff remains
                # authoritative for behavior.
                self._dirty_market_slugs.discard(slug)
                self._dirty_market_since.pop(slug, None)
                continue
            transition_proved = (
                priority_dispatch
                and self._position_priority_has_fresh_transition_proof(slug)
            )
            account_proved = (transition_proved or (
                priority_dispatch and self._position_priority_has_fresh_account_proof(slug)))
            if not state_refresh_failed and not account_proved:
                try:
                    await self._refresh_execution_state_for_decision(now)
                except Exception as exc:
                    # Preserve the last observation until its 30-second bound;
                    # once stale, the frame refuses new risk. Do not retry this
                    # account-wide REST proof for every remaining market in the
                    # same sweep; the outer loop will give it another chance.
                    state_refresh_failed = True
                    if read_backoff.is_backpressure(exc):
                        # A failed account proof permits no decision, but a
                        # shared read outage is not a defect in every market.
                        # Yield before per-market recovery can cancel reducers
                        # or retire owners. Ordinary recovery/stop stays active.
                        return None
            # REST can discover a fill while proving a different, flat owner.
            # Re-enter the SAME guarded path for that position before another
            # entry decision. A changed quantity/order state can wake an owner
            # already visited (notably cancel-confirm -> exact reducer).
            # Bound extra visits so changing inventory cannot starve the slate.
            priorities = self._position_priority_tokens()
            if (priority_dispatch or position_only) and slug not in priorities:
                # Inventory can flatten during the proof above. A position
                # interruption must not turn into an extra flat entry pass.
                self._mark_market_dirty(slug, self._now())
                continue
            if slug not in priorities:
                # A bounded position budget must not strand an uncovered or
                # newly due owner behind the remaining flat slate. Yield to a
                # fresh outer pass instead of increasing mutation/visit limits.
                exhausted = next((owner for owner, token in priorities.items()
                                  if priority_seen.get(owner) != token
                                  and priority_visits.get(owner, 0)
                                  >= self._position_priority_visit_limit(owner)
                                  and self._position_priority_must_yield(owner)), None)
                if exhausted is not None:
                    self._mark_market_dirty(exhausted, self._now())
                    pending_slugs = [owner for owner in pending_slugs
                                     if owner in priorities and owner != exhausted]
                    continue
            # A proof for a held owner can discover another owner's fill too.
            # Urgent inventory must precede a covered owner's optional reprice,
            # not just flat entries. Stable sorting preserves peer order; only
            # changed tokens get extra dispatches under the existing budgets.
            ranks = {owner: self._position_priority_rank(owner) for owner in priorities}
            current_rank = ranks.get(slug, 3)
            isolated = self._isolated_unexecutable_slugs() if priorities else frozenset()
            at = self._now()
            urgent = next((owner for owner in sorted(priorities, key=ranks.get)
                           if ranks[owner] < current_rank
                           and priority_seen.get(owner) != priorities[owner]
                           and priority_visits.get(owner, 0)
                           < self._position_priority_visit_limit(owner)
                           and not self.ledger.market_has_unresolved_intent(owner)
                           and owner not in isolated
                           and (self._next_decision_at.get(owner) is None
                                or at >= self._next_decision_at[owner])), None)
            if urgent is not None:
                priority_seen[urgent] = priorities[urgent]
                priority_visits[urgent] = priority_visits.get(urgent, 0) + 1
                priority_dispatches.add(urgent)
                if priority_dispatch:
                    # Preserve exit-only dispatch if this owner flattens while
                    # the higher-priority owner is being proved/serviced.
                    priority_dispatches.add(slug)
                pending_slugs = [urgent, slug, *(
                    owner for owner in pending_slugs if owner != urgent)]
                continue
            if slug in priorities:
                priority_seen[slug] = priorities[slug]
                if not priority_dispatch:
                    # A naturally reached held owner consumes a visit too;
                    # inter-position preemption cannot increase its budget.
                    priority_visits[slug] = priority_visits.get(slug, 0) + 1
            try:
                # Account reconciliation can take seconds. Its pre-await time
                # must not make an expired equity mark appear fresh.
                if not transition_proved and not equity_refresh_failed:
                    await self._refresh_equity_for_decision(self._now())
            except Exception:
                # Preserve the last observation until its 15-second bound;
                # once stale, `_frame` removes entry eligibility fail-closed.
                # A failed account-wide bracket is not repaired by hammering
                # it once per remaining flat market in this same sweep.
                equity_refresh_failed = True
            if position_only and self.ledger.position(slug) == 0:
                continue
            # Fetch this owner's quiet book AFTER account/equity proof, not
            # at the front of a full-slate service pass. Thirty-two sequential
            # reads can age the first book past five seconds before planning,
            # forcing a second read in book_stale recovery. Contract timers,
            # economics and all failure/retirement gates remain unchanged.
            try:
                if service_books:
                    await self._refresh_event_guard()
                    if self._stop_requested:
                        return self._stop_requested
                    await self._service_market_book(slug, self._now())
            except RuntimeInvariantViolation as invariant:
                return await self._handle_strategy_plan_failure(invariant)
            except MarketStreamUnavailable as exc:
                self._request_market_stream_rebind(exc)
                return None
            except Exception as exc:
                await self._report_runtime_error(f"market_service:{slug}", exc)
                stop = await self._retire_market(
                    slug, f"service_failed_{type(exc).__name__}")
                if stop:
                    return stop
                continue
            if self._stop_requested:
                return self._stop_requested
            if position_only and self.ledger.position(slug) == 0:
                continue
            previous_action_generation = self._market_action_generation.get(
                slug, 0)
            outcome, delay = await self._decide_for_market(slug, self._now())
            finished_at = self._now()
            action_at = (
                self._last_action_at.get(slug)
                if self._market_action_generation.get(slug, 0)
                != previous_action_generation else None)
            self._scheduler_markets_evaluated += 1
            self._record_scheduler_decision(
                slug, finished_at=finished_at, action_at=action_at)
            self._dirty_market_slugs.discard(slug)
            if action_at is not None:
                # A create response can carry an embedded OPEN/FILL and make
                # its PLACE intent terminal before any later private event.
                # The mutation itself is therefore a decision input: revisit
                # this exact owner so the second side, reducer or next cancel
                # is not forced to wait for an unrelated book/timer event.
                self._dirty_market_slugs.add(slug)
                self._market_dirty_generation[slug] = (
                    self._market_dirty_generation.get(slug, 0) + 1)
                self._dirty_market_since[slug] = action_at
                self._market_last_input_at[slug] = action_at
            # A REST IOC may be submitted and become terminal between account
            # snapshots. Its position and working-order token can therefore
            # look unchanged unless the reconciler's bounded exit state is
            # included by the account-source subclass. Re-read that token
            # after the decision and immediately re-enter this same guarded
            # path when it changed. Unknown intents are still rejected at the
            # top of the loop, and the per-owner limit prevents starvation.
            priority_after = self._position_priority_tokens()
            continued_position = False
            if (slug in priority_seen and slug in priority_after
                    and priority_seen[slug] != priority_after[slug]
                    and self._position_priority_replan_immediately(
                        slug, priority_seen[slug], priority_after[slug])
                    and priority_visits.get(slug, 0)
                    < self._position_priority_visit_limit(slug)):
                priority_seen[slug] = priority_after[slug]
                priority_visits[slug] = priority_visits.get(slug, 0) + 1
                priority_dispatches.add(slug)
                pending_slugs = [slug, *(
                    owner for owner in pending_slugs if owner != slug)]
                continued_position = True
            # A normal full sweep can reach a market whose REST terminal proof
            # queued priority earlier in this same loop. Its replan has now
            # happened, so do not service it again next iteration.
            self._priority_replan_slugs.discard(slug)
            if outcome:
                return outcome
            if (not continued_position and slug in priority_after
                    and priority_visits.get(slug, 0)
                    >= self._position_priority_visit_limit(slug)
                    and self._position_priority_must_yield(slug)):
                self._mark_market_dirty(slug, self._now())
                # Preserve service for other held owners; only flat work is
                # deferred. One busy position must not starve another exit.
                pending_slugs = [owner for owner in pending_slugs
                                 if owner in priority_after and owner != slug]
            if delay > 0:
                self._next_decision_at[slug] = (
                    self._now() + timedelta(seconds=delay))
                if (slug in priority_after
                        and self._position_priority_must_yield(slug)):
                    # A locally refused reducer changed no position/order
                    # token. It still needs another priority visit once its
                    # backoff expires, not after the entire flat slate. Keep
                    # the same visit budget and every proof/admission gate.
                    priority_seen.pop(slug, None)
            elif slug in priority_after:
                # REST position service must not inherit a flat quote's reward
                # dwell timer. Explicit failure backoff above remains binding.
                self._next_decision_at.pop(slug, None)
            else:
                resting_sides = frozenset(
                    order.side for order in self._working_orders(slug))
                eligibility_at = (
                    self.reconciler.next_flat_decision_deadline(
                        slug, resting_sides=resting_sides))
                if (eligibility_at is not None
                        and eligibility_at > self._now()):
                    self._next_decision_at[slug] = eligibility_at
                else:
                    self._next_decision_at.pop(slug, None)
        return None

    async def _service_bound_markets(
        self, slugs: tuple[str, ...] | None = None,
        *, refresh_books: bool = True,
    ) -> str:
        """Visit every bound market, surviving any one of them failing.

        Serviced, not merely selected: a market holding risk must keep being
        visited after selection moves on, or its exit is never planned.

        Returns a stop reason, or "" to keep running. A market that raises is
        reported and retired rather than allowed to end the session -- the same
        treatment `_plan_and_apply` already gives a planning failure. That
        guard sat only around `reconciler.plan`, so anything raising in the
        servicing steps AROUND it escaped. `_evaluate_rewards` did exactly that
        on 2026-08-16T19:01Z with a book whose bids had all been pulled: the
        runner died, shutdown parked a live position rather than cross at a
        loss, and being unable to prove flat the supervisor refused to restart.
        79 minutes down, and nineteen healthy markets taken along with the one
        that broke.

        Absorbing a local defect is not the same as hiding a systemic one.
        Retirement is bounded at ten rounds per market, and a full slate of
        retirements still stops the session, so a fault in every market still
        surfaces as one.
        """

        serviced_slugs = self.managed_slugs() if slugs is None else slugs
        if self._streams_need_rebind or self._stop_requested:
            return ""
        if slugs is None and self._post_flat_top_up_can_preempt(self._now()):
            return ""
        try:
            await self._refresh_event_guard()
        except MarketStreamUnavailable as exc:
            self._request_market_stream_rebind(exc)
            return ""
        if self._stop_requested:
            return ""
        await self._refresh_due_contract_batches(
            self._now(), serviced_slugs)
        for slug in serviced_slugs:
            if slug not in self._markets:
                continue
            try:
                if refresh_books:
                    await self._service_market_state(slug, self._now())
                else:
                    await self._service_market_state(
                        slug, self._now(), refresh_book=False)
            except RuntimeInvariantViolation as invariant:
                # About the process, not this market. Stays terminal.
                return await self._handle_strategy_plan_failure(invariant)
            except MarketStreamUnavailable as exc:
                self._request_market_stream_rebind(exc)
                return ""
            except Exception as exc:
                await self._report_runtime_error(f"market_service:{slug}", exc)
                stop = await self._retire_market(
                    slug, f"service_failed_{type(exc).__name__}")
                if stop:
                    return stop
            if (slugs is None
                    and self._post_flat_top_up_can_preempt(self._now())):
                return ""
        return ""

    async def _service_market_state(
        self, slug: str, now: datetime, *, refresh_book: bool = True,
    ) -> None:
        """Bring one market's contracts, book and economics up to date.

        Each market carries its own REST cadence. A shared schedule would let a
        single quiet book consume the governor on behalf of all of them, and a
        single failing market would reset everyone else's timers.
        """

        state = self.state_for(slug)
        if (state.next_contract_refresh is None
                or now >= state.next_contract_refresh):
            try:
                # A barred market never quotes, so the gates that guard
                # quoting have nothing to protect here -- applying them would
                # only raise `no longer pregame` on every refresh for the whole
                # time we are exiting, once a second, for hours.
                # Exit-only markets load with the gates relaxed: those gates
                # exist to stop us OPENING risk, and a market we are trying to
                # leave has already opened it. Without this an exit-only market
                # whose game has started raises `no longer pregame` on every
                # refresh and never gets a fresh book to price against.
                await self._refresh_contracts(
                    now,
                    slug,
                    for_exit=self._is_barred_from_quoting(slug),
                    accept_bound_lifecycle_end=True,
                )
                self._runtime_error_counts.pop(
                    f"market_incentive_refresh:{slug}", None)
            except _ConfirmedMarketNotOpen:
                state.incentive = None
                state.reward_eligible = frozenset()
                self._runtime_error_counts.pop(
                    f"market_incentive_refresh:{slug}", None)
            except Exception as exc:
                # Only this market stops quoting. The others are unaffected.
                state.incentive = None
                state.reward_eligible = frozenset()
                await self._report_runtime_error(
                    f"market_incentive_refresh:{slug}", exc)
            state.next_contract_refresh = self._now() + timedelta(
                seconds=(
                    CONTRACT_REFRESH_SECONDS
                    if f"market_incentive_refresh:{slug}"
                    not in self._runtime_error_counts
                    else self._contract_refresh_delay(slug)))
        await self._service_market_book(
            slug, self._now(), refresh_book=refresh_book)

    async def _service_market_book(
        self, slug: str, now: datetime, *, refresh_book: bool = True,
    ) -> None:
        """Service book/economics without repeating bulk contract requests."""

        state = self.state_for(slug)
        if (slug in self._inactive_market_wait_slugs
                or (self._market_metadata_proves_not_open(state.market)
                    and self._owner_is_flat_and_clear(slug))):
            # Contract metadata, not book depth, is the missing authority. A
            # flat clear owner is already queued for replacement; an exact
            # positioned owner is isolated above. The next scheduled contract
            # refresh is the only useful recovery read for either state.
            return
        if not refresh_book and state.economically_ineligible_since is None:
            # Bulk lifecycle service still runs before stream draining,
            # selection and risk gates. Only the book read is deferred to the
            # guarded per-owner decision; no receipt timestamp is renewed.
            # An economic replacement dwell is already a book consumer before
            # the decision sweep (it can cross its deadline during service).
            # Retain its normal refresh so an old price refusal cannot release
            # a newly viable market.
            self._release_retired_if_flat(slug)
            self._evaluate_rewards(self._now(), slug)
            return
        # Keep the book last among this market's REST work so metadata latency
        # cannot age the book before the decision that follows.
        try:
            with self._routine_book_scope(slug):
                state.next_book_refresh = await self._refresh_quiet_book_if_due(
                    state.next_book_refresh or now, slug)
            # Only a refresh that actually RAN proves the book is readable. A
            # "not due yet" return proves nothing, and clearing the counter on
            # it stopped consecutive failures from ever accumulating to
            # MAX_RECOVERY_ATTEMPTS -- see _refresh_quiet_book_if_due.
            if self._refreshed_book_this_call:
                self._runtime_error_counts.pop(
                    f"quiet_book_rest_refresh:{slug}", None)
        except MarketStreamUnavailable:
            raise
        except ApiAdmissionDeferred as exc:
            # No book request was sent. Preserve the last receipt/failure
            # evidence; local endpoint backoff is not a broken market and
            # cannot retire an owner. Fresh streamed books still plan normally.
            state.next_book_refresh = self._now() + timedelta(
                seconds=self._api_admission_delay(exc))
        except EmptyBookError as exc:
            if self._enter_settlement_wait(slug, self._now()):
                self._runtime_error_counts.pop(
                    f"quiet_book_rest_refresh:{slug}", None)
                return
            await self._report_runtime_error(
                f"quiet_book_rest_refresh:{slug}", exc)
            state.next_book_refresh = self._now() + timedelta(seconds=3)
            if (self._runtime_error_counts.get(
                    f"quiet_book_rest_refresh:{slug}", 0)
                    >= MAX_RECOVERY_ATTEMPTS):
                stop = await self._retire_market(
                    slug, "unrecoverable_book_EmptyBookError",
                    reset_exit_budget=False)
                if stop:
                    raise RuntimeInvariantViolation(
                        "market_retirement_limit_exceeded", stop)
        except Exception as exc:
            state.next_book_refresh = self._now() + timedelta(seconds=3)
            # A book that will not parse is not a book we can ever quote, and
            # retrying it every three seconds holds a market slot while burning
            # REST budget. `book is not two-sided` was the most common error in
            # the log at 2,121 occurrences across 21 of 77 markets, with two
            # markets retried about 1,100 times each. Retire it on the same
            # bound the strategy-state recoveries use.
            if isinstance(exc, AdapterError) and exc.status_code == 429:
                self._log_book_rate_limit(slug)
            else:
                await self._report_runtime_error(
                    f"quiet_book_rest_refresh:{slug}", exc)
                if (self._runtime_error_counts.get(
                        f"quiet_book_rest_refresh:{slug}", 0)
                        >= MAX_RECOVERY_ATTEMPTS):
                    stop = await self._retire_market(
                        slug, f"unrecoverable_book_{type(exc).__name__}",
                        reset_exit_budget=False)
                    if stop:
                        raise RuntimeInvariantViolation(
                            "market_retirement_limit_exceeded", stop)
        # Check before evaluating: a retired market that reached flat should
        # stop being treated as retired in the same pass that would otherwise
        # keep blocking its slot.
        self._release_retired_if_flat(slug)
        self._evaluate_rewards(self._now(), slug)

    async def _handle_action_failure(
        self, exc: Exception, slug: str, *, risk_reducing: bool = False,
    ) -> str | None:
        """Retry ambiguous remote outcomes; terminate local invariant failures."""

        if isinstance(exc, ApiAdmissionDeferred):
            # No request crossed the network boundary.  The existing desired
            # state remains authoritative. Apply the same account-wide entry
            # admission circuit before later plans reach durable intent
            # creation; otherwise every market pass would create another exact
            # FAILED row during one shared cooldown. Reporting or quarantining
            # here would turn harmless backpressure into order churn.
            # A private observation that arrives in the narrow interval
            # between planning and the final no-I/O create fence is local
            # state synchronization, not shared API backpressure.  The final
            # fence still rejects that create, but it must not freeze every
            # other market for one second.  The next owner pass drains and
            # classifies the event before planning again.
            local_only = {
                "LOCAL_API_INTERRUPTED",
                "LOCAL_HYBRID_OBSERVATION_PENDING",
            }
            if exc.error_code not in local_only:
                delay = self._api_admission_delay(exc)
                deferred_until = self._now() + timedelta(
                    seconds=delay)
                if (self._entry_mutation_blocked_until is None
                        or self._entry_mutation_blocked_until < deferred_until):
                    self._entry_mutation_blocked_until = deferred_until
            return None
        if isinstance(exc, OrderMutationRejected):
            await self._report_runtime_error(
                "mutation_access_denied", exc, include_traceback=False)
            self._entry_mutation_blocked_until = (
                self._now()
                + timedelta(
                    seconds=self.ENTRY_MUTATION_ACCESS_RECHECK_SECONDS))
            if risk_reducing:
                self._risk_mutation_blocked_until = (
                    self._now()
                    + timedelta(
                        seconds=self.RISK_MUTATION_ACCESS_RECHECK_SECONDS))
            if not self._mutation_access_denial_announced:
                self._mutation_access_denial_announced = True
                await self.notifier.send_async(
                    "⛔ Liquidity Bot mutation access was rejected by "
                    "Polymarket. New entries pause for one minute, then one "
                    "entry will probe recovery. Cancels and exits remain "
                    "available unless their own request is rejected. "
                    "Existing venue state remains tracked; no cancellation "
                    "or flatness was inferred.",
                    critical=True,
                    channel="errors",
                )
            # The request is positively FAILED, not ambiguous. Quarantining
            # would cancel healthy resting quotes and convert one account-wide
            # venue denial into twenty market retirements.
            return None
        await self._report_runtime_error(f"action_driver:{slug}", exc)
        if isinstance(exc, RuntimeInvariantViolation):
            # About the process, not this market. Terminal.
            return self._terminal_exception_reason("action_driver", exc)
        if not isinstance(exc, (AdapterError, OrderOutcomeUncertain)):
            # Some other exception driving this market's action. Retire the
            # market and keep the rest quoting rather than ending the session
            # over one market's mutation. Bounded like every retirement.
            return await self._retire_market(
                slug, f"action_failed_{type(exc).__name__}")
        # Losing a race against our own placement is not a failure worth
        # tearing the book down for. The order we just sent is not yet visible
        # as working, so the next pass tries to place again, place() refuses
        # because that market has an unresolved intent, and quarantining in
        # response cancels the very order that caused the refusal. Measured
        # 2026-08-14: PLACE 2597 against CANCEL 2596, each cancel about 1.5
        # seconds after its place, with no planner ever asking for one.
        #
        # Nothing is unsafe here -- the mutation is durably recorded and will
        # reconcile on its own. Waiting is the whole remedy.
        if "unresolved order intent" in str(exc):
            return None
        try:
            await self._quarantine_working_orders(slug)
        except RuntimeInvariantViolation as invariant:
            await self._report_runtime_error(
                "action_driver_terminal", invariant)
            return f"critical:{invariant.code}"
        except Exception as recovery_exc:
            await self._report_runtime_error(
                f"action_driver_recovery:{slug}", recovery_exc)
        return None
