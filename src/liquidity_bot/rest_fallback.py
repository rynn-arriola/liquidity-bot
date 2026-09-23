"""REST account-state proof hooks; never an automatic bypass.

Public books still use the normal market stream. No private events or transport
heartbeats are manufactured. The normal runner remains the default. This mode
trades latency for availability, and cannot detect silently stale venue data.
"""

from __future__ import annotations

from . import read_backoff

import asyncio
import json
from collections import Counter
from contextvars import ContextVar
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal

from .adapters import AdapterError, ApiAdmissionDeferred
from .errors import RuntimeInvariantViolation, MarketStreamUnavailable
from .execution_ledger import LedgerConflict
from .commissioning import CommissioningUnavailable
from .live_commissioning import CommissioningRunner
from .live_orders import AsyncTradingGateway
from .live_session import PrivateStateGate
from .rest_polling_policy import RestPollingPolicy
from .account_source import normalize_mode
from .streams import PrivateStreamPool


class RestStateGate(PrivateStateGate):
    """Account-source lifecycle and complete REST proof, NOT socket health."""

    def session_evidence_ready(self, *, now: datetime) -> bool:
        if not (self.connected and self.rest_reconciled
                and self.last_reconcile_at is not None):
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

    def drain_events(self, *, limit=None):
        return ()


class RestProofTradingGateway(AsyncTradingGateway):
    """Expire create/reducer permission after admission waits; allow cancels."""

    def __init__(self, gateway, proof_current, reducing_proof_current):
        super().__init__(gateway)
        self.proof_current = proof_current
        self.reducing_proof_current = reducing_proof_current
        self._reducing_market = ContextVar(
            "rest_reducing_market", default=None)

    async def create(self, payload, *, reducing=False):
        market = str(payload.get("marketSlug", "")) if reducing else None
        token = self._reducing_market.set(market)
        try:
            return await super().create(payload, reducing=reducing)
        finally:
            self._reducing_market.reset(token)

    async def _mutation(self, operation, call, *, traffic_class="safety",
                        admission_check=None):
        def check():
            scoped_market = self._reducing_market.get()
            scoped_reduction_proved = (
                operation == "POST /v1/orders"
                and traffic_class == "safety"
                and bool(scoped_market)
                and self.reducing_proof_current(scoped_market)
            )
            if (operation == "POST /v1/orders"
                    and not self.proof_current()
                    and not scoped_reduction_proved):
                raise ApiAdmissionDeferred(
                    "REST account proof expired; request not sent",
                    error_code="LOCAL_REST_STATE_EXPIRED",
                    retry_after_seconds=1.0)
            # The shared entry callback samples the final clock after all
            # proof/ownership reads, including this wrapper's checks.
            if admission_check is not None:
                admission_check()

        return await super()._mutation(
            operation, call, traffic_class=traffic_class, admission_check=check)


