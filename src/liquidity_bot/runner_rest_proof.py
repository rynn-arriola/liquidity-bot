"""REST account-proof collection and reconciliation for the commissioning runner.

Moved verbatim from ``live_commissioning.CommissioningRunner`` (pure
relocation, no behavior change). Methods run on the runner instance via
inheritance, so every ``self`` attribute and override resolves as before.
"""

from __future__ import annotations

from .api_diagnostics import api_workflow
from datetime import datetime
from decimal import Decimal
from typing import Any
from .errors import AdapterError, is_not_found
from .execution_ledger import TERMINAL_ORDER_STATES, LedgerConflict
from .runner_shared import ORPHAN_PLACE_CLEAR_MIN_SECONDS, ORPHAN_PLACE_CLEAR_PROOFS


class RestProofMixin:

    def _note_order_read_failure(self, exchange_id: str, exc: BaseException) -> None:
        """Remember an exact read the venue answered with "not found"."""

        found = getattr(self, "_order_read_not_found", None)
        if found is not None and isinstance(exc, AdapterError) and is_not_found(exc):
            found.add(exchange_id)

    @staticmethod
    def _listing_matches_owned_row(order: Any, row: Any) -> bool:
        return (
            order.status in {"OPEN", "PARTIAL"}
            and str(row["status"]) not in TERMINAL_ORDER_STATES
            and not str(row["client_order_id"]).startswith("venue:")
            and order.client_order_id == row["client_order_id"]
            and order.exchange_order_id == row["exchange_order_id"]
            and order.market_slug == row["market_slug"]
            and order.side.upper() == row["side"]
            and order.price == Decimal(row["price"])
            and order.original_qty == Decimal(row["original_qty"]))

    def _open_listing_supersedes_not_found(
        self, failures: list[tuple[str, str]], orders: tuple[Any, ...],
    ) -> list[tuple[str, str]]:
        """A complete open-order listing is positive state; a 404 is not.

        Measured 2026-09-26: in two in-play markets the venue answered
        GET /v1/order/{id} with 404 for up to seven minutes after placement
        while the same order was in the complete open-order listing (none was
        ever marked absent). Each 404 failed the whole-account proof, so every
        market read `execution_state_unknown` for about an hour in total over
        two hours. Only a single listed, non-terminal row whose identity and
        economics match the owned ledger row supersedes the 404, and only
        after that listing was applied to the ledger. An order missing from the
        listing keeps its failure: absence is never cancellation proof.
        """

        not_found = getattr(self, "_order_read_not_found", None) or set()
        if not failures or not not_found:
            return failures
        listed: dict[str, list[Any]] = {}
        for order in orders:
            listed.setdefault(order.exchange_order_id, []).append(order)
        kept: list[tuple[str, str]] = []
        superseded: list[str] = []
        for exchange_id, detail in failures:
            matches = listed.get(exchange_id, [])
            rows = self.ledger.query(
                "SELECT * FROM execution_orders WHERE exchange_order_id=?",
                (exchange_id,))
            if (exchange_id in not_found and len(matches) == 1 and len(rows) == 1
                    and self._listing_matches_owned_row(matches[0], rows[0])):
                superseded.append(exchange_id)
            else:
                kept.append((exchange_id, detail))
        if superseded:
            print(
                f"[order-read-listed] at={self._now().isoformat()} "
                f"superseded_not_found={len(superseded)} "
                f"ids={','.join(sorted(superseded))}",
                flush=True,
            )
        return kept

    async def _advance_orphan_place_clear_proofs(
        self,
        *,
        observed_at: datetime,
        open_orders: tuple[Any, ...],
        positions: tuple[Any, ...],
    ) -> tuple[str, ...]:
        """Terminalize no-id PLACE ambiguity only after repeated flat proof.

        The retail create endpoint does not receive our local client id, so an
        uncertain create has no order-specific lookup key and may never be
        replayed.  Complete open-order and position snapshots can nevertheless
        prove that no *live risk* survives in its market.  We require three
        consecutive proofs spanning ten seconds, no local order row, and no
        locally observed execution at or after the intent.  Any contrary fact
        resets that intent's proof.  The terminal FAILED label means the
        mutation is abandoned, not that its original HTTP outcome is known.
        """

        candidates = self._unresolved_places_without_exchange_id()
        candidate_ids = {str(row["intent_id"]) for row in candidates}
        self._orphan_place_clear_proofs = {
            intent_id: proof
            for intent_id, proof in self._orphan_place_clear_proofs.items()
            if intent_id in candidate_ids
        }
        if not candidates:
            return ()

        live_order_markets = {
            str(order.market_slug) for order in open_orders
            if str(order.market_slug).strip()
        }
        held_markets = {
            str(position.market_slug) for position in positions
            if position.net_qty != 0
        }
        resolved: list[str] = []
        for row in candidates:
            intent_id = str(row["intent_id"])
            market = str(row["market_slug"] or "").strip()
            client_order_id = str(row["client_order_id"] or "").strip()
            created_at = datetime.fromisoformat(str(row["created_at"]))
            local_order = self.ledger.query(
                "SELECT 1 FROM execution_orders WHERE client_order_id=? LIMIT 1",
                (client_order_id,),
            )
            later_execution = self.ledger.query(
                "SELECT 1 FROM execution_fills WHERE market_slug=? "
                "AND event_at>=? LIMIT 1",
                (market, created_at.isoformat()),
            )
            blocked = (
                not market
                or bool(local_order)
                or market in live_order_markets
                or market in held_markets
                or bool(later_execution)
            )
            if blocked:
                self._orphan_place_clear_proofs.pop(intent_id, None)
                continue

            count, first_at = self._orphan_place_clear_proofs.get(
                intent_id, (0, observed_at))
            count += 1
            self._orphan_place_clear_proofs[intent_id] = (count, first_at)
            if (count < ORPHAN_PLACE_CLEAR_PROOFS
                    or (observed_at - first_at).total_seconds()
                    < ORPHAN_PLACE_CLEAR_MIN_SECONDS):
                continue

            self.ledger.transition_intent(
                intent_id,
                "FAILED",
                updated_at=observed_at,
                error=(
                    "automatic recovery abandoned no-id PLACE after repeated "
                    "complete account proofs found no live order or position; "
                    "the original create outcome remains unknown"),
            )
            self._orphan_place_clear_proofs.pop(intent_id, None)
            resolved.append(intent_id)
            self._mark_market_dirty(market, observed_at)
            await self.notifier.send_async(
                "Liquidity Bot safely retired an unresolved PLACE without an "
                f"exchange order id in {market} after repeated complete "
                "order/position proofs found no surviving venue risk. The "
                "uncertain create was not retried."
            )
        return tuple(resolved)

    async def _fetch_rest_order_snapshot(self, now: datetime):
        """Account-source hook; normal WebSocket reconciliation is unchanged."""
        return await self.account.fetch_open_orders(observed_at=now)

    async def _collect_rest_snapshots(self, now: datetime):
        """Collection boundary; the reconciliation owner alone projects data."""
        orders = await self._fetch_rest_order_snapshot(now)
        positions = await self.account.fetch_positions(observed_at=now)
        return orders, positions

    @api_workflow("account_reconcile", reason_kw="tag")
    async def _rest_reconcile(
        self, now: datetime, *, tag: str = "",
        strict_order_markets: frozenset[str] | None = None,
        advance_gate: bool = True,
    ) -> None:
        """Re-read the whole account from REST and apply it to the ledger.

        `tag` namespaces the snapshot event ids. Two reconciles at the same
        clock reading would otherwise build the same id from different content,
        which the ledger correctly rejects as a replay -- so a caller that may
        run alongside another at the same instant passes its own tag.

        By default, every owned live/ambiguous order is retrieved and any
        failure fails the reconcile. A recovery already scoped to particular
        markets may pass those markets in ``strict_order_markets``. Individual
        reads and absent-order repairs for other positively-attributed markets
        are deferred to their own recovery/normal sweep; complete account
        snapshots still update them, and their per-market gates remain closed
        if ambiguity survives. An empty set is the private-stream form:
        reconnect visibility account-wide, but keep every ambiguous owner
        dormant. An unattributable order is always read strictly.

        ``advance_gate=False`` is for a risk-reducing, market-scoped proof. It
        may update positive account facts, but cannot certify that every owned
        order was re-read and therefore cannot renew account-wide entry
        authority.
        """

        # Do not walk every exact order before discovering that a required
        # snapshot is already in endpoint recovery. This is not a proof or a
        # reservation; each wire request still checks the shared circuit.
        unresolved_owners = self._unresolved_intent_owners()
        if not unresolved_owners and not self.ledger.absent_orders():
            await self.gateway.ensure_account_reads_available(
                *self._reconciliation_read_endpoints())
        # Ambiguous order repair is still useful while positions are down:
        # positive terminal/open proof can resolve a cancel or permit its
        # existing exact retry. Do not hide that work behind this optimization.
        execution_owners_before = self._execution_owner_slugs()
        # First ask for terminal state on every bot-owned working order. A
        # missing order in an open-order snapshot is not cancellation proof.
        exchange_markets: dict[str, set[str]] = {}
        for row in (
            *self.ledger.working_orders(owned_only=True),
            *self.ledger.unresolved_intents(),
        ):
            exchange_id = str(row["exchange_order_id"] or "").strip()
            if not exchange_id:
                continue
            market = str(row["market_slug"] or "").strip()
            exchange_markets.setdefault(exchange_id, set())
            if market:
                exchange_markets[exchange_id].add(market)
        exchange_ids = set(exchange_markets)
        # Ask about every one before failing, rather than raising on the first
        # id the venue will not answer for. `sorted()` makes which id that is
        # deterministic rather than harmless: measured 2026-08-18, the first in
        # sort order was a just-placed order the venue served no state for over
        # two minutes, so the second -- which would have resolved on the first
        # try -- was never asked at all. Both intents stayed unresolved, and
        # the 60s critical stop is armed by exactly that, so the account-wide
        # terminal stop fired and the bot was down for four hours.
        #
        # Default strictness is deliberately unchanged: any failure still
        # fails the reconcile, because a working order we could not re-read is
        # live risk and nothing may proceed as if it were known. Recovery may
        # explicitly contain a positively-owned failure to that market; its
        # per-market gate remains closed. Either way, one unreadable order no
        # longer hides every other order's answer or the complete snapshots.
        reconcile_failures: list[tuple[str, str]] = []
        skipped_order_ids: set[str] = set()
        skipped_order_markets: set[str] = set()
        read_ids: list[str] = []
        for exchange_id in sorted(exchange_ids):
            markets = exchange_markets.get(exchange_id, set())
            if strict_order_markets is not None and len(markets) == 1:
                market = next(iter(markets))
                if market not in strict_order_markets:
                    skipped_order_ids.add(exchange_id)
                    skipped_order_markets.add(market)
                    continue
            read_ids.append(exchange_id)
        self._order_read_not_found = set()
        reconcile_failures.extend(await self._read_owned_order_states(read_ids, now))
        # Deliberately before failure classification. The sweep above has just
        # written the venue's current word on every readable order, which is
        # the only thing that licenses re-issuing a cancel -- and an unreadable
        # order in some other market must not postpone repairing this one.
        await self._retry_uncertain_cancels(
            now, markets=strict_order_markets)
        blocking_reconcile_failures: list[tuple[str, str]] = []
        for exchange_id, detail in reconcile_failures:
            markets = exchange_markets.get(exchange_id, set())
            # More than one owner is a ledger conflict, and no owner means the
            # ambiguity cannot be contained. Both stay account-wide failures.
            if len(markets) != 1:
                blocking_reconcile_failures.append((exchange_id, detail))
                continue
            market = next(iter(markets))
            if (strict_order_markets is None
                    or market in strict_order_markets):
                blocking_reconcile_failures.append((exchange_id, detail))
        # Then the orders we have already lost sight of. These are in neither
        # set above -- `UNKNOWN_ABSENT` is not a working state and their
        # intents are long since RECONCILED -- so before this nothing ever
        # asked about them again and they stayed absent for the rest of the
        # session. One such row holds `risk_state_known(None)` false
        # account-wide, which is what wedged every stream bind on 2026-08-16.
        #
        # Best-effort, unlike the default-strict sweep above: these are already
        # lost and a persistent 404 must not take the whole reconcile -- and
        # every bind that depends on it -- down with it. Absence is still never
        # read as cancellation: only a terminal state from the venue clears the
        # row.
        absent_ids = set()
        for row in self.ledger.absent_orders():
            exchange_id = row["exchange_order_id"]
            if not exchange_id or exchange_id in exchange_ids:
                continue
            absent_ids.add(exchange_id)
            if (strict_order_markets is not None
                    and str(row["market_slug"]) not in strict_order_markets):
                continue
            # Throttled, unlike the strict sweep above. `_rest_reconcile` runs
            # on every service cycle and every bind, and `retrieve` answers a
            # 404 by retrying four times with 0.5/1/2s backoff -- so an order
            # the venue will not resolve would add up to 3.5s of sleeping to
            # EVERY reconcile, indefinitely. That ages books, and `book_stale`
            # already blocks quoting on its own. An absence is not urgent: it
            # blocks only its own market, and one attempt a minute resolves a
            # transient inconsistency just as surely as sixty do.
            asked_at = self._absent_asked_at.get(exchange_id)
            if (asked_at is not None
                    and (now - asked_at).total_seconds()
                    < self.ABSENT_RECHECK_SECONDS):
                continue
            self._absent_asked_at[exchange_id] = now
            # Checked BEFORE the re-ask, and deliberately not inside an error
            # branch. The first version of this only resolved on a NotFound
            # from `retrieve`, and measured 2026-08-17 that never happened:
            # all twenty stuck rows sat in markets the venue reported
            # RESOLVED, `retrieve` answered without raising, and the rows
            # never left `UNKNOWN_ABSENT` -- so rotation stayed blocked
            # account-wide and the bot quoted almost nothing for an evening.
            #
            # An order in a resolved market cannot be live whatever `retrieve`
            # returns, so settlement is the whole test and the re-ask is
            # irrelevant once it holds.
            if await self._market_has_settled(str(row["market_slug"])):
                if self.ledger.resolve_absent_order_in_settled_market(
                        exchange_id, observed_at=now, reason="market_resolved"):
                    await self.notifier.send_async(
                        "Liquidity Bot resolved an absent order in settled "
                        f"market {row['market_slug']}; it can no longer be "
                        "live there, and rotation is unblocked.")
                self._runtime_error_counts.pop("absent_order", None)
                continue
            try:
                # Not `reconcile_order`: its event id is the venue's own event
                # time and status, so a re-ask after the terminal state is
                # already on file is a replay and repairs nothing. See
                # `reconcile_absent_order`.
                await self.executor.reconcile_absent_order(
                    exchange_id, observed_at=now)
                self._runtime_error_counts.pop("absent_order", None)
            except Exception as exc:
                # Escalates on repeat through the usual attempt counter. A
                # wedge that looks healthy is the worst outcome there is: the
                # 2026-08-16 stall was invisible for 24 minutes because nothing
                # about this state was ever reported.
                await self._report_runtime_error("absent_order", exc)
        # Forget orders that are no longer absent, so the throttle cannot grow
        # without bound across a long session.
        self._absent_asked_at = {
            key: at for key, at in self._absent_asked_at.items()
            if key in absent_ids}
        orders, positions = await self._collect_rest_snapshots(now)
        # Recent venue-retained fills backfill gaps while available. The local
        # ledger is the only execution history this project controls across a
        # restart plus a venue-side history reset. Without this sweep the
        # $40 stop reads one-sided round trips: measured 2026-08-15, a $1.55
        # loss was recorded as a $3.26 gain because only the opening fill was
        # ever written. Failing this must not fail the whole reconcile -- the
        # order and position snapshots above are what gate new risk.
        try:
            execution_batch = await self._fetch_execution_history(now)
            # Receipt is after the awaited collection, not the poll's start.
            # Otherwise a poll spanning an ET boundary backdates newly learned
            # losses into a window whose final mark already happened. Keep one
            # receipt for the batch so venue event order breaks ties even when
            # the history endpoint returns newest-first. Replays retain their
            # original durable receipt in the ledger. Proof freshness continues
            # to use the older `now`; receiving fills does not renew authority.
            received_at = self._now()
            for execution in execution_batch:
                # The ledger accepts identical replay and outcome enrichment.
                # A raised conflict is immutable identity disagreement in BOTH
                # account modes, not optional missing history.
                self.ledger.apply_execution(execution, received_at=received_at)
            # Trade lifecycle is independent of execution delivery. In
            # particular, BUSTED can appear after a fill was already recorded;
            # apply it after linking all execution legs in this batch.
            for trade_state in getattr(execution_batch, "trade_states", ()):
                self.ledger.apply_trade_state(trade_state, received_at=received_at)
            self._runtime_error_counts.pop("execution_backfill", None)
        except LedgerConflict:
            # A malformed/unavailable history page is optional positive
            # backfill and can be retried. Contradictory trade identity is not:
            # continuing to treat the previous REST proof as fresh could build
            # risk while fill reversal and P&L are disputed.
            self.gate.on_reconciliation_conflict()
            raise
        except Exception as exc:
            await self._report_runtime_error("execution_backfill", exc)
            if self.REQUIRE_EXECUTION_BACKFILL:
                raise
        stamp = f"{tag}:{now.isoformat()}" if tag else now.isoformat()
        self.ledger.apply_order_snapshot(
            f"commissioning-rest-orders:{stamp}", orders,
            as_of=now, observed_at=now, complete=True)
        had_near_deadline_position = (
            self._position_near_its_flatten_deadline(self._now()))
        held_before = self._held_refill_owners() if had_near_deadline_position else ()
        self.ledger.apply_position_snapshot(
            f"commissioning-rest-positions:{stamp}", positions,
            as_of=now, observed_at=now, complete_account=True)
        self._record_execution_timing("positions")
        orphan_places_resolved = await self._advance_orphan_place_clear_proofs(
            observed_at=now,
            open_orders=orders,
            positions=positions,
        )
        if orphan_places_resolved:
            print(
                f"[orphan-place-recovery] at={now.isoformat()} "
                f"resolved={len(orphan_places_resolved)} "
                f"intent_ids={','.join(orphan_places_resolved)}",
                flush=True,
            )
        if self._settlement_wait_slugs:
            self._sync_settlement_wait_positions(self._now())
        if self._inactive_market_wait_slugs:
            self._sync_inactive_market_wait_positions(self._now())
        self._request_post_flat_top_up_if_needed(
            had_near_deadline_position, self._now(), held_before=held_before)
        self._record_resolved_intent_owners(unresolved_owners)
        for slug in execution_owners_before.symmetric_difference(
                self._execution_owner_slugs()):
            self._mark_market_dirty(slug, self._now())
        # The complete open-order listing above has been applied to the ledger.
        blocking_reconcile_failures = self._open_listing_supersedes_not_found(
            blocking_reconcile_failures, orders)
        self._order_read_not_found = None
        if blocking_reconcile_failures:
            raise AdapterError(
                "could not re-read "
                f"{len(blocking_reconcile_failures)} working order(s): "
                + "; ".join(
                    f"{exchange_id}: {detail}"
                    for exchange_id, detail in blocking_reconcile_failures))
        if skipped_order_ids:
            print(
                f"[rest-reconcile] at={self._now().isoformat()} "
                f"scoped_order_reads_skipped={len(skipped_order_ids)} "
                f"markets={','.join(sorted(skipped_order_markets))}",
                flush=True,
            )
        # Freshness begins when the multi-call reconciliation has completed,
        # not when its first request entered the shared REST limiter.
        if advance_gate:
            self.gate.on_rest_reconciled(self._now())

    def _retained_value_replacement_snapshot(
        self, now: datetime, *, current_projected_reward: float,
    ) -> tuple[
        str | None, float | None, float | None, float | None,
        float | None, str, bool | None,
    ]:
        """Describe the best spare slot from already-fetched selection data.

        This is deliberately synchronous and local. It cannot refresh a book,
        scan the catalog, release a market or feed the planner. A stale or
        absent selector snapshot is recorded as missing evidence rather than
        promoted into a pretend replacement.
        """

        selection = self._market_selection
        observed_at = self._market_selection_observed_at
        if selection is None or observed_at is None:
            return (None, None, None, None, None,
                    "selection_unavailable", None)
        age = (now - observed_at).total_seconds()
        if age < 0:
            return (None, None, None, None, age,
                    "selection_clock_invalid", None)
        if age > self.config.market_selection_refresh_seconds:
            return (None, None, None, None, age,
                    "selection_stale", None)
        excluded = set(self.active_slugs) | self._barred_slugs(now)
        candidates = selection.top_viable(
            max(1, len(selection.opportunities)), exclude=excluded)
        if not candidates:
            return (None, None, None, None, age,
                    "no_viable_spare", True)
        candidate = candidates[0]
        reward = candidate.projected_reward_total
        expected = candidate.best_expected_net_value
        return (
            candidate.market.slug,
            reward,
            expected,
            reward - current_projected_reward,
            age,
            "recent_selector_spare",
            False,
        )

    def _next_unresolved_reconcile_scope(
        self, rows: list[Any] | None = None,
    ) -> frozenset[str] | None:
        """Choose a bounded owner batch, round-robin; None means unscoped."""

        unresolved = rows if rows is not None else self.ledger.unresolved_intents()
        if any(not str(row["market_slug"] or "").strip()
               for row in unresolved):
            return None
        # A single exchange id attributed to multiple markets is not scoped
        # ownership. Include current working rows because a conflicting owner
        # can be split between the order table and its unresolved intent. The
        # strict account-wide reconcile retains the existing fail-closed
        # treatment for this case.
        exchange_markets: dict[str, set[str]] = {}
        for row in (*self.ledger.working_orders(owned_only=True), *unresolved):
            exchange_id = str(row["exchange_order_id"] or "").strip()
            market = str(row["market_slug"] or "").strip()
            if exchange_id and market:
                exchange_markets.setdefault(exchange_id, set()).add(market)
        if any(len(owners) != 1 for owners in exchange_markets.values()):
            return None
        markets = sorted({
            str(row["market_slug"]) for row in unresolved
            if str(row["market_slug"] or "").strip()
        })
        if not markets:
            return frozenset()
        size = min(self.UNRESOLVED_RECONCILE_OWNER_BATCH_SIZE, len(markets))
        start = self._unresolved_reconcile_cursor % len(markets)
        selected = {
            markets[(start + offset) % len(markets)]
            for offset in range(size)
        }
        self._unresolved_reconcile_cursor += size
        return frozenset(selected)

    def _quiet_book_needs_rest_refresh(self, now: datetime) -> bool:
        if self.book_observed_at is None:
            return True
        age = (now - self.book_observed_at).total_seconds()
        return age < 0 or age >= 3

    def _position_priority_has_fresh_transition_proof(
        self, slug: str,
    ) -> bool:
        """Return whether same-owner service already has exact account proof.

        This is a scheduling-only hook for account sources that can positively
        prove one risk-reducing transition without renewing entry authority.
        The default source never bypasses its normal account/equity refresh.
        """

        return False

    def _note_periodic_reconcile(self, exc: Exception | None) -> None:
        """Log periodic account-proof failures and the first success after them.

        Local log only: no Discord, no traceback, and no effect on the gate.
        The freshness gate, not this record, still decides whether new risk is
        allowed. Measured 2026-09-25: these failures were previously silent,
        hiding evidence for the open 401/429 investigation.
        """

        try:
            failures = getattr(self, "_periodic_reconcile_failures", 0)
            if exc is None:
                if failures:
                    print(
                        f"[periodic-reconcile] at={self._now().isoformat()} "
                        f"outcome=recovered after_failures={failures}",
                        flush=True)
                self._periodic_reconcile_failures = 0
                return
            failures += 1
            self._periodic_reconcile_failures = failures
            print(
                f"[periodic-reconcile] at={self._now().isoformat()} "
                f"outcome=failed consecutive={failures} "
                f"status={getattr(exc, 'status_code', None)} "
                f"error={type(exc).__name__}: {self._redacted_error_detail(exc)}",
                flush=True)
        except Exception:
            pass  # Diagnostics never alter the periodic proof loop.

    async def _reconcile_pending_hard_exit(
        self, slug: str | None = None,
    ) -> str:
        """Confirm a submitted IOC without converting latency into shutdown."""

        slug = slug or self.market_slug
        await self._reconcile_hard_exit_state(slug)
        if (self.ledger.market_has_unresolved_intent(slug)
                or self.ledger.working_orders(slug)):
            return "pending"
        if self.ledger.position(slug) == 0:
            self.reconciler.confirm_hard_exit_terminal(slug)
            return "flat"
        # Venue state is terminal and exact inventory remains. This is a safe
        # retry boundary, not permission to duplicate an ambiguous mutation.
        self.reconciler.confirm_hard_exit_terminal(slug)
        return "retry"

    async def _reconcile_hard_exit_state(self, slug: str) -> None:
        """Refresh the evidence required to classify one submitted IOC."""

        await self._rest_reconcile(self._now())
