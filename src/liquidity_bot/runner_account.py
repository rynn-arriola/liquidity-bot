"""Account balance/equity refresh and loss-window risk gates for the commissioning runner.

Moved verbatim from ``live_commissioning.CommissioningRunner`` (pure
relocation, no behavior change). Methods run on the runner instance via
inheritance, so every ``self`` attribute and override resolves as before.
"""

from __future__ import annotations

from .cash_activity import fetch_nontrading_cash, fetch_nontrading_cash_changes
from . import read_backoff
from .api_diagnostics import api_workflow
import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from .account_snapshots import AccountEquitySnapshot, account_equity_snapshot
from .account_reporting import AccountReportSnapshot
from .errors import AccountSnapshotConsistencyError, AdapterError, RuntimeInvariantViolation
from .models import MarketMetadata
from .reward_calendar import NEW_YORK, risk_day
from .reconciler import ActionKind, MarketFrame, PlanAction
from .runner_shared import DECISION_EQUITY_REFRESH_MARGIN_SECONDS, NONTRADING_CASH_MAX_PAGES


class AccountRiskMixin:

    def _window_entry_ready(self, now: datetime) -> bool:
        return self._window_risk is None or self._window_risk.ready(now)

    def _portfolio_worst_loss(self, *, excluding: str) -> float:
        """Worst-case open exposure across every market except one.

        Hardcoded to 0 until 2026-08-14, which silently disabled the portfolio
        half of invariant 3: each market checked its own candidate against the
        whole portfolio budget and nothing ever accumulated. Harmless at one
        contract, where eight markets totalled $8. At forty it is the
        difference between a bounded book and $320 of exposure behind a $40
        stop that can only react after the loss.

        Worst case per market mirrors the candidate calculation in the
        reconciler: a long loses its full entry price if the market settles to
        zero, a short loses the complement, and a resting order is valued as
        though it fills.
        """

        total = 0.0
        for slug in self._shutdown_slugs():
            if slug == excluding:
                continue
            position = self.ledger.position(slug)
            if position:
                basis = self.ledger.inventory_basis(slug)
                unit_loss = (1.0 if basis is None else
                             float(basis.average_entry_price) if position > 0
                             else 1.0 - float(basis.average_entry_price))
                total += float(abs(position)) * unit_loss
            for order in self._working_orders(slug, include_absent=True):
                total += float(order.remaining_qty) * (
                    order.price if order.side == "bid" else 1.0 - order.price)
            # PLACE may be live before any order row arrives. Row-backed
            # intents are already counted above (or positively terminal).
            for intent in self.ledger.unresolved_intents(slug):
                if intent["operation"] != "PLACE":
                    continue
                if self.ledger.query(
                        "SELECT 1 FROM execution_orders WHERE client_order_id=?",
                        (intent["client_order_id"],)):
                    continue
                payload = json.loads(intent["payload_json"])
                price = Decimal(str(payload["price"]["value"]))
                qty = Decimal(str(payload["quantity"]))
                fills = self.ledger.query(
                    "SELECT qty FROM execution_fills WHERE client_order_id=? AND reversed=0",
                    (intent["client_order_id"],))
                remaining = max(Decimal(0), qty - sum(
                    (Decimal(str(row["qty"])) for row in fills), Decimal(0)))
                side = str(payload.get("intent", ""))
                if (not price.is_finite() or not qty.is_finite()
                        or not 0 <= price <= 1 or qty <= 0
                        or side not in {"ORDER_INTENT_BUY_LONG", "ORDER_INTENT_SELL_SHORT",
                                        "ORDER_INTENT_BUY_SHORT", "ORDER_INTENT_SELL_LONG"}):
                    raise RuntimeInvariantViolation(
                        "reserved_order_invalid", "unresolved order risk is malformed")
                total += float(remaining * (
                    price if side in {"ORDER_INTENT_BUY_LONG", "ORDER_INTENT_SELL_SHORT"}
                    else 1 - price))
        return total

    def _entry_risk_allowed(self, now: datetime, market: MarketMetadata) -> bool:
        """Shared clock-sensitive entry policy; no ledger or network reads."""
        today = date.fromisoformat(risk_day(now))
        allowed = (
            self.risk_guard is not None
            and self._window_entry_ready(now)
            and self.risk_guard.can_add_new_risk(now=now)
            and self._provisional_hard_stop_at is None
            and self._baseline_day == today
            and self._nontrading_cash is not None
            and self._nontrading_cash_day == today
            and not self._cash_report_blocked
            and not self._entry_pause_active()
            and market.event_allows_entry(now)
        )
        if not allowed:
            try:
                self._risk_diagnostics.entry_blocked(self, now, market)
            except Exception:
                pass  # Diagnostics cannot alter entry, cancellation or exit policy.
        return allowed

    async def _refresh_trading_balance(self, now: datetime) -> None:
        """Only routine decision/periodic callers may defer report-only work.

        Startup, recovery, shutdown, source handoff and direct refresh callers
        retain the complete synchronous proof. One serial owner, no new task.
        """
        previous = self._defer_cash_reporting
        self._defer_cash_reporting = True
        try:
            await self._refresh_balance(now)
        finally:
            self._defer_cash_reporting = previous

    def _can_defer_cash_report(self, now: datetime) -> bool:
        meter = self._window_risk
        report = self._account_report
        today = date.fromisoformat(risk_day(now))
        return bool(
            self._defer_cash_reporting and self.config.cash_history_refresh_seconds
            and meter is not None and meter.current is not None
            and meter.current.start == meter.policy.bounds(now)[0]
            and not meter.held and not meter.provisional
            and not self._cash_report_blocked and not self._stop_requested
            and not self._streams_need_rebind
            and self._transport_recovery_component is None
            and self._baseline_day == today and self._nontrading_cash_day == today
            and self._nontrading_cash is not None and report is not None
            and report.equity.observed_at <= now
            and report.matches(day=today, opening_equity=self._day_opening_equity,
                               opening_cash=self._cash_baseline,
                               cash_boundary=self._cash_activity_boundary))

    def _remember_account_report(
        self, snapshot: AccountEquitySnapshot, nontrading: Decimal,
    ) -> None:
        if self._baseline_day is None or self._day_opening_equity is None:
            return
        if self._cash_activity_boundary is None:
            return
        self._account_report = AccountReportSnapshot(
            snapshot, nontrading, self._baseline_day, self._day_opening_equity,
            self._cash_baseline, self._cash_activity_boundary)
        self._account_report_invalid = False
        self._cash_report_blocked = False
        self._cash_report_attempted_at = snapshot.observed_at

    def _reporting_equity(self, now: datetime) -> AccountEquitySnapshot | None:
        if self._window_risk is None:
            return self._equity_snapshot
        report = self._account_report
        if report is None or self._account_report_invalid:
            return None
        if (not report.matches(day=date.fromisoformat(risk_day(now)),
                               opening_equity=self._day_opening_equity,
                               opening_cash=self._cash_baseline,
                               cash_boundary=self._cash_activity_boundary)
                or report.nontrading_cash != self._nontrading_cash):
            return None
        # Age belongs to reporting only, never buying power or the HARD meter.
        limit = max(15, self.config.cash_history_refresh_seconds + 15)
        if not 0 <= (now - report.equity.observed_at).total_seconds() <= limit:
            return None
        return report.equity

    async def _refresh_window_safety(self, now: datetime) -> None:
        """Publish fresh trading proof independently of an optional cash walk."""
        await self.gateway.ensure_account_reads_available("balance", "positions")
        snapshot = await self.account.fetch_equity_snapshot(observed_at=now)
        observed_at = self._now()
        if (not self._can_defer_cash_report(observed_at)
                or risk_day(snapshot.observed_at) != risk_day(observed_at)):
            raise AdapterError("financial boundary changed during safety snapshot")
        if snapshot.position_basis is None:
            raise AdapterError("open-position basis is unavailable for account safety")
        if not 0 <= (observed_at - snapshot.observed_at).total_seconds() <= self.risk_guard.max_equity_age_seconds:
            raise AdapterError("settled window account snapshot is stale")
        self._equity_snapshot = snapshot
        self.balance = snapshot.balance
        self._observe_risk_window(snapshot, observed_at)
        self.risk_guard.observe_equity(
            self._window_risk.realized_equity, observed_at=snapshot.observed_at)
        self._refresh_overnight_entry_order_cap(observed_at)

        attempted = self._cash_report_attempted_at
        if (attempted is not None and 0 <= (observed_at - attempted).total_seconds()
                < self.config.cash_history_refresh_seconds):
            return
        if (self._stop_requested or self._hard_stop_active()
                or self._entry_observation_pending()
                or any(self._position_priority_rank(slug) < 2
                       for slug in self._position_priority_tokens())
                or self._position_near_its_flatten_deadline(observed_at)):
            return  # Preserve overdue work; no catch-up burst or exit delay.
        self._cash_report_attempted_at = observed_at
        self._risk_refresh_stage = "cash_report"
        try:
            await self.gateway.ensure_account_reads_available("activities", "balance")
            changes = await fetch_nontrading_cash_changes(
                self.gateway, boundary=self._cash_activity_boundary,
                through=observed_at, max_pages=NONTRADING_CASH_MAX_PAGES)
            nontrading = sum((change.amount for change in changes
                             if change.completed_at > self._cash_activity_boundary), Decimal("0"))
            second = await self.account.fetch_balance(observed_at=self._now())
            if snapshot.balance.current_balance != second.current_balance:
                raise AccountSnapshotConsistencyError("cash changed during account report")
            if (risk_day(self._now()) != risk_day(snapshot.observed_at)
                    or self._window_risk.policy.bounds(self._now())[0]
                    != self._window_risk.current.start):
                raise AccountSnapshotConsistencyError("account report crossed a financial boundary")
            report = account_equity_snapshot(
                second, snapshot.positions, observed_at=snapshot.observed_at)
            self._nontrading_cash = nontrading
            self._nontrading_cash_observed_at = self._now()
            self._nontrading_cash_day = self._baseline_day
            self._remember_account_report(report, nontrading)
            # Cash agrees; retain the newest buying-power response without
            # renewing the older safety or position observation clock.
            self.balance = second
            self._publish_live_pnl_snapshot()
            self._runtime_error_counts.pop("account_cash_report", None)
        except AdapterError as exc:
            self._account_report_invalid = True
            if not (isinstance(exc, AccountSnapshotConsistencyError)
                    or read_backoff.is_backpressure(exc)):
                self._cash_report_blocked = True
                raise  # Malformed/unknown transfers remain conservative.
            await self._report_runtime_error("account_cash_report", exc)

    @api_workflow("financial_refresh")
    async def _refresh_balance(self, now: datetime) -> None:
        previous = self.risk_guard.equity_observed_at if self.risk_guard else None
        self._risk_refresh_stage = "equity_snapshot"
        try:
            await self._refresh_balance_candidate(now)
        except AccountSnapshotConsistencyError:
            # A normal fill/settlement can land between the balance and
            # position/history reads.  Throwing away the last proven risk mark
            # immediately caused every entry to cancel even though no read or
            # schema had failed.  Retry the complete bracket exactly once with
            # a fresh timestamp.  Nothing from the rejected candidate is
            # published, and a second race still reaches the fail-closed path
            # below.
            try:
                self._risk_refresh_stage = "equity_snapshot_retry"
                await self._refresh_balance_candidate(self._now())
            except Exception as exc:
                if self._window_risk is not None and self.risk_guard is not None:
                    self.risk_guard.equity_observed_at = None
                    self._account_report_invalid = True
                    self._cash_report_blocked = True
                try:
                    self._risk_diagnostics.refresh_failed(
                        self._now(), stage=self._risk_refresh_stage,
                        error=exc, previous_observed_at=previous)
                except Exception:
                    pass
                raise
        except Exception as exc:
            # A failed settlement proof is unknown, not another fresh reading
            # of the previous confirmed window loss. Preserve the dollar
            # checkpoint, but immediately withdraw entry authority.
            if self._window_risk is not None and self.risk_guard is not None:
                self.risk_guard.equity_observed_at = None
                self._account_report_invalid = True
                self._cash_report_blocked = True
            try:
                self._risk_diagnostics.refresh_failed(
                    self._now(), stage=self._risk_refresh_stage,
                    error=exc, previous_observed_at=previous)
            except Exception:
                pass  # Never mask the original safety failure with logging failure.
            raise

    async def _refresh_balance_candidate(self, now: datetime) -> None:
        if self.risk_guard is None:
            raise RuntimeError("risk guard is not initialized")
        if self._can_defer_cash_report(now):
            await self._refresh_window_safety(now)
            return
        await self.gateway.ensure_account_reads_available(
            "balance", "positions", "activities")
        snapshot = await self.account.fetch_equity_snapshot(observed_at=now)
        self._risk_refresh_stage = "day_baseline"

        observed_at = self._now()
        today = date.fromisoformat(risk_day(observed_at))
        if (self._baseline_day != today
                and self._provisional_hard_stop_at is None):
            snapshot_day = date.fromisoformat(risk_day(snapshot.observed_at))
            if snapshot_day != today:
                # The bracketed account read itself crossed midnight. It is
                # not a defensible opening mark for either side of the
                # boundary; leave entry blocked and take another snapshot.
                return
            # Roll the durable ET-day marks before asking cash history to
            # classify activity. The scan is bounded by the baseline's cash
            # boundary and verifies that the current risk day matches
            # ``_baseline_day``. The opposite order deadlocks at midnight:
            # yesterday remains active, the check fails, and balance/cash
            # reads retry forever while entry stays blocked.
            #
            # Publish only the raw candidate needed to create the baseline;
            # ``_frame`` separately requires a current-day cash adjustment,
            # so entry remains fail-closed until the work below succeeds.
            self._equity_snapshot = snapshot
            self.balance = snapshot.balance
            self._roll_realized_baseline()
        # Every published mark needs cash activity from the same bracket.
        # A timer-cached adjustment can misclassify a newly completed transfer
        # (even if an offsetting fill leaves the raw cash balance unchanged).
        self._risk_refresh_stage = "cash_history"
        if self._cash_activity_boundary is None:
            raise RuntimeError("cash-activity boundary is unavailable")
        cash_through = observed_at
        if self._window_risk is None:
            nontrading = await fetch_nontrading_cash(
                self.gateway, boundary=self._cash_activity_boundary,
                through=cash_through, max_pages=NONTRADING_CASH_MAX_PAGES)
        else:
            changes = await fetch_nontrading_cash_changes(
                self.gateway,
                boundary=self._cash_activity_boundary,
                through=cash_through, max_pages=NONTRADING_CASH_MAX_PAGES)
            nontrading = sum((change.amount for change in changes
                             if change.completed_at > self._cash_activity_boundary), Decimal("0"))
        # Cash activity can complete while its pages are being read. The
        # snapshot already bracketed positions with equal cash reads; one
        # final equal cash read proves the activity adjustment belongs to
        # that same account state.
        self._risk_refresh_stage = "final_balance"
        second = await self.account.fetch_balance(observed_at=self._now())
        if snapshot.balance.current_balance != second.current_balance:
            raise AccountSnapshotConsistencyError(
                "settled cash changed during non-trading activity snapshot")
        if date.fromisoformat(risk_day(self._now())) != today:
            raise AdapterError(
                "ET risk day changed during non-trading activity snapshot")
        self._risk_refresh_stage = "snapshot_validation"
        if self._window_risk is not None:
            if snapshot.position_basis is None:
                raise AdapterError(
                    "open-position basis is unavailable for account safety")
            age = (self._now() - snapshot.observed_at).total_seconds()
            if not 0 <= age <= self.risk_guard.max_equity_age_seconds:
                raise AdapterError("settled window account snapshot is stale")
        # Keep every consumer on the final bracketed balance row.  Do not
        # publish any part of the candidate snapshot until all reads have
        # succeeded; a failed activity bracket must leave the previous
        # proven mark intact and merely let its freshness expire.
        self._risk_refresh_stage = "publication"
        snapshot = account_equity_snapshot(
            second, snapshot.positions, observed_at=snapshot.observed_at)
        self._nontrading_cash = nontrading
        self._nontrading_cash_observed_at = self._now()
        self._nontrading_cash_day = self._baseline_day

        self._equity_snapshot = snapshot
        self.balance = snapshot.balance
        # Realised P&L from the fill ledger, per market and in total. This
        # used to attribute the whole account's marked-equity change to the
        # primary market, which was wrong twice over: marked equity is not
        # realised, and one market is not all of them.
        self._roll_realized_baseline()

        # Publication waits for the complete bracket, but DATA age begins at
        # its oldest contributing observation. Equal final cash does not
        # refresh the earlier position mark. Slow reads must not manufacture
        # another 15 seconds of entry authority.
        observed_at = self._now()
        # Fiat cash plus each open position's documented cashValue is the
        # account mark.  Retail's balance-row assetNotional has been observed
        # as zero with nonzero positions and therefore cannot protect this
        # stop. Rewards, deposits and withdrawals are then removed exactly as
        # they are from the realised sensor.
        adjusted_equity = snapshot.marked_equity - self._nontrading_cash
        if self._window_risk is not None:
            # The venue can report a net-flat position before cash tied in the
            # matched outcomes is returned. Cash+basis therefore created a
            # false loss ratchet. The window uses only durable matched-fill
            # P&L; open orders, open inventory and cash timing are irrelevant.
            self._observe_risk_window(snapshot, observed_at)
            adjusted_equity = self._window_risk.realized_equity
        self.risk_guard.observe_equity(
            adjusted_equity, observed_at=snapshot.observed_at)
        self._refresh_loss_entry_order_cap(observed_at)
        self._refresh_overnight_entry_order_cap(observed_at)
        self._remember_account_report(snapshot, nontrading)
        self._publish_live_pnl_snapshot()

    def _observe_risk_window(
        self, snapshot: AccountEquitySnapshot, now: datetime,
    ) -> None:
        meter = self._window_risk
        if meter is None or self.risk_guard is None:
            return
        previous, was_held = meter.current, meter.held
        was_provisional = meter.provisional
        try:
            window_start = meter.policy.bounds(now)[0]
            settled_pnl = self.ledger.realized_pnl(
                received_since=window_start,
                received_through=snapshot.observed_at)
            mark = meter.observe(
                settled_pnl=settled_pnl,
                observed_at=snapshot.observed_at, now=now,
                full_cap=self.config.full_entry_order_capacity)
        except ValueError as exc:
            raise AdapterError(str(exc)) from exc
        self.risk_guard.adopt_day_baseline(mark.opening_equity)
        self._loss_entry_order_cap = mark.cap
        if (mark != previous or was_held != meter.held
                or was_provisional != meter.provisional):
            self._dirty_market_slugs.update(self.active_slugs)
            local = mark.start.astimezone(NEW_YORK)
            print(f"[window-risk] at={now.isoformat()} start={local.isoformat()} "
                  f"end={mark.end.astimezone(NEW_YORK).isoformat()} "
                  f"loss={max(Decimal('0'), -meter.realized_equity)} "
                  f"hard={meter.policy.stop_loss_usd} cap={mark.cap} "
                  f"held={meter.held} provisional={meter.provisional} "
                  "basis=matched_fills open_orders_and_positions=excluded",
                  flush=True)
        if meter.held and not was_held:
            self._hard_stop_hold_notice = "held"
            self._entry_pause_cancel_requested = True
            for slug in self._retired:
                self._retired[slug].next_retry_at = now
            self._publish_active_scope_snapshot(status="ENTRY_PAUSED")
        elif meter.provisional and not was_provisional:
            self._entry_pause_cancel_requested = True
            self._provisional_hard_stop_notice = "started"
            self._publish_active_scope_snapshot(status="ENTRY_PAUSED")
        elif was_provisional and not meter.provisional:
            self._provisional_hard_stop_notice = "cleared"
            self._publish_active_scope_snapshot()

    async def _refresh_equity_for_decision(self, now: datetime) -> None:
        """Keep the account-risk observation fresh across a long slot sweep."""

        guard = self.risk_guard
        if guard is None:
            raise RuntimeError("risk guard is not initialized")
        observed_at = guard.equity_observed_at
        if observed_at is None or (self._window_risk is not None and (
                self._window_risk.current is None
                or self._window_risk.current.start
                != self._window_risk.policy.bounds(now)[0])):
            await self._refresh_trading_balance(now)
            return
        age = (now - observed_at).total_seconds()
        refresh_at = max(
            0.0,
            guard.max_equity_age_seconds
            - DECISION_EQUITY_REFRESH_MARGIN_SECONDS,
        )
        if age < 0 or age >= refresh_at:
            await self._refresh_trading_balance(now)

    async def _refresh_periodic_balance(self, now: datetime) -> datetime:
        """Coalesce a just-completed bracket without changing the 5s cadence."""
        guard = self.risk_guard
        observed_at = guard.equity_observed_at if guard is not None else None
        if (observed_at is not None
                and risk_day(now) == risk_day(observed_at)
                and (self._window_risk is None or (
                    self._window_risk.current is not None
                    and self._window_risk.current.start
                    == self._window_risk.policy.bounds(now)[0]))
                and 0 <= (now - observed_at).total_seconds() < 5):
            # Keep the original due time: skipping a duplicate at age four
            # seconds must not postpone the next periodic check by five more.
            return observed_at + timedelta(seconds=5)
        await self._refresh_trading_balance(now)
        return now + timedelta(seconds=5)

    def _refresh_loss_entry_order_cap(self, observed_at: datetime) -> None:
        """Tighten the durable ET-day entry-order budget from marked HARD use."""

        if self._window_risk is not None:
            return  # Window observation owns its durable cap atomically.
        guard = self.risk_guard
        day = self._baseline_day
        if (guard is None or day is None or guard.current_equity is None
                or guard.hard_stop_loss is None):
            return
        if date.fromisoformat(risk_day(observed_at)) != day:
            return
        loss = max(
            Decimal("0"), guard.starting_equity - guard.current_equity)
        fraction = loss / guard.hard_stop_loss
        full_cap = self.config.full_entry_order_capacity
        candidate = self.config.entry_order_cap_for_loss(loss)
        stored_before = self.ledger.entry_order_cap_for_day(
            day.isoformat(), full_cap=full_cap)
        effective = self.ledger.ratchet_entry_order_cap(
            day.isoformat(), candidate_cap=candidate, full_cap=full_cap,
            loss_fraction=fraction, observed_at=observed_at)

        previous_day = self._loss_entry_order_cap_day
        previous = self._loss_entry_order_cap
        transition_from = (
            stored_before if previous_day is None
            else previous)
        self._loss_entry_order_cap_day = day
        self._loss_entry_order_cap = effective
        if transition_from == effective:
            return

        action = "et_day_reset" if effective > transition_from else "reduce"
        print(
            f"[loss-entry-order-cap] at={observed_at.isoformat()} "
            f"day={day.isoformat()} marked_loss={loss} "
            f"hard={guard.hard_stop_loss} "
            f"utilization={fraction:.4f} from={transition_from} "
            f"to={effective} action={action} "
            "positions_and_reducers=excluded",
            flush=True,
        )
        self._loss_entry_order_cap_notice = (
            day, transition_from, effective, fraction)
        # Wake every flat owner so an over-cap slate starts draining without
        # waiting for a book/timer event. The existing single-mutation driver
        # keeps cancellation bounded and serial.
        self._dirty_market_slugs.update(
            slug for slug in self.active_slugs
            if self.ledger.position(slug) == 0)

    def _loss_cap_overflow_order_ids(
        self, observed_at: datetime | None = None,
    ) -> frozenset[str]:
        """Choose lowest-ranked flat entry orders beyond the effective cap."""

        working, reservations = self._flat_entry_orders_and_reservations()
        now = self._now() if observed_at is None else observed_at
        keep_count = max(
            0, self._effective_entry_order_cap(now) - reservations)
        if len(working) <= keep_count:
            return frozenset()
        rank = {slug: index for index, slug in enumerate(self.active_slugs)}

        def priority(row: Any) -> tuple[int, float, str, str]:
            slug = str(row["market_slug"])
            raw_side = str(row["side"] or "").lower()
            side = "bid" if raw_side in {"buy", "bid"} else "offer"
            expected = self.state_for(slug).expected_net_by_side.get(
                side, float("-inf"))
            return (
                rank.get(slug, len(rank)),
                -expected,
                str(row["event_at"]),
                str(row["exchange_order_id"]),
            )

        ordered = sorted(working, key=priority)

        # Allocate complete market pairs before one-sided leftovers. This
        # avoids keeping one higher-ranked orphan and breaking a complete pair
        # immediately below it when an even cap can retain the pair instead.
        # Within each class, active selection rank and expected value still
        # decide priority.
        by_market: dict[str, list[Any]] = {}
        for row in ordered:
            by_market.setdefault(str(row["market_slug"]), []).append(row)
        retained_ids: set[str] = set()
        remaining = keep_count
        ranked_slugs = tuple(dict.fromkeys(
            (*self.active_slugs, *(str(row["market_slug"]) for row in ordered))))
        for slug in ranked_slugs:
            rows = by_market.get(slug, [])
            bids = [row for row in rows
                    if str(row["side"] or "").lower() in {"buy", "bid"}]
            offers = [row for row in rows
                      if str(row["side"] or "").lower()
                      not in {"buy", "bid"}]
            if not bids or not offers:
                continue
            pair = (min(bids, key=priority), min(offers, key=priority))
            pair_ids = {
                str(row["exchange_order_id"]) for row in pair
                if row["exchange_order_id"]
            }
            if remaining >= len(pair_ids):
                retained_ids.update(pair_ids)
                remaining -= len(pair_ids)
        for row in ordered:
            order_id = str(row["exchange_order_id"] or "")
            if (remaining <= 0 or not order_id
                    or order_id in retained_ids):
                continue
            retained_ids.add(order_id)
            remaining -= 1
        return frozenset(
            str(row["exchange_order_id"])
            for row in ordered
            if row["exchange_order_id"]
            and str(row["exchange_order_id"]) not in retained_ids)

    def _loss_cap_cancel_action(
        self, frame: MarketFrame,
    ) -> PlanAction | None:
        """Return one owned overflow cancel without touching position markets."""

        if frame.position != 0 or not frame.state_known:
            return None
        overflow = self._loss_cap_overflow_order_ids(frame.now)
        for order in reversed(frame.working_orders):
            if order.order_id in overflow:
                time_cap = self.config.entry_order_cap_for_time(frame.now)
                reason = (
                    "overnight_entry_order_cap"
                    if (time_cap < self._loss_entry_order_cap
                        and self.config.entry_order_cap_is_overnight(frame.now))
                    else "daytime_entry_order_cap"
                    if time_cap < self._loss_entry_order_cap
                    else "loss_scaled_entry_order_cap"
                )
                if self._effective_entry_order_cap(frame.now) < min(
                        time_cap, self._loss_entry_order_cap):
                    reason = "account_source_entry_order_cap"
                return PlanAction(
                    ActionKind.CANCEL,
                    frame.market.slug,
                    reason,
                    side=order.side,
                    order_id=order.order_id,
                )
        return None

    def _loss_cap_blocks_new_entry(
        self, observed_at: datetime | None = None,
    ) -> bool:
        working, reservations = self._flat_entry_orders_and_reservations()
        now = self._now() if observed_at is None else observed_at
        return (
            len(working) + reservations
            >= self._effective_entry_order_cap(now)
        )

    def _maybe_release_window_stop(self, now: datetime) -> None:
        meter, guard = self._window_risk, self.risk_guard
        if (meter is None or not meter.held or meter.current is None
                or guard is None or not guard.can_add_new_risk(now=now)
                or self._streams_need_rebind
                or not self.gate.ready(self.ledger, now=now)):
            return
        reconciled, equity_at = self.gate.last_reconcile_at, guard.equity_observed_at
        snapshot = self._equity_snapshot
        if (reconciled is None or equity_at is None or snapshot is None
                or not meter.current.start <= reconciled <= equity_at <= now
                or snapshot.observed_at != equity_at
                or self._nontrading_cash_day != self._baseline_day
                or self._nontrading_cash is None
                or any(p.net_qty != 0 for p in snapshot.positions)
                or self.ledger.working_orders() or self.ledger.absent_orders()
                or self.ledger.unresolved_intents() or self._has_open_position()):
            return
        self._assert_runtime_scope()
        if meter.release(now=now, flat_proven=True):
            self._dirty_market_slugs.update(self.active_slugs)
            self._hard_stop_hold_notice = "released" if not meter.held else None
            self._publish_active_scope_snapshot()
            print(f"[window-risk] at={now.isoformat()} "
                  "state=released proof=fresh_next_window_flat_account", flush=True)

    async def _announce_loss_entry_order_cap(self) -> None:
        """Notify once for each in-process ET-day order-cap transition."""

        notice, self._loss_entry_order_cap_notice = (
            self._loss_entry_order_cap_notice, None)
        if notice is None:
            return
        day, previous, current, fraction = notice
        if current < previous:
            detail = (
                f"reduced from {previous} to {current} after marked loss "
                f"reached {fraction:.1%} of HARD. This cap remains through "
                "the ET day")
        else:
            detail = f"reset from {previous} to {current} for the new ET day"
        await self.notifier.send_async(
            f"⚠️ Liquidity Bot entry-order cap {detail} ({day}). "
            "Open-position reducers and exits are excluded.")

    @staticmethod
    def _is_risk_reducing_mutation(
        action: PlanAction, position: Decimal,
    ) -> bool:
        """Return whether the mutation removes orders or existing exposure."""

        return (
            action.kind in {ActionKind.CANCEL, ActionKind.PLACE_IOC}
            or (action.kind == ActionKind.PLACE_POST_ONLY and position != 0)
        )

    def _position_priority_has_fresh_account_proof(self, slug: str) -> bool:
        """Optional account-only reuse; never skips equity or grants authority."""
        return False

    async def _account_source_recovery_checkpoint(self) -> bool:
        """Optional serial manual handoff; never an automatic source switch."""
        return False

    def _whole_account_safe_to_stop(self) -> bool:
        """True only when every market is proven flat and state is known."""

        if not self.ledger.risk_state_known():
            return False
        # An empty set is genuinely safe: no market holds risk.
        return all(
            self.kill_switch.status(slug).safe_to_stop
            for slug in self._shutdown_slugs())
