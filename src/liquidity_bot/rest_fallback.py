"""Explicit, experimental REST account-state mode; never an automatic bypass.

Public books still use the normal market stream. No private events or transport
heartbeats are manufactured. The normal runner remains the default. This mode
trades latency for availability, and cannot detect silently stale venue data.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

from .adapters import AdapterError, ApiAdmissionDeferred
from .errors import RuntimeInvariantViolation
from .commissioning import CommissioningUnavailable
from .live_commissioning import CommissioningRunner
from .live_orders import AsyncTradingGateway
from .live_session import PrivateStateGate


class RestStateGate(PrivateStateGate):
    """Account-source lifecycle and complete REST proof, NOT socket health."""

    def session_ready(self, ledger, *, now: datetime) -> bool:
        if not (self.connected and self.rest_reconciled
                and self.last_reconcile_at is not None
                and ledger.ownership_is_known()):
            return False
        age = (now - self.last_reconcile_at).total_seconds()
        return 0 <= age <= self.max_silence_seconds

    def observe(self, event, *, received_at: datetime) -> None:
        raise RuntimeError("REST mode must not consume private events")


class DisabledPrivateStream:
    """Cancellable idle task for the shared scheduler; supplies no evidence."""

    def __init__(self, client):
        self.closed = False

    async def start(self, market_slugs):
        self.closed = False

    async def close(self):
        self.closed = True

    async def next_event(self, timeout=None):
        await asyncio.wait_for(asyncio.Event().wait(), timeout=timeout)

    def drain_events(self):
        return ()


class RestProofTradingGateway(AsyncTradingGateway):
    """Expire create/reducer permission after admission waits; allow cancels."""

    def __init__(self, gateway, proof_current):
        super().__init__(gateway)
        self.proof_current = proof_current

    async def _mutation(self, operation, call, *, traffic_class="safety",
                        admission_check=None):
        def check():
            if admission_check is not None:
                admission_check()
            if operation == "POST /v1/orders" and not self.proof_current():
                raise ApiAdmissionDeferred(
                    "REST account proof expired; request not sent",
                    error_code="LOCAL_REST_STATE_EXPIRED",
                    retry_after_seconds=1.0)

        return await super()._mutation(
            operation, call, traffic_class=traffic_class, admission_check=check)


class RestFallbackRunner(CommissioningRunner):
    """Shared strategy/lifecycle, with a strict REST-only account source.

    Account-wide proofs read all owned working/ambiguous orders individually,
    then complete open orders, exact positions and execution history. A hard
    exit may use an explicitly market-scoped order proof; it cannot grant new
    entry authority, and a failed scoped proof revokes existing authority. No
    failed poll retains entry authority. Proof age includes the entire serial
    read duration.
    """

    ACCOUNT_STATE_DESCRIPTION = "complete REST account state (temporary mode)"
    SOURCE_REBOUND_DESCRIPTION = "rebound market/REST sources"
    REQUIRE_EXECUTION_BACKFILL = True
    REST_POLL_SECONDS = 5.0
    PENDING_ENTRY_ORDER_CEILING = 30
    ORDER_READ_CONCURRENCY = 4

    def __init__(self, *args, **kwargs):
        kwargs["private_stream_factory"] = DisabledPrivateStream
        super().__init__(*args, **kwargs)
        self.gate = RestStateGate(max_silence_seconds=30)
        self._rest_poll_completed_at = None
        # Durable fills are the restart anchor, not an instruction to replay
        # the venue's entire retained account history at every restart.
        stored_fills = frozenset(str(row["execution_id"]) for row in
                                 self.ledger.query("SELECT execution_id FROM execution_fills"))
        self._rest_history_seen: frozenset[str] | None = stored_fills or None
        self._rest_history_pending: frozenset[str] = frozenset()
        self.executor.gateway = RestProofTradingGateway(
            self.gateway,
            lambda: self.gate.session_ready(self.ledger, now=self._now()))

    async def _read_owned_order_states(self, exchange_ids, now):
        # Only independent reads overlap. All still share the unchanged host
        # governor; no mutation is parallelized and every owner is checked.
        semaphore = asyncio.Semaphore(self.ORDER_READ_CONCURRENCY)

        async def read(exchange_id):
            async with semaphore:
                try:
                    await self.executor.reconcile_order(exchange_id, observed_at=now)
                except Exception as exc:
                    return exc
            return None

        tasks = [asyncio.create_task(read(item)) for item in exchange_ids]
        try:
            results = await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        failures = []
        for exchange_id, result in zip(exchange_ids, results):
            if isinstance(result, RuntimeInvariantViolation):
                raise result
            if result is not None:
                failures.append((exchange_id, f"{type(result).__name__}: {result}"))
        return failures

    async def _fetch_execution_history(self, now):
        batch = await self.account.fetch_executions(
            observed_at=now, require_coverage=True,
            known_execution_ids=self._rest_history_seen)
        self._rest_history_pending = frozenset(
            item.execution_id for item in batch if hasattr(item, "execution_id"))
        return batch

    async def _rest_reconcile(self, now, *, tag="", strict_order_markets=None):
        started = self._now()
        self.gate.on_reconciliation_conflict()
        try:
            # History, including conflicts, is mandatory without private fills.
            await super()._rest_reconcile(started, tag=tag)
            # Positive, successfully applied history remains useful even if
            # this first full backfill outlives the account-freshness budget.
            # The next proof must still re-read current orders and positions.
            self._rest_history_seen = (
                (self._rest_history_seen or frozenset()) | self._rest_history_pending)
            self.gate.on_rest_reconciled(started)
            if not 0 <= (self._now() - started).total_seconds() <= 30:
                raise ApiAdmissionDeferred(
                    "REST account proof exceeded freshness bound",
                    error_code="LOCAL_REST_STATE_EXPIRED", retry_after_seconds=1.0)
        except BaseException:
            self.gate.on_reconciliation_conflict()
            raise
        self._rest_poll_completed_at = self._now()
        # REST has no event to dirty an owner after a partial fill, position
        # quantity change or terminal order. Re-evaluate the bounded slate.
        for slug in self.active_slugs:
            self._mark_market_dirty(slug, self._now())

    async def _wait_for_initial_private_state(self, timeout_seconds=15):
        # A REST-only proof replaces this bind step explicitly, never by
        # toggling fake balance-snapshot or private-message flags.
        await self._rest_reconcile(self._now(), tag="rest-source-bind")
        if not self.gate.session_ready(self.ledger, now=self._now()):
            raise CommissioningUnavailable("REST account source is not ready")

    async def _refresh_execution_state_for_decision(self, now):
        completed = self._rest_poll_completed_at
        if (not self.gate.session_ready(self.ledger, now=now)
                or completed is None
                or not 0 <= (now - completed).total_seconds() < self.REST_POLL_SECONDS):
            await self._rest_reconcile(now, tag="rest-source-decision")

    async def _reconcile_hard_exit_state(self, slug):
        """Prove one IOC terminal without turning scoped proof into entry proof.

        A whole-account REST pass individually retrieves every owned order.
        With 30 entry orders that exceeded the hard-exit's complete retry
        window in production. Confirmation needs this owner's order states
        plus complete order, position and history snapshots, not 29 unrelated
        individual reads. The scoped result deliberately cannot renew global
        REST entry authority or the normal poll clock.
        """

        observed_at = self._now()
        try:
            await CommissioningRunner._rest_reconcile(
                self,
                observed_at,
                tag=f"hard-exit:{slug}",
                strict_order_markets=frozenset({str(slug)}),
                advance_gate=False,
            )
        except BaseException:
            # An incomplete safety proof is account uncertainty. Revoke entry
            # authority immediately; only the next complete proof may restore
            # it. Successful scoped proof preserves, but never renews, the
            # already-established short-lived account gate so the confirmed
            # risk-reducing retry can proceed without another 30-order scan.
            self.gate.on_reconciliation_conflict()
            raise
        self._rest_history_seen = (
            (self._rest_history_seen or frozenset())
            | self._rest_history_pending)

    def _hard_exit_retry_delay(self, slug):
        # The focused proof above is the delay. Re-enter immediately while its
        # terminal position facts and displayed book are still fresh.
        return 0.0

    def _position_priority_visit_limit(self, slug):
        attempts, sent_at, started_at = (
            self.reconciler.hard_exit_schedule_token(slug))
        if attempts or sent_at is not None or started_at is not None:
            # Three submissions need at most one send and one terminal-proof
            # visit each, plus the existing cancel/reducer transitions. This
            # remains finite and does not change the mutation attempt ceiling.
            return max(4, 2 * self.reconciler.policy.max_hard_exit_attempts + 2)
        return super()._position_priority_visit_limit(slug)

    def _position_priority_replan_immediately(self, slug, before, after):
        # The final token element is reconciler-only hard-exit state. Other
        # position/order changes retain the established bounded scheduler path.
        return bool(before and after and before[-1] != after[-1])

    def _position_priority_tokens(self):
        # Ignore snapshot timestamps: an unchanged proof must not repeatedly
        # interrupt reward-ranked entries. Exact quantity/working-order changes
        # do matter, including positive terminal proof after a cancel.
        managed, positions, _ = self._scheduler_owner_sets()
        orders = {}
        for row in self.ledger.working_orders():
            slug = str(row["market_slug"])
            if slug in positions:
                orders.setdefault(slug, []).append((
                    str(row["exchange_order_id"]), str(row["status"]),
                    str(row["original_qty"]), str(row["filled_qty"])))
        unresolved = {}
        for intent_id, (_, slug, _) in self._unresolved_intent_owners().items():
            unresolved.setdefault(slug, []).append(intent_id)
        absent = {}
        for row in self.ledger.absent_orders():
            absent.setdefault(str(row["market_slug"]), []).append(
                str(row["exchange_order_id"]))
        priorities = {}
        for slug in managed:
            if slug not in positions:
                continue
            # Non-selected exposure still belongs to the existing exit-only
            # service path; creating its state does not grant entry eligibility.
            self.state_for(slug)
            priorities[slug] = (
                self.ledger.position(slug), tuple(sorted(orders.get(slug, ()))),
                tuple(sorted(unresolved.get(slug, ()))),
                tuple(sorted(absent.get(slug, ()))),
                self.reconciler.hard_exit_schedule_token(slug))
        return priorities

    def _accept_book(self, book):
        # The outer scheduler may hold one older completed next_book result
        # while a REST poll drains newer queued books. Do not roll back to it.
        state = self._markets.get(book.slug)
        if (state is not None and state.book_observed_at is not None
                and book.received_at is not None
                and book.received_at < state.book_observed_at):
            return
        super()._accept_book(book)

    async def _refresh_event_guard(self):
        await super()._refresh_event_guard()
        # A complete REST proof can take longer than the book entry lifetime.
        # Consume actual queued public updates immediately before planning;
        # never relabel an old price with the poll's completion timestamp.
        drain = getattr(self.market_stream, "drain_books", None)
        if callable(drain):
            try:
                for book in drain():
                    self._accept_book(book)
            except Exception:
                self.gate.on_disconnect()
                self._streams_need_rebind = True
                raise

    async def preflight(self):
        result = await super().preflight()
        if result == "RECOVERY_ONLY":
            await self._wait_for_initial_private_state()
        print("[account-state] mode=rest-fallback private_socket=disabled "
              "decision_poll_seconds=5 idle_poll_seconds=10 "
              "max_proof_age_seconds=30 max_pending_entries=30 "
              "order_read_concurrency=4 experimental=yes", flush=True)
        return result
