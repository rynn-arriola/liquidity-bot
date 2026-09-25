"""One trading engine with operator-requested, verified account-source handoffs.

The compatibility REST runner owns the existing source-specific proof hooks.
This coordinator owns only control, candidate collection and a serial handoff;
it never plans, submits, cancels or resets an order/position.
"""
from __future__ import annotations

from . import read_backoff

import asyncio
import json
from collections import deque
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from itertools import islice

from .account_source import AccountSourceControl, AccountSourcePolicy
from .account_observations import AccountSnapshotReader, PositionWatch, DeferredIdentityResolver
from .account_snapshots import AsyncAccountSnapshotGateway
from .errors import (
    AdapterError, ApiAdmissionDeferred, RuntimeInvariantViolation, is_not_found)
from .execution_ledger import LedgerConflict, ACTIVE_ORDER_STATES, TERMINAL_ORDER_STATES
from .venue_state import normalize_execution, normalize_order, normalize_position
from .live_commissioning import CommissioningRunner
from .live_session import PrivateStateGate
from .private_stream import PrivateEventKind
from .hybrid_stream import HybridDelivery, HybridGap, HybridPrivateStream
from .rest_fallback import (DisabledPrivateStream, RestFallbackRunner, RestStateGate,
                            RestProofTradingGateway)


class SourceTradingGateway(RestProofTradingGateway):
    """No promotion may overlap a mutation or reuse a prior source generation."""

    def __init__(self, runner):
        super().__init__(runner.gateway,
            lambda: (not runner._using_rest_source()
                     or runner.gate.session_ready(runner.ledger, now=runner._now())),
            runner._scoped_reducing_mutation_proof_current)
        self.runner = runner

    async def _mutation(self, operation, call, *, traffic_class="safety",
                        admission_check=None):
        owner = self.runner
        generation = owner.source_control.generation
        scope = tuple(owner.active_slugs)
        owner._source_mutations_in_flight += 1

        def check():
            scoped_hybrid_reducer = False
            if (owner._source_switching or generation != owner.source_control.generation
                    or scope != tuple(owner.active_slugs)):
                raise ApiAdmissionDeferred("account source changed; request not sent",
                    error_code="LOCAL_ACCOUNT_SOURCE_CHANGED", retry_after_seconds=1)
            if (operation == "POST /v1/orders"
                    and isinstance(owner.private_stream, HybridPrivateStream)):
                if owner.private_stream.pending:
                    # Includes a task-held earlier event. No create/reducer uses
                    # quantity captured before an unapplied fill or position hint.
                    raise ApiAdmissionDeferred("private observation pending; request not sent",
                        error_code="LOCAL_HYBRID_OBSERVATION_PENDING", retry_after_seconds=1)
                if owner._hybrid_requires_reconcile:
                    scoped_hybrid_reducer = owner._hybrid_reduction_proved(
                        self._reducing_market.get())
                    if not scoped_hybrid_reducer:
                        raise ApiAdmissionDeferred(
                            "private observation pending; request not sent",
                            error_code="LOCAL_HYBRID_OBSERVATION_PENDING",
                            retry_after_seconds=1)
            if admission_check:
                admission_check()
            # Consume only after every downstream admission callback passes.
            # A locally rejected request must not burn the one-use certificate.
            if (scoped_hybrid_reducer
                    and not owner._hybrid_reduction_proved(
                        self._reducing_market.get(), consume=True)):
                raise ApiAdmissionDeferred("scoped reducer proof changed; request not sent",
                    error_code="LOCAL_HYBRID_OBSERVATION_PENDING", retry_after_seconds=1)
        try:
            return await super()._mutation(operation, call, traffic_class=traffic_class,
                                          admission_check=check)
        finally:
            owner._source_mutations_in_flight -= 1


