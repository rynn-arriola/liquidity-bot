"""Stream reconnect, bound-market recovery and venue-outage waiting for the commissioning runner.

Moved verbatim from ``live_commissioning.CommissioningRunner`` (pure
relocation, no behavior change). Methods run on the runner instance via
inheritance, so every ``self`` attribute and override resolves as before.
"""

from __future__ import annotations

from .api_diagnostics import api_workflow
import asyncio
import traceback
from contextlib import nullcontext
from datetime import timedelta
from typing import Any
from .commissioning import CommissioningRefused, CommissioningUnavailable
from .errors import AdapterError, ApiAdmissionDeferred, MarketStreamUnavailable, RuntimeInvariantViolation
from .execution_ledger import LedgerConflict
from .streams import STREAM_SILENCE_TIMEOUT_SECONDS
from .runner_shared import MAINTENANCE_FAILURE_THRESHOLD, MAINTENANCE_POLL_SECONDS, MAINTENANCE_STABLE_READS, MAX_BOUND_STREAM_RECOVERY_ATTEMPTS, MAX_MARKET_STREAM_RECOVERY_ATTEMPTS, MAX_PRIVATE_STREAM_RECOVERY_ATTEMPTS, _CancellationEvidencePending, _OrphanPlaceEvidencePending


class StreamRecoveryMixin:

    def _log_blind_quarantine_failure(self, component: str, exc: Exception) -> None:
        """Name why the best-effort blind cancellation pass failed.

        The pass stays best-effort (recovery continues exactly as before), but a
        failure here can leave quotes resting while no private listener works,
        and it used to be swallowed silently. At most one line per component per
        minute; the count says how many were folded in. Logging cannot raise.
        """

        try:
            now = self._now()
            state = getattr(self, "_blind_quarantine_log", None)
            if state is None:
                state = self._blind_quarantine_log = {}
            last, folded = state.get(component, (None, 0))
            if last is not None and 0 <= (now - last).total_seconds() < 60:
                state[component] = (last, folded + 1)
                return
            state[component] = (now, 0)
            print(f"[blind-quarantine-failed] at={now.isoformat()} "
                  f"component={component} folded={folded} "
                  f"error={type(exc).__name__}: {self._redacted_error_detail(exc)}",
                  flush=True)
        except Exception:
            pass

    def _identified_recovery_markets(self) -> tuple[str, ...]:
        """Return every ledger market that may still require retirement."""

        slugs = {
            str(row["market_slug"])
            for row in self.ledger.working_orders(owned_only=True)
        }
        slugs.update(
            str(row["market_slug"])
            for row in self.ledger.unresolved_intents()
        )
        slugs.update(
            str(row["market_slug"])
            for row in self.ledger.query(
                "SELECT market_slug FROM execution_positions "
                "WHERE CAST(net_qty AS REAL)!=0")
        )
        slugs.discard("")
        # Recover the whole authorized set, not a single market. This raised
        # when leftover state spanned more than one market, and
        # CommissioningRefused exits 2, which the supervisor never restarts --
        # so the bot stayed down. Written when the runner held one market at a
        # time; with eight quoting, state in several markets after any hard
        # stop is the expected case rather than an anomaly. Observed
        # 2026-08-14T23:03Z, down until an operator noticed.
        #
        # More markets than the authorization is still refused: that is state
        # this session could not have created, so it is not ours to retire.
        if len(slugs) > self.config.max_markets:
            raise CommissioningRefused(
                f"automatic recovery found state in {len(slugs)} markets, "
                f"above the authorized {self.config.max_markets}")
        return tuple(sorted(slugs))

    def _startup_recovery_is_scoped(self, blockers: tuple[str, ...]) -> bool:
        """Allow restart only to retire this bot's identified risk.

        Scoped to the whole active set, not the primary market. Checking only
        slot zero refused recovery whenever a crash left a position on any
        other slot, which raises CommissioningRefused and exits 2 -- a code the
        supervisor never restarts. The bot would stay down for exactly the
        state it was built to recover. See invariant 58: the authorized scope
        is the active set.
        """

        allowed = {
            "shared account has open orders",
            "shared account has nonzero positions",
            "execution ledger has unresolved intents",
        }
        if not blockers or not set(blockers).issubset(allowed):
            return False
        authorized = set(self.active_slugs)
        if not authorized:
            return False
        working = self.ledger.working_orders()
        if any(str(row["client_order_id"]).startswith("venue:")
               or row["market_slug"] not in authorized for row in working):
            return False
        nonzero = self.ledger.query(
            "SELECT market_slug FROM execution_positions "
            "WHERE CAST(net_qty AS REAL)!=0")
        if any(row["market_slug"] not in authorized for row in nonzero):
            return False
        for row in nonzero:
            try:
                basis = self.ledger.inventory_basis(row["market_slug"])
            except Exception:
                return False
            if basis is None:
                # A venue position that merely happens to be in the selected
                # market is not bot-owned recovery state. Persisted fills must
                # reconstruct its exact quantity before shutdown may mutate it.
                return False
        unresolved = self.ledger.unresolved_intents()
        if any(row["market_slug"] not in authorized
               or not row["exchange_order_id"] for row in unresolved):
            return False
        return bool(working or nonzero)

    def _preserve_position_orders_during_bound_recovery(
        self, slug: str,
    ) -> bool:
        """Whether this account source can prove one exact reducer is safe.

        The default private source returns false: losing/rebinding its account
        transport retains the existing global quarantine. A source with fresh
        independent account proof may override this for exact reducers only.
        """

        return False

    def _bound_recovery_position_preemption_enabled(self) -> bool:
        """Whether shared-feed recovery has independent account proof."""

        return False

    async def _service_bound_recovery_positions(self) -> str | None:
        """Optional serial checkpoint for already-owned urgent exposure."""

        return None

    def _request_market_stream_rebind(self, error: MarketStreamUnavailable) -> None:
        # Only the outer owner can transfer task-held private evidence before
        # reconnecting. Yield to it without per-market retirement/proof fanout.
        self.gate.on_disconnect()
        self._streams_need_rebind = True
        if self._pending_market_stream_error is None:
            self._pending_market_stream_error = error

    async def _consume_ready_stream_tasks(
        self,
        market_task: asyncio.Task[Any],
        private_task: asyncio.Task[Any],
    ) -> tuple[asyncio.Task[Any], asyncio.Task[Any], str | None]:
        """Apply completed stream work and drain data queued behind it.

        Called both when the loop wakes and after serial REST service. A task
        can consume the first event while service is in progress; draining the
        adapter without applying that held result first would reorder private
        mutations. This method preserves that order, then catches up fully
        before the decision gate is evaluated.
        """

        if self._streams_need_rebind:
            await self._settle_stream_readers(
                market_task, private_task, recovery_pending=True)
            error = (self._pending_market_stream_error
                     or AdapterError("stream bind requires recovery"))
            stop = await self._recover_bound_streams(error)
            if stop:
                return market_task, private_task, stop
            self._pending_market_stream_error = None
            market_task = asyncio.create_task(self.market_stream.next_book())
            private_task = asyncio.create_task(self.private_stream.next_event())
            return market_task, private_task, None

        if market_task.done():
            try:
                self._accept_book(market_task.result())
                drain_books = getattr(self.market_stream, "drain_books", None)
                if callable(drain_books):
                    for book in drain_books():
                        self._accept_book(book)
            except Exception as exc:
                stop = await self._recover_market_stream(
                    exc, private_task=private_task)
                if stop:
                    return market_task, private_task, stop
                private_task = asyncio.create_task(self.private_stream.next_event())
            market_task = asyncio.create_task(self.market_stream.next_book())

        if private_task.done():
            try:
                await self._observe_private(private_task.result(), self._now())
                # Private updates cannot be coalesced safely (fills and order
                # transitions are ordered), so drain in wire order.
                await self._drain_private_events()
            except Exception as exc:
                stop = await self._recover_private_stream(exc)
                if stop:
                    return market_task, private_task, stop
            private_task = asyncio.create_task(self.private_stream.next_event())

        # A complete REST reconcile can keep the execution gate fresh while a
        # WebSocket shard has silently died. Check transport liveness
        # independently so REST is only a short backstop, never proof that all
        # three private shards and the market connection still deliver data.
        now = self._now()
        liveness_errors: dict[str, Exception] = {}
        for name, stream in (
                ("market", self.market_stream),
                ("private", self.private_stream)):
            assert_live = getattr(stream, "assert_live", None)
            if not callable(assert_live):
                continue
            try:
                assert_live(
                    now=now,
                    max_silence_seconds=STREAM_SILENCE_TIMEOUT_SECONDS)
            except Exception as exc:
                liveness_errors[name] = exc
        if len(liveness_errors) == 2:
            await self._settle_stream_readers(
                market_task, private_task, recovery_pending=True)
            combined = AdapterError(
                "both WebSockets exceeded the independent silence limit: "
                f"market={liveness_errors['market']}; "
                f"private={liveness_errors['private']}")
            stop = await self._recover_bound_streams(combined)
            if stop:
                return market_task, private_task, stop
            market_task = asyncio.create_task(self.market_stream.next_book())
            private_task = asyncio.create_task(self.private_stream.next_event())
        elif "market" in liveness_errors:
            market_task.cancel()
            await asyncio.gather(market_task, return_exceptions=True)
            stop = await self._recover_market_stream(
                liveness_errors["market"], private_task=private_task)
            if stop:
                return market_task, private_task, stop
            private_task = asyncio.create_task(self.private_stream.next_event())
            market_task = asyncio.create_task(self.market_stream.next_book())
        elif "private" in liveness_errors:
            await self._settle_stream_readers(
                None, private_task, recovery_pending=True)
            stop = await self._recover_private_stream(liveness_errors["private"])
            if stop:
                return market_task, private_task, stop
            private_task = asyncio.create_task(self.private_stream.next_event())

        return market_task, private_task, None

    async def _settle_stream_readers(
        self, market_task, private_task, *, recovery_pending: bool = False,
    ) -> None:
        """Release readers without losing their already-consumed evidence.

        Callers replace these readers only after this method completes. It
        never reconnects or submits orders; normal recovery/shutdown owns that.
        A market error cannot prevent projection of a returned private event.
        """
        named = [(name, task) for name, task in (
            ("market", market_task), ("private", private_task)) if task is not None]
        for _, task in named:
            if not task.done():
                task.cancel()
        results = await asyncio.gather(
            *(task for _, task in named), return_exceptions=True)
        errors = []
        for (name, _), result in zip(named, results):
            if isinstance(result, asyncio.CancelledError):
                continue
            if isinstance(result, BaseException):
                errors.append(result)
                continue
            try:
                if result is not None:
                    if name == "private":
                        await self._observe_private(result, self._now())
                    else:
                        self._accept_book(result)
            except Exception as exc:
                errors.append(exc)
        try:
            await self._drain_private_events()
        except Exception as exc:
            errors.append(exc)
        if errors:
            self.gate.on_disconnect()
            if not recovery_pending:
                self._streams_need_rebind = True
            for exc in errors:
                if isinstance(exc, (LedgerConflict, RuntimeInvariantViolation)):
                    self.gate.on_reconciliation_conflict()
                    raise exc
                if not isinstance(exc, Exception):
                    raise exc
            await self._report_runtime_error("stream_reader_teardown", errors[0])

    @api_workflow("stream_bind")
    async def _open_streams_for_active_market(
        self, *, strict_order_markets: frozenset[str] | None = None,
    ) -> None:
        """Bind fresh public/private streams and re-prove account state.

        Normal active-set changes remain account-wide strict. Recovery may
        pass an empty set after it has already requested cancellation for all
        known quotes: fresh account snapshots then restore the session while
        each unresolved order's existing per-market gate contains its owner.
        Incremental top-up is the other bounded exception: it first completes
        an account-wide strict proof while the old scope and streams remain
        intact, then uses this scoped read only for the socket handoff.
        """

        # Close what we are about to drop. `_rotate_reward_market_inner` closes
        # the old pair itself, but the top-up path did not, so every top-up
        # leaked a private and a market socket -- four top-ups in the
        # 2026-08-16 session alone, on an account whose private feed is already
        # split across several connections to stay under the per-connection
        # subscription cap. Doing it here covers every caller. Best-effort: a
        # socket we are discarding must never be able to fail the bind that
        # replaces it, and closing an already-closed stream is a no-op.
        for stale in (self.private_stream, self.market_stream):
            if stale is None:
                continue
            try:
                await stale.close()
            except Exception:
                traceback.print_exc()
        private = self.private_stream_factory(self.client)
        market = self.market_stream_factory(self.client)
        self.private_stream = private
        self.market_stream = market
        try:
            await private.start(list(self.active_slugs))
            self.gate.on_connect()
            await market.start(list(self.active_slugs))
            await self._rest_reconcile(
                self._now(), strict_order_markets=strict_order_markets)
            await self._wait_for_initial_private_state()
        except Exception:
            self.gate.on_disconnect()
            await private.close()
            await market.close()
            # Both attributes now point at closed streams, and the gate reads
            # disconnected. Neither `_recover_private_stream` nor
            # `_recover_market_stream` will fire on their own: those trigger
            # when a `next_*` task RAISES, and reading a stream we closed
            # deliberately never produced that exception -- the 2026-08-16
            # session logged no `component=private_stream` line at all while
            # stuck. The run loop must be told explicitly.
            self._streams_need_rebind = True
            raise
        self._streams_need_rebind = False

    @api_workflow("private_stream_recovery")
    async def _recover_private_stream(self, initial: Exception) -> str | None:
        """Stay alive but dormant until private state is proven again."""

        # A heartbeat-silent shard can be replaced under an overlapping
        # listener without sacrificing every resting order. The decision
        # coroutine owns the loop here, so no new mutation can occur. Every
        # replacement first proves that it is receiving private state
        # with its account-balance delivery. The venue can omit an order EOF
        # for an empty scoped subscription, so buffered order pages are useful
        # but optional. A complete REST balance/order/position proof bridges
        # the old connection's gap. Confirmed read throttles receive a slower,
        # bounded retry while orders and the decision freeze are preserved.
        # Any other or exhausted proof falls through to the older global
        # cancel/quarantine recovery below.
        repair_stale = getattr(self.private_stream, "repair_stale_shards", None)
        if (callable(repair_stale)
                and "private WebSocket shard silence:" in str(initial)):
            await self._report_runtime_error("private_stream", initial, 1)
            try:
                self.gate.on_reconciliation_conflict()
                scopes = await repair_stale(
                    now=self._now(),
                    max_silence_seconds=STREAM_SILENCE_TIMEOUT_SECONDS)
                # A heartbeat may arrive after assert_live raises but before
                # this frozen recovery coroutine re-checks the pool. An empty
                # repair set means the shard recovered; it does not justify
                # canceling the whole slate. The account-wide REST proof and
                # final liveness gate below are still mandatory.
                await self._drain_private_events()
                await self._retry_private_handoff_read(
                    "account_state", lambda: self._rest_reconcile(
                        self._now(), tag="private-shard-handoff",
                        strict_order_markets=frozenset()))
                # The WebSocket balance deliveries above prove each new socket
                # is live; this final account-global REST read proves a current
                # decision balance after any slower account-state retry.
                await self._retry_private_handoff_read(
                    "balance", lambda: self._refresh_balance(self._now()))
                await self._drain_private_events()
                self.private_stream.assert_live(
                    now=self._now(),
                    max_silence_seconds=STREAM_SILENCE_TIMEOUT_SECONDS)
                if not self.gate.session_ready(
                        self.ledger, now=self._now()):
                    raise CommissioningUnavailable(
                        "private shard handoff did not restore fresh account state")
                repaired_markets = sum(
                    len(scope) for scope in scopes if scope is not None)
                print(
                    f"[private-shard-recovery] at={self._now().isoformat()} "
                    f"resumed=yes shards={len(scopes)} "
                    f"markets={repaired_markets} cancels=0 "
                    f"working={len(self.ledger.working_orders(owned_only=True))}",
                    flush=True,
                )
                await self.notifier.send_async(
                    "✅ Liquidity Bot recovered the silent private WebSocket "
                    f"shard(s), re-proved the account, and preserved "
                    f"resting orders across {repaired_markets} market(s).",
                    channel="errors",
                )
                self._runtime_error_counts.pop("private_stream", None)
                return None
            except RuntimeInvariantViolation as invariant:
                await self._report_runtime_error(
                    "private_stream_terminal", invariant)
                return f"critical:{invariant.code}"
            except Exception as repair_exc:
                print(
                    "[private-shard-recovery] overlap repair failed; "
                    "falling back to global quarantine: "
                    f"{type(repair_exc).__name__}: {repair_exc}",
                    flush=True,
                )
                initial = repair_exc

        exc = initial
        attempt = 0
        venue_failure_streak = 0
        self.gate.on_disconnect()
        while True:
            if await self._account_source_recovery_checkpoint():
                return None
            attempt += 1
            stream_listening = False
            await self._report_runtime_error("private_stream", exc, attempt)
            try:
                try:
                    await self.private_stream.close()
                except Exception:
                    traceback.print_exc()
                try:
                    await self._refresh_balance(self._now())
                except Exception as balance_exc:
                    print(
                        "[runtime-recovery] balance refresh failed: "
                        f"{type(balance_exc).__name__}: {balance_exc}",
                        flush=True,
                    )
                stop = self._stop_reason(self._now())
                if stop:
                    return stop
                stream = self.private_stream_factory(self.client)
                self.private_stream = stream
                await stream.start(list(self.active_slugs))
                stream_listening = True
                self.gate.on_connect()
                # Listen before canceling. The private order stream is the
                # venue's authoritative terminal-outcome channel; canceling the
                # whole slate before this start created every terminal event in
                # a visibility gap and forced repair onto GET-by-id, which can
                # return NotFound for minutes. No decision can run while this
                # recovery coroutine owns the loop, so starting the listener
                # first cannot authorize new risk.
                await self._quarantine_working_orders(require_terminal=False)
                await self._wait_for_initial_private_state()
                await self._drain_private_events()
                ambiguous_markets = sorted({
                    str(row["market_slug"])
                    for row in (
                        *self.ledger.unresolved_intents(),
                        *self.ledger.absent_orders(),
                    )
                    if str(row["market_slug"] or "").strip()
                })
                if ambiguous_markets:
                    print(
                        f"[private-recovery] at={self._now().isoformat()} "
                        f"resumed=yes scoped_ambiguous={len(ambiguous_markets)} "
                        f"markets={','.join(ambiguous_markets)}",
                        flush=True,
                    )
                await self.notifier.send_async(
                    "✅ Liquidity Bot private stream recovered; REST and fresh "
                    "private state passed. "
                    + (
                        f"Quoting may resume on unaffected markets; "
                        f"{len(ambiguous_markets)} market(s) remain scoped "
                        "until terminal order proof."
                        if ambiguous_markets else "Quoting may resume."
                    ))
                self._runtime_error_counts.pop("private_stream", None)
                return None
            except RuntimeInvariantViolation as invariant:
                await self._report_runtime_error(
                    "private_stream_terminal", invariant)
                return f"critical:{invariant.code}"
            except Exception as recovery_exc:
                self.gate.on_disconnect()
                # If the replacement stream itself could not start, quotes are
                # still blind. Preserve the old fail-safe response: make one
                # best-effort cancellation pass before backing off. This branch
                # may miss terminal events, but it is reached only when there is
                # no working private listener to receive them.
                if not stream_listening:
                    try:
                        await self._quarantine_working_orders(
                            require_terminal=False)
                    except Exception as quarantine_exc:
                        self._log_blind_quarantine_failure(
                            "private_stream", quarantine_exc)
                exc = recovery_exc
                venue_failure_streak = (
                    venue_failure_streak + 1
                    if self._is_transient_recovery_error(recovery_exc) else 0)
                stop = self._stop_reason(self._now())
                if stop:
                    return stop
                if attempt >= MAX_PRIVATE_STREAM_RECOVERY_ATTEMPTS:
                    if venue_failure_streak >= MAINTENANCE_FAILURE_THRESHOLD:
                        stop = await self._wait_for_venue_recovery(
                            "private_stream", recovery_exc)
                        if stop:
                            return stop
                        attempt = 0
                        venue_failure_streak = 0
                        continue
                    exhausted = AdapterError(
                        "private stream recovery exhausted "
                        f"{MAX_PRIVATE_STREAM_RECOVERY_ATTEMPTS} attempts: "
                        f"{type(recovery_exc).__name__}: {recovery_exc}")
                    await self._report_runtime_error(
                        "private_stream_terminal", exhausted, attempt)
                    # `main` maps runner_failure to restart only after shutdown
                    # proves the whole account flat. Unresolved/absent orders
                    # make `result.flat` false and therefore exit 3 instead.
                    return "runner_failure:private_stream_recovery_exhausted"
                await self.sleep(min(2 ** min(attempt - 1, 5), 30))

    @api_workflow("market_stream_recovery")
    async def _recover_market_stream(
        self, initial: Exception, *, private_task: asyncio.Task[Any] | None = None,
    ) -> str | None:
        """Reconnect market data after canceling quotes made on the old book."""

        # Transfer private consumption before waiting outside the main loop.
        # A completed task owns the oldest event; apply it before draining the
        # socket. A pending read is cancelled/awaited before taking over, so it
        # cannot steal the confirmation the recovery needs. The caller starts
        # one replacement reader only after this coroutine returns.
        if private_task is not None:
            if not private_task.done():
                private_task.cancel()
            try:
                event = await private_task
            except asyncio.CancelledError:
                if asyncio.current_task().cancelling():
                    raise
                pass
            except Exception as private_exc:
                # Both transports failed. Re-establish private visibility first
                # through the existing strict account/scope recovery path.
                return await self._recover_bound_streams(private_exc)
            else:
                await self._observe_private(event, self._now())
        exc = initial
        attempt = 0
        venue_failure_streak = 0
        while True:
            attempt += 1
            await self._report_runtime_error("market_stream", exc, attempt)
            try:
                try:
                    await self.market_stream.close()
                except Exception:
                    traceback.print_exc()
                try:
                    await self._refresh_balance(self._now())
                except Exception as balance_exc:
                    print(
                        "[runtime-recovery] balance refresh failed: "
                        f"{type(balance_exc).__name__}: {balance_exc}",
                        flush=True,
                    )
                stop = self._stop_reason(self._now())
                if stop:
                    return stop
                await self._quarantine_working_orders(drain_private_events=True)
                stream = self.market_stream_factory(self.client)
                self.market_stream = stream
                await stream.start(list(self.active_slugs))
                self._accept_book(await stream.next_book(timeout=15))
                await self.notifier.send_async(
                    "✅ Liquidity Bot market stream recovered with a fresh full book. "
                    "Quoting may resume.")
                self._runtime_error_counts.pop("market_stream", None)
                return None
            except RuntimeInvariantViolation as invariant:
                await self._report_runtime_error(
                    "market_stream_terminal", invariant)
                return f"critical:{invariant.code}"
            except _OrphanPlaceEvidencePending as pending:
                # An uncertain no-id create is neither a failed stream attempt
                # nor permission to resend. Complete reconciliation above keeps
                # proving account state; wait in-process until the conservative
                # proof terminalizes it. HARD/operator stop checks still run on
                # every pass, and this wait never creates venue risk.
                exc = pending
                attempt = 0
                venue_failure_streak = 0
                await self._report_runtime_error(
                    "market_stream_orphan_place_evidence", pending)
                stop = self._stop_reason(self._now())
                if stop:
                    return stop
                await self.sleep(self._recovery_delay(
                    "market_stream_orphan_place_evidence",
                    base=5.0,
                    cap=60.0,
                ))
            except _CancellationEvidencePending as pending:
                # During a Retail maintenance episode the cancel endpoint can
                # acknowledge a request while later authoritative order reads
                # still report OPEN.  That is neither permission to resume nor
                # a failed market-stream reconnect.  Consuming the bounded
                # reconnect budget here exited the runner non-flat after 12
                # attempts and abandoned every resting order.  Stay in the
                # same no-entry recovery loop until a terminal order state (or
                # an operator/risk stop) arrives; each pass still performs the
                # existing positive reads and durable cancel retry protocol.
                exc = pending
                attempt = 0
                venue_failure_streak = 0
                await self._report_runtime_error(
                    "market_stream_cancel_evidence", pending)
                stop = self._stop_reason(self._now())
                if stop:
                    return stop
                await self.sleep(self._recovery_delay(
                    "market_stream_cancel_evidence",
                    base=5.0,
                    cap=60.0,
                ))
            except Exception as recovery_exc:
                exc = recovery_exc
                venue_failure_streak = (
                    venue_failure_streak + 1
                    if self._is_transient_recovery_error(recovery_exc) else 0)
                stop = self._stop_reason(self._now())
                if stop:
                    return stop
                if attempt >= MAX_MARKET_STREAM_RECOVERY_ATTEMPTS:
                    if venue_failure_streak >= MAINTENANCE_FAILURE_THRESHOLD:
                        stop = await self._wait_for_venue_recovery(
                            "market_stream", recovery_exc)
                        if stop:
                            return stop
                        attempt = 0
                        venue_failure_streak = 0
                        continue
                    exhausted = AdapterError(
                        "market stream recovery exhausted "
                        f"{MAX_MARKET_STREAM_RECOVERY_ATTEMPTS} attempts: "
                        f"{type(recovery_exc).__name__}: {recovery_exc}")
                    await self._report_runtime_error(
                        "market_stream_terminal", exhausted, attempt)
                    return "runner_failure:market_stream_recovery_exhausted"
                await self.sleep(min(2 ** min(attempt - 1, 5), 30))

    @api_workflow("bound_stream_recovery")
    async def _recover_bound_streams(self, initial: Exception) -> str | None:
        """Rebind streams after a failed bind or mid-pass shared failure.

        `_recover_private_stream` and `_recover_market_stream` each repair one
        side and assume the other is healthy. A failed
        `_open_streams_for_active_market` closes BOTH, so neither is usable and
        the whole bind has to be redone -- which is exactly what that method
        does, so this retries it rather than reimplementing the sequence.

        Rebinds account visibility before quarantine. Private account mode
        keeps the strict whole-slate quarantine because a fill may have landed
        unseen. REST account mode already has fresh independent account proof,
        so it may preserve only one exact reducer and service due exposure
        between flat-order cancellations. In both modes the recovery coroutine
        owns the loop: no new-risk decision can run before quarantine and
        fresh-state proof finish.
        """

        exc = initial
        attempt = 0
        venue_failure_streak = 0
        await self.notifier.send_async(
            "⚠️ Liquidity Bot is recovering shared stream coverage. "
            "New entries are paused while streams and account state are "
            "revalidated. Retrying.")
        while True:
            attempt += 1
            await self._report_runtime_error("bound_streams", exc, attempt)
            try:
                try:
                    await self._refresh_balance(self._now())
                except Exception as balance_exc:
                    print(
                        "[runtime-recovery] balance refresh failed: "
                        f"{type(balance_exc).__name__}: {balance_exc}",
                        flush=True,
                    )
                stop = self._stop_reason(self._now())
                if stop:
                    return stop
                # Restore private visibility before creating cancellation
                # events, for the same reason as `_recover_private_stream`.
                # `_open_streams_for_active_market` cannot return control to a
                # decision loop, so this order does not create a quote window.
                await self._open_streams_for_active_market(
                    strict_order_markets=frozenset())
                prioritize_positions = (
                    self._bound_recovery_position_preemption_enabled())
                stop = await self._quarantine_working_orders(
                    require_terminal=False,
                    # Opening REST-backed streams just performed a complete
                    # account reconcile. Do not repeat that potentially slow
                    # read before checking already-owned exposure.
                    account_state_already_reconciled=prioritize_positions,
                    preempt_positions=prioritize_positions,
                )
                if isinstance(stop, str) and stop:
                    return stop
                await self._drain_private_events()
                ambiguous_markets = sorted({
                    str(row["market_slug"])
                    for row in (
                        *self.ledger.unresolved_intents(),
                        *self.ledger.absent_orders(),
                    )
                    if str(row["market_slug"] or "").strip()
                })
                if ambiguous_markets:
                    print(
                        f"[bound-stream-recovery] at={self._now().isoformat()} "
                        f"resumed=yes scoped_ambiguous={len(ambiguous_markets)} "
                        f"markets={','.join(ambiguous_markets)}",
                        flush=True,
                    )
                await self.notifier.send_async(
                    f"✅ Liquidity Bot {self.SOURCE_REBOUND_DESCRIPTION}; "
                    f"{self.ACCOUNT_STATE_DESCRIPTION} passed. "
                    + (
                        "Quoting may resume on unaffected markets; "
                        f"{len(ambiguous_markets)} market(s) remain scoped "
                        "until terminal order proof."
                        if ambiguous_markets else "Quoting may resume."
                    ),
                    channel="errors",
                )
                self._runtime_error_counts.pop("bound_streams", None)
                return None
            except RuntimeInvariantViolation as invariant:
                await self._report_runtime_error(
                    "bound_streams_terminal", invariant)
                return f"critical:{invariant.code}"
            except Exception as recovery_exc:
                if not self.gate.connected:
                    try:
                        await self._quarantine_working_orders(
                            require_terminal=False)
                    except Exception as quarantine_exc:
                        self._log_blind_quarantine_failure(
                            "bound_streams", quarantine_exc)
                exc = recovery_exc
                venue_failure_streak = (
                    venue_failure_streak + 1
                    if self._is_transient_recovery_error(recovery_exc) else 0)
                stop = self._stop_reason(self._now())
                if stop:
                    return stop
                if attempt >= MAX_BOUND_STREAM_RECOVERY_ATTEMPTS:
                    if venue_failure_streak >= MAINTENANCE_FAILURE_THRESHOLD:
                        stop = await self._wait_for_venue_recovery(
                            "bound_streams", recovery_exc)
                        if stop:
                            return stop
                        attempt = 0
                        venue_failure_streak = 0
                        continue
                    exhausted = AdapterError(
                        "bound stream recovery exhausted "
                        f"{MAX_BOUND_STREAM_RECOVERY_ATTEMPTS} attempts: "
                        f"{type(recovery_exc).__name__}: {recovery_exc}")
                    await self._report_runtime_error(
                        "bound_streams_terminal", exhausted, attempt)
                    # Shutdown determines restartability only after proving the
                    # whole account flat. This cap never converts ambiguity or
                    # inventory into a restartable result.
                    return "runner_failure:bound_stream_recovery_exhausted"
                await self.sleep(min(2 ** min(attempt - 1, 5), 30))

    @classmethod
    def _is_transient_recovery_error(cls, exc: Exception) -> bool:
        """Typed throttling is recoverable, but is not a maintenance claim."""

        current: BaseException | None = exc
        seen: set[int] = set()
        throttled = False
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            if isinstance(current, RuntimeInvariantViolation):
                return False
            if isinstance(current, ApiAdmissionDeferred):
                throttled = True
            if (isinstance(current, AdapterError)
                    and current.status_code == 429):
                throttled = True
            current = current.__cause__
        return throttled or cls._is_venue_unavailable(exc)

    async def _wait_for_venue_recovery(
        self, component: str, initial: Exception, *, planned: bool = False,
    ) -> str | None:
        """Hold dormant until authenticated venue reads are stably healthy.

        Probes are intentionally read-only and are not applied as ledger
        snapshots.  In particular, an order missing from these observations is
        never interpreted as cancelled.  The component-specific recovery then
        performs the normal quarantine, reconciliation and fresh-stream gates
        before the run loop can quote again.
        """

        self._transport_recovery_component = component
        self._publish_active_scope_snapshot(status="ENTRY_PAUSED")
        pause_tag = "maintenance-pause" if planned else "venue-recovery-pause"
        health_label = "maintenance" if planned else "venue recovery"
        print(
            f"[{pause_tag}] at={self._now().isoformat()} "
            f"component={component} state=entered "
            f"reason={type(initial).__name__}:{initial}",
            flush=True,
        )
        if planned:
            await self.notifier.send_async(
                "🛠️ Liquidity Bot completed the scheduled maintenance delay. "
                "New quoting remains paused while read-only account health "
                "passes three times over about two minutes; no order absence "
                "is treated as cancellation.")
        else:
            await self.notifier.send_async(
                "🛠️ Liquidity Bot venue recovery pause: waiting for API/stream recovery "
                f"in {component}. New quoting is paused; no "
                "order absence is treated as cancellation. Read-only account "
                "health must pass three times over about two minutes before the "
                "normal reconciliation and stream gates are retried.",
                critical=True,
            )
        stable = 0
        while stable < MAINTENANCE_STABLE_READS:
            stop = self._maintenance_stop_reason()
            if stop:
                return stop
            try:
                observed_at = self._now()
                orders = await self.account.fetch_open_orders(
                    observed_at=observed_at)
                positions = await self.account.fetch_positions(
                    observed_at=observed_at)
                balance = await self.account.fetch_balance(
                    observed_at=observed_at)
                stable += 1
                print(
                    f"[{pause_tag}] at={self._now().isoformat()} "
                    f"component={component} health=success "
                    f"stable={stable}/{MAINTENANCE_STABLE_READS} "
                    f"open_orders={len(orders)} positions={len(positions)} "
                    f"cash={balance.current_balance} "
                    f"reported_asset_notional={balance.asset_notional}",
                    flush=True,
                )
            except RuntimeInvariantViolation:
                raise
            except Exception as probe_exc:
                # A 429 is not evidence of maintenance, but inside an existing
                # no-entry hold it is a failed health sample, not permission to
                # terminate and abandon recovery. Never count it as success.
                if not self._is_transient_recovery_error(probe_exc):
                    await self._report_runtime_error(
                        "maintenance_probe_terminal", probe_exc)
                    raise
                stable = 0
                attempt = self._runtime_error_counts.get(
                    "maintenance_probe", 0) + 1
                await self._report_runtime_error(
                    "maintenance_probe", probe_exc, attempt=attempt,
                    # Preserve the first diagnosis and a periodic full sample,
                    # but do not print the identical stack every minute of a
                    # long venue outage. The concise attempt line and health
                    # marker remain present for every probe.
                    include_traceback=(attempt == 1 or attempt % 30 == 0))
                print(
                    f"[{pause_tag}] at={self._now().isoformat()} "
                    f"component={component} health=failed stable=0/"
                    f"{MAINTENANCE_STABLE_READS} "
                    f"reason={type(probe_exc).__name__}:{probe_exc}",
                    flush=True,
                )
            if stable < MAINTENANCE_STABLE_READS:
                print(
                    f"[recovery-wait] component={component} "
                    f"next_probe_at={(self._now() + timedelta(seconds=MAINTENANCE_POLL_SECONDS)).isoformat()} "
                    f"stable={stable}/{MAINTENANCE_STABLE_READS}", flush=True)
                stop = await self._maintenance_sleep(MAINTENANCE_POLL_SECONDS)
                if stop:
                    return stop
        await self.notifier.send_async(
            f"✅ Liquidity Bot {health_label} health gate passed three complete "
            "authenticated account reads. Normal stream recovery and exact "
            "order/position reconciliation are resuming; quoting remains "
            "blocked until those existing gates pass.")
        print(
            f"[{pause_tag}] at={self._now().isoformat()} "
            f"component={component} state=health_gate_passed",
            flush=True,
        )
        self._runtime_error_counts.pop("maintenance_probe", None)
        self._transport_recovery_component = None
        return None

    async def _recover_strategy_state(self, reason: str, slug: str) -> str | None:
        """Quarantine an unexpected strategy state without ending the process.

        The refresh must target the market that actually went stale. Refreshing
        the primary would leave a stale book on any other slot untouched, so it
        would report the same condition forever and never quote again.
        """

        component = f"strategy_state:{slug}"
        # A stale read is a decision refusal, but if the immediate REST refresh
        # succeeds it is not an operational error. Reporting before the refresh
        # produced 8,722 attempt=1 "errors" in one healthy day even though the
        # recursive plan used a fresh book milliseconds later. Count and report
        # only a stale condition that the recovery itself cannot clear.
        if reason != "book_stale":
            await self._report_runtime_error(
                component, AdapterError(str(reason or "unknown")))
        try:
            if reason in {"book_stale", "book_market_mismatch",
                          "hard_exit_deferred_thin_book"}:
                # A normal flat stale-book replan cannot bypass the optional
                # allowance used moments earlier by quiet-book service.
                with (self._routine_book_scope(slug) if reason == "book_stale"
                      else nullcontext()):
                    await self._refresh_book(self._now(), slug)
                if reason == "book_stale":
                    # Refreshing the book IS the recovery. Quarantining below
                    # cancels every resting quote, and a stale book does not
                    # make an already-resting post-only order unsafe -- it only
                    # means we cannot make a NEW decision until the book
                    # arrives. Measured 2026-08-14: 226 book_stale events in
                    # six minutes against 45 placements, so nearly every quote
                    # was cancelled about two seconds after being placed and
                    # nothing ever rested. Reward is paid for resting.
                    #
                    # New risk is still refused for as long as the book is
                    # stale: the reconciler returns this critical before it can
                    # plan anything, so no quote is placed on stale data.
                    return
            return await self._quarantine_strategy_orders(slug)
        except RuntimeInvariantViolation:
            raise
        except MarketStreamUnavailable as exc:
            self._request_market_stream_rebind(exc)
            return
        except ApiAdmissionDeferred:
            # Book cooldown does not prove the resting orders unsafe and must
            # not turn one endpoint throttle into per-owner quarantine/error
            # escalation. The unchanged stale-book gate still bars new plans.
            return
        except Exception as exc:
            if (isinstance(exc, AdapterError) and exc.status_code == 429
                    and str(exc.operation or "").startswith("markets.book(")):
                self._log_book_rate_limit(slug)
                return
            if reason == "book_stale":
                await self._report_runtime_error(
                    component, AdapterError(str(reason or "unknown")))
            await self._report_runtime_error(
                f"strategy_state_recovery:{slug}", exc)

    def _recovery_delay(self, component: str, *, base: float = 1.0,
                        cap: float = 30.0) -> float:
        attempt = max(1, self._runtime_error_counts.get(component, 1))
        return min(base * (2 ** min(attempt - 1, 5)), cap)
