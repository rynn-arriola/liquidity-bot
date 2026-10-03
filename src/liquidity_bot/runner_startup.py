"""Startup preflight gates and startup market scoping for the commissioning runner.

Moved verbatim from ``live_commissioning.CommissioningRunner`` (pure
relocation, no behavior change). Methods run on the runner instance via
inheritance, so every ``self`` attribute and override resolves as before.
"""

from __future__ import annotations

from .api_diagnostics import api_workflow
import time
from datetime import datetime
from .commissioning import CommissioningRefused, CommissioningUnavailable
from .errors import AdapterError, ApiAdmissionDeferred
from .live_session import ControlledActionDriver, ControlledTrialStartup
from .readiness import ReadinessVerdict
from .runtime_safety import SessionRiskGuard
from .runner_shared import MAINTENANCE_FAILURE_THRESHOLD, _OrphanPlaceEvidencePending


class StartupMixin:

    def _startup_settlement_wait_is_scoped(
        self, blockers: tuple[str, ...], now: datetime,
    ) -> bool:
        """Authorize unrelated entry while every nonflat owner is isolated."""

        if set(blockers) != {"shared account has nonzero positions"}:
            return False
        nonzero = {
            str(row["market_slug"])
            for row in self.ledger.query(
                "SELECT market_slug FROM execution_positions "
                "WHERE CAST(net_qty AS REAL)!=0")
        }
        return (
            bool(nonzero)
            and nonzero == self._isolated_unexecutable_slugs()
            and self.ledger.risk_state_known()
            and all(self._can_wait_unexecutable(slug, now)
                    for slug in nonzero)
        )

    def _startup_managed_position_is_scoped(
        self, blockers: tuple[str, ...] | None, now: datetime,
    ) -> bool:
        """Allow other slots around one exact post-kickoff recovery owner."""

        if (blockers is not None
                and set(blockers) != {"shared account has nonzero positions"}):
            return False
        nonzero = tuple(
            str(row["market_slug"])
            for row in self.ledger.query(
                "SELECT market_slug FROM execution_positions "
                "WHERE CAST(net_qty AS REAL)!=0"))
        if len(nonzero) != 1:
            return False
        slug = nonzero[0]
        if (slug not in self._recovery_slugs
                or slug not in set(self.active_slugs)
                or slug in self._isolated_unexecutable_slugs()
                or not self.ledger.risk_state_known()
                or self.ledger.working_orders()
                or self.ledger.absent_orders()
                or self.ledger.unresolved_intents()):
            return False
        try:
            basis = self.ledger.inventory_basis(slug)
        except Exception:
            return False
        if basis is None or basis.net_qty != self.ledger.position(slug):
            return False
        state = self.state_for(slug)
        market = state.market
        return bool(
            market is not None
            and market.game_start is not None
            and market.game_start <= now
            and state.book is not None
            and state.book_observed_at is not None)

    async def _add_startup_markets_around_reserved_position(
        self, now: datetime,
    ) -> None:
        """Use remaining configured slots without delaying risk monitoring."""

        if (not self.config.auto_select_reward_markets
                or self.reward_selector is None
                or not (
                    self._isolated_unexecutable_slugs()
                    or self._startup_managed_position_is_scoped(None, now))):
            return
        available = self.config.max_markets - len(self.active_slugs)
        if available <= 0:
            return
        scan_started = time.monotonic()
        try:
            selection = await self.reward_selector.select(now=now)
        except (ApiAdmissionDeferred, AdapterError) as exc:
            # Discovery cannot improve or exit the quarantined position. A
            # failed scan therefore leaves its monitor scope intact, and the
            # ordinary bounded runtime top-up path can retry later.
            self._selection_scan_seconds_last = time.monotonic() - scan_started
            await self._report_runtime_error(
                "startup_reserved_position_selection", exc)
            return
        self._selection_scan_seconds_last = time.monotonic() - scan_started
        self._log_selection_scan(selection)
        self._market_selection = selection
        self._market_selection_observed_at = self._now()
        if selection.selected is None:
            return
        chosen = tuple(
            item.market.slug for item in selection.top_viable(
                available, exclude=self._barred_slugs()))
        if chosen:
            self._bind_markets((*self.active_slugs, *chosen))

    async def preflight_until_ready(self) -> str:
        """Retry startup while the only obstacle is a transient condition.

        A market that has not opened, an unpublished incentive programme, a
        late private snapshot or a Discord blip all clear on their own, but
        each used to exit 2, which the supervisor never restarts. One bad
        moment therefore ended an entire run before a single order.

        Nothing unsafe is retried: ownership, cross-market state and config
        drift raise ``CommissioningRefused`` and still exit 2 immediately. The
        wait itself carries no risk, because a runner that has not completed
        preflight holds no orders and no position, and each attempt re-proves
        the whole account from scratch.
        """

        attempt = 0
        venue_failure_streak = 0
        while True:
            attempt += 1
            try:
                mode = await self.preflight()
                self._runtime_error_counts.pop("startup_unavailable", None)
                return mode
            except ApiAdmissionDeferred as exc:
                # A cooperating process published a host cooldown before this
                # read crossed the network boundary. Startup is still flat and
                # safe: discard partial bindings, wait once, and re-prove the
                # complete preflight without consuming an outage/error budget.
                attempt = 0
                venue_failure_streak = 0
                await self._release_partial_startup()
                if self._stop_requested:
                    raise CommissioningRefused(
                        "operator stopped during API admission backpressure"
                    ) from exc
                delay = self._api_admission_delay(exc)
                self._log_api_backpressure("startup", delay)
                await self.sleep(delay)
            except AdapterError as exc:
                # Do not let maintenance consume the supervisor's three-fast-
                # crash allowance. Only classified transport/5xx/throttle
                # failures enter the long-lived pause; malformed responses,
                # validation failures and ownership/state defects still leave
                # this process and surface normally.
                if not self._is_transient_recovery_error(exc):
                    raise
                venue_failure_streak += 1
                await self._report_runtime_error(
                    "startup_venue_unavailable", exc, venue_failure_streak)
                await self._release_partial_startup()
                if venue_failure_streak >= MAINTENANCE_FAILURE_THRESHOLD:
                    stop = await self._wait_for_venue_recovery("startup", exc)
                    if stop:
                        raise CommissioningRefused(
                            f"operator stopped during venue recovery pause: {stop}"
                        ) from exc
                    venue_failure_streak = 0
                    attempt = 0
                    continue
                await self.sleep(self._recovery_delay(
                    "startup_venue_unavailable", base=5.0, cap=30.0))
            except _OrphanPlaceEvidencePending as exc:
                # This is local ambiguity, not a stream/venue failure and not a
                # condition that improves by restarting. Stay flat, repeat the
                # complete account proof, and remain operator-interruptible.
                attempt = 0
                venue_failure_streak = 0
                await self._report_runtime_error(
                    "startup_orphan_place_evidence", exc)
                await self._release_partial_startup()
                if self._stop_requested:
                    raise CommissioningRefused(
                        f"operator stopped during orphan PLACE recovery: {exc}"
                    ) from exc
                await self.sleep(self._recovery_delay(
                    "startup_orphan_place_evidence", base=5.0, cap=60.0))
            except CommissioningUnavailable as exc:
                venue_failure_streak = 0
                if self._stop_requested:
                    raise CommissioningRefused(
                        f"operator stopped during startup: {exc}") from exc
                await self._report_runtime_error(
                    "startup_unavailable", exc, attempt,
                    include_traceback=(attempt == 1 or attempt % 30 == 0))
                # Discard anything the partial attempt bound so the retry
                # re-selects and re-proves rather than trusting stale state.
                await self._release_partial_startup()
                await self.sleep(self._recovery_delay(
                    "startup_unavailable", base=5.0, cap=60.0))

    async def _release_partial_startup(self) -> None:
        """Drop streams and market state left by a failed startup attempt."""

        for stream in (self.private_stream, self.market_stream):
            try:
                await stream.close()
            except Exception:
                pass
        self.gate.on_disconnect()
        self.market_stream = self.market_stream_factory(self.client)
        self.private_stream = self.private_stream_factory(self.client)
        # A retry can fail before automatic selection binds its first market
        # (notably while proving a startup orphan). The compatibility
        # properties require a primary slug, so clear them only when one exists;
        # `_bind_markets(())` below discards every per-market state either way.
        if self.active_slugs:
            self.market = None
            self.incentive = None
            self.book = None
            self.book_observed_at = None
        if self.config.auto_select_reward_markets:
            self._bind_markets(())

    @api_workflow("startup")
    async def preflight(self) -> str:
        now = self._now()
        self.started_at = now
        # Deliberately not gated on "this ledger ever placed an order". That
        # test is true forever once a single order has been placed, so every
        # later session became recovery-only and the bot could never trade
        # again without a fresh database. It was survivable while recovery
        # exited 0 and simply stopped; once recovery started exiting 5 to
        # restart, it became an infinite loop that the supervisor's fast-crash
        # guard killed -- observed 2026-08-14T05:07-05:09Z. Genuine leftover
        # state is already detected by `recovery_only` below, from live venue
        # orders, positions and unresolved intents.
        self.config.assert_authorized(now=now)
        self.cutoff_at = None
        # Reconcile against the venue BEFORE deciding what to recover.
        #
        # `_identified_recovery_markets` reads the ledger, and the ledger stops
        # updating the moment a runner dies -- so after a crash it describes the
        # account as it was at the crash, not as it is now. Observed
        # 2026-08-16T21:36Z and again at 21:41: the ledger still held one
        # working order and a 5-lot position in `dumvit-midmih` from the 19:01
        # crash, while the venue held neither. Preflight bound that market for
        # recovery, its game had kicked off at 19:45 and finished, so its book
        # came back empty on both sides and startup exited 2 -- twice, refusing
        # to start over a position that did not exist.
        #
        # Cheap and unconditional: one whole-account read, before any decision
        # depends on what we think we hold. Everything downstream -- the
        # recovery binding, `ControlledTrialStartup`, the flat proof -- is more
        # accurate for it, and on a clean start it finds nothing and costs one
        # round trip.
        repaired_denials = self.executor.resolve_recorded_permission_denials(
            now=now)
        if repaired_denials:
            print(
                f"[startup-intent-repair] at={now.isoformat()} "
                "reason=definitive_permission_rejection "
                f"intents={len(repaired_denials)}",
                flush=True,
            )
        reconciled_at_startup = False
        try:
            await self._rest_reconcile(now, tag="startup")
            reconciled_at_startup = True
            now = self._now()
        except Exception as exc:
            # Not fatal on its own. `ControlledTrialStartup` below still proves
            # the account against the venue and refuses if it cannot, so a
            # transient read here must not be the thing that stops the session.
            await self._report_runtime_error("startup_reconcile", exc)
        if reconciled_at_startup:
            orphan_places = self._unresolved_places_without_exchange_id()
            if orphan_places:
                # A create that returned no exchange id cannot be retrieved by
                # id, and the local client id was never sent to the venue. Do
                # not bind its (possibly finished) market as if it held a
                # position: an empty book then becomes a retryable transport
                # failure and the supervisor performs three pointless fast
                # restarts. It is also unsafe to clear the intent from one
                # absence snapshot. Keep this process alive and flat while the
                # repeated complete-account proof in `_rest_reconcile`
                # accumulates; the uncertain create is never retried.
                markets = sorted({
                    str(row["market_slug"] or "").strip()
                    for row in orphan_places
                    if str(row["market_slug"] or "").strip()
                })
                raise _OrphanPlaceEvidencePending(
                    "startup found "
                    f"{len(orphan_places)} unresolved PLACE intent(s) without "
                    "an exchange order id; waiting in-process for repeated "
                    "complete account proof with no surviving venue risk"
                    + (f"; markets={','.join(markets)}" if markets else ""))
        if self.config.auto_select_reward_markets:
            recovery_slugs = self._identified_recovery_markets()
            if recovery_slugs:
                self._bind_markets(recovery_slugs)
                # Recovery exists to get out, never to quote. Marking these
                # retired up front lets the pregame and incentive gates be
                # skipped when loading them (see _refresh_contracts for_exit)
                # with no risk of building on a market that has started:
                # `_recovery_slugs` is barred from quoting for the whole
                # session -- the one bar that did NOT become a cooldown --
                # while the reconciler keeps planning its exit.
                for slug in recovery_slugs:
                    self._retired_ever.add(slug)
                    self._recovery_slugs.add(slug)
            elif self.config.recovery_only:
                if (not reconciled_at_startup or not self.ledger.risk_state_known()
                        or not self.ledger.ownership_is_known()
                        or self.ledger.working_orders()
                        or self.ledger.absent_orders()
                        or self.ledger.unresolved_intents()
                        or self._has_open_position()):
                    raise CommissioningRefused("host recovery lacks complete flat proof")
                return "HOST_RECOVERY_FLAT"
            else:
                await self._select_reward_market(now)
                now = self._now()
        # Every bound market is best effort, including slot zero. Requiring all
        # of them would let one unlucky market block startup entirely, and
        # startup failure is the most expensive kind -- nothing quotes at all.
        #
        # Slot zero used to be exempt from that reasoning and refreshed
        # unguarded, which made it the single point of failure the rest of this
        # loop exists to avoid. Observed 2026-08-16T21:36Z: the primary
        # published a book with no levels on either side, `parse_book_response`
        # raised `book is empty on both sides`, and the whole startup exited 2
        # -- a code the supervisor never restarts -- over one dud market out of
        # twenty. About one market in four publishes a one-sided book (see
        # adapters.py), so drawing a bad slot zero is not rare.
        #
        # A market we are RECOVERING is different and still refuses: it holds a
        # position we were restarted to retire, and dropping it would abandon
        # live risk.
        # `_recovery_slugs`, not `_retired_ever`. They are equal here -- at
        # startup nothing else has retired yet -- but `_retired_ever` is a
        # reporting set that now outlives the bar it used to imply, and a
        # control decision must not read from it.
        await self._refresh_bound_books(now)
        if (not self.config.recovery_only and (self._isolated_unexecutable_slugs()
                or self._startup_managed_position_is_scoped(None, self._now()))):
            # One exact recovery owner reserves its slot. Empty/non-open
            # owners remain mutation-free; a readable post-kickoff owner keeps
            # the unchanged exit planner. Use only the remaining configured
            # slots, and never let a failed scan drop position monitoring.
            await self._add_startup_markets_around_reserved_position(
                self._now())
            await self._refresh_bound_books(self._now())
        # Selection/startup book work may span the event guard's 30-second
        # freshness window. Refresh exact parent lifecycle in one bounded batch
        # after the final active tuple is known. This is real venue evidence;
        # no local timestamp is renewed or inferred. Decisions repeat the same
        # guard immediately before planning and sending any entry.
        await self._refresh_event_guard()
        # A singleton private subscription makes the documented position
        # update (which omits marketSlug) attributable without guessing. Whole-
        # account REST reconciliation remains the authoritative safety check.
        await self.private_stream.start(list(self.active_slugs))
        self.gate.on_connect()
        await self.market_stream.start(list(self.active_slugs))
        # Absent orders belong here for the same reason they belong in the
        # `_rest_reconcile` sweep: they are in neither of the other two sets,
        # so without this the reconcile that could clear them never runs at
        # startup. `ControlledTrialStartup` then reads `risk_state_known()`
        # account-wide, finds the absence, and blocks on "exchange order
        # ownership or state is unknown" -- which `_startup_recovery_is_scoped`
        # does not allow, so preflight raises CommissioningRefused and exits 2,
        # a code the supervisor never restarts. One order we lost sight of
        # would have kept the bot from ever starting again.
        # Skipped when the startup reconcile above already ran: it is the same
        # whole-account read, and repeating it at the same clock reading reuses
        # the snapshot event id, which the ledger correctly rejects as a replay
        # with different content. This runs only when that one failed.
        if (not reconciled_at_startup
                and (self.ledger.working_orders(owned_only=True)
                     or self.ledger.unresolved_intents()
                     or self.ledger.absent_orders())):
            await self._rest_reconcile(now)
        try:
            await self._refresh_earnings(now)
        except Exception as exc:
            # Earnings are reporting, not a safety input: they are delayed by
            # days, never authorize an entry or an exit, and nothing below
            # reads them. The run loop already treats a failure here as
            # recoverable; preflight did not, so a venue-side blip refused
            # startup outright. Observed 2026-08-17T03:05Z --
            # `GET /v1/incentives/earnings` returned InternalServerError and
            # the process exited 2, a code the supervisor never restarts, with
            # a flat account and nothing wrong with it.
            self._earnings_observed_at = None
            self._reward_refresh_failed = True
            await self._report_runtime_error("reward_earnings_refresh", exc)
        accepted = ReadinessVerdict(
            "COMMISSIONING_REWARD_ECONOMICS_ENFORCED", True, False, (),
            ("scale remains blocked until attributable paid rewards calibrate",))
        report = await ControlledTrialStartup(
            self.account, self.ledger).reconcile(accepted, observed_at=now)
        settlement_wait_trade = (
            not report.ready
            and self._startup_settlement_wait_is_scoped(
                report.blockers, self._now()))
        managed_position_trade = (
            not report.ready
            and not settlement_wait_trade
            and self._startup_managed_position_is_scoped(
                report.blockers, self._now()))
        recovery_only = (
            not report.ready
            and not settlement_wait_trade
            and not managed_position_trade
            and self._startup_recovery_is_scoped(report.blockers))
        if (not report.ready and not recovery_only
                and not settlement_wait_trade and not managed_position_trade):
            raise CommissioningRefused("; ".join(report.blockers))
        # The startup proof can span several paginated REST calls. Start its
        # freshness window when that proof completed, just like periodic
        # reconciliation, or a slow preflight can arrive at the loop stale.
        self.gate.on_rest_reconciled(self._now())
        self._equity_snapshot = await self.account.fetch_equity_snapshot(
            observed_at=now)
        self.balance = self._equity_snapshot.balance
        self.risk_guard = SessionRiskGuard(
            started_at=now,
            starting_equity=self._equity_snapshot.marked_equity,
            max_duration_seconds=float("inf"),
            max_equity_age_seconds=15,
            hard_stop_loss=self.config.hard_stop_loss_usd,
        )
        # No raw-cash HARD sample: a failed cash-activity proof during startup
        # must not durably latch a withdrawal as trading loss in recovery.
        # Before any order can be placed. `SessionRiskGuard` stamped
        # `starting_equity` from this instant, which measures the process; this
        # re-points it at the ET day's opening equity from the ledger, so a
        # restart inherits the day's drawdown instead of resetting it.
        self._roll_realized_baseline()
        # Raw cash can include a reward or deposit. Prove the non-trading
        # adjustment before the driver exists, so startup can never arm new
        # risk with a reward-masked session-loss sensor.
        await self._refresh_balance(self._now())
        self.driver = ControlledActionDriver(
            self.executor, self.ledger, self.risk_guard,
            new_entry_allowed=lambda: (
                not self.config.recovery_only
                and not self._entry_pause_active()
                and not self._loss_cap_blocks_new_entry(self._now())),
            entry_expiry_policy=self.config.entry_expiry_policy,
            force_exit_before_start_seconds=self.config.force_exit_before_start_seconds,
            strategy=self.config.strategy)
        self._orders_before = self.ledger.query(
            "SELECT COUNT(*) n FROM execution_intents WHERE operation='PLACE'")[0]["n"]
        hard_recovery = recovery_only and self._hard_stop_active()
        if self.config.recovery_only or (recovery_only and not hard_recovery):
            return "RECOVERY_ONLY"
        await self._wait_for_initial_private_state()
        selection_note = ""
        if self._market_selection is not None:
            selection_note = (
                f" Auto-selected {self.market_slug} from "
                f"{self._market_selection.rewarded_executable_markets} rewarded "
                f"executable markets joined from "
                f"{self._market_selection.catalog_markets} buffered event-market "
                "rows after evaluating "
                f"{self._market_selection.books_evaluated} books; "
                f"{self._market_selection.unsupported_programs} programs used "
                "an unsupported or undocumented lifecycle and remained read-only.")
        if settlement_wait_trade:
            empty_count = len(self._settlement_wait_slugs)
            inactive_count = len(self._inactive_market_wait_slugs)
            if inactive_count == 0:
                selection_note += (
                    f" {empty_count} exact post-kickoff position(s) have no "
                    "executable book and remain quarantined for "
                    "settlement/book recovery; each reserves one configured "
                    "slot and its full portfolio exposure.")
            else:
                selection_note += (
                    f" {empty_count + inactive_count} exact position(s) remain "
                    "mutation-free while unexecutable "
                    f"(empty_book={empty_count}, not_open={inactive_count}); "
                    "each reserves one configured slot and its full portfolio "
                    "exposure.")
        elif managed_position_trade:
            selection_note += (
                " One exact post-kickoff recovery position remains barred "
                "from building and under the unchanged exit manager; it "
                "reserves one configured slot and its full portfolio exposure.")
        if hard_recovery:
            selection_note += (
                " Persisted HARD recovery: entries remain paused while owned "
                "orders/positions evacuate. Next-day entry requires fresh flat proof.")
        # Built from config, never from literals. This announced "one market,
        # one contract per quote, $5 soft stop, $15 hard stop" long after every
        # one of those had changed -- four wrong facts as the operator's first
        # sight of each run. Nothing tied the message to the numbers, which is
        # the actual defect; the test does that now.
        gate = ("enforced" if self.config.enforce_reward_cost_gate
                else "advisory")
        delivery = await self.notifier.send_async(
            f"🟠 Liquidity Bot LIVE commissioning started: up to "
            f"{self.config.max_markets} markets, {self.config.quote_size} "
            f"contracts per quote, reward-versus-exit economics {gate}, "
            f"${self.config.hard_stop_loss_usd} hard stop, "
            f"${self.config.execution_fee_observation_usd} ET-day "
            "execution-fee telemetry reference, "
            "operator-controlled stop; pregame and loss gates remain active."
            + selection_note)
        if not getattr(delivery, "sent", False):
            raise CommissioningRefused(
                "Discord startup alert delivery failed before order entry")
        return "HARD_RECOVERY" if hard_recovery else "TRADE"