class PrivateCandidate:
    """Read-only, bounded candidate; the runner alone projects observations."""

    MAX_EVENTS = 4096

    def __init__(self, stream, scope, *, timeout=45):
        self.stream = stream
        self.scope = tuple(scope)
        self.timeout = timeout
        self.events = deque()
        self.task = None
        self.error = None
        self.started = False
        self.order_scopes = set()
        self.position_scopes = set()
        self.partial_requests = set()
        self.orders = {}
        self.positions = {}
        self._order_pages = {}
        self._position_pages = {}

    def collect(self, event):
        if len(self.events) >= self.MAX_EVENTS:
            raise AdapterError("candidate private event buffer overflow")
        self.events.append(event)
        if event.kind == PrivateEventKind.ERROR:
            raise AdapterError("candidate private stream reported an error")
        if event.market_scope is not None and not set(event.market_scope) <= set(self.scope):
            raise AdapterError("candidate private scope mismatch")
        if event.kind in {PrivateEventKind.ORDER_SNAPSHOT,
                          PrivateEventKind.POSITION_SNAPSHOT}:
            if (not event.request_id or not isinstance(event.payload, dict)
                    or type(event.payload.get("eof")) is not bool
                    or not event.market_scope):
                raise AdapterError("candidate private snapshot lacks scope/completeness")
            key = (event.kind, event.request_id)
            if event.payload["eof"]:
                self.partial_requests.discard(key)
                target = (self.order_scopes if event.kind == PrivateEventKind.ORDER_SNAPSHOT
                          else self.position_scopes)
                target.update(event.market_scope)
            else:
                self.partial_requests.add(key)
            if event.kind == PrivateEventKind.ORDER_SNAPSHOT:
                rows = event.payload.get("orders")
                if not isinstance(rows, list):
                    raise AdapterError("candidate order page is malformed")
                page = self._order_pages.setdefault(key, [])
                page.extend(normalize_order(row, observed_at=event.received_at) for row in rows)
                if (any(order.market_slug not in event.market_scope for order in page)
                        or len({order.exchange_order_id for order in page}) != len(page)):
                    raise AdapterError("candidate order page scope/identity conflict")
                if event.payload["eof"]:
                    for order_id, order in tuple(self.orders.items()):
                        if order.market_slug in event.market_scope:
                            del self.orders[order_id]
                    for order in page:
                        self.orders[order.exchange_order_id] = order
                    del self._order_pages[key]
            else:
                rows = event.payload.get("positions")
                if not isinstance(rows, dict):
                    raise AdapterError("candidate position page is malformed")
                page = self._position_pages.setdefault(key, [])
                page.extend(normalize_position(slug, row, observed_at=event.received_at)
                            for slug, row in rows.items())
                if (any(position.market_slug not in event.market_scope for position in page)
                        or len({position.market_slug for position in page}) != len(page)):
                    raise AdapterError("candidate position page scope/identity conflict")
                if event.payload["eof"]:
                    for slug in event.market_scope:
                        self.positions[slug] = Decimal(0)
                    for position in page:
                        self.positions[position.market_slug] = position.net_qty
                    del self._position_pages[key]
        elif event.kind == PrivateEventKind.ORDER_UPDATE:
            raw = event.payload.get("execution", {})
            order = normalize_order(raw.get("order"), observed_at=event.received_at)
            self.orders[order.exchange_order_id] = order
        elif event.kind == PrivateEventKind.POSITION_UPDATE:
            raw = event.payload.get("afterPosition", event.payload.get("after_position",
                event.payload.get("position", event.payload)))
            slug = (event.payload.get("marketSlug") or event.payload.get("market_slug")
                    or raw.get("marketSlug") or raw.get("market_slug"))
            if not slug and event.market_scope and len(event.market_scope) == 1:
                slug = event.market_scope[0]
            if slug not in self.scope or (event.market_scope and slug not in event.market_scope):
                raise AdapterError("candidate position update lacks exact scope")
            position = normalize_position(slug, raw, observed_at=event.received_at)
            self.position_scopes.add(slug)
            self.positions[slug] = position.net_qty

    async def run(self):
        try:
            async with asyncio.timeout(self.timeout):
                await self.stream.start(self.scope)
                self.started = True
                while True:
                    self.collect(await self.stream.next_event())
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.error = exc
            await self.stream.close()

    def ready(self, ledger):
        if (not self.started or self.error or self.partial_requests
                or not getattr(self.stream, "initial_state_ready", False)):
            return False
        expected_orders = {str(row["market_slug"]) for row in ledger.working_orders()}
        expected_positions = {str(row["market_slug"]) for row in ledger.query(
            "SELECT market_slug FROM execution_positions WHERE CAST(net_qty AS REAL)!=0")}
        return (expected_orders <= self.order_scopes
                and expected_positions <= self.position_scopes)

    def agrees(self, ledger):
        """Actual private account facts must agree with the final REST proof."""
        working = {str(row["exchange_order_id"]): row for row in ledger.working_orders()}
        reported = {key: order for key, order in self.orders.items()
                    if order.status in ACTIVE_ORDER_STATES}
        if set(working) != set(reported):
            return False
        # Called by the serial owner after positive candidate projection. Only
        # look up the established binding here; never adopt in the collector.
        if any(not RestFallbackRunner._order_matches_row(replace(reported[key],
                   client_order_id=(ledger.resolve_client_order_id(key)
                                    or reported[key].client_order_id)), row)
               for key, row in working.items()):
            return False
        expected_positions = {str(row["market_slug"]): Decimal(row["net_qty"])
            for row in ledger.query("SELECT market_slug, net_qty FROM execution_positions")
            if Decimal(row["net_qty"]) != 0}
        return ({slug: qty for slug, qty in self.positions.items() if qty != 0}
                == expected_positions)

    async def freeze(self):
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
        if self.error:
            raise self.error
        for _ in range(self.MAX_EVENTS):
            events = self.stream.drain_events(limit=1)
            if not events:
                return
            for event in events:
                self.collect(event)
        raise AdapterError("candidate private backlog exceeds handoff bound")

    async def close(self):
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
        await self.stream.close()


