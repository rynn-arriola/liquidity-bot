"""Operator stop, HARD latch, order quarantine and shutdown/flatten proof for the commissioning runner.

Moved verbatim from ``live_commissioning.CommissioningRunner`` (pure
relocation, no behavior change). Methods run on the runner instance via
inheritance, so every ``self`` attribute and override resolves as before.
"""

from __future__ import annotations

from .api_diagnostics import api_workflow
import traceback
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from .errors import AdapterError, ApiAdmissionDeferred, MarketStreamUnavailable
from .reward_calendar import reward_day_bounds, risk_day
from .reconciler import ActionKind, PlanAction
from .shadow_math import cross_exit
from .runner_shared import _CancellationEvidencePending, _OrphanPlaceEvidencePending


class ShutdownMixin:

    def request_stop(self, reason: str = "operator_requested") -> None:
        first_request = self._stop_requested is None
        self._stop_requested = str(reason or "operator_requested")
        if first_request:
            # A shared cooldown used to sleep inside a worker thread for as
            # long as 60 seconds. SIGTERM could set this flag but the run loop
            # could not reach shutdown until that sleep ended. Interrupt only
            # waits that already began; shutdown captures a fresh generation
            # and retains full access to cancels, proof reads and IOC exits.
            interrupt = getattr(self.gateway.limiter, "interrupt_waits", None)
            if callable(interrupt):
                interrupt()

    def _hard_stop_active(self) -> bool:
        return (self._hard_stop_hold_day is not None
                or (self._window_risk is not None and self._window_risk.held))

    def _scheduled_maintenance_flatten_required(self) -> bool:
        """Flatten only for a positively identified current window."""

        return (
            self._maintenance_decision.paused
            or self._maintenance_health_required_for is not None
        )

    def _latch_hard_stop(self, now: datetime) -> None:
        """Commit before cancelling/flattening; preserve the breached meter day."""

        day = self._baseline_day or date.fromisoformat(risk_day(now))
        self.ledger.record_hard_stop_hold(day.isoformat(), at=now)
        self._hard_stop_hold_day = day
        self._hard_stop_hold_at = now
        self._hard_stop_hold_notice = "held"
        self._provisional_hard_stop_at = None
        self._provisional_hard_stop_markets.clear()
        self._provisional_hard_stop_notice = None
        self._entry_pause_cancel_requested = True
        for slug in self.active_slugs:
            self._mark_market_dirty(slug, now)
            if slug in self._retired:
                self._retired[slug].next_retry_at = now
        self._publish_active_scope_snapshot(status="ENTRY_PAUSED")
        print(f"[daily-hard-stop] at={now.isoformat()} day={day} "
              "state=held action=cancel_entries_and_flatten", flush=True)

    def _maybe_release_hard_stop(self, now: datetime) -> None:
        """Next ET day is necessary, not sufficient: fresh flat proof first."""

        if self._window_risk is not None:
            self._maybe_release_window_stop(now)
            return
        day = self._hard_stop_hold_day
        today = date.fromisoformat(risk_day(now))
        if (day is None or today <= day or self._baseline_day != today
                or self._nontrading_cash_day != today
                or self._nontrading_cash is None or self.risk_guard is None
                or not self.risk_guard.can_add_new_risk(now=now)
                or self._streams_need_rebind
                or not self.gate.ready(self.ledger, now=now)):
            return
        reconciled = self.gate.last_reconcile_at
        equity_at = self.risk_guard.equity_observed_at
        if (reconciled is None or equity_at is None
                or self._hard_stop_hold_at is None
                or reconciled < self._hard_stop_hold_at
                or equity_at < reconciled
                or risk_day(reconciled) != today.isoformat()
                or self._equity_snapshot is None
                or any(position.net_qty != 0
                       for position in self._equity_snapshot.positions)
                or self.ledger.working_orders() or self.ledger.absent_orders()
                or self.ledger.unresolved_intents()
                or self.ledger.query("SELECT 1 FROM execution_positions "
                                     "WHERE CAST(net_qty AS REAL)!=0 LIMIT 1")):
            return
        self._assert_runtime_scope()
        self.ledger.release_hard_stop_hold(day.isoformat(), at=now)
        self._hard_stop_hold_day = None
        self._hard_stop_hold_at = None
        self._hard_stop_hold_notice = "released"
        for slug in self.active_slugs:
            self._mark_market_dirty(slug, now)
        self._publish_active_scope_snapshot(
            status="ENTRY_PAUSED" if self._entry_pause_active() else "RUNNING")
        print(f"[daily-hard-stop] at={now.isoformat()} day={day} "
              "state=released proof=fresh_next_day_flat_account", flush=True)

    def _stop_reason(self, now: datetime) -> str | None:
        if self._stop_requested:
            return self._stop_requested
        if self.risk_guard is None:
            return "risk_guard_missing"
        if (self._market_fault_pause_until is not None
                and now >= self._market_fault_pause_until
                and not self._streams_need_rebind
                and self.gate.ready(self.ledger, now=now)
                and self.gate.last_reconcile_at is not None
                and self.gate.last_reconcile_at >= self._market_fault_pause_until
                and self.risk_guard.can_add_new_risk(now=now)):
            self._market_fault_pause_until = None
            # Retired owners and their ambiguity/cooldowns remain barred. Only
            # normal discovery may find healthy replacements after this hold.
            self._publish_active_scope_snapshot()
            print(f"[market-fault-recovery] at={now.isoformat()} "
                  "state=released proof=fresh_account_after_cooldown", flush=True)
        self._maybe_release_hard_stop(now)
        open_position_slugs = {
            str(row["market_slug"])
            for row in self.ledger.query(
                "SELECT market_slug FROM execution_positions "
                "WHERE CAST(net_qty AS REAL)!=0")
        }
        hard_hit = (self._window_risk is None
                    and self._hard_stop_hold_day is None
                    and self.risk_guard.hard_stop_reached(now=now))
        if hard_hit and open_position_slugs:
            if self._provisional_hard_stop_at is None:
                day = self._baseline_day or date.fromisoformat(risk_day(now))
                self.ledger.record_provisional_hard_stop(
                    day.isoformat(), at=now, markets=open_position_slugs)
                self._provisional_hard_stop_day = day
                self._provisional_hard_stop_at = now
                self._provisional_hard_stop_markets = set(open_position_slugs)
                self._provisional_hard_stop_notice = "started"
                print(
                    f"[hard-stop-provisional] at={now.isoformat()} "
                    f"markets={','.join(sorted(open_position_slugs))} "
                    "action=block_new_risk_and_wait_for_closed_position",
                    flush=True,
                )
            else:
                # An already-resting entry can fill while its cancellation is
                # in flight. It is exposed to the same marked breach and must
                # join the set whose closure can settle the provisional state.
                if not open_position_slugs <= self._provisional_hard_stop_markets:
                    self.ledger.record_provisional_hard_stop(
                        self._provisional_hard_stop_day.isoformat(),
                        at=self._provisional_hard_stop_at,
                        markets=self._provisional_hard_stop_markets | open_position_slugs)
                    self._provisional_hard_stop_markets.update(open_position_slugs)
        elif hard_hit and self._provisional_hard_stop_at is None:
            # No open inventory can explain this as temporary paper movement.
            self._latch_hard_stop(now)

        if self._provisional_hard_stop_at is not None:
            reconcile_at = self.gate.last_reconcile_at
            equity_at = self.risk_guard.equity_observed_at
            closed: set[str] = set()
            if (reconcile_at is not None and equity_at is not None
                    and equity_at >= reconcile_at >= self._provisional_hard_stop_at):
                for slug in self._provisional_hard_stop_markets:
                    rows = self.ledger.query(
                        "SELECT net_qty,received_at FROM execution_positions "
                        "WHERE market_slug=?",
                        (slug,),
                    )
                    if len(rows) != 1 or Decimal(str(rows[0]["net_qty"])) != 0:
                        continue
                    position_at = datetime.fromisoformat(
                        str(rows[0]["received_at"]))
                    if (position_at.tzinfo is not None
                            and position_at.utcoffset() is not None
                            and reconcile_at >= position_at >= self._provisional_hard_stop_at):
                        closed.add(slug)
            # A recovered mark by itself is not enough: the same open trade can
            # bounce above and below the line. Require a breach-time position
            # to have actually closed, then accept the fresh adjusted balance.
            if hard_hit and closed:
                self._latch_hard_stop(now)
            if not hard_hit and closed:
                self.ledger.clear_provisional_hard_stop()
                self._provisional_hard_stop_at = None
                self._provisional_hard_stop_markets.clear()
                self._provisional_hard_stop_notice = "cleared"
                print(
                    f"[hard-stop-cleared] at={now.isoformat()} "
                    f"closed={','.join(sorted(closed))} "
                    "reason=adjusted_equity_recovered_after_position_close",
                    flush=True,
                )
        today = risk_day(now)
        if (self._execution_fee_observation_day is not None
                and self._execution_fee_observation_day != today):
            self._execution_fee_observation_day = None
        day_start, _ = reward_day_bounds(today)
        daily_rows = self.ledger.query(
            "SELECT f.client_order_id,f.fee FROM execution_fills f "
            "WHERE f.received_at>=? AND f.received_at<=? "
            "AND f.reversed=0 AND f.liquidity='TAKER' "
            "AND EXISTS (SELECT 1 FROM execution_intents i "
            "WHERE i.operation='PLACE' "
            "AND i.client_order_id=f.client_order_id)",
            (day_start.isoformat(), now.astimezone(timezone.utc).isoformat()),
        )
        positive_daily_fees = sum(
            (max(Decimal("0"), Decimal(str(row["fee"])))
             for row in daily_rows),
            Decimal("0"),
        )
        if positive_daily_fees >= self.config.execution_fee_observation_usd:
            if self._execution_fee_observation_day != today:
                self._execution_fee_observation_pending = (
                    today, positive_daily_fees)
                print(
                    f"[execution-fee-observation] at={now.isoformat()} "
                    f"day={today} "
                    f"gross_taker_fees={positive_daily_fees} "
                    f"reference={self.config.execution_fee_observation_usd} "
                    "action=observe_only",
                    flush=True,
                )
            self._execution_fee_observation_day = today
        blockers = self.risk_guard.blockers(now=now)
        # Missing/stale equity is recoverable only by staying alive with new
        # risk disabled. A clock failure or explicit critical condition still
        # enters terminal flatten-and-stop handling.
        transient = {
            "marked account equity is unknown",
            "marked account equity is stale",
        }
        terminal = tuple(item for item in blockers if item not in transient)
        if terminal:
            return "risk_stop:" + ",".join(terminal)
        return None

    async def _announce_provisional_hard_stop(self) -> None:
        """Announce a paper breach once, then its position-close recovery."""

        hold_notice, self._hard_stop_hold_notice = self._hard_stop_hold_notice, None
        if self._window_risk is not None:
            provisional, self._provisional_hard_stop_notice = (
                self._provisional_hard_stop_notice, None)
            if hold_notice == "held":
                await self.notifier.send_async(
                    "⏸️ Six-hour HARD loss reached. Cancelling pending entries "
                    "and exiting positions. Entries remain paused until a later "
                    "ET window and fresh flat-account proof.", critical=True)
            elif hold_notice == "released":
                await self.notifier.send_async(
                    "✅ New six-hour ET window: HARD pause cleared after fresh "
                    "flat-account proof. Trading safety gates still apply.")
            elif provisional == "started":
                await self.notifier.send_async(
                    "⏸️ Open-position loss is provisional. New entries are paused "
                    "while normal exits continue; only confirmed closed losses "
                    "count toward the window stop and order reductions.")
            elif provisional == "cleared":
                await self.notifier.send_async(
                    "✅ Breach-time positions closed. The six-hour budget now "
                    "uses the confirmed result; normal entry gates apply.")
            return
        if hold_notice == "held":
            await self.notifier.send_async(
                "⏸️ HARD loss confirmed. Cancelling pending entries and exiting "
                "all positions with terminal confirmation. Trading stays paused "
                "for the ET day, including across restarts. Next-day entry "
                "requires a fresh flat account and healthy trading gates.",
                critical=True)
        elif hold_notice == "released":
            await self.notifier.send_async(
                "✅ Daily HARD pause cleared for the new ET day after fresh "
                "flat-account proof. Other entry and maintenance gates still apply.")
        notice, self._provisional_hard_stop_notice = (
            self._provisional_hard_stop_notice, None)
        if notice == "started" and not self._provisional_hard_stop_announced:
            self._provisional_hard_stop_announced = True
            await self.notifier.send_async(
                "⚠️ Liquidity Bot marked equity crossed the hard-stop limit "
                "with open inventory. New entries are blocked immediately; "
                "existing positions keep their normal exits. The breach is "
                "provisional until a breach-time position closes and the "
                "adjusted account loss is measured again.",
                critical=True,
            )
        elif notice == "cleared":
            self._provisional_hard_stop_announced = False
            await self.notifier.send_async(
                "✅ Liquidity Bot cleared the provisional hard stop after a "
                "position closed. The fresh adjusted account loss was below "
                "the hard-stop limit, so quoting may resume.")

    async def _quarantine_working_orders(
        self, slug: str | None = None, *, require_terminal: bool = True,
        drain_private_events: bool = False,
        account_state_already_reconciled: bool = False,
        preempt_positions: bool = False,
    ) -> str | None:
        """Cancel known quotes, optionally requiring terminal proof to return.

        ``slug`` limits the quarantine to one market. A per-market fault --
        this market's execution state is unknown, this market's action failed
        -- says nothing about the other seven, whose quotes are resting
        legitimately and earning. Cancelling them destroys the accrual that
        reward pays for and buys no safety. Observed 2026-08-15T01:42Z: one
        market reporting `execution_state_unknown` cancelled every other
        market's quote within seconds of placement, on repeat.

        The global form (``slug=None``) is still correct for faults that really
        are account-wide: a private-stream reconnect, and rotation, which is
        about to rebind every market anyway. REST-backed shared public-feed
        recovery is the narrow exception: after a fresh complete account proof
        it may preserve one exact reducer while retiring risk-building orders.
        """

        # Name the caller. This is one of only two paths that cancel an order,
        # the planner never asked for a cancel in the live logs, and the live
        # ratio was PLACE 2637 against CANCEL 2636 -- so something reaches here
        # constantly and the logs could not say what.
        import traceback as _tb
        caller = "?"
        for frame in reversed(_tb.extract_stack()[:-1]):
            if frame.name not in {"_quarantine_working_orders"}:
                caller = f"{frame.name}:{frame.lineno}"
                break
        print(
            f"[quarantine] at={self._now().isoformat()} called_by={caller}",
            flush=True,
        )
        # Only the recovery owner that has suspended the normal next_event
        # consumer may drain here; otherwise a task-held older event could
        # be applied after a newer queued event.
        if drain_private_events:
            await self._drain_private_events()
        now = self._now()
        # A single-market repair must not wait on an unreadable order owned by
        # another market. Private-stream repair uses the empty set: reconnect
        # account visibility, then let each ambiguous market's normal gate
        # contain it. The global terminal form remains strict for every order.
        terminal_order_markets = (
            None if slug is None and require_terminal
            else frozenset({slug}) if require_terminal and slug is not None
            else frozenset()
        )
        # The first pass is observational: tolerate only positively-attributed
        # read failures long enough to apply the complete account snapshots.
        # Positive OPEN evidence can then license a cancel even when GET-by-id
        # is lagging. The second pass below enforces the requested terminal
        # postcondition.
        if not account_state_already_reconciled:
            await self._rest_reconcile(
                now, strict_order_markets=frozenset())
        self._assert_runtime_scope()

        async def service_positions() -> str | None:
            if not preempt_positions:
                return None
            stop = await self._service_bound_recovery_positions()
            if stop:
                return stop
            if self._streams_need_rebind:
                raise (self._pending_market_stream_error
                       or MarketStreamUnavailable(
                           "public stream failed during position-priority recovery"))
            return None

        stop = await service_positions()
        if isinstance(stop, str) and stop:
            return stop
        # Retire quotes in every market that could hold them, not just the
        # primary. Resuming with one market's order still working would let it
        # fill unobserved while the others rebind.
        # Nothing bound and nothing in the ledger means nothing to retire.
        slugs = (slug,) if slug is not None else self._shutdown_slugs()
        if preempt_positions:
            # Validate/cancel position-owned orders before flat-market quotes.
            # If a reducer became oversized after a partial fill, leaving it
            # behind a long flat-order slate could reverse the position.
            slugs = tuple(sorted(
                slugs,
                key=lambda market: self.ledger.position(market) == 0,
            ))
        for market in slugs:
            stop = await service_positions()
            if isinstance(stop, str) and stop:
                return stop
            if self.ledger.market_has_unresolved_intent(market):
                # The intent and UNKNOWN_ABSENT row identify exactly which
                # market is ambiguous. After a fresh private bind, the normal
                # per-market gate keeps this owner dormant while other markets
                # resume. Keep walking so one ambiguous owner cannot prevent
                # cancellation of positively-known orders in later markets.
                # Reissuing a cancel without positive OPEN evidence is
                # forbidden; see `_retry_uncertain_cancels`.
                continue
            if self.ledger.working_orders(market):
                if (preempt_positions
                        and self._preserve_position_orders_during_bound_recovery(
                            market)):
                    continue
                await self.kill_switch.initiate(market, now=self._now())
                stop = await service_positions()
                if isinstance(stop, str) and stop:
                    return stop
        if drain_private_events:
            await self._drain_private_events()
        stop = await service_positions()
        if isinstance(stop, str) and stop:
            return stop
        await self._rest_reconcile(
            self._now(), strict_order_markets=terminal_order_markets)
        self._assert_runtime_scope()
        stop = await service_positions()
        if isinstance(stop, str) and stop:
            return stop
        if not require_terminal:
            return None
        self._assert_quarantine_terminal(slugs)
        return None

    def _assert_quarantine_terminal(self, slugs: tuple[str, ...]) -> None:
        """Positive terminal proof, shared by global and scoped recovery."""
        for market in slugs:
            unresolved = self.ledger.unresolved_intents(market)
            if unresolved and all(
                    str(row["operation"]).upper() == "PLACE"
                    and str(row["state"]).upper() == "UNCERTAIN"
                    and not str(row["exchange_order_id"] or "").strip()
                    for row in unresolved):
                raise _OrphanPlaceEvidencePending(
                    "runtime recovery is waiting for repeated complete account "
                    f"proof for a no-id PLACE in {market}")
            if unresolved:
                operation = str(unresolved[0]["operation"]).upper()
                if all(
                        str(row["operation"]).upper() == "CANCEL"
                        for row in unresolved):
                    raise _CancellationEvidencePending(
                        "runtime recovery is waiting for terminal cancel "
                        f"proof in {market}")
                raise AdapterError(
                    "runtime recovery "
                    f"{operation.lower()} is not yet reconciled in {market}")
            if self.ledger.working_orders(market):
                raise AdapterError(
                    f"runtime recovery order is still working in {market}")
        return None

    def _position_near_its_flatten_deadline(self, now: datetime) -> bool:
        """True when held inventory is close enough to kickoff that a scan hurts.

        The top-up runs in exactly the state full rotation refuses to run in --
        `_safe_to_scan()` False, which includes holding a position. The
        rotation blockers are not only about quarantining orders: they also
        keep a long blocking scan away from live inventory. A selection scan
        measured 25-26 seconds, and for its duration `_decide_for_market` runs
        for no market, so `_inventory_plan` cannot issue the pre-kickoff
        flatten. Spending any of the 300-second window that way risks the one
        outcome the whole exit design exists to prevent: settlement.

        Unknown game time counts as near. A scan is deferrable; a missed
        flatten is not.
        """

        margin = self.reconciler.policy.force_exit_before_start_seconds * 2
        for slug in self.active_slugs:
            if self.ledger.position(slug) == 0:
                continue
            if slug in self._isolated_unexecutable_slugs():
                # There is no legal mutation to miss. The top-up path keeps
                # the old streams open through discovery, then performs its
                # normal complete account proof and fresh-stream handoff. A
                # later book/open status removes this exception before exit
                # planning.
                continue
            state = self._markets.get(slug)
            market = getattr(state, "market", None)
            if market is not None and self.config.strategy.is_live(market, now):
                # BC-2026-10-03-A: a live game has no kickoff; its deadline is
                # the live inventory clock. Near = within 45 s of the forced
                # exit (a scan blocks decisions for ~25 s), or clock unknown.
                basis = self.ledger.inventory_basis(slug)
                if basis is None or basis.opened_at is None:
                    return True
                age = (now - basis.opened_at).total_seconds()
                if age >= self.config.strategy.live.max_inventory_age_seconds - 45:
                    return True
                continue
            start = getattr(market, "game_start", None)
            if start is None:
                return True
            if (start - now).total_seconds() <= margin:
                return True
        return False

    def _maintenance_stop_reason(self) -> str | None:
        """Read stop state safely even if preflight has no risk guard yet."""

        if self._stop_requested:
            return self._stop_requested
        if self.risk_guard is None:
            return None
        return self._stop_reason(self._now())

    async def _quarantine_strategy_orders(self, slug: str) -> str | None:
        return await self._quarantine_working_orders(slug)

    def _shutdown_slugs(self) -> tuple[str, ...]:
        """Every market that could still hold risk, not just the active set.

        A market dropped by rotation can still own a working order, a nonzero
        position or an unresolved intent. Shutdown must retire all of them, so
        this unions the active set with everything the ledger still shows.
        """

        slugs = set(self.active_slugs)
        slugs.update(
            str(row["market_slug"])
            for row in self.ledger.working_orders())
        slugs.update(str(row["market_slug"]) for row in self.ledger.absent_orders())
        slugs.update(
            str(row["market_slug"])
            for row in self.ledger.unresolved_intents())
        slugs.update(
            str(row["market_slug"]) for row in self.ledger.query(
                "SELECT market_slug FROM execution_positions "
                "WHERE CAST(net_qty AS REAL)!=0"))
        slugs.discard("")
        return tuple(sorted(slugs))

    @api_workflow("shutdown")
    async def _shutdown(self, reason: str) -> None:
        await self.notifier.send_async(
            f"⚠️ Liquidity Bot LIVE shutdown started: {reason}. "
            "New risk is disabled; reconciling to flat.",
            critical=(reason == "EMERGENCY_EQUITY_HARD_STOP"
                      or reason.startswith("critical:")
                      or reason.startswith("risk_stop:")))
        shutdown_failures = 0
        exit_budget_announced = False
        slug = self.market_slug
        started_at = self._now()
        deadline = started_at + timedelta(
            seconds=self.config.max_shutdown_seconds)
        # The start notification is the first alert in the cadence. Without
        # this anchor, a quiet unresolved/working state would send an immediate
        # duplicate and then go silent until the deadline.
        last_alert_at: datetime | None = started_at
        # Shutdown keeps a flatten budget independent of the quoting loop. It
        # prices a freshly read book against the conservative recorded basis,
        # so an exit the runtime could not complete may still complete here.
        # Inheriting an exhausted runtime counter meant the terminal path
        # never attempted a flatten at all and inventory reached settlement.
        exit_attempts: dict[str, int] = {}

        async def alert(message: str, *, force: bool = False) -> bool:
            """Repeat a stuck-shutdown alert instead of latching it once.

            A position that needs manual intervention must keep announcing
            itself; a single message is too easy to miss overnight.
            """

            nonlocal last_alert_at
            moment = self._now()
            if (not force and last_alert_at is not None
                    and (moment - last_alert_at).total_seconds()
                    < self.config.shutdown_alert_interval_seconds):
                return False
            last_alert_at = moment
            await self.notifier.send_async(message, critical=True)
            return True

        while True:
            delay = 1.0
            now = self._now()
            try:
                # Stream tasks are paused while shutdown owns the loop, but the
                # connected private socket still buffers terminal cancel/fill
                # events. Apply those before falling back to REST reads.
                await self._drain_private_events()
                await self._rest_reconcile(now)
                await self._refresh_event_guard()
                if (self._isolated_unexecutable_slugs()
                        and self._only_settlement_wait_positions_remain(
                            self._now())):
                    # All mutable state is gone and the remaining positions
                    # have no venue price. Retrying the same empty book for 15
                    # minutes cannot improve safety; stop promptly while
                    # reporting nonflat truth. A later explicitly authorized
                    # start reconstructs and monitors the same exact owners.
                    waiting = sorted(self._isolated_unexecutable_slugs())
                    self._shutdown_proved_flat = False
                    await self._refresh_balance(now)
                    self._accrue_score(now)
                    await self._send_summary(now)
                    await self.notifier.send_async(
                        f"⚠️ Liquidity Bot LIVE stopped after {reason} with "
                        f"{len(waiting)} exact position(s) quarantined for "
                        "market reopening, book recovery or settlement: "
                        f"{', '.join(waiting)}. The account is not flat; no "
                        "order remains and no position was ignored.",
                        critical=True)
                    return
                # Every market that ever held risk must be retired, not just
                # the primary. A slug the session has already dropped can still
                # own an order or a position, and leaving it behind is exactly
                # how inventory reaches settlement.
                parked: list[str] = []
                # Cancel known working owners before any book/depth read.
                owners = sorted(
                    self._shutdown_slugs(),
                    key=lambda owner: (
                        not bool(self.ledger.working_orders(owner)), owner))
                for market_slug in owners:
                    now = self._now()
                    if now >= deadline:
                        break
                    try:
                        position = self.ledger.position(market_slug)
                        working = self.ledger.working_orders(market_slug)
                        unresolved = self.ledger.market_has_unresolved_intent(
                            market_slug)
                        if position != 0 and not unresolved:
                            # A position keeps its exit working. Shutdown used to
                            # cancel every order and then cross out at whatever the
                            # book showed, with no price cap anywhere on that path
                            # -- so a redeploy liquidated at the market. Measured
                            # 2026-08-15: short 5 at 0.65 with a break-even reducer
                            # resting, cancelled and crossed at 0.96 on a deploy
                            # restart, -$1.55 on a position that was costing
                            # nothing to hold.
                            #
                            # Leaving the exit resting is strictly better: it may
                            # fill at basis while we are down, and the next process
                            # binds the market exit-only through recovery. Orders
                            # that could ADD to the position are still pulled --
                            # nothing may grow a position nobody is watching.
                            # Kickoff is the one deadline parking may not outlive.
                            # A held position that reaches settlement is unbounded
                            # up to the full contract value, which is how maker-lab
                            # lost $443.88 over four days. Inside the flatten
                            # window we still cross out at whatever it costs.
                            state = self.state_for(market_slug)
                            start = (
                                state.market.game_start
                                if state.market is not None else None)
                            near_kickoff = (
                                start is None
                                or (state.market is not None
                                    and not state.market.event_allows_entry(self._now()))
                                or (start - now).total_seconds()
                                <= self.config.force_exit_before_start_seconds)
                            # Host recovery has no following normal trader to
                            # resume a parked exit. Use the existing bounded
                            # cancel-confirm/price-capped flatten path instead.
                            if not near_kickoff and not self.config.recovery_only:
                                reducing = (
                                    "ORDER_SIDE_SELL" if position > 0
                                    else "ORDER_SIDE_BUY")
                                if any(str(order["side"]) != reducing
                                       for order in working):
                                    self._log_shutdown(
                                        market_slug, "cancel_building",
                                        position=position, working=len(working))
                                    await self.kill_switch.initiate(
                                        market_slug, now=now)
                                    continue
                                # Parking only means anything if the exit is
                                # actually there. With nothing resting the position
                                # is not parked, it is stranded -- shutdown reports
                                # non-flat, the supervisor refuses to restart, and
                                # the position sits with no way out. Observed
                                # 2026-08-15 on aec-valorant-ff-ep: +5 held, no
                                # order, bot down. Fall through to the flatten
                                # below instead, which is the pre-existing path for
                                # exactly this state.
                                if working:
                                    self._log_shutdown(
                                        market_slug, "parked", position=position,
                                        working=len(working),
                                        seconds_to_kickoff=(
                                            None if start is None
                                            else round((start - now).total_seconds())))
                                    parked.append(market_slug)
                                    continue
                        if working and not unresolved:
                            self._log_shutdown(
                                market_slug, "cancel_working", position=position,
                                working=len(working))
                            await self.kill_switch.initiate(market_slug, now=now)
                            continue
                        if position == 0 or working or unresolved:
                            # Silence here made a stuck shutdown look exactly like
                            # a finished one. A flat market is genuinely nothing to
                            # report; one still holding is waiting on something and
                            # must say so.
                            if position != 0:
                                self._log_shutdown(
                                    market_slug, "waiting", position=position,
                                    working=len(working), unresolved=unresolved)
                            continue
                        attempts = exit_attempts.get(market_slug, 0)
                        if attempts >= self.config.max_shutdown_exit_attempts:
                            self._log_shutdown(
                                market_slug, "budget_exhausted",
                                position=position, attempts=attempts,
                                budget=self.config.max_shutdown_exit_attempts)
                            await alert(
                                "🚨 Liquidity Bot exhausted its shutdown exit budget. "
                                "Maker quoting remains terminal; manual intervention "
                                f"is required for remaining position {position} in "
                                f"{market_slug}.",
                                force=not exit_budget_announced)
                            exit_budget_announced = True
                            delay = 30.0
                            continue
                        await self._refresh_book(now, market_slug)
                        state = self.state_for(market_slug)
                        if state.book is None or state.market is None:
                            raise AdapterError("shutdown book is unavailable")
                        basis = self.ledger.inventory_basis(market_slug)
                        entry_side = "bid" if position > 0 else "offer"
                        conservative_entry = (
                            float(basis.average_entry_price) if basis is not None
                            else .99 if position > 0 else .01)
                        exit_result = cross_exit(
                            state.book, entry_side=entry_side,
                            entry_price=conservative_entry,
                            qty=float(abs(position)), depth_haircut=.5)
                        if (not exit_result.complete
                                or exit_result.worst_price is None):
                            # This raise is why a shutdown could burn its whole
                            # budget without ever sending an order. Say so before
                            # raising, or the alert reports a spent budget with no
                            # trace of what refused.
                            self._log_shutdown(
                                market_slug, "depth_refused", position=position,
                                entry_side=entry_side, haircut=0.5,
                                complete=exit_result.complete)
                            raise AdapterError(
                                "insufficient stressed depth to flatten exact "
                                "position")
                        side = "offer" if position > 0 else "bid"
                        action = PlanAction(
                            ActionKind.PLACE_IOC, market_slug,
                            "commissioning_shutdown_flatten", side=side,
                            price=exit_result.worst_price, qty=abs(position))
                        if self.driver is None:
                            raise RuntimeError("action driver is unavailable")
                        if self._now() >= deadline:
                            break
                        # Count before mutation because a transport failure
                        # after send may still have reached the venue.
                        attempts += 1
                        exit_attempts[market_slug] = attempts
                        # Logged before the send, for the same reason the counter
                        # increments before it: a transport failure after the send
                        # may still have reached the venue, and a record written
                        # only on success would omit exactly those cases.
                        self._log_shutdown(
                            market_slug, "flatten", position=position, side=side,
                            price=exit_result.worst_price, qty=abs(position),
                            attempt=attempts,
                            budget=self.config.max_shutdown_exit_attempts)
                        try:
                            await self.driver.apply_one(
                                [action], market=state.market,
                                position=position, now=now)
                        except ApiAdmissionDeferred:
                            # Positive local NOT-SENT proof is not an IOC
                            # attempt. Keep the shutdown deadline/backoff and
                            # repeat full reconciliation before another try.
                            # All sent/ambiguous outcomes retain their budget.
                            exit_attempts[market_slug] -= 1
                            raise
                        delay = max(delay, min(2 ** max(attempts - 1, 0), 5))
                    except AdapterError as exc:
                        # A scoped read/mutation failure cannot starve another
                        # identified owner's cancel or exit. Ownership and
                        # invariant failures still reach the account handler.
                        self._log_shutdown(
                            market_slug, "owner_failed", error=type(exc).__name__)
                        delay = max(delay, 5.0)
                # The venue is the authority on whether we hold risk. Waiting
                # on local bookkeeping keeps a flat process alive for minutes:
                # risk_state_known() is false during any in-flight cancel, and
                # that is 35% of wall-clock time, so shutdown ground through
                # its budget with nothing open. Slow stops are why deploys
                # needed kill -9, and kill -9 is what left the state that
                # produced a recovery run and its alerts on every restart.
                if self._venue_proves_flat() or self._whole_account_safe_to_stop():
                    await self._refresh_balance(now)
                    self._accrue_score(now)
                    await self._send_summary(now)
                    await self.notifier.send_async(
                        f"✅ Liquidity Bot LIVE stopped flat after {reason}.")
                    return
                if parked and self._only_parked_positions_remain(parked):
                    # Not flat, but deliberately so. Spinning to the 900s
                    # deadline here would make every deploy a fifteen-minute
                    # stop and end it on a critical alert.
                    await self._refresh_balance(now)
                    self._accrue_score(now)
                    await self._send_summary(now)
                    await self.notifier.send_async(
                        f"✅ Liquidity Bot LIVE stopped after {reason} with "
                        f"{len(parked)} position(s) left resting on their exit: "
                        f"{', '.join(sorted(parked))}. No order can add to them "
                        "and the next process resumes their exit.")
                    return
                shutdown_failures = 0
            except Exception as exc:
                shutdown_failures += 1
                delay = min(2 ** min(shutdown_failures - 1, 5), 30)
                # Recorded unconditionally. The traceback below is gated on
                # `alert`, which is rate-limited by
                # shutdown_alert_interval_seconds -- so inside that window a
                # failing shutdown produced no Discord message AND no local
                # record at all. That combination is why the krovit-sukiho
                # budget exhaustion could not be diagnosed afterwards.
                self._log_shutdown(
                    slug, "failed", error=type(exc).__name__,
                    failures=shutdown_failures)
                if await alert(
                        "🚨 Liquidity Bot cannot yet prove flat during shutdown "
                        f"({type(exc).__name__}). Process remains in recovery."):
                    traceback.print_exc()
            # Checked after the proven-flat return above, so a healthy shutdown
            # always completes. Staying alive forever hid the one state that
            # needs a human, so the window ends in a non-flat exit instead.
            if self._now() >= deadline:
                self._shutdown_proved_flat = False
                await self.notifier.send_async(
                    f"🚨 Liquidity Bot shutdown window expired after {reason} "
                    "without proving flat. Remaining position "
                    f"{self.ledger.position(slug)} in {slug} requires immediate "
                    "operator inspection; the process is exiting non-flat.",
                    critical=True)
                return
            await alert(
                "🚨 Liquidity Bot still cannot prove flat during shutdown. "
                f"Position {self.ledger.position(slug)} in {slug}; maker "
                "quoting remains terminal and reconciliation continues.")
            await self.sleep(delay)