class RestFallbackRunner(CommissioningRunner):
    """Shared strategy/lifecycle, with a strict REST-only account source.

    Strict proofs read all owned working/ambiguous orders individually. The
    optional steady-state optimization substitutes two matching complete order
    snapshots only for unchanged, flat, unambiguous OPEN entries. Positions
    and execution history remain complete and mandatory on every proof. A hard
    exit may use an explicitly market-scoped order proof; it cannot grant new
    entry authority, and a failed scoped proof revokes existing authority. No
    failed poll retains entry authority. Proof age includes the entire serial
    read duration.
    """

    ACCOUNT_STATE_DESCRIPTION = "complete REST account state"
    SOURCE_REBOUND_DESCRIPTION = "rebound market/REST sources"
    REQUIRE_EXECUTION_BACKFILL = True
    REST_POLL_SECONDS = 5.0
    PENDING_ENTRY_ORDER_CEILING = 30
    ORDER_READ_CONCURRENCY = 4
    HISTORY_DEEP_OVERLAP_SECONDS = 30.0

    def __init__(self, *args, rest_polling_policy=None,
                 account_state_mode="rest", **kwargs):
        self.account_state_mode = normalize_mode(account_state_mode)
        self.rest_polling_policy = rest_polling_policy or RestPollingPolicy()
        self._bulk_order_proof = False
        self._bulk_skipped = {}
        self._hard_exit_terminal_proofs = {}
        self._hard_exit_retry_authorizations = {}
        self._position_confirmation_proofs = {}
        self._strategy_recovery_owner = None
        self._rest_order_proof_counts = Counter()
        self._rest_order_proof_last_report = None
        self._websocket_factory = kwargs.get("private_stream_factory", PrivateStreamPool)
        if self._using_rest_source():
            kwargs["private_stream_factory"] = DisabledPrivateStream
        super().__init__(*args, **kwargs)
        if self._using_rest_source():
            self.gate = RestStateGate(max_silence_seconds=30)
        self._rest_poll_completed_at = None
        # Durable fills are the restart anchor, not an instruction to replay
        # the venue's entire retained account history at every restart.
        stored_fills = frozenset(str(row["execution_id"]) for row in
                                 self.ledger.query("SELECT execution_id FROM execution_fills"))
        self._rest_history_seen: frozenset[str] | None = stored_fills or None
        self._rest_history_pending: frozenset[str] = frozenset()
        self._rest_history_deep_completed_at = None
        self.executor.gateway = RestProofTradingGateway(
            self.gateway,
            lambda: (not self._using_rest_source()
                     or self.gate.session_ready(self.ledger, now=self._now())),
            self._scoped_reducing_mutation_proof_current)
        self._set_source_descriptions()

    def _using_rest_source(self):
        return self.account_state_mode == "rest"

    def _set_source_descriptions(self):
        rest = self._using_rest_source()
        self.PENDING_ENTRY_ORDER_CEILING = 30 if rest else None
        self.REQUIRE_EXECUTION_BACKFILL = rest
        self.ACCOUNT_STATE_DESCRIPTION = (
            "complete REST account state" if rest else
            CommissioningRunner.ACCOUNT_STATE_DESCRIPTION)
        self.SOURCE_REBOUND_DESCRIPTION = (
            "rebound market/REST sources" if rest else
            CommissioningRunner.SOURCE_REBOUND_DESCRIPTION)

    async def _read_owned_order_states(self, exchange_ids, now):
        if not self._using_rest_source():
            return await super()._read_owned_order_states(exchange_ids, now)
        if self._bulk_order_proof and exchange_ids:
            orders = await self.account.fetch_open_orders(
                observed_at=now, require_complete_fields=True)
            by_id = self._checked_bulk_orders(orders)
            local = {str(row["exchange_order_id"]): row
                     for row in self.ledger.working_orders(owned_only=True)}
            unresolved = self.ledger.unresolved_intents()
            unresolved_by_exchange = {}
            unresolved_by_market = {}
            for intent in unresolved:
                exchange_id = str(intent["exchange_order_id"] or "").strip()
                if exchange_id:
                    unresolved_by_exchange.setdefault(
                        exchange_id, []).append(intent)
                unresolved_by_market.setdefault(
                    str(intent["market_slug"]), []).append(intent)
            absent_markets = {str(row["market_slug"])
                              for row in self.ledger.absent_orders()}
            read_ids = []
            for exchange_id in exchange_ids:
                order, row = by_id.get(exchange_id), local.get(exchange_id)
                ordinary_unchanged = (
                    order is not None and row is not None
                    and order.market_slug not in unresolved_by_market
                    and order.market_slug not in absent_markets
                    and self.ledger.position(order.market_slug) == 0
                    and order.status == "OPEN" and order.filled_qty == 0
                    and order.event_at >= datetime.fromisoformat(row["event_at"])
                    and self._order_matches_row(order, row))
                pending_places = unresolved_by_exchange.get(exchange_id, ())
                pending_place = (
                    pending_places[0] if len(pending_places) == 1 else None)
                positive_create = (
                    order is not None and row is None and pending_place is not None
                    and order.market_slug not in absent_markets
                    and len(unresolved_by_market.get(order.market_slug, ())) == 1
                    and self.ledger.position(order.market_slug) == 0
                    and self._pending_place_matches_order(order, pending_place))
                if ordinary_unchanged or positive_create:
                    self._bulk_skipped[exchange_id] = order
                    self._rest_order_proof_counts[
                        "bulk_create" if positive_create else "bulk_unchanged"] += 1
                else:
                    read_ids.append(exchange_id)
            exchange_ids = read_ids
        self._rest_order_proof_counts["exact"] += len(exchange_ids)
        return await self._read_exact_order_states(exchange_ids, now)

    @staticmethod
    def _order_matches_row(order, row):
        return (
            order.client_order_id == row["client_order_id"]
            and order.exchange_order_id == row["exchange_order_id"]
            and order.market_slug == row["market_slug"]
            and order.side.upper() == row["side"]
            and order.status == row["status"]
            and order.price == Decimal(row["price"])
            and order.original_qty == Decimal(row["original_qty"])
            and order.filled_qty == Decimal(row["filled_qty"]))

    @staticmethod
    def _pending_place_matches_order(order, intent):
        """Use a complete positive OPEN row as create proof, never absence.

        A create response supplies the exchange id but remains unresolved until
        venue order state arrives.  During an optimized steady-state proof the
        first complete open-order snapshot is already positive venue evidence.
        When every immutable economic/identity field matches the one pending
        PLACE, a second matching complete snapshot can safely replace the
        otherwise redundant GET-by-id.  Cancels/modifies, missing rows,
        partials, positions, ambiguous owners and any mismatch retain exact
        retrieval.
        """

        if (str(intent["operation"]).upper() != "PLACE"
                or str(intent["state"]).upper() not in {
                    "SUBMITTING", "ACKNOWLEDGED", "UNCERTAIN"}
                or str(intent["exchange_order_id"] or "")
                    != order.exchange_order_id
                or str(intent["client_order_id"]) != order.client_order_id
                or str(intent["market_slug"]) != order.market_slug
                or order.status != "OPEN" or order.filled_qty != 0):
            return False
        try:
            payload = json.loads(str(intent["payload_json"]))
            if not isinstance(payload, dict):
                return False
            venue_intent = str(payload.get("intent") or "").upper()
            expected_side = {
                "ORDER_INTENT_BUY_LONG": "ORDER_SIDE_BUY",
                "ORDER_INTENT_BUY_SHORT": "ORDER_SIDE_SELL",
            }.get(venue_intent)
            raw_price = payload.get("price")
            if isinstance(raw_price, dict):
                raw_price = raw_price.get("value")
            return (
                expected_side is not None
                and order.side.upper() == expected_side
                and order.price == Decimal(str(raw_price))
                and order.original_qty == Decimal(str(payload.get("quantity")))
                and order.event_at >= datetime.fromisoformat(
                    str(intent["created_at"])))
        except (json.JSONDecodeError, TypeError, ValueError, ArithmeticError):
            return False

    def _checked_bulk_orders(self, orders):
        """A transient foreign/duplicate row must not disappear between reads."""
        by_id = {}
        clients = set()
        for order in orders:
            if order.client_order_id.startswith("venue:"):
                # Preserve the positive ownership violation even if the next
                # response omits this row. Throwing before recording it would
                # let a later clean response silently forget foreign risk.
                observed = self._now()
                self.ledger.apply_order_event(
                    "rest-bulk-foreign",
                    f"{observed.isoformat()}:{order.exchange_order_id}",
                    order, received_at=observed)
                raise LedgerConflict("unowned order in REST bulk proof")
            if (not order.exchange_order_id
                    or order.exchange_order_id in by_id
                    or order.client_order_id in clients):
                raise LedgerConflict("duplicate or missing identity in REST bulk proof")
            by_id[order.exchange_order_id] = order
            clients.add(order.client_order_id)
        return by_id

    async def _fetch_rest_order_snapshot(self, now):
        if not self._bulk_order_proof:
            return await super()._fetch_rest_order_snapshot(now)
        orders = await self.account.fetch_open_orders(
            observed_at=now, require_complete_fields=True)
        by_id = self._checked_bulk_orders(orders)
        changed = []
        for exchange_id, before in self._bulk_skipped.items():
            after = by_id.get(exchange_id)
            # Compare economic/ownership fields, not snapshot receipt times.
            # A missing row still needs exact positive terminal evidence.
            if (after is None
                    or after.event_at < before.event_at
                    or any(getattr(after, field) != getattr(before, field)
                           for field in ("client_order_id", "market_slug", "side",
                                         "status", "price", "original_qty", "filled_qty"))):
                changed.append(exchange_id)
        failures = await self._read_exact_order_states(changed, now)
        self._rest_order_proof_counts["second_snapshot_changed"] += len(changed)
        if failures:
            raise AdapterError("changed/missing order lacks exact REST proof: "
                               + "; ".join(f"{key}: {detail}" for key, detail in failures))
        return orders

    def _report_rest_order_proofs(self, now):
        """Report proof-path counts without another venue request."""

        last = self._rest_order_proof_last_report
        if last is not None and 0 <= (now - last).total_seconds() < 60:
            return
        counts = self._rest_order_proof_counts
        if sum(counts.values()):
            print(
                f"[rest-order-proof] at={now.isoformat()} "
                f"bulk_unchanged={counts['bulk_unchanged']} "
                f"bulk_create={counts['bulk_create']} "
                f"exact={counts['exact']} "
                f"second_snapshot_changed={counts['second_snapshot_changed']}",
                flush=True,
            )
        counts.clear()
        self._rest_order_proof_last_report = now

    async def _read_exact_order_states(self, exchange_ids, now):
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
        throttle = None
        for exchange_id, result in zip(exchange_ids, results):
            if isinstance(result, (RuntimeInvariantViolation, LedgerConflict)):
                raise result
            if result is not None:
                failures.append((exchange_id, f"{type(result).__name__}: {result}"))
                if read_backoff.is_backpressure(result):
                    throttle = result
        if throttle is not None:
            raise throttle
        return failures

    def _execution_history_overlap_pages(self, now):
        """Keep active/ambiguous proof deep; bound flat late-publication audit."""
        deep = self._rest_history_deep_completed_at
        if (not self.rest_polling_policy.enabled or not self._bulk_order_proof
                or self._rest_history_seen is None
                or self._has_open_position() or self.ledger.unresolved_intents()
                or self.ledger.absent_orders() or deep is None
                or not 0 <= (now - deep).total_seconds()
                    < self.HISTORY_DEEP_OVERLAP_SECONDS):
            return 2
        return 1

    async def _fetch_execution_history(self, now):
        if not self._using_rest_source():
            return await super()._fetch_execution_history(now)
        overlap_pages = self._execution_history_overlap_pages(now)
        batch = await self.account.fetch_executions(
            observed_at=now, require_coverage=True,
            known_execution_ids=self._rest_history_seen,
            minimum_overlap_pages=overlap_pages)
        if overlap_pages == 2:
            self._rest_history_deep_completed_at = self._now()
        self._rest_history_pending = frozenset(
            item.execution_id for item in batch if hasattr(item, "execution_id"))
        return batch

    async def _rest_reconcile(self, now, *, tag="", strict_order_markets=None):
        self._position_confirmation_proofs.clear()
        if not self._using_rest_source():
            return await super()._rest_reconcile(
                now, tag=tag, strict_order_markets=strict_order_markets)
        started = self._now()
        ready = self.gate.session_ready(self.ledger, now=started)
        if (self.rest_polling_policy.enabled and tag == "periodic"
                and ready and not self._rest_poll_due(started)):
            return
        if self._bulk_order_proof:
            raise RuntimeInvariantViolation("rest_proof_overlap", "overlapping account proofs")
        # Startup, rebind, recovery, shutdown and a failed/expired prior proof
        # retain every exact read. A scoped caller never selects this shortcut.
        self._bulk_order_proof = bool(
            self.rest_polling_policy.enabled and ready
            and tag in {"rest-source-decision", "periodic"})
        self._bulk_skipped = {}
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
        finally:
            self._bulk_order_proof = False
            self._bulk_skipped = {}
        self._rest_poll_completed_at = self._now()
        self._report_rest_order_proofs(self._now())
        # REST has no event to dirty an owner after a partial fill, position
        # quantity change or terminal order. Re-evaluate the bounded slate.
        for slug in self.active_slugs:
            self._mark_market_dirty(slug, self._now())

    async def _wait_for_initial_private_state(self, timeout_seconds=15):
        if not self._using_rest_source():
            return await super()._wait_for_initial_private_state(timeout_seconds)
        # A REST-only proof replaces this bind step explicitly, never by
        # toggling fake balance-snapshot or private-message flags.
        await self._rest_reconcile(self._now(), tag="rest-source-bind")
        if not self.gate.session_ready(self.ledger, now=self._now()):
            raise CommissioningUnavailable("REST account source is not ready")

    def _rest_poll_due(self, now):
        completed = self._rest_poll_completed_at
        interval = self.REST_POLL_SECONDS
        if self.rest_polling_policy.enabled:
            interval = (self.rest_polling_policy.active_poll_seconds
                        if self._has_open_position() or self.ledger.unresolved_intents()
                        else self.rest_polling_policy.normal_poll_seconds)
        return (completed is None
                or not 0 <= (now - completed).total_seconds() < interval)

    async def _refresh_execution_state_for_decision(self, now):
        if not self._using_rest_source():
            return await super()._refresh_execution_state_for_decision(now)
        if (not self.gate.session_ready(self.ledger, now=now)
                or self._rest_poll_due(now)):
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

        if not self._using_rest_source():
            return await super()._reconcile_hard_exit_state(slug)
        proved_at = self._usable_hard_exit_terminal_proof(
            slug, consume=True)
        if proved_at is not None:
            # The post-mutation transition proof already consumed exact order
            # state plus complete open-order, position and history snapshots.
            # Repeating that same proof here used the entire retry window even
            # though this IOC was positively terminal.
            return proved_at
        await self._reconcile_position_state(slug, tag="hard-exit")
        return self._now()

    def _usable_hard_exit_terminal_proof(self, slug, *, consume=False):
        """Return one exact, fresh proof for confirmation or the next retry."""

        cached = (self._hard_exit_terminal_proofs.pop(slug, None)
                  if consume else self._hard_exit_terminal_proofs.get(slug))
        if cached is None:
            return None
        proof_token, proved_at = cached
        token = self.reconciler.hard_exit_schedule_token(slug)
        attempts, sent_at, started_at = token
        now = self._now()
        age = (now - proved_at).total_seconds()
        sent_age = ((now - sent_at).total_seconds()
                    if sent_at is not None else None)
        valid = (
            proof_token == token
            and attempts > 0
            and started_at is not None
            and 0 <= age
            <= self.reconciler.policy.hard_exit_confirmation_timeout_seconds
            # A positive terminal proof remains useful when the proof itself
            # took longer than the confirmation preference. Its completion
            # time, not the IOC submission time, is the freshness boundary.
            # The unchanged retry-window check still decides whether another
            # IOC may be sent.
            and (sent_age is None or sent_age >= 0)
            and self.ledger.position(slug) != 0
            and not self.ledger.unresolved_intents(slug)
            and not self.ledger.working_orders(slug)
            and not self.ledger.absent_orders(slug)
        )
        if not valid:
            self._hard_exit_terminal_proofs.pop(slug, None)
            return None
        return proved_at

    def _position_priority_has_fresh_transition_proof(self, slug):
        if not self._using_rest_source():
            return super()._position_priority_has_fresh_transition_proof(slug)
        return self._usable_hard_exit_terminal_proof(slug) is not None

    def _position_confirmation_version(self):
        # Over-invalidate on ANY ledger write, including identity adoption and
        # unrelated mutations. No cache may survive a new account observation.
        return (self.ledger.connection.total_changes, tuple(self.active_slugs),
                self.account_state_mode,
                getattr(getattr(self, "source_control", None), "generation", None))

    def _position_priority_has_fresh_account_proof(self, slug):
        if not self._using_rest_source():
            return super()._position_priority_has_fresh_account_proof(slug)
        cached = self._position_confirmation_proofs.pop(slug, None)
        if cached is None:
            return False
        version, started, completed = cached
        now = self._now()
        # Share the just-completed cancel confirmation with its immediate
        # replacement consumer once. No new snapshot timestamp, global poll
        # clock, risk mark or mutation authority is created by this shortcut.
        return bool(
            not self._stop_requested and self.rest_polling_policy.enabled
            and version == self._position_confirmation_version()
            and 0 <= (now - started).total_seconds() <= 30
            and 0 <= (now - completed).total_seconds() < 1
            and self.gate.session_ready(self.ledger, now=now)
            and self.ledger.position(slug) != 0
            and not self.ledger.working_orders(slug)
            and not self.ledger.unresolved_intents()
            and not self.ledger.absent_orders())

    def _scoped_reducing_mutation_proof_current(self, slug):
        """Recheck one exact retry authorization at the network boundary."""

        authorization = self._hard_exit_retry_authorizations.pop(slug, None)
        if authorization is None:
            return False
        prior_token, proved_at = authorization
        attempts, sent_at, started_at = (
            self.reconciler.hard_exit_schedule_token(slug))
        prior_attempts, prior_sent_at, prior_started_at = prior_token
        age = (self._now() - proved_at).total_seconds()
        pending = self.ledger.unresolved_intents(slug)
        return (
            prior_attempts > 0
            and prior_sent_at is None
            and prior_started_at is not None
            and attempts == prior_attempts + 1
            and sent_at is not None
            and started_at == prior_started_at
            and 0 <= age
            <= self.reconciler.policy.hard_exit_confirmation_timeout_seconds
            and self.ledger.position(slug) != 0
            # The executor has durably prepared this exact retry before its
            # final admission check. The pre-plan proof required zero unknown
            # intent; allow only the single new, not-yet-sent PLACE it created.
            and len(pending) == 1
            and str(pending[0]["operation"]) == "PLACE"
            and str(pending[0]["state"]) == "SUBMITTING"
            and not pending[0]["exchange_order_id"]
            and not self.ledger.working_orders(slug)
            and not self.ledger.absent_orders(slug)
        )

    def _frame(self, now, slug=None):
        if not self._using_rest_source():
            return super()._frame(now, slug)
        frame = super()._frame(now, slug)
        owner = slug or self.market_slug
        proved_at = self._usable_hard_exit_terminal_proof(owner)
        if (not frame.state_known and frame.position != 0
                and proved_at is not None):
            # Scoped proof is deliberately owner-only: it can keep an exact
            # reducer/IOC moving when the account-wide entry gate ages out,
            # but it can never restore entry authority for this or any other
            # market.
            frame = replace(
                frame, state_known=True, new_risk_allowed=False)
        token = self.reconciler.hard_exit_schedule_token(owner)
        if (frame.position != 0 and proved_at is not None
                and token[0] > 0 and token[1] is None
                and token[2] is not None):
            # Planning records the next IOC token before the executor reaches
            # its final network-boundary admission check. Preserve the exact
            # pre-plan token so only that one increment on this owner can use
            # the scoped proof; another market or flat create cannot borrow it.
            self._hard_exit_retry_authorizations[owner] = (token, proved_at)
        return frame

    async def _reconcile_pending_hard_exit(self, slug=None):
        """Confirm one IOC and carry its exact proof to the bounded retry."""

        if not self._using_rest_source():
            return await super()._reconcile_pending_hard_exit(slug)

        slug = slug or self.market_slug
        proved_at = await self._reconcile_hard_exit_state(slug)
        if (self.ledger.market_has_unresolved_intent(slug)
                or self.ledger.working_orders(slug)):
            return "pending"
        if self.ledger.position(slug) == 0:
            self.reconciler.confirm_hard_exit_terminal(slug)
            self._hard_exit_terminal_proofs.pop(slug, None)
            return "flat"
        # The exact terminal proof also authorizes the next bounded IOC. Keep
        # its original completion time: carrying it forward must never renew
        # stale evidence or global entry authority.
        self.reconciler.confirm_hard_exit_terminal(slug)
        token = self.reconciler.hard_exit_schedule_token(slug)
        if (proved_at is not None
                and not self.ledger.unresolved_intents(slug)
                and not self.ledger.working_orders(slug)
                and not self.ledger.absent_orders(slug)):
            self._hard_exit_terminal_proofs[slug] = (token, proved_at)
        return "retry"

    async def _quarantine_strategy_orders(self, slug):
        """One strict account proof, then exact-owner cancellation proof.

        Only strategy recovery uses this path. Startup, rebind, global
        quarantine and shutdown retain their strict whole-account checks.
        The terminal pass still consumes complete orders/positions/history,
        but never renews the global entry gate or poll clock.
        """
        if not self._using_rest_source():
            return await super()._quarantine_strategy_orders(slug)
        if self._strategy_recovery_owner is not None:
            # Urgent service is serial but may itself encounter a critical.
            # Leave that owner for its normal backed-off recovery; do not
            # recursively nest another full proof/priority sweep.
            raise ApiAdmissionDeferred(
                "strategy recovery already in progress",
                error_code="LOCAL_RECOVERY_IN_PROGRESS", retry_after_seconds=1)
        scope = tuple(self.active_slugs)
        generation = getattr(getattr(self, "source_control", None), "generation", None)

        def checkpoint():
            if self._stop_requested:
                return self._stop_requested
            if (not self._using_rest_source() or tuple(self.active_slugs) != scope
                    or getattr(self, "_source_switching", False)
                    or getattr(getattr(self, "source_control", None), "generation", None)
                    != generation):
                raise ApiAdmissionDeferred(
                    "account source or scope changed during recovery",
                    error_code="LOCAL_ACCOUNT_SOURCE_CHANGED", retry_after_seconds=1)
            if self._streams_need_rebind:
                raise (self._pending_market_stream_error or MarketStreamUnavailable(
                    "public stream failed during scoped strategy recovery"))
            self._assert_runtime_scope()
            return None

        self._strategy_recovery_owner = slug
        try:
            stop = checkpoint()
            if stop:
                return stop
            print(
                f"[quarantine] at={self._now().isoformat()} "
                f"called_by=_quarantine_strategy_orders market={slug} source=rest",
                flush=True,
            )
            await self._rest_reconcile(self._now(), tag="strategy-recovery-start")
            stop = checkpoint()
            if stop:
                return stop
            # The recovering owner is excluded from priority dispatch until
            # its quarantine completes; otherwise we could cancel a reducer
            # that this very checkpoint just placed. Other due/uncovered
            # owners use the unchanged position-only guarded executor.
            stop = await self._service_urgent_positions()
            if stop:
                return stop
            stop = checkpoint()
            if stop:
                return stop
            if not self.gate.session_evidence_ready(now=self._now()):
                raise ApiAdmissionDeferred(
                    "complete account proof expired during recovery",
                    error_code="LOCAL_REST_STATE_EXPIRED", retry_after_seconds=1)
            # Re-read local state after every service await: a fill or terminal
            # update may have changed this owner. No second pass is needed
            # when the complete proof already cleared its orders/ambiguity.
            if (self.ledger.working_orders(slug) or self.ledger.absent_orders(slug)
                    or self.ledger.market_has_unresolved_intent(slug)):
                if (not self.ledger.market_has_unresolved_intent(slug)
                        and self.ledger.working_orders(slug)):
                    await self.kill_switch.initiate(slug, now=self._now())
                    stop = checkpoint()
                    if stop:
                        return stop
                await self._reconcile_position_state(slug, tag="strategy-recovery-terminal")
                stop = checkpoint()
                if stop:
                    return stop
            self._assert_quarantine_terminal((slug,))
            if self.ledger.absent_orders(slug):
                raise AdapterError("strategy recovery is waiting for positive order proof")
            return None
        except BaseException:
            self.gate.on_reconciliation_conflict()
            raise
        finally:
            self._strategy_recovery_owner = None

    async def _reconcile_position_state(self, slug, *, tag):
        self._position_confirmation_proofs.clear()
        observed_at = self._now()
        try:
            await CommissioningRunner._rest_reconcile(
                self,
                observed_at,
                tag=f"{tag}:{slug}",
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
        if (tag == "position-transition" and self.ledger.position(slug) != 0
                and not self.ledger.working_orders(slug)
                and not self.ledger.unresolved_intents()
                and not self.ledger.absent_orders()):
            self._position_confirmation_proofs[slug] = (
                self._position_confirmation_version(), observed_at, self._now())

    async def _decide_for_market(self, slug, now, *, replanned=False):
        if not self._using_rest_source():
            return await super()._decide_for_market(slug, now, replanned=replanned)
        before = self._market_action_generation.get(slug, 0)
        outcome, delay = await super()._decide_for_market(
            slug, now, replanned=replanned)
        if (outcome or delay or self._stop_requested
                or self._market_action_generation.get(slug, 0) == before
                or self.ledger.position(slug) == 0):
            return outcome, delay
        if (any(row["operation"] == "CANCEL"
                for row in self.ledger.unresolved_intents(slug))
                or self.reconciler.hard_exit_schedule_token(slug)[1] is not None):
            # Repricing is still cancel-confirm-replace, but confirmation may
            # not wait behind an unrelated flat slate or discovery. This does
            # no second plan/apply: the normal scheduler owns the next action.
            try:
                await self._reconcile_position_state(slug, tag="position-transition")
            except (LedgerConflict, RuntimeInvariantViolation):
                raise
            except Exception as exc:
                await self._report_runtime_error("position_transition_proof", exc)
                return None, 1.0
            token = self.reconciler.hard_exit_schedule_token(slug)
            if (token[1] is not None and self.ledger.position(slug) != 0
                    and not self.ledger.unresolved_intents(slug)
                    and not self.ledger.working_orders(slug)
                    and not self.ledger.absent_orders(slug)):
                self._hard_exit_terminal_proofs[slug] = (token, self._now())
            else:
                self._hard_exit_terminal_proofs.pop(slug, None)
        return outcome, delay

    def _position_exit_deadline(self, slug):
        """Scheduling mirror of existing exit clocks, not exit authorization."""
        quantity = abs(self.ledger.position(slug))
        state = self._markets.get(slug)
        market = getattr(state, "market", None)
        basis = self.ledger.inventory_basis(slug)
        if market is None or basis is None:
            return None
        policy = self.reconciler.policy
        age_limit = policy.max_inventory_age_seconds
        minimum = getattr(market, "minimum_trade_qty", None)
        minimum = Decimal(str(minimum)) if minimum is not None else None
        if (policy.small_residual_min_qty <= quantity <= policy.small_residual_max_qty
                and minimum is not None and minimum > 0
                and quantity >= minimum and quantity % minimum == 0):
            age_limit = min(age_limit, policy.small_residual_timeout_seconds)
        deadline = basis.opened_at + timedelta(seconds=age_limit)
        if market.game_start is not None:
            deadline = min(deadline, market.game_start - timedelta(
                seconds=policy.force_exit_before_start_seconds))
        return deadline

    def _position_priority_must_yield(self, slug):
        if not self._using_rest_source():
            return super()._position_priority_must_yield(slug)
        if (self.ledger.position(slug) == 0
                or slug in self._isolated_unexecutable_slugs()):
            return False
        return self._position_priority_rank(slug) < 2

    def _position_priority_rank(self, slug):
        """Due exit, uncovered inventory, then covered routine repricing.

        This is local scheduling evidence only. The normal guarded executor
        still proves ownership, terminal cancels and exact current quantity.
        """
        if not self._using_rest_source():
            return super()._position_priority_rank(slug)
        deadline = self._position_exit_deadline(slug)
        if deadline is not None and self._now() >= deadline:
            return 0
        if deadline is None or not self._position_has_exact_reducer(slug):
            return 1
        return 2

    async def _service_urgent_positions(self):
        if not self._using_rest_source():
            return await super()._service_urgent_positions()
        if self._stop_requested:
            return self._stop_requested
        # The ordinary decision sweep already prioritizes positions, but used
        # to start only AFTER periodic reads and full-slate book service. A
        # known due/uncovered owner must enter that same guarded path first.
        # This adds no parallel executor or new mutation/proof authority.
        now = self._now()
        slugs = tuple(slug for slug in self._position_priority_tokens()
                      if self._position_priority_must_yield(slug)
                      and not self.ledger.market_has_unresolved_intent(slug)
                      and (self._next_decision_at.get(slug) is None
                           or now >= self._next_decision_at[slug]))
        if not slugs:
            return None
        slugs = tuple(sorted(slugs, key=self._position_priority_rank))
        outcome = await self._service_bound_markets(slugs, refresh_books=False)
        if outcome or self._stop_requested:
            return outcome or self._stop_requested
        # Service/proof may discover flatness. Never turn this extra exit pass
        # into another entry opportunity when that happens.
        return await self._decision_sweep(slugs, position_only=True)

    def _preserve_position_orders_during_bound_recovery(self, slug):
        """Keep only one exactly-sized reducer during public-feed recovery."""

        return self._position_has_exact_reducer(slug)

    def _position_has_exact_reducer(self, slug):
        """Builders, duplicates and uncertain orders are not exit coverage."""

        position = self.ledger.position(slug)
        if (position == 0 or self.ledger.market_has_unresolved_intent(slug)
                or self.ledger.absent_orders(slug)):
            return False
        orders = self._working_orders(slug)
        reducing_side = "offer" if position > 0 else "bid"
        return (len(orders) == 1
                and orders[0].side == reducing_side
                and orders[0].remaining_qty == abs(position))

    def _bound_recovery_position_preemption_enabled(self):
        """REST account proof remains valid when only the public feed failed."""

        return self._using_rest_source()

    async def _service_bound_recovery_positions(self):
        """Let due/uncovered positions preempt serial flat-order quarantine."""

        return await self._service_urgent_positions()

    def _position_near_its_flatten_deadline(self, now):
        if super()._position_near_its_flatten_deadline(now):
            return True
        if not self._using_rest_source():
            return False
        for row in self.ledger.query(
                "SELECT market_slug FROM execution_positions WHERE CAST(net_qty AS REAL)!=0"):
            slug = str(row["market_slug"])
            if slug in self._isolated_unexecutable_slugs():
                continue
            # A gap or pending cancel needs a replacement, not a blocking
            # catalog scan. Include inventory/tiny deadlines as well as kickoff.
            if not self._position_has_exact_reducer(slug):
                return True
            deadline = self._position_exit_deadline(slug)
            if deadline is None or (deadline - now).total_seconds() <= 60:
                return True
        return False

    def _hard_exit_retry_delay(self, slug):
        if not self._using_rest_source():
            return super()._hard_exit_retry_delay(slug)
        # The focused proof above is the delay. Re-enter immediately while its
        # terminal position facts and displayed book are still fresh.
        return 0.0

    def _position_priority_visit_limit(self, slug):
        if not self._using_rest_source():
            return super()._position_priority_visit_limit(slug)
        attempts, sent_at, started_at = (
            self.reconciler.hard_exit_schedule_token(slug))
        if attempts or sent_at is not None or started_at is not None:
            # Three submissions need at most one send and one terminal-proof
            # visit each, plus the existing cancel/reducer transitions. This
            # remains finite and does not change the mutation attempt ceiling.
            return max(4, 2 * self.reconciler.policy.max_hard_exit_attempts + 2)
        return super()._position_priority_visit_limit(slug)

    def _position_priority_replan_immediately(self, slug, before, after):
        if not self._using_rest_source():
            return super()._position_priority_replan_immediately(slug, before, after)
        # Complete a positively confirmed cancel before another flat entry.
        # A new resting reducer ends this continuation; do not churn it in an
        # unbounded reprice loop. Hard IOC continuation retains its prior token.
        return bool(before and after and (
            before[-1] != after[-1]
            or (before[1] and not after[1] and not after[2] and not after[3])))

    def _position_priority_tokens(self):
        if not self._using_rest_source():
            return super()._position_priority_tokens()
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
            if slug not in positions or slug == self._strategy_recovery_owner:
                continue
            # Non-selected exposure still belongs to the existing exit-only
            # service path; creating its state does not grant entry eligibility.
            self.state_for(slug)
            deadline = self._position_exit_deadline(slug)
            priorities[slug] = (
                self.ledger.position(slug), tuple(sorted(orders.get(slug, ()))),
                tuple(sorted(unresolved.get(slug, ()))),
                tuple(sorted(absent.get(slug, ()))),
                # Time crossing a real exit boundary must wake an already
                # visited owner even when its quantity and order never change.
                (deadline is None or self._now() >= deadline),
                self.reconciler.hard_exit_schedule_token(slug))
        return priorities

    def _accept_book(self, book):
        if not self._using_rest_source():
            return super()._accept_book(book)
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
        if not self._using_rest_source():
            return
        # A complete REST proof can take longer than the book entry lifetime.
        # Consume actual queued public updates immediately before planning;
        # never relabel an old price with the poll's completion timestamp.
        drain = getattr(self.market_stream, "drain_books", None)
        if callable(drain):
            try:
                for book in drain():
                    self._accept_book(book)
            except AdapterError as exc:
                self.gate.on_disconnect()
                self._streams_need_rebind = True
                if isinstance(exc, MarketStreamUnavailable):
                    raise
                raise MarketStreamUnavailable("public stream drain failed") from exc

    async def preflight(self):
        result = await super().preflight()
        if not self._using_rest_source():
            return result
        if result == "RECOVERY_ONLY":
            await self._wait_for_initial_private_state()
        print("[account-state] mode=rest private_socket=disabled "
              f"bulk_order_proof={str(self.rest_polling_policy.enabled).lower()} "
              f"decision_poll_seconds={self.rest_polling_policy.normal_poll_seconds if self.rest_polling_policy.enabled else 5} "
              f"active_poll_seconds={self.rest_polling_policy.active_poll_seconds if self.rest_polling_policy.enabled else 5} "
              "idle_poll_seconds=10 "
              f"max_proof_age_seconds=30 max_pending_entries={self.PENDING_ENTRY_ORDER_CEILING} "
              f"order_read_concurrency={self.ORDER_READ_CONCURRENCY} auto_switch=disabled", flush=True)
        return result