class AccountSourceRunner(RestFallbackRunner):
    """Permanent entry point; old runner classes remain replay-compatible."""

    def __init__(self, *args, account_state_mode="websocket", source_policy=None, **kwargs):
        self.source_policy = source_policy or AccountSourcePolicy()
        super().__init__(*args, account_state_mode=account_state_mode, **kwargs)
        self.ORDER_READ_CONCURRENCY = self.source_policy.read_concurrency
        self.source_control = AccountSourceControl(
            self.account_state_mode, self.ledger.path.parent)
        self._source_candidate = None
        self._source_switching = False
        self._source_scope = None
        self._source_last_publish = None
        self._source_mutations_in_flight = 0
        self.executor.gateway = SourceTradingGateway(self)
        self._snapshot_reader = AccountSnapshotReader()
        self._collected_history = None
        self._hybrid_counts = {"orders": 0, "hints": 0, "gaps": 0,
                               "ack_only": 0, "urgent": 0, "cadence": 0,
                               "scoped_admissions": 0, "covered_fill": 0}
        self._hybrid_fill_proof = None
        self._hybrid_covered_fills = {}
        self._hybrid_collecting_fill_coverage = False
        self._hybrid_collected_fills = ()
        self._hybrid_requires_reconcile = False
        self._hybrid_reducer_proofs = {}
        self._hybrid_last_report = None

    def _hybrid_order_ack_only(self, observation, now):
        """Recognize a known order's no-fill acknowledgement, never new exposure.

        This is only a refresh scheduling optimization. The normal projector
        still validates/applies the event; unknown fields/types retain full proof.
        """
        raw = (observation.payload.get("execution", {})
               if isinstance(observation.payload, dict) else {})
        if not isinstance(raw, dict):
            return False
        # Only an exact OPEN/NEW acknowledgement is harmless in hybrid mode.
        # A terminal cancel may race a private fill/position message that this
        # incomplete stream never delivers, so it always schedules REST proof.
        if raw.get("type") != "EXECUTION_TYPE_NEW":
            return False
        wire = raw.get("order", {})
        if not isinstance(wire, dict) or not any(
                key in wire for key in ("cumQuantity", "cum_quantity")):
            return False
        order = normalize_order(wire, observed_at=now,
            resolve_client_id=lambda exchange_id: self.ledger.resolve_client_order_id(exchange_id))
        client_id = self.ledger.resolve_client_order_id(order.exchange_order_id)
        if not client_id or client_id.startswith("venue:"):
            return False
        if order.status != "OPEN":
            return False
        rows = self.ledger.query(
            "SELECT * FROM execution_orders WHERE exchange_order_id=?",
            (order.exchange_order_id,))
        if not rows:
            return any(self._pending_place_matches_order(order, intent)
                       for intent in self.ledger.unresolved_intents())
        if len(rows) != 1:
            return False
        row = rows[0]
        return (row["status"] in {"OPEN", "PENDING", "PARTIAL"}
                and not row["unknown_reason"]
                and self._order_matches_row(replace(order, status=row["status"]), row))

    def _hybrid_fill_already_covered(self, observation, now):
        """Scheduling only: exact replay of positive history in the last full proof.

        A durable fill ID alone is insufficient. No new read, quantity inference,
        receipt reuse, timestamp renewal or bypass of the serial projector occurs.
        Keep only a bounded batch; a cache miss takes the ordinary urgent path.
        """
        raw = (observation.payload.get("execution", {})
               if isinstance(observation.payload, dict) else {})
        if not isinstance(raw, dict) or raw.get("type") not in {
                "EXECUTION_TYPE_FILL", "EXECUTION_TYPE_PARTIAL_FILL"}:
            return False
        proof = self._hybrid_fill_proof
        if proof is None or self.account_state_mode != "hybrid":
            return False
        scope, generation, started, completed = proof
        if (scope != tuple(self.active_slugs)
                or generation != self.source_control.generation
                or self._stop_requested or self._streams_need_rebind
                or self._source_switching or self._source_mutations_in_flight
                or self._hybrid_requires_reconcile
                or self._rest_poll_completed_at != completed
                or self.gate.last_reconcile_at != started
                or not started <= completed <= now
                or not self.gate.session_ready(self.ledger, now=now)
                or self._rest_poll_due(now)
                or self.ledger.unresolved_intents() or self.ledger.absent_orders()):
            return False
        wire = raw.get("order")
        # Normalizers have defaults for other consumers. Unknown wire fields
        # must not turn into evidence for this optional replay shortcut.
        if (not isinstance(wire, dict)
                or not any(wire.get(k) is not None for k in ("cumQuantity", "cum_quantity"))
                or not any(wire.get(k) for k in (
                    "lastTransactTime", "last_transact_time", "createTime", "create_time"))
                or not any(raw.get(k) for k in ("transactTime", "transact_time"))
                or not any(raw.get(k) is not None for k in (
                    "commissionNotionalCollected", "commission_notional_collected"))):
            return False
        resolver = self.ledger.resolve_client_order_id
        execution = normalize_execution(raw, observed_at=now, resolve_client_id=resolver)
        covered = self._hybrid_covered_fills.get(execution.execution_id)
        if (covered is None or execution.market_slug not in scope
                or execution.event_at > started
                or covered.trade_state == "TRADE_STATE_BUSTED"
                # REST enriches trade lifecycle/taker metadata; neither is a
                # private update here. Every identity and economic field matches.
                or replace(covered, liquidity=execution.liquidity,
                           trade_state=execution.trade_state) != execution):
            return False
        order = normalize_order(wire, observed_at=now, resolve_client_id=resolver)
        rows = self.ledger.query(
            "SELECT * FROM execution_orders WHERE exchange_order_id=?",
            (execution.exchange_order_id,))
        fills = self.ledger.query(
            "SELECT reversed,trade_id FROM execution_fills WHERE execution_id=?",
            (execution.execution_id,))
        return (len(rows) == 1 and len(fills) == 1
                and not fills[0]["reversed"]
                and fills[0]["trade_id"] == execution.trade_id
                and not rows[0]["unknown_reason"]
                and not order.client_order_id.startswith("venue:")
                and order.event_at <= started
                and order.event_at == datetime.fromisoformat(rows[0]["event_at"])
                and self._order_matches_row(order, rows[0]))

    def _hybrid_order_needs_immediate_proof(self, observation, now):
        """Wake REST for fills, malformed data or changed order facts.

        Flat terminal/no-fill notifications still bar new POSTs until the ordinary
        REST cadence proves the account, but do not each restart a whole-account
        proof. A synchronized expiry wave can therefore coalesce into the same
        proof REST mode would have performed. Malformed data remains urgent.
        """
        raw = (observation.payload.get("execution", {})
               if isinstance(observation.payload, dict) else {})
        if not isinstance(raw, dict):
            return True
        execution_type = str(raw.get("type") or "").upper()
        if execution_type in {
                "EXECUTION_TYPE_FILL", "EXECUTION_TYPE_PARTIAL_FILL"}:
            return True
        if execution_type not in {
                "EXECUTION_TYPE_NEW", "EXECUTION_TYPE_CANCEL",
                "EXECUTION_TYPE_CANCELED", "EXECUTION_TYPE_EXPIRED",
                "EXECUTION_TYPE_REJECTED"}:
            return True
        wire = raw.get("order", {})
        if not isinstance(wire, dict):
            return True
        if not any(key in wire for key in ("cumQuantity", "cum_quantity")):
            return True
        try:
            order = normalize_order(wire, observed_at=now,
                resolve_client_id=lambda exchange_id:
                    self.ledger.resolve_client_order_id(exchange_id))
        except AdapterError:
            return True
        rows = self.ledger.query(
            "SELECT * FROM execution_orders WHERE exchange_order_id=?",
            (order.exchange_order_id,))
        if len(rows) != 1:
            return True
        row = rows[0]
        # A GTD entry can become the remaining exact reducer after the other
        # side fills. Its terminal event must wake proof now, not wait behind
        # flat expiry batching. This schedules REST; it does not authorize an
        # exit or infer a position from the private order notification.
        if (order.status in TERMINAL_ORDER_STATES
                and row["status"] in ACTIVE_ORDER_STATES
                and self.ledger.position(order.market_slug) != 0
                and not any(intent["operation"] == "CANCEL"
                            and intent["exchange_order_id"] == order.exchange_order_id
                            for intent in self.ledger.unresolved_intents(order.market_slug))):
            # A bot-requested cancel already owns immediate scoped confirmation
            # in _decide_for_market. Resetting its global poll clock would add
            # a second full proof before the replacement reducer.
            return True
        # Ignore only the expected state transition itself. Any changed
        # identity/economic field or cumulative quantity is anomalous enough to
        # demand immediate authoritative proof.
        return not self._order_matches_row(replace(order, status=row["status"]), row)

    def _hybrid_reducer_certificate_current(self, slug):
        """Common certificate checks, before planning and at final admission."""
        proof = self._hybrid_reducer_proofs.get(slug)
        if proof is None:
            return False
        scope, generation, quantity, started = proof
        return (scope == tuple(self.active_slugs)
                and generation == self.source_control.generation
                and self.account_state_mode == "hybrid"
                and 0 <= (self._now() - started).total_seconds() <= 30
                and quantity != 0 and quantity == self.ledger.position(slug)
                and not self.private_stream.pending
                and not self.ledger.working_orders(slug)
                and not self.ledger.absent_orders())

    async def _prepare_market_decision(self, slug):
        if self.account_state_mode != "hybrid":
            return await super()._prepare_market_decision(slug)
        scope, generation = tuple(self.active_slugs), self.source_control.generation
        held = self.ledger.position(slug) != 0
        await self._drain_private_events()
        if (self._stop_requested or scope != tuple(self.active_slugs)
                or generation != self.source_control.generation
                or self.account_state_mode != "hybrid"):
            return False
        if held and self.ledger.position(slug) == 0:
            return False  # An exit pass cannot become an extra entry pass.
        if (self._hybrid_requires_reconcile and self.ledger.position(slug) != 0
                and not self.ledger.working_orders(slug)
                and not self.ledger.unresolved_intents()
                and not self.ledger.absent_orders()):
            if not self._hybrid_reducer_certificate_current(slug):
                try:
                    # One bounded proof, not a retry loop or global gate renewal.
                    await self._reconcile_position_state(slug, tag="hybrid-preplan")
                except (LedgerConflict, RuntimeInvariantViolation):
                    raise
                except AdapterError as exc:
                    await self._report_runtime_error("hybrid_preplan_proof", exc)
                    return False
            # Do not reserve a hard-exit attempt for a known-unsendable action.
            return (not self._stop_requested
                    and self._hybrid_reducer_certificate_current(slug))
        return True

    def _entry_observation_pending(self):
        return (self.account_state_mode == "hybrid"
                and (self.private_stream.pending or self._hybrid_requires_reconcile))

    def _hybrid_reduction_proved(self, slug, *, consume=False):
        """A completed scoped REST proof clears only this reducer's hint veto.

        It does not renew the global gate or polling clock. Pending stream data,
        another observation/proof, changed scope/quantity, ambiguity or age still
        refuse. The usual REST gateway and driver checks remain mandatory.
        """
        if not self._hybrid_reducer_certificate_current(slug):
            return False
        quantity = self._hybrid_reducer_proofs[slug][2]
        pending = self.ledger.unresolved_intents()
        if len(pending) != 1:
            return False
        intent = pending[0]
        if not (intent["market_slug"] == slug and intent["operation"] == "PLACE"
                and intent["state"] == "SUBMITTING" and not intent["exchange_order_id"]):
            return False
        try:
            payload = json.loads(intent["payload_json"])
            valid = (Decimal(str(payload.get("quantity"))) == abs(quantity)
                    and payload.get("marketSlug") == slug
                    and payload.get("intent") == (
                        "ORDER_INTENT_BUY_SHORT" if quantity > 0
                        else "ORDER_INTENT_BUY_LONG"))
        except (ValueError, ArithmeticError, AttributeError, TypeError):
            return False
        if valid and consume:
            self._hybrid_reducer_proofs.pop(slug, None)
            self._hybrid_counts["scoped_admissions"] += 1
        return valid

    async def _observe_private(self, event, now):
        # Even a no-op/private gap ends same-cycle execution-proof reuse. It
        # does not invalidate the independent REST gate or change poll cadence.
        self._execution_proof_event_revision += 1
        self._invalidate_execution_proof()
        if not isinstance(event, HybridDelivery):
            return await super()._observe_private(event, now)
        stream = event.stream
        if stream.already_applied(event):
            return
        observation = event.event
        # No private observation renews the REST authority/quantity clock.
        # An uncertain notification schedules proof and bars POST admission. Keep
        # the preceding valid REST certificate only as eligibility for the
        # existing two-list unchanged-flat-order optimization; destroying it
        # on every event would force 30 exact reads per notification. Failed
        # schema/integrity/REST proof still revokes the certificate entirely.
        # Routine terminal notifications wait for the already-bounded REST
        # cadence so an expiry wave produces one proof rather than one proof per
        # frame. Fill evidence and malformed data still wake REST immediately.
        # A transport gap only removes an optional accelerator: current REST
        # authority remains exactly as valid as it is in explicit REST mode.
        needs_proof = False
        immediate_proof = False
        affected_slug = None
        try:
            if isinstance(observation, HybridGap):
                self._hybrid_counts["gaps"] += 1
            elif observation.kind == PrivateEventKind.ORDER_UPDATE:
                covered_fill = self._hybrid_fill_already_covered(observation, now)
                needs_proof = not (covered_fill
                    or self._hybrid_order_ack_only(observation, now))
                if needs_proof:
                    immediate_proof = self._hybrid_order_needs_immediate_proof(
                        observation, now)
                # Reuse the exact existing order/fill identity, ownership and
                # idempotence rules. Its private gate is scratch only; balances
                # and complete account proof still belong to REST.
                active_gate = self.gate
                self.gate = PrivateStateGate()
                try:
                    await CommissioningRunner._observe_private(self, observation, now)
                finally:
                    self.gate = active_gate
                self._hybrid_counts["orders"] += 1
                raw = (observation.payload.get("execution", {})
                       if isinstance(observation.payload, dict) else {})
                wire = raw.get("order", {}) if isinstance(raw, dict) else {}
                affected_slug = (str(wire.get("marketSlug")
                                     or wire.get("market_slug") or "").strip()
                                 if isinstance(wire, dict) else None)
                if not needs_proof:
                    self._hybrid_counts["covered_fill" if covered_fill else "ack_only"] += 1
            else:
                # Initial snapshots can be delayed/partial. Global position
                # updates may omit market identity. Both trigger full REST
                # proof; neither can clear orders or overwrite exact quantity.
                self._hybrid_counts["hints"] += 1
                needs_proof = True
                immediate_proof = observation.kind == PrivateEventKind.POSITION_UPDATE
                for slug in self.active_slugs:
                    self._mark_market_dirty(slug, now)
        except (LedgerConflict, RuntimeInvariantViolation):
            self.gate.on_reconciliation_conflict()
            raise
        except AdapterError:
            # Wire/schema failure removes this optional shortcut. Integrity
            # failures above retain the existing terminal handling.
            stream.request_reconnect()
            self.gate.on_reconciliation_conflict()
            self._hybrid_counts["gaps"] += 1
            needs_proof = True
            immediate_proof = True
        finally:
            if needs_proof:
                self._hybrid_fill_proof = None
                self._hybrid_covered_fills.clear()
                if immediate_proof or not affected_slug:
                    self._hybrid_reducer_proofs.clear()
                else:
                    self._hybrid_reducer_proofs.pop(affected_slug, None)
                self._hybrid_requires_reconcile = True
                self._hybrid_counts[
                    "urgent" if immediate_proof else "cadence"] += 1
                if immediate_proof:
                    self._rest_poll_completed_at = None
                if affected_slug:
                    self._position_confirmation_proofs.pop(affected_slug, None)
                    self._hard_exit_terminal_proofs.pop(affected_slug, None)
                    self._hard_exit_retry_authorizations.pop(affected_slug, None)
                else:
                    self._position_confirmation_proofs.clear()
                    self._hard_exit_terminal_proofs.clear()
                    self._hard_exit_retry_authorizations.clear()
            stream.applied(event)

    async def _rest_reconcile(self, now, *, tag="", strict_order_markets=None):
        self._hybrid_reducer_proofs.clear()
        if self.account_state_mode == "hybrid":
            await self._drain_private_events()
            now = self._now()
            # The base periodic path can return without collecting anything.
            # Preserve an applied hint until an actual full proof, rather than
            # treating that cadence no-op as reconciliation completion.
            if (self.rest_polling_policy.enabled and tag == "periodic"
                    and self.gate.session_ready(self.ledger, now=now)
                    and not self._rest_poll_due(now)):
                return
        self._hybrid_fill_proof = None
        self._hybrid_covered_fills.clear()
        self._hybrid_collected_fills = ()
        scope, generation = tuple(self.active_slugs), self.source_control.generation
        self._hybrid_collecting_fill_coverage = self.account_state_mode == "hybrid"
        try:
            result = await super()._rest_reconcile(
                now, tag=tag, strict_order_markets=strict_order_markets)
        finally:
            self._hybrid_collecting_fill_coverage = False
        if self.account_state_mode == "hybrid":
            self._hybrid_requires_reconcile = False
            if (scope == tuple(self.active_slugs)
                    and generation == self.source_control.generation
                    and self.gate.session_ready(self.ledger, now=self._now())):
                self._hybrid_fill_proof = (scope, generation,
                    self.gate.last_reconcile_at, self._rest_poll_completed_at)
                self._hybrid_covered_fills = {
                    item.execution_id: item for item in self._hybrid_collected_fills}
        self._hybrid_collected_fills = ()
        return result

    async def _reconcile_position_state(self, slug, *, tag):
        self._hybrid_fill_proof = None
        self._hybrid_covered_fills.clear()
        self._hybrid_reducer_proofs.clear()
        if self.account_state_mode == "hybrid":
            await self._drain_private_events()
        started = self._now()
        scope, generation = tuple(self.active_slugs), self.source_control.generation
        result = await super()._reconcile_position_state(slug, tag=tag)
        if result is not None:
            started = result.started_at  # Reuse never manufactures fresh proof.
        if (self.account_state_mode == "hybrid"
                and scope == tuple(self.active_slugs)
                and generation == self.source_control.generation
                and not self.private_stream.pending
                and self.ledger.position(slug) != 0
                and not self.ledger.working_orders(slug)
                and not self.ledger.unresolved_intents()
                and not self.ledger.absent_orders()):
            self._hybrid_reducer_proofs[slug] = (
                tuple(self.active_slugs), self.source_control.generation,
                self.ledger.position(slug), started)
        elif self.account_state_mode == "hybrid":
            # The base REST helper may have cached its own scoped certificate.
            # If a private event arrived during the read, neither layer may
            # reuse that now-stale proof on the next priority pass.
            self._position_confirmation_proofs.pop(slug, None)
        return result

    def _catalog_positions(self):
        return {str(row["market_slug"]): Decimal(row["net_qty"])
                for row in self.ledger.query(
                    "SELECT market_slug,net_qty FROM execution_positions")
                if Decimal(row["net_qty"]) != 0}

    async def _replacement_candidates_for_top_up(self, *args, **kwargs):
        # The caller has settled task-held stream events before entering top-up.
        # Keep this scope around collection only, never around release/commit.
        if not self.active_slugs or self.driver is None:
            return await super()._replacement_candidates_for_top_up(*args, **kwargs)
        source = self.account_state_mode
        generation = self.source_control.generation
        scope = tuple(self.active_slugs)
        initial_positions = self._catalog_positions()
        watch = PositionWatch(self.account.fetch_positions, self._now)

        async def checkpoint():
            if (self._stop_requested or source != self.account_state_mode
                    or generation != self.source_control.generation
                    or scope != tuple(self.active_slugs)):
                raise ApiAdmissionDeferred("catalog interrupted by owner state",
                    error_code="LOCAL_CATALOG_INTERRUPTED", retry_after_seconds=1)
            if self.account_state_mode == "hybrid":
                if self.private_stream.pending:
                    await self._drain_private_events()
                # Classify before interrupting optional work. Exact no-fill
                # ACKs and listener gaps leave REST authority intact; aborting
                # on queue presence alone repeatedly discarded healthy scans.
                # Earlier applied uncertainty must still interrupt even when
                # the queue is now empty. The REST position watch below stays
                # active because optional private delivery can be incomplete.
                if self._hybrid_requires_reconcile:
                    raise ApiAdmissionDeferred("catalog yielded to private observation",
                        error_code="LOCAL_CATALOG_ACCOUNT_CHANGED", retry_after_seconds=1)
            if not self._using_rest_source():
                try:
                    await self._drain_private_events()
                except AdapterError as exc:
                    self.gate.on_disconnect()
                    self._streams_need_rebind = True
                    raise ApiAdmissionDeferred("catalog yielded to private recovery",
                        error_code="LOCAL_CATALOG_PRIVATE_RECOVERY", retry_after_seconds=1) from exc
                current_positions = self._catalog_positions()
                if (current_positions != initial_positions
                        or set(current_positions) & self._priority_replan_slugs):
                    raise ApiAdmissionDeferred("catalog yielded to private position service",
                        error_code="LOCAL_CATALOG_ACCOUNT_CHANGED", retry_after_seconds=1)
            else:
                try:
                    observation = watch.take_ready()
                except BaseException as exc:
                    self.gate.on_reconciliation_conflict()
                    self._rest_poll_completed_at = None
                    if isinstance(exc, AdapterError):
                        raise ApiAdmissionDeferred("catalog account watch failed",
                            error_code="LOCAL_CATALOG_WATCH_FAILED", retry_after_seconds=1) from exc
                    raise
                if observation is not None:
                    positions = {row.market_slug: row.net_qty
                                 for row in observation.positions if row.net_qty != 0}
                    age = (self._now() - observation.started_at).total_seconds()
                    if (not 0 <= age <= 30
                            or positions != self._catalog_positions()):
                        # A change/absence is a wakeup, never authoritative
                        # flatness or quantity. The unchanged full proof runs
                        # before the ordinary position handler may mutate.
                        self.gate.on_reconciliation_conflict()
                        self._rest_poll_completed_at = None
                        print(f"[account-watch] at={self._now().isoformat()} "
                              f"source={source} outcome=reconcile_required "
                              f"observation_age_seconds={age:.3f}", flush=True)
                        raise ApiAdmissionDeferred("catalog yielded to account observation",
                            error_code="LOCAL_CATALOG_ACCOUNT_CHANGED", retry_after_seconds=1)
                interval = (self.rest_polling_policy.active_poll_seconds
                            if self._has_open_position() or self.ledger.unresolved_intents()
                            else self.rest_polling_policy.normal_poll_seconds)
                if not self.rest_polling_policy.enabled:
                    interval = self.REST_POLL_SECONDS
                # Background collection must inherit neither the catalog
                # callback (which reads the ledger) nor its lower-priority lane.
                with self.gateway.read_checkpoints(None), self.gateway.traffic_class("safety"):
                    watch.start_if_due(source=source, generation=generation, scope=scope,
                                       interval=interval)
            if self._position_near_its_flatten_deadline(self._now()):
                raise ApiAdmissionDeferred("catalog yielded to position service",
                    error_code="LOCAL_CATALOG_POSITION_PRIORITY", retry_after_seconds=1)

        try:
            with self.gateway.read_checkpoints(checkpoint):
                await checkpoint()
                result = await super()._replacement_candidates_for_top_up(*args, **kwargs)
                await checkpoint()
                return result
        finally:
            await watch.close()

    def _set_source_descriptions(self):
        super()._set_source_descriptions()
        if self._using_rest_source():
            self.PENDING_ENTRY_ORDER_CEILING = self.source_policy.rest_pending_entry_ceiling
        if self.account_state_mode == "hybrid":
            self.ACCOUNT_STATE_DESCRIPTION = "REST account proof with private WebSocket assistance"
            self.SOURCE_REBOUND_DESCRIPTION = "rebound market/hybrid account sources"

    async def _read_exact_order_states(self, exchange_ids, now):
        semaphore = asyncio.Semaphore(self.ORDER_READ_CONCURRENCY)

        async def read(exchange_id):
            async with semaphore:
                identities = DeferredIdentityResolver()
                order = await self.executor.read_order_state(
                    exchange_id, observed_at=now, resolve_client_id=identities)
                return order, identities

        tasks = [asyncio.create_task(read(item)) for item in exchange_ids]
        try:
            results = await asyncio.gather(*tasks, return_exceptions=True)
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        failures = []
        not_found = set()
        throttle = None
        # Workers only read; one synchronous ordered projector owns the ledger.
        for exchange_id, result in zip(exchange_ids, results):
            if isinstance(result, (RuntimeInvariantViolation, LedgerConflict)):
                raise result
            if isinstance(result, BaseException):
                if isinstance(result, asyncio.CancelledError):
                    raise result
                failures.append((exchange_id, f"{type(result).__name__}: {result}"))
                if isinstance(result, AdapterError) and is_not_found(result):
                    not_found.add(exchange_id)
                if read_backoff.is_backpressure(result):
                    throttle = result
            else:
                order, identities = result
                resolved = identities.project(self.ledger.resolve_client_order_id)
                order = replace(order, client_order_id=(resolved.get(exchange_id)
                                                       or order.client_order_id))
                self.executor.apply_order_state(exchange_id, order, observed_at=now)
        if self.account_state_mode == "hybrid" and self.private_stream.pending:
            # All read workers have joined. Apply the held/queued stream prefix
            # on this serial owner before declaring a failed lookup unresolved.
            # Only a newly applied positive terminal transition can supersede
            # a 404. Auth/transport/schema failures keep their original meaning.
            before = {row["exchange_order_id"]: row["status"] for row in
                      self.ledger.working_orders(owned_only=True)}
            await self._drain_private_events()
            proved = set()
            for exchange_id in not_found:
                rows = self.ledger.query(
                    "SELECT * FROM execution_orders WHERE exchange_order_id=?",
                    (exchange_id,))
                if len(rows) != 1 or exchange_id not in before:
                    continue
                row = rows[0]
                if (row["status"] in TERMINAL_ORDER_STATES
                        and not row["unknown_reason"]
                        and not str(row["client_order_id"]).startswith("venue:")
                        and row["market_slug"] in self.active_slugs
                        and datetime.fromisoformat(row["received_at"]) >= now
                        and not self.ledger.unresolved_intents(row["market_slug"])
                        and not self.ledger.absent_orders(row["market_slug"])):
                    proved.add(exchange_id)
            failures = [(key, detail) for key, detail in failures if key not in proved]
        if throttle is not None:
            # Join every worker and preserve successful positive facts first.
            # Do not erase typed endpoint backpressure into a per-owner string.
            raise throttle
        return failures

    async def _collect_rest_snapshots(self, now):
        if not self._using_rest_source():
            return await super()._collect_rest_snapshots(now)
        # Order-list validation can demand exact reads and apply positive
        # foreign/terminal facts, so it remains on the serial owner. Only the
        # pure complete account reads below may overlap.
        orders = await self._fetch_rest_order_snapshot(now)
        identities = DeferredIdentityResolver()
        history_reader = AsyncAccountSnapshotGateway(self.gateway, resolve_client_id=identities)
        history_overlap_pages = self._execution_history_overlap_pages(now)

        async def already_read_orders():
            return orders

        async def history():
            return await history_reader.fetch_executions(
                observed_at=now, require_coverage=True,
                known_execution_ids=self._rest_history_seen,
                minimum_overlap_pages=history_overlap_pages)

        observation = await self._snapshot_reader.collect(
            source=self.account_state_mode,
            generation=self.source_control.generation, scope=self.active_slugs,
            clock=self._now, orders=already_read_orders,
            positions=lambda: self.account.fetch_positions(observed_at=now),
            history=history)
        if (observation.generation != self.source_control.generation
                or observation.source != self.account_state_mode
                or observation.scope != tuple(self.active_slugs)):
            raise AdapterError("obsolete account observation")
        # Projection/adoption resumes on the serial owner, never in a worker.
        resolved = identities.project(self.ledger.resolve_client_order_id)
        if history_overlap_pages == 2:
            self._rest_history_deep_completed_at = self._now()
        self._collected_history = replace(observation.history, executions=tuple(
            replace(item, client_order_id=(resolved.get(item.exchange_order_id)
                                          or item.client_order_id))
            for item in observation.history))
        return observation.orders, observation.positions

    async def _fetch_execution_history(self, now):
        if self._using_rest_source() and self._collected_history is not None:
            batch, self._collected_history = self._collected_history, None
            self._rest_history_pending = frozenset(item.execution_id for item in batch)
            if self._hybrid_collecting_fill_coverage:
                self._hybrid_collected_fills = tuple(islice(
                    (item for item in batch if item.market_slug in self.active_slugs), 512))
            return batch
        if self._source_switching:
            return await self.account.fetch_executions(
                observed_at=now, require_coverage=True,
                known_execution_ids=self._rest_history_seen)
        return await super()._fetch_execution_history(now)

    async def _read_owned_order_states(self, exchange_ids, now):
        if self._source_switching:
            return await self._read_exact_order_states(exchange_ids, now)
        return await super()._read_owned_order_states(exchange_ids, now)

    async def preflight(self):
        result = await super().preflight()
        self.source_control.persist_selected(self.account_state_mode)
        self.source_control.state = "active"
        self._publish_source()
        return result

    def _publish_source(self):
        try:
            self.source_control.publish(self._now())
        except OSError:
            # Reporting failure cannot stop positions. The control CLI refuses
            # stale status; persisting an actual source choice remains strict.
            print("[account-source] status_write_failed; control may be unavailable", flush=True)
        self._source_last_publish = self._now()
        if (isinstance(self.private_stream, HybridPrivateStream)
                and (self._hybrid_last_report is None
                     or (self._now() - self._hybrid_last_report).total_seconds() >= 60)):
            self._hybrid_last_report = self._now()
            print(f"[hybrid-account] state={self.private_stream.state} "
                  f"pending={self.private_stream.pending} "
                  f"orders={self._hybrid_counts['orders']} "
                  f"hints={self._hybrid_counts['hints']} gaps={self._hybrid_counts['gaps']} "
                  f"ack_only={self._hybrid_counts['ack_only']} "
                  f"covered_fill={self._hybrid_counts['covered_fill']} "
                  f"urgent={self._hybrid_counts['urgent']} "
                  f"cadence={self._hybrid_counts['cadence']} "
                  f"scoped_admissions={self._hybrid_counts['scoped_admissions']} "
                  "authority=rest", flush=True)

    async def _source_notice(self, state, reason):
        control = self.source_control
        control.state, control.reason = state, reason
        self._publish_source()
        print(f"[account-source] selected={control.selected} state={state} "
              f"generation={control.generation} reason={reason}", flush=True)
        try:
            await self.notifier.send_async(
                f"Account source: {control.selected.upper()} | {state} | {reason}")
        except Exception:
            print("[account-source] notification_failed", flush=True)

    async def _project_candidate_events(self, candidate, *, aborting=False):
        # Positive fills/terminal evidence must survive a failed promotion.
        # A separate gate observes delivery but cannot grant risk before REST.
        # Only this coroutine writes the authoritative ledger.
        active_gate = self.gate
        scratch_gate = PrivateStateGate()
        scratch_gate.on_connect()
        self.gate = scratch_gate
        try:
            while candidate.events:
                event = candidate.events.popleft()
                if event.kind == PrivateEventKind.ERROR:
                    scratch_gate.on_disconnect()
                    continue
                try:
                    await CommissioningRunner._observe_private(self, event, self._now())
                except AdapterError:
                    if not aborting:
                        raise
                    # Malformed candidate data cannot stop a healthy selected
                    # source. Its gate is already revoked; retain subsequent
                    # positive evidence and require ordinary reconciliation.
                    scratch_gate.on_disconnect()
        finally:
            self.gate = active_gate
        return scratch_gate

    async def _abort_source_switch(self, reason):
        candidate, self._source_candidate = self._source_candidate, None
        try:
            if candidate is not None:
                await candidate.close()
                if any(event.kind in {PrivateEventKind.ORDER_SNAPSHOT,
                                      PrivateEventKind.ORDER_UPDATE,
                                      PrivateEventKind.POSITION_SNAPSHOT,
                                      PrivateEventKind.POSITION_UPDATE}
                       for event in candidate.events):
                    self.gate.on_reconciliation_conflict()
                    await self._project_candidate_events(candidate, aborting=True)
        finally:
            if candidate is not None:
                # Candidate request IDs are unique to its connection. Never
                # retain an abandoned partial snapshot for a later handoff.
                for kind, request_id in candidate.partial_requests:
                    pages = (self.projector._order_pages if kind == PrivateEventKind.ORDER_SNAPSHOT
                             else self.projector._position_pages)
                    pages.pop(request_id, None)
            self.source_control.request = None
            self._source_scope = None
            self._source_switching = False
            await self._source_notice("failed", reason)

    async def _retry_uncertain_cancels(self, now, *, markets=None):
        # A source handoff is read-only. Existing normal recovery still owns
        # positively-authorized cancel retries after the handoff returns.
        if not self._source_switching:
            return await super()._retry_uncertain_cancels(now, markets=markets)

    async def _source_checkpoint(self, private_task=None, *, allow_commit=True):
        """Called only by the serial decision/recovery owner, never a reader."""
        control = self.source_control
        now = self._now()
        if self._stop_requested:
            if control.request:
                await self._abort_source_switch("operator_stop")
            return private_task
        if self._source_last_publish is None or (
                now - self._source_last_publish).total_seconds() >= 5:
            if not control.request:
                control.state = ("active" if self.gate.session_ready(self.ledger, now=now)
                                 else "unavailable")
            self._publish_source()
        try:
            request = control.take_request(now)
        except (ValueError, TypeError, OSError):
            await self._source_notice("failed", "invalid_or_stale_request")
            return private_task
        if request:
            self._source_scope = tuple(self.active_slugs)
            if not self._source_scope:
                await self._abort_source_switch("no_bound_scope")
                return private_task
            if request.target == "websocket":
                self._source_candidate = PrivateCandidate(
                    self._websocket_factory(self.client), self._source_scope,
                    timeout=self.source_policy.verification_seconds)
                self._source_candidate.task = asyncio.create_task(
                    self._source_candidate.run())
            await self._source_notice("verifying", "operator_request")
        request = control.request
        if request is None:
            return private_task
        if (not request.valid_for(control, self._now())
                or self._source_scope != tuple(self.active_slugs)):
            await self._abort_source_switch("expired_or_scope_changed")
            return private_task
        candidate = self._source_candidate
        if candidate and candidate.error:
            await self._abort_source_switch("candidate_failed_or_timed_out")
            return private_task
        if candidate and not candidate.ready(self.ledger):
            return private_task
        if not allow_commit:
            return private_task
        if self._source_mutations_in_flight:
            return private_task
        # A healthy passive position can transfer; an uncovered/urgent owner
        # gets its existing exit path first. No flatten is required to switch.
        if self._position_near_its_flatten_deadline(self._now()):
            await self._abort_source_switch("deferred_for_position_exit")
            return private_task
        return await self._commit_source_switch(private_task)

    async def _commit_source_switch(self, private_task):
        control, request = self.source_control, self.source_control.request
        old_mode, old_gate = self.account_state_mode, self.gate
        old_stream, old_factory = self.private_stream, self.private_stream_factory
        candidate = self._source_candidate
        self._source_switching = True
        promoted = False
        try:
            # Take the earlier task-held event BEFORE draining later events.
            if private_task is not None:
                if not private_task.done():
                    private_task.cancel()
                result = (await asyncio.gather(private_task, return_exceptions=True))[0]
                if isinstance(result, (LedgerConflict, RuntimeInvariantViolation)):
                    raise result
                if not isinstance(result, BaseException) and result is not None:
                    await self._observe_private(result, self._now())
                elif isinstance(result, Exception):
                    old_gate.on_disconnect()
                private_task = None
            if old_mode == "websocket":
                # A dead selected feed must not prevent an explicitly requested
                # REST proof. Drain one item at a time so an error cannot discard
                # earlier positive events returned by a bulk drain.
                drain = getattr(old_stream, "drain_events", None)
                if callable(drain):
                    for _ in range(PrivateCandidate.MAX_EVENTS * 3):
                        try:
                            events = drain(limit=1)
                        except AdapterError:
                            old_gate.on_disconnect()
                            continue
                        if not events:
                            break
                        for event in events:
                            await self._observe_private(event, self._now())
                    else:
                        raise AdapterError("selected private backlog exceeds handoff bound")
            if candidate:
                await candidate.freeze()
                candidate_gate = await self._project_candidate_events(candidate)
                if not candidate.ready(self.ledger):
                    raise AdapterError("candidate private state incomplete")
                self.private_stream = candidate.stream
                self.private_stream_factory = self._websocket_factory
                self.gate = candidate_gate
            else:
                self.private_stream = DisabledPrivateStream(self.client)
                await self.private_stream.start(self.active_slugs)
                self.private_stream_factory = DisabledPrivateStream
                self.gate = RestStateGate(max_silence_seconds=30)
                self.gate.on_connect()
            self.account_state_mode = request.target
            self._set_source_descriptions()
            self.REQUIRE_EXECUTION_BACKFILL = True
            self._hard_exit_terminal_proofs.clear()
            self._hard_exit_retry_authorizations.clear()
            self._rest_poll_completed_at = None
            started = self._now()
            # Exact order proof plus complete account snapshots/history. No
            # candidate or background task is allowed to execute this method.
            async with asyncio.timeout(30):
                await self._rest_reconcile(started, tag="operator-source-handoff")
                await self._refresh_balance(self._now())
                if request.target == "websocket":
                    # Include events that arrived during the balance read too.
                    # Project one at a time; a following error loses no fill.
                    for _ in range(PrivateCandidate.MAX_EVENTS):
                        events = self.private_stream.drain_events(limit=1)
                        if not events:
                            break
                        for event in events:
                            candidate.collect(event)
                            await self._observe_private(candidate.events.popleft(), self._now())
                    else:
                        raise AdapterError("candidate backlog exceeds handoff bound")
                    self.private_stream.assert_live(now=self._now())
            self._assert_runtime_scope()
            if (self._stop_requested or not request.valid_for(control, self._now())
                    or tuple(self.active_slugs) != self._source_scope
                    or not 0 <= (self._now() - started).total_seconds() <= 30
                    or not self.gate.ready(self.ledger, now=self._now())
                    or (candidate is not None and not candidate.ready(self.ledger))
                    or (candidate is not None and not candidate.agrees(self.ledger))
                    or self._position_near_its_flatten_deadline(self._now())):
                raise AdapterError("handoff proof stopped, stale, ambiguous or urgent")
            # No await between persistence and the in-memory generation commit.
            # A crash after persistence restarts into full normal preflight.
            control.persist_selected(request.target)
            control.selected = request.target
            control.generation += 1
            control.request = None
            self._source_candidate = None
            self._source_scope = None
            promoted = True
            self._set_source_descriptions()
            retire = getattr(old_stream, "retire_with_events", None)
            if old_mode == "websocket" and callable(retire):
                tail = await retire()
                if tail:
                    self.gate.on_reconciliation_conflict()
                    retired = PrivateCandidate(old_stream, self.active_slugs)
                    retired.events.extend(tail)
                    await self._project_candidate_events(retired)
                    # Preserve positive late fills but expire the pre-tail
                    # proof; ordinary REST reconciliation must renew authority.
                    self._rest_poll_completed_at = None
            else:
                await old_stream.close()
            for slug in self.active_slugs:
                self._mark_market_dirty(slug, self._now())
            await self._source_notice("active", "handoff_completed")
        except BaseException as exc:
            if promoted:
                # The committed source owns execution; cleanup/report failure
                # must never silently switch back or rewrite the saved mode.
                raise
            if self.private_stream is not old_stream:
                await self.private_stream.close()
            self.account_state_mode = old_mode
            self.private_stream, self.private_stream_factory = old_stream, old_factory
            self.gate = old_gate
            self.gate.on_reconciliation_conflict()
            self._set_source_descriptions()
            await self._abort_source_switch("handoff_proof_failed")
            if isinstance(exc, (LedgerConflict, RuntimeInvariantViolation,
                                asyncio.CancelledError)):
                raise
        finally:
            self._source_switching = False
        return asyncio.create_task(self.private_stream.next_event())

    async def _consume_ready_stream_tasks(self, market_task, private_task):
        private_task = await self._source_checkpoint(private_task)
        return await super()._consume_ready_stream_tasks(market_task, private_task)

    async def _refresh_execution_state_for_decision(self, now):
        if self.account_state_mode == "hybrid":
            await self._drain_private_events()
            now = self._now()
        await self._source_checkpoint(allow_commit=False)
        return await super()._refresh_execution_state_for_decision(now)

    async def _account_source_recovery_checkpoint(self):
        before = self.source_control.generation
        task = await self._source_checkpoint()
        # Recovery has no outer next_event task. Do not leave a second consumer
        # behind: apply any event it obtained, otherwise cancel its idle read.
        if task is not None:
            if not task.done():
                task.cancel()
            result = (await asyncio.gather(task, return_exceptions=True))[0]
            if not isinstance(result, BaseException) and result is not None:
                await self._observe_private(result, self._now())
        return before != self.source_control.generation

    async def _shutdown(self, reason):
        if self.source_control.request:
            try:
                await self._abort_source_switch("shutdown")
            except Exception as exc:
                self.gate.on_reconciliation_conflict()
                await self._report_runtime_error("account_source_shutdown", exc)
                reason = "critical:account_source_shutdown"
        self.source_control.state = "stopping"
        self._publish_source()
        return await super()._shutdown(reason)

    async def run(self):
        try:
            return await super().run()
        finally:
            if self._source_candidate is not None:
                await self._source_candidate.close()
            self.source_control.state = "stopped"
            self._publish_source()
