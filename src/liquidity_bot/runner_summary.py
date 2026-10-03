"""Discord summary composition and delivery for the commissioning runner.

Moved verbatim from ``live_commissioning.CommissioningRunner`` (pure
relocation, no behavior change). Methods run on the runner instance via
inheritance, so every ``self`` attribute and override resolves as before.
Reporting stays isolated from trading decisions.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from decimal import Decimal

from .discord_summary import LiquiditySummary, build_liquidity_summary
from .resting_time import resting_side_hours_by_day
from .reward_calendar import NEW_YORK, reward_day


class SummaryReportingMixin:

    async def _send_summary(self, now: datetime, *, critical: bool = False,
                            problems: tuple[str, ...] = ()) -> None:
        try:
            score_seconds_today = self._flush_daily_score(now)
            self._runtime_error_counts.pop("daily_score_persistence", None)
        except Exception as exc:
            # This metric is observation only. In particular, `_send_summary`
            # also runs inside shutdown, where escaping would be misread as a
            # failure to prove flat. Retain pending score for a later retry,
            # use the best available display value and report the fault on the
            # normal bounded recovery channel.
            score_seconds_today = self._daily_score_fallback(now)
            await self._report_runtime_error("daily_score_persistence", exc)
        today = date.fromisoformat(reward_day(now))
        try:
            resting_hours, _ = await asyncio.to_thread(
                resting_side_hours_by_day, str(self.ledger.path), [today],
                now=now)
            resting_side_hours_today = resting_hours.get(today.isoformat(), 0.0)
            self._runtime_error_counts.pop("resting_time_summary", None)
        except Exception as exc:
            # This is the same read-only ledger calculation used by the
            # seven-day reward table. It must never delay order work on the
            # event loop or turn an observational failure into a bot stop.
            resting_side_hours_today = 0.0
            await self._report_runtime_error("resting_time_summary", exc)
        # The caller's clock predates a potentially long decision sweep and
        # the awaited report query. Balances can refresh during either. Judge
        # freshness at rendering time, not against that obsolete timestamp.
        now = max(now, self._now())
        today = date.fromisoformat(reward_day(now))
        # Whole-account, not the primary market. Reporting only slot zero
        # meant a position on any other slot showed as open_positions 0 and an
        # inventory age of 0 -- the two numbers an operator watches to decide
        # whether to intervene.
        reported_slugs = self._shutdown_slugs() or self.active_slugs
        positions_by_slug = {
            slug: self.ledger.position(slug) for slug in reported_slugs}
        open_positions = sum(
            1 for value in positions_by_slug.values() if value != 0)
        position = positions_by_slug.get(self.market_slug, Decimal("0"))
        oldest_inventory_age = 0.0
        for slug in reported_slugs:
            slug_basis = self.ledger.inventory_basis(slug)
            if slug_basis is None:
                continue
            oldest_inventory_age = max(
                oldest_inventory_age,
                max(0.0, (now - slug_basis.opened_at).total_seconds()))
        working = self.ledger.working_orders()
        unresolved = self.ledger.unresolved_intents()
        unknown = self.ledger.account_unknown_order_count()
        equity_change = Decimal("0")
        if self._equity_snapshot is not None and self.risk_guard is not None:
            if self._window_risk is not None:
                # In window-risk mode this field is realised trading P&L, not
                # the separate daily marked-equity report.
                equity_change = (
                    self.risk_guard.current_equity
                    - self.risk_guard.starting_equity)
            else:
                equity_change = (
                    self._equity_snapshot.marked_equity
                    - self.risk_guard.starting_equity)
        place_count = self.ledger.query(
            "SELECT COUNT(*) n FROM execution_intents WHERE operation='PLACE'")[0]["n"]
        entry_fills, exit_fills = self.ledger.fill_role_counts(
            since=self.started_at or now)
        observed = [self.state_for(slug).book_observed_at
                    for slug in self.active_slugs]
        oldest_book = min((at for at in observed if at is not None),
                          default=None)
        market_age = ((now - oldest_book).total_seconds()
                      if oldest_book is not None else None)
        private_age = ((now - self.gate.last_message_at).total_seconds()
                       if self.gate.last_message_at else None)
        # Counted from resting orders, never from `[action]` log activity: a
        # market that placed its quotes and is resting quietly logs no actions
        # at all, so an activity-based count falls exactly when things are
        # going well. Measured 2026-08-16 -- it read 17 while only 9 markets
        # actually held an order.
        #
        # Reads the ledger directly rather than `_working_orders`, which raises
        # RuntimeInvariantViolation on an order with no owned place intent. The
        # summary is also sent from the shutdown loop, where that exception is
        # caught as a failure to prove flat -- so a reporting metric would have
        # kept a flat process from stopping.
        resting_markets = sum(
            1 for slug in self.active_slugs
            if self.ledger.working_orders(slug))
        idle_reasons = self._idle_market_reasons()
        capital_reason = None
        if self.balance is None or self.risk_guard is None:
            capital_reason = "balance or freshness guard unavailable"
        else:
            balance_age = (now - self.balance.observed_at).total_seconds()
            if balance_age < 0:
                capital_reason = "balance timestamp is in the future"
            elif balance_age > self.risk_guard.max_equity_age_seconds:
                capital_reason = f"balance stale ({balance_age:.0f}s old)"
        if capital_reason is None and (unresolved or unknown or problems):
            capital_reason = (
                "account integrity problem" if problems else
                f"order proof pending ({len(unresolved)} intents, {unknown} unknown)")
        pending_entries = None
        if (self._window_risk is not None and not unknown and not problems
                and self.gate.ready(self.ledger, now=now)):
            flat_orders, reservations = self._flat_entry_orders_and_reservations()
            pending_entries = len(flat_orders) + reservations
        report_snapshot = self._reporting_equity(now)
        payload = build_liquidity_summary(LiquiditySummary(
            mode="LIVE",
            readiness_status="COMMISSIONING_REWARD_ECONOMICS_ENFORCED",
            observed_at=now,
            session_started_at=self.started_at or now,
            active_markets=len(self.active_slugs),
            resting_markets=resting_markets,
            idle_market_reasons=idle_reasons,
            reward_eligible_sides=sum(
                len(self.state_for(slug).reward_eligible)
                for slug in self.active_slugs),
            score_seconds=score_seconds_today,
            resting_side_hours_today=resting_side_hours_today,
            entry_fills=entry_fills,
            exit_samples=exit_fills,
            exit_completion_rate=(
                1.0 if open_positions == 0 and entry_fills
                else min(exit_fills / entry_fills, 1.0) if entry_fills else 0.0),
            open_positions=open_positions,
            working_orders=len(working),
            max_inventory_age_seconds=oldest_inventory_age,
            unresolved_intents=len(unresolved),
            unknown_orders=unknown,
            trading_realized_usd=equity_change,
            open_marked_usd=Decimal("0"),
            hard_stop_loss_usd=self.config.hard_stop_loss_usd,
            pending_entry_count=pending_entries,
            pending_entry_cap=(self._effective_entry_order_cap(now)
                               if self._window_risk is not None else None),
            hard_stop_state=(
                "HARD paused"
                if self._window_risk is not None and self._window_risk.held else
                "Awaiting position closure"
                if self._window_risk is not None and self._window_risk.provisional else
                "Awaiting fresh window proof"
                if self._window_risk is not None and (
                    not self._window_entry_ready(now)
                    or self.risk_guard is None
                    or not self.risk_guard.can_add_new_risk(now=now)) else
                "Other entry pause" if self._window_risk is not None and self._entry_pause_active() else
                "Window budget available" if self._window_risk is not None else None),
            hard_stop_window=(
                self._window_risk.policy.bounds(now)[0].astimezone(NEW_YORK).strftime('%H:%M')
                + '–' + self._window_risk.policy.bounds(now)[1].astimezone(NEW_YORK).strftime('%H:%M')
                + ' ET · confirmed' if self._window_risk is not None else None),
            # Supply, straight off the last selection scan. These counters
            # already exist on the selection object and were never reported
            # anywhere -- which is why "is it quoting 3 because 3 is all
            # there is, or because it is broken?" needed a 50-call script on
            # the droplet to answer.
            slot_capacity=self.config.max_markets,
            awaiting_rotation_markets=self._awaiting_rotation(),
            cycling_markets=self._cycling_markets(),
            quote_size=self.config.quote_size,
            portfolio_exposure_cap_usd=(
                self.config.max_portfolio_exposure_usd),
            # Reward cash can arrive on later days, so withdrawal guidance
            # keeps seven full daily hard-stop budgets as operating runway.
            # Pending rewards are not cash and cannot reduce this reserve.
            # This is guidance only, not a new stop or trading rule.
            weekly_reserve_usd=self.config.weekly_operating_reserve_usd,
            settled_cash_usd=(
                self.balance.current_balance
                if self.balance is not None else None),
            buying_power_usd=(
                self.balance.buying_power
                if self.balance is not None else None),
            # A withdrawal number is more consequential than an ordinary
            # status metric. Fail closed on stale balance evidence or any
            # unresolved/unknown ownership state. Working owned orders are
            # safe: the calculation separately caps free cash by venue buying
            # power, so their reserved funds cannot be called withdrawable.
            capital_state_proven=capital_reason is None,
            capital_unavailable_reason=capital_reason,
            viable_markets=(
                self._market_selection.viable_markets
                if self._market_selection is not None else 0),
            rewarded_markets=(
                self._market_selection.rewarded_executable_markets
                if self._market_selection is not None else 0),
            spread_filtered_markets=(
                self._market_selection.catalog_spread_filtered_markets
                if self._market_selection is not None else 0),
            selection_failures=(
                len(self._market_selection.failures)
                if self._market_selection is not None else 0),
            payout_floor_one_markets=(
                self._payout_floor_count(self._market_selection, 1.0)),
            payout_floor_buffer_markets=(
                self._payout_floor_count(self._market_selection, 1.5)),
            # Marked trading P&L against the DAY's opening equity, from the
            # ledger so a restart cannot reset it. Account-wide by nature:
            # equity carries no per-system attribution. Proven rewards,
            # deposits and other non-trading cash are removed from this
            # operator-facing headline just as they are from the hard guard.
            today_pnl_usd=(
                report_snapshot.marked_trading_pnl(
                    opening_equity=self._day_opening_equity,
                    nontrading_cash=self._nontrading_cash)
                if report_snapshot is not None
                and self._day_opening_equity is not None
                and self._nontrading_cash is not None
                and self._baseline_day == today
                and self._nontrading_cash_day == today else None),
            today_realized_pnl_usd=(
                report_snapshot.realized_trading_pnl(
                    opening_cash=self._cash_baseline,
                    nontrading_cash=self._nontrading_cash)
                if report_snapshot is not None
                and self._cash_baseline is not None
                and self._nontrading_cash is not None
                and self._baseline_day == today
                and self._nontrading_cash_day == today
                # A carried position needs an opening basis to decompose the
                # day's mark. The durable baseline currently stores opening
                # cash and equity, so publish only the still-provable total.
                and self._day_opening_equity == self._cash_baseline
                else None),
            today_unrealized_pnl_usd=(
                report_snapshot.open_unrealized_pnl()
                if report_snapshot is not None
                and self._day_opening_equity == self._cash_baseline
                and self._baseline_day == today
                and self._nontrading_cash_day == today
                and self._nontrading_cash is not None
                else None),
            today_pnl_as_of=(
                report_snapshot.observed_at
                if report_snapshot is not None else None),
            # Exact delta observed by `hard_stop_reached`. `_refresh_balance`
            # removes proven non-trading cash before updating the guard, so
            # the meter and marked-trading headline use the same accounting
            # scope even when a reward or deposit lands.
            hard_stop_pnl_usd=(
                self.risk_guard.current_equity
                - self.risk_guard.starting_equity
                if self.risk_guard is not None
                and self.risk_guard.current_equity is not None
                and (self._window_risk is None or (
                    self._window_risk.current is not None
                    and self._window_risk.current.start <= now < self._window_risk.current.end
                    and self.risk_guard.equity_observed_at is not None
                    and 0 <= (now - self.risk_guard.equity_observed_at).total_seconds()
                    <= self.risk_guard.max_equity_age_seconds))
                else None),
            # Lifetime, and genuinely this bot's: the ledger holds only this
            # bot's fills, so no date filter is needed. Understates losses,
            # because a settled position has its entry recorded and its exit
            # invisible -- $67.38 across 29 markets as of 2026-08-19.
            # This bot's own bottom line: its ledger trading (the ledger holds
            # only this bot's fills) plus the reward it actually earned, dated
            # from its first order. PAID only -- invariant 22 keeps pending out
            # of accounted net -- and pending is carried separately as context.
            # Understates losses: a settled position has its entry recorded and
            # its exit invisible, $67.38 across 29 markets as of 2026-08-19.
            overall_pnl_usd=self._bot_overall_pnl(),
            attributable_paid_rewards_usd=self._bot_era_rewards.get(
                "PAID", Decimal("0")),
            pending_rewards_usd=self._bot_era_rewards.get(
                "PENDING", Decimal("0")),
            pnl_label="marked account equity Δ",
            rest_requests_per_second=float(self.config.rest_requests_per_second),
            market_stream_age_seconds=market_age,
            private_stream_age_seconds=private_age,
            ledger_bytes=self._ledger_bytes_on_disk(),
            integrity_problems=problems,
            report_database=str(self.ledger.path.resolve()),
            rewards_observed_at=getattr(self, "_report_rewards_observed_at", None),
            rewards_refresh_failed=getattr(self, "_reward_refresh_failed", False),
            reward_cache_scope=getattr(self, "_reward_cache_scope", None),
            account_source=getattr(self, "account_state_mode", "websocket"),
            inventory_limit_seconds=self.config.max_inventory_age_seconds,
        ))
        # Expose actual create count only in the description, never imply that
        # shadow observations were venue orders.
        economically_allowed = sorted({
            side for slug in self.active_slugs
            for side in self.state_for(slug).expected_net_by_side})
        maximum_remaining = max(
            (item.maximum_remaining_reward
             for slug in self.active_slugs
             for item in self.state_for(
                 slug).reward_projection_by_side.values()),
            default=0.0,
        )
        paid_observed = self._earnings_by_status.get("PAID", Decimal("0"))
        pending_observed = self._earnings_by_status.get("PENDING", Decimal("0"))
        risk_by_market_side = {
            (slug, side): value
            for slug in self.active_slugs
            for side, value in self.state_for(
                slug).fill_probability_by_side.items()}
        maximum_fill_probability = max(
            risk_by_market_side.values(), default=0.0)
        fill_driver = "none"
        if risk_by_market_side:
            riskiest = max(
                risk_by_market_side, key=risk_by_market_side.__getitem__)
            fill_driver = self.state_for(
                riskiest[0]).fill_risk_driver_by_side.get(
                    riskiest[1], "unknown")
        # These are diagnostics, not decisions. They used to be appended to the
        # embed footer, which grew to one unreadable line of twenty values.
        # Logged instead, where they can be grepped.
        print(
            f"[selection] at={now.isoformat()} "
            f"session_creates={place_count - self._orders_before} "
            f"economic_sides={','.join(economically_allowed) or 'none'} "
            f"max_remaining_reward={maximum_remaining:.4f} "
            f"fill_risk_30s={maximum_fill_probability:.4f} "
            f"fill_driver={fill_driver} "
            f"earnings_paid={paid_observed:.2f} "
            f"earnings_pending={pending_observed:.2f} "
            f"capital_proven={capital_reason is None} "
            f"capital_reason={capital_reason or 'ok'} "
            f"retired={len(self._retired_ever)}"
            + (f" event_pages={getattr(self._market_selection, 'event_pages_used', 0)}"
               f" event_markets={self._market_selection.catalog_markets}"
               f" executable={self._market_selection.executable_catalog_markets}"
               f" rewarded={self._market_selection.rewarded_executable_markets}"
               f" books_ranked={self._market_selection.books_evaluated}"
               f" viable={self._market_selection.viable_markets}"
               f" payout_est_ge_1="
               f"{self._payout_floor_count(self._market_selection, 1.0)}"
               f" payout_est_ge_1_5="
               f"{self._payout_floor_count(self._market_selection, 1.5)}"
               if self._market_selection is not None else "")
            + (f" scan_seconds={self._selection_scan_seconds_last:.1f}"
               if self._selection_scan_seconds_last is not None else ""),
            flush=True,
        )
        await self.notifier.send_payload_async(
            payload, critical=critical, channel="summary")
        await self._alarm_if_quoting_nothing(
            now, resting=resting_markets, reasons=idle_reasons)

    def _scheduled_summary_has_transient_capital_ambiguity(
        self, now: datetime,
    ) -> bool:
        """Return whether a routine summary may briefly wait for order proof.

        Only current order ambiguity is deferrable. Missing/stale balance
        evidence is reported immediately as unavailable, because waiting must
        not hide an account-data problem. This helper performs local reads
        only and cannot alter ownership or ask the venue for fresher state.
        """

        if self.balance is None or self.risk_guard is None:
            return False
        balance_age = (now - self.balance.observed_at).total_seconds()
        if not 0 <= balance_age <= self.risk_guard.max_equity_age_seconds:
            return False
        return bool(
            self.ledger.unresolved_intents()
            or self.ledger.account_unknown_order_count())

    async def _send_scheduled_summary(self, now: datetime) -> datetime:
        """Send a routine summary or return its next bounded proof recheck.

        The capital calculation remains fail-closed. A clean state sends now;
        an ordinary in-flight order waits at most 60 seconds; persistent
        ambiguity then sends the unchanged `UNAVAILABLE` representation.
        """

        now = max(now, self._now())
        if not getattr(self.config, "routine_summary", True):
            return now + timedelta(minutes=5)       # the Trading Bots card posts instead
        if not self._scheduled_summary_has_transient_capital_ambiguity(now):
            self._summary_proof_wait_started_at = None
            await self._send_summary(now)
            return now + timedelta(minutes=5)

        started = self._summary_proof_wait_started_at
        if started is None or now < started:
            self._summary_proof_wait_started_at = now
            return now + timedelta(
                seconds=self.SUMMARY_PROOF_RECHECK_SECONDS)

        if ((now - started).total_seconds()
                >= self.SUMMARY_PROOF_DEFERRAL_SECONDS):
            self._summary_proof_wait_started_at = None
            await self._send_summary(now)
            return now + timedelta(minutes=5)

        return min(
            started + timedelta(seconds=self.SUMMARY_PROOF_DEFERRAL_SECONDS),
            now + timedelta(seconds=self.SUMMARY_PROOF_RECHECK_SECONDS),
        )
