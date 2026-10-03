"""Market-selection scans and slot top-up scheduling for the commissioning runner.

Moved verbatim from ``live_commissioning.CommissioningRunner`` (pure
relocation, no behavior change). Methods run on the runner instance via
inheritance, so every ``self`` attribute and override resolves as before.
"""

from __future__ import annotations

from .api_diagnostics import api_workflow
import time
from datetime import datetime, timedelta, timezone
from .commissioning import CommissioningRefused
from .errors import AdapterError, ApiAdmissionDeferred, RuntimeInvariantViolation
from .execution_ledger import LedgerConflict
from .runner_shared import POST_FLAT_SELECTION_CACHE_SECONDS


class TopUpSchedulingMixin:

    async def _select_reward_market(self, now: datetime) -> str:
        """Wait for a viable reward market rather than refusing to start.

        A universe with nothing viable right now is the normal case, not a
        misconfiguration: economically authorized windows are short and
        infrequent. Refusing startup returns exit 2, which the supervisor
        treats as terminal, so the process would stay down until an operator
        noticed. Waiting costs nothing, because a runner with no bound market
        holds no orders, no position and no stream subscription.

        Only an operator stop, HARD, or a genuine adapter failure ends
        the wait. Every scan is read-only and paced by the shared governor.
        """

        if self.reward_selector is None:
            raise RuntimeError("automatic reward selector is unavailable")
        # Markets this ledger's runner already held that still pass every
        # mandatory-retention check keep their "already selected" status
        # (operator decision 2026-09-26). Empty on any doubt.
        readmitted = await self._warm_restart_readmission(now)
        attempt = 0
        while True:
            attempt += 1
            scan_started = time.monotonic()
            try:
                selection = await self.reward_selector.select(now=self._now())
            finally:
                self._selection_scan_seconds_last = (
                    time.monotonic() - scan_started)
            self._log_selection_scan(selection)
            if selection.selected is not None or readmitted:
                self._market_selection = selection
                self._market_selection_observed_at = self._now()
                # Bind the whole authorized set, not just the best one.
                # Binding a single market meant the session quoted one market
                # until the first rotation 900 seconds later, so the measured
                # constraint -- authorized uptime, not per-market size -- went
                # untouched for the first quarter hour of every run.
                # Re-admitted markets come first; fresh viable ones fill the rest.
                # `top_viable` refuses a zero limit, and a full warm restart
                # leaves none (2026-09-26 05:21Z: 32 of 32 re-admitted, three
                # startup crashes before the supervisor held attention).
                remaining = self.config.max_markets - len(readmitted)
                fresh = tuple(
                    item.market.slug for item in selection.top_viable(
                        remaining,
                        exclude=self._barred_slugs() | set(readmitted))
                ) if remaining > 0 else ()
                chosen = readmitted + fresh
                self._bind_markets(
                    chosen or (selection.selected.market.slug,))
                self._runtime_error_counts.pop("startup_market_wait", None)
                return self.market_slug
            self._market_selection = selection
            self._market_selection_observed_at = self._now()
            if self._stop_requested:
                raise CommissioningRefused(
                    "operator stopped while waiting for a viable reward market")
            # Not a fault. The gate rejected every book it scanned, which is
            # the common case -- the backtest authorized one quote in 24 hours.
            # Routing it through _report_runtime_error paged Discord as
            # critical with "new risk is disabled until reconnect and
            # reconciliation pass", which describes a broken connection and is
            # simply untrue here. Alerts that cry wolf on the normal idle state
            # are how a real one gets ignored. Log every scan; say it out loud
            # only once, and only so a silent night is distinguishable from a
            # dead process.
            print(
                f"[market-wait] at={self._now().isoformat()} attempt={attempt} "
                f"executable={selection.executable_catalog_markets} "
                f"books={selection.books_evaluated} viable=0",
                flush=True,
            )
            if attempt == 1:
                await self.notifier.send_async(
                    "Liquidity Bot is scanning and waiting: no market clears "
                    f"the reward gate yet ({selection.executable_catalog_markets} "
                    f"executable, {selection.books_evaluated} books). Nothing is "
                    "wrong; it will quote when one qualifies.",
                    critical=False)
            await self.sleep(
                float(self.config.market_selection_refresh_seconds))

    async def _warm_restart_readmission(self, now: datetime) -> tuple[str, ...]:
        """Remembered markets that still pass mandatory retention; never raises."""

        from collections import Counter
        from . import warm_restart
        path = warm_restart.scope_path(self.ledger.path)

        def report(outcome, remembered=0, kept=(), dropped=None, age=None):
            print(f"[warm-restart] at={self._now().isoformat()} outcome={outcome} "
                  f"remembered={remembered} readmitted={len(kept)} "
                  f"dropped={dict(dropped or {})} "
                  f"age_seconds={'-' if age is None else round(age, 1)}", flush=True)

        try:
            slugs, age = warm_restart.read_scope(path, now=self._now())
        except FileNotFoundError:
            report("no_scope")
            return ()
        except Exception as exc:
            report(f"ignored:{type(exc).__name__}")
            return ()
        if not slugs:
            report("empty_scope", age=age)
            return ()
        try:
            selector = self.reward_selector
            markets = await selector.gateway.fetch_markets_by_slugs(slugs)
            incentives = await selector.gateway.fetch_incentives(tuple(markets))
            now = self._now()
            window_end = selector.policy.discovery_window_end(now)
            barred = self._barred_slugs(now)
            dropped = Counter()
            kept = []
            for slug in slugs:
                market = markets.get(slug)
                if market is None:
                    dropped["not_found"] += 1
                elif slug in barred:
                    dropped["barred"] += 1
                elif not selector._catalog_market_is_executable(
                        market, now=now, window_end=window_end):
                    # Closed, started, excluded league or not tradable now.
                    dropped["not_executable"] += 1
                elif (not selector.policy.strategy.is_live(market, now)
                      and (market.game_start - now).total_seconds()
                      <= self.config.force_exit_before_start_seconds):
                    dropped["inside_flatten_window"] += 1
                elif (slug not in incentives or selector._applicable_period(
                        market, incentives[slug], now) is None):
                    dropped["no_active_reward"] += 1
                else:
                    kept.append(slug)
            kept = tuple(kept[:self.config.max_markets])
        except Exception as exc:
            report(f"read_failed:{type(exc).__name__}", remembered=len(slugs), age=age)
            return ()
        report("readmitted", remembered=len(slugs), kept=kept, dropped=dropped, age=age)
        return kept

    def _remember_warm_restart_scope(self, slugs) -> None:
        """Best effort: optional evidence must never veto a scope change."""

        try:
            from . import warm_restart
            warm_restart.write_scope(
                warm_restart.scope_path(self.ledger.path), slugs, now=self._now())
        except Exception as exc:
            print(f"[warm-restart] at={self._now().isoformat()} "
                  f"outcome=scope_write_failed error={type(exc).__name__}", flush=True)

    def _request_lifecycle_top_up(self, now: datetime) -> None:
        """Schedule one incremental scan without authorizing a slot release."""

        if self._lifecycle_top_up_requested_at is not None:
            return
        self._lifecycle_top_up_requested_at = now
        print(
            f"[top-up-request] at={now.isoformat()} reason=lifecycle",
            flush=True,
        )

    def _selection_snapshot_for_measurement(self, now: datetime) -> dict:
        from .selection_provenance import rank_snapshot
        return rank_snapshot(self._market_selection,
                             self._market_selection_observed_at, now,
                             self.config.market_selection_refresh_seconds)

    def _remember_selection_binding(self, slugs: tuple[str, ...]) -> None:
        try:
            now = self._now()
            snapshot = self._selection_snapshot_for_measurement(now)
            self._selection_provenance.bind(slugs, snapshot, now)
        except Exception:
            # Keep retained origins and explicit unknown tombstones for new
            # owners. A later scan must not backfill a guessed original rank.
            old = self._selection_provenance.origins
            self._selection_provenance.origins = {
                slug: old.get(slug, {"rank": None, "observed_at": None,
                                    "snapshot_id": None, "bound_at": None,
                                    "reason": "measurement_error"})
                for slug in slugs}

    def _selection_provenance_for_market(self, now: datetime, slug: str) -> dict:
        try:
            return self._selection_provenance.evidence(
                slug, self._selection_snapshot_for_measurement(now))
        except Exception:
            return {"schema": 1, "reason": "measurement_error", "original": None}

    def _selection_rank_for_market(
        self, now: datetime, market_slug: str,
    ) -> int | None:
        """Return evidence-only rank from the recent in-memory selector slate.

        This helper is synchronous, makes no venue call and cannot alter the
        selected market set. Missing, future-dated or stale evidence remains
        NULL rather than assigning a market a guessed cohort.
        """

        selection = self._market_selection
        observed_at = self._market_selection_observed_at
        if selection is None or observed_at is None:
            return None
        age = (now - observed_at).total_seconds()
        if age < 0 or age > self.config.market_selection_refresh_seconds:
            return None
        candidates = selection.top_viable(
            max(1, len(selection.opportunities)))
        for rank, candidate in enumerate(candidates, start=1):
            if candidate.market.slug == market_slug:
                return rank
        return None

    def _mandatory_top_up_gap(self, now: datetime) -> int:
        """Slots free or held by owners with mandatory release evidence.

        Economic-idle replacement deliberately stays on its operator-approved
        900-second cadence. It must not become urgent merely because an
        unrelated position closed. Lifecycle, reward-end and genuinely empty
        capacity are different: every one is already unable to produce useful
        pregame reward work and can request the post-flat fast path.
        """

        mandatory = {
            slug for slug in self.active_slugs
            if (self._expired_and_flat(slug, now)
                or self._closed_and_flat(slug)
                or self._reward_authorization_lost_and_flat(slug, now))
        }
        retained = len(self.active_slugs) - len(mandatory)
        return max(0, self.config.max_markets - retained)

    def _held_refill_owners(self) -> tuple[str, ...]:
        return tuple(str(row["market_slug"]) for row in self.ledger.query(
            "SELECT market_slug FROM execution_positions WHERE CAST(net_qty AS REAL)!=0"))

    def _request_post_flat_top_up_if_needed(
        self, had_near_deadline_position: bool, now: datetime,
        *, held_before: tuple[str, ...],
    ) -> None:
        """Request one immediate refill when the final urgent position clears."""

        if (not had_near_deadline_position
                or self._position_near_its_flatten_deadline(now)):
            return
        # REST urgency includes reducer gaps/quantity corrections. Restoring
        # coverage is not a position close and must not launch another scan.
        if not any(self.ledger.position(slug) == 0 for slug in held_before):
            return
        underfilled = self._mandatory_top_up_gap(now)
        if underfilled <= 0:
            return
        if self._post_flat_top_up_requested_at is None:
            self._post_flat_top_up_requested_at = now
            self._post_flat_underfilled_slots = underfilled
            print(
                f"[top-up-request] at={now.isoformat()} reason=post_flat "
                f"underfilled_slots={underfilled}",
                flush=True,
            )
        else:
            self._post_flat_underfilled_slots = max(
                self._post_flat_underfilled_slots, underfilled)

    def _post_flat_top_up_can_preempt(self, now: datetime) -> bool:
        """Whether ordinary market service may yield to the one-shot refill."""

        if self._post_flat_top_up_requested_at is None:
            return False
        if self._position_near_its_flatten_deadline(now):
            return False
        if self._mandatory_top_up_gap(now) <= 0:
            # The evidence that created the request changed before scheduling.
            # Do not let a stale optimization label or preempt a later scan.
            self._post_flat_top_up_requested_at = None
            self._post_flat_underfilled_slots = 0
            return False
        return True

    def _clear_incremental_top_up_requests(
        self, consumed_post_flat_at: datetime | None,
    ) -> None:
        self._reward_end_top_up_requested = False
        self._lifecycle_top_up_requested_at = None
        # The socket handoff performs another complete position snapshot. If
        # that discovers a different final urgent position becoming flat, it
        # creates a new request during this top-up. Clear only the request this
        # attempt actually consumed; never erase the newer evidence.
        if self._post_flat_top_up_requested_at == consumed_post_flat_at:
            self._post_flat_top_up_requested_at = None
            self._post_flat_underfilled_slots = 0

    def _urgent_top_up_can_use_cache(self, post_flat_requested: bool) -> bool:
        """Whether an event-driven refill may revalidate recent candidates."""

        return (
            post_flat_requested
            or self._lifecycle_top_up_requested_at is not None
            or self._reward_end_top_up_requested
        )

    def _active_set_needs_top_up(self, now: datetime) -> bool:
        """True when safe, incremental discovery has useful work to do.

        A slot can be free for two different reasons: a bound market reached
        kickoff and became releasable, or startup validation rejected a
        selected market before it was ever bound.  The second case used to be
        invisible to the scheduler, leaving (for example) 18 active markets
        despite a fresh scan proving that far more than 20 were viable.
        """

        return (
            len(self.active_slugs) < self.config.max_markets
            or any(self._expired_and_flat(slug, now)
                   for slug in self.active_slugs)
            or any(self._closed_and_flat(slug)
                   for slug in self.active_slugs)
            or any(self._reward_authorization_lost_and_flat(slug, now)
                   for slug in self.active_slugs)
            or any(self._economically_idle_and_flat(slug, now)
                   for slug in self.active_slugs)
            or any(self._reward_idle_and_flat(slug, now)
                   for slug in self.active_slugs)
        )

    def _initial_market_selection_due(self, now: datetime) -> datetime:
        """Schedule an immediate refill when startup bound fewer than 20."""

        if not self.config.auto_select_reward_markets:
            return datetime.max.replace(tzinfo=timezone.utc)
        if self._active_set_needs_top_up(now):
            return now
        return now + timedelta(
            seconds=self.config.market_selection_refresh_seconds)

    def _incremental_top_up_due(
        self, now: datetime, next_selection: datetime,
    ) -> bool:
        """Whether the run loop should perform an incremental discovery scan."""

        return (
            self.config.auto_select_reward_markets
            and not self._stop_requested
            and not self._streams_need_rebind
            and not self._entry_pause_active()
            and now >= self._top_up_retry_not_before
            and (now >= next_selection
                 or self._reward_end_top_up_requested
                 or self._lifecycle_top_up_requested_at is not None
                 or self._post_flat_top_up_requested_at is not None)
            and (self._reward_end_top_up_requested
                 or self._lifecycle_top_up_requested_at is not None
                 or self._post_flat_top_up_requested_at is not None
                 or not self._safe_to_scan())
            and not self._position_near_its_flatten_deadline(now)
            and self._active_set_needs_top_up(now)
        )

    async def _replacement_candidates_for_top_up(
        self,
        now: datetime,
        *,
        exclude: set[str],
        wanted: int,
        allow_cached_selection: bool,
    ) -> tuple[tuple[str, ...], str, float | None]:
        """Return freshly authorized replacement slugs and their evidence path."""

        if self.reward_selector is None:
            return (), "unavailable", None
        observed = self._market_selection_observed_at
        cached = self._market_selection
        # A completed scan may outlive a deferred post-scan account/bind proof.
        # The run loop then labels its retry "normal due". Preserve just that
        # completed selection for the existing bounded fresh revalidation path;
        # an interrupted/partial catalog never installs this identity token.
        retry_completed = (cached is not None and cached is getattr(
            self, "_top_up_retry_selection", None))
        allow_cached_selection = allow_cached_selection or retry_completed
        if allow_cached_selection and cached is not None and observed is not None:
            cache_age = (now - observed).total_seconds()
            if 0 <= cache_age <= POST_FLAT_SELECTION_CACHE_SECONDS:
                # Overfetch a small bounded set so one changed book does not
                # force a full catalog scan. The revalidator still spends no
                # more than the selector's existing twenty-book live budget.
                candidate_limit = min(
                    self.config.max_markets, max(wanted, wanted * 2))
                candidates = tuple(
                    item.market.slug for item in cached.top_viable(
                        candidate_limit, exclude=exclude))
                if not candidates:
                    # Recent complete selection is bounded no-supply evidence,
                    # never entry authority. Keep its original clock so another
                    # urgent request cannot indefinitely renew this shortcut.
                    return (), "cached_no_supply", cache_age
                revalidate = getattr(
                    self.reward_selector, "revalidate_viable", None)
                if candidates and callable(revalidate):
                    validation_started = time.monotonic()
                    try:
                        refreshed = await revalidate(
                            candidates, now=self._now())
                    except ApiAdmissionDeferred:
                        # Backpressure is not evidence that the cache failed.
                        # Do not immediately replace bounded entry revalidation
                        # with a much larger background discovery scan.
                        raise
                    except (LedgerConflict, RuntimeInvariantViolation):
                        self.gate.on_reconciliation_conflict()
                        raise
                    except Exception as exc:
                        if isinstance(exc, AdapterError) and exc.status_code == 429:
                            # A venue limit needs the same no-fanout treatment
                            # as local admission deferral, not a larger scan.
                            raise
                        print(
                            f"[top-up-cache] at={self._now().isoformat()} "
                            f"outcome=fallback error={type(exc).__name__}",
                            flush=True,
                        )
                    else:
                        candidate_set = set(candidates)
                        viable = tuple(
                            item.market.slug for item in refreshed
                            if (item.viable_sides
                                and item.market.slug in candidate_set
                                and item.market.slug not in exclude))
                        elapsed = time.monotonic() - validation_started
                        if viable:
                            print(
                                f"[top-up-cache] at={self._now().isoformat()} "
                                f"outcome=validated age_seconds={cache_age:.1f} "
                                f"checked={len(candidates)} viable={len(viable)} "
                                f"validation_seconds={elapsed:.1f}",
                                flush=True,
                            )
                            return viable, "cached_revalidated", cache_age
                        print(
                            f"[top-up-cache] at={self._now().isoformat()} "
                            f"outcome=fallback age_seconds={cache_age:.1f} "
                            f"checked={len(candidates)} viable={len(viable)} "
                            f"wanted={wanted} validation_seconds={elapsed:.1f}",
                            flush=True,
                        )

        scan_started = time.monotonic()
        try:
            selection = await self.reward_selector.select(now=self._now())
        finally:
            self._selection_scan_seconds_last = time.monotonic() - scan_started
        self._log_selection_scan(selection)
        self._market_selection = selection
        self._market_selection_observed_at = self._now()
        self._top_up_retry_selection = selection
        return (
            tuple(item.market.slug for item in selection.top_viable(
                self.config.max_markets, exclude=exclude)),
            "fresh_scan",
            None,
        )

    def _top_up_timing_fields(
        self, now: datetime, selection_source: str,
        cache_age: float | None,
    ) -> str:
        triggers: list[str] = []
        if self._post_flat_top_up_requested_at is not None:
            triggers.append("post_flat")
        if self._reward_end_top_up_requested:
            triggers.append("reward_end")
        if self._lifecycle_top_up_requested_at is not None:
            triggers.append("lifecycle")
        trigger = "+".join(triggers) if triggers else "scheduled"
        fields = f"trigger={trigger} selection_source={selection_source}"
        if cache_age is not None:
            fields += f" cache_age_seconds={cache_age:.1f}"
        if self._lifecycle_top_up_requested_at is not None:
            wait = max(0.0, (
                now - self._lifecycle_top_up_requested_at).total_seconds())
            fields += f" lifecycle_wait_seconds={wait:.1f}"
        if self._post_flat_top_up_requested_at is not None:
            wait = max(0.0, (
                now - self._post_flat_top_up_requested_at).total_seconds())
            slot_minutes = (
                wait * self._post_flat_underfilled_slots / 60.0)
            fields += (
                f" post_flat_wait_seconds={wait:.1f}"
                f" post_flat_underfilled_slots="
                f"{self._post_flat_underfilled_slots}"
                f" post_flat_underfilled_slot_minutes={slot_minutes:.2f}")
        return fields

    @api_workflow("market_top_up")
    async def _top_up_active_markets(
        self,
        now: datetime,
        *,
        allow_cached_selection: bool = False,
        allow_economic_idle: bool = True,
    ) -> bool:
        """Fill free slots without disturbing markets that are still resting.

        Full rotation requires a whole-account flat state (`_rotation_blockers`,
        `_discovery_is_free`) because it quarantines every resting order and
        rebinds from scratch. With twenty markets something is almost always
        resting, so that condition is essentially never met: measured
        2026-08-16, rotation ran exactly twice, both at startup, and the active
        set stayed frozen while markets kicked off one by one. Coverage decayed
        19 -> 11 -> 8 -> 7 -> 6 across the previous session for this reason as
        much as for the retirement ban.

        This path is deliberately narrower. It releases markets that are
        past kickoff and flat, are positively reported closed/not-open and
        flat, or whose exact reward program is positively confirmed ended and
        which are flat with no order ownership left.  It may also replace a
        flat, clear pregame owner after ten continuous minutes of a named
        current-book economic refusal, but only when the same scan supplies an
        actual viable replacement. The same optional rule covers repeated
        successful contract reads without applicable rewards, but requires
        fresh contract proof again before release. It keeps every other market and its resting
        orders exactly where they are, fills any capacity startup validation
        left free, and never quarantines. Streams are restarted because they
        cannot subscribe incrementally, but a stream restart does not touch
        resting orders -- those live on the venue and keep earning through it.
        That is the whole difference from full rotation, which cancels them.
        """

        if self._stop_requested or self._streams_need_rebind:
            return False
        if not self.config.auto_select_reward_markets:
            return False
        if self.reward_selector is None:
            return False
        if self._position_near_its_flatten_deadline(now):
            return False
        expired = [slug for slug in self.active_slugs
                   if self._expired_and_flat(slug, now)]
        closed = [slug for slug in self.active_slugs
                  if slug not in expired and self._closed_and_flat(slug)]
        reward_ended = [
            slug for slug in self.active_slugs
            if (slug not in expired and slug not in closed
                and self._reward_authorization_lost_and_flat(slug, now)
                and self.state_for(slug).reward_period_end_confirmed_at
                is not None)]
        reward_inactive = [
            slug for slug in self.active_slugs
            if (slug not in expired and slug not in closed
                and slug not in reward_ended
                and self._reward_authorization_lost_and_flat(slug, now))]
        mandatory_released = expired + closed + reward_ended + reward_inactive
        economic_idle_candidates = sorted(
            (slug for slug in self.active_slugs
             if slug not in mandatory_released
             and allow_economic_idle
             and self._economically_idle_and_flat(slug, now)),
            key=lambda slug: (
                self.state_for(slug).economically_ineligible_since
                or datetime.max.replace(tzinfo=timezone.utc),
                slug,
            ),
        )
        reward_idle_candidates = [
            slug for slug in self.active_slugs
            if slug not in mandatory_released
            and slug not in economic_idle_candidates
            and allow_economic_idle
            and self._reward_idle_and_flat(slug, now)]
        optional_candidates = sorted(
            economic_idle_candidates + reward_idle_candidates,
            key=lambda slug: (
                (self.state_for(slug).reward_unavailable_since
                 if slug in reward_idle_candidates
                 else self.state_for(slug).economically_ineligible_since)
                or datetime.max.replace(tzinfo=timezone.utc), slug))
        base_keep = tuple(slug for slug in self.active_slugs
                          if slug not in mandatory_released)
        capacity_without_optional_release = max(
            0, self.config.max_markets - len(base_keep))
        if (not mandatory_released and not optional_candidates
                and capacity_without_optional_release == 0):
            return False
        # Exclude every current owner, including optional idle candidates.  An
        # idle owner may be retained if supply is short, but it must not count
        # as its own replacement and manufacture a no-op stream rebind.
        exclude = (self._barred_slugs() | set(base_keep)
                   | set(mandatory_released)
                   | set(optional_candidates))
        wanted = max(
            1,
            capacity_without_optional_release + len(optional_candidates),
        )
        self._top_up_cache_expires_at = None
        replacement_candidates, selection_source, cache_age = (
            await self._replacement_candidates_for_top_up(
                now,
                exclude=exclude,
                wanted=wanted,
                allow_cached_selection=allow_cached_selection,
            ))
        if self._stop_requested or self._streams_need_rebind:
            return False
        if selection_source in {"cached_revalidated", "cached_no_supply"}:
            self._top_up_cache_expires_at = (
                self._market_selection_observed_at
                + timedelta(seconds=POST_FLAT_SELECTION_CACHE_SECONDS))
        optional_capacity = max(
            0, len(replacement_candidates) - capacity_without_optional_release)
        optional_released = optional_candidates[:optional_capacity]
        economic_idle = [s for s in optional_released if s in economic_idle_candidates]
        reward_idle = [s for s in optional_released if s in reward_idle_candidates]
        released = mandatory_released + optional_released
        keep = tuple(slug for slug in self.active_slugs
                     if slug not in released)
        free = max(0, self.config.max_markets - len(keep))
        replacements = replacement_candidates[:free]
        plan_at = self._now()
        timing_fields = self._top_up_timing_fields(
            plan_at, selection_source, cache_age)
        print(
            f"[top-up-plan] at={plan_at.isoformat()} {timing_fields} "
            f"released={len(released)} kept={len(keep)} "
            f"added={len(replacements)} free_slots={free} "
            f"expired={len(expired)} closed={len(closed)} "
            f"reward_ended={len(reward_ended)} "
            f"reward_inactive={len(reward_inactive)} "
            f"economic_idle={len(economic_idle)} "
            f"economic_idle_candidates={len(economic_idle_candidates)} "
            f"reward_idle={len(reward_idle)} "
            f"reward_idle_candidates={len(reward_idle_candidates)} "
            f"released_slugs={','.join(sorted(released))}",
            flush=True,
        )
        if not replacements and not keep:
            # Releasing everything to hold nothing is never an improvement on
            # holding dead markets. `_bind_markets(())` leaves `market_slug`
            # raising RuntimeError, and the next caller that omits a slug --
            # `_refresh_book(now)` among them -- takes the run loop down.
            print(
                f"[top-up] at={self._now().isoformat()} skipped=nothing_retained "
                f"expired={len(expired)} closed={len(closed)} "
                f"reward_ended={len(reward_ended)} "
                f"economic_idle={len(economic_idle)}",
                flush=True,
            )
            return False
        if not replacements and not released:
            # Capacity is free, but this scan found nothing safe to add. Keep
            # the current subscriptions intact and retry only on the normal
            # bounded selection cadence.
            return False
        # Transaction boundary: take complete account snapshots and strictly
        # prove every owner being released while the old active set and both
        # healthy streams are still intact. Retained owners stay retained and
        # individually gated if one of their GET-by-ID views lags. On
        # 2026-08-25 this proof happened only after `_bind_markets` and after both
        # streams had been closed. Three attributable GET-by-ID NotFound reads
        # therefore turned a harmless one-slot top-up refusal into global
        # recovery, which cancelled 32 orders and retired 18 owners. Failing
        # here leaves the caller able to resume its old stream tasks without a
        # scope change, cancellation or rebind.
        await self._rest_reconcile(
            self._now(), strict_order_markets=frozenset(released))
        if self._stop_requested or self._streams_need_rebind:
            return False
        # Closed/not-open is positive lifecycle proof, not an inference from a
        # missing selector row. Re-read each exact owner at the transaction
        # boundary; a failed read or a market that reopened aborts before the
        # active tuple or either healthy stream changes.
        for slug in closed:
            self.state_for(slug).market = await self.gateway.fetch_market(slug)
        # Reuse the bounded bulk reader and its normal error/backoff handling.
        # A returned reward, failed read or unknown lifecycle invalidates the
        # optional plan while the old owners and streams are still intact.
        for slug in reward_idle:
            self.state_for(slug).next_contract_refresh = None
        if reward_idle:
            await self._refresh_due_contract_batches(self._now(), tuple(reward_idle))
            if self._stop_requested:
                return False
        if self._stop_requested or self._streams_need_rebind:
            return False
        self._assert_runtime_scope()
        # The complete snapshot above can discover a fill, a newly absent
        # order, or another unresolved mutation. Recheck the release predicate
        # at the transaction boundary instead of committing a decision made
        # before that evidence arrived.
        no_longer_releasable = [
            slug for slug in released
            if not (self._expired_and_flat(slug, self._now())
                    or self._closed_and_flat(slug)
                    or self._reward_authorization_lost_and_flat(
                        slug, self._now())
                    or self._economically_idle_and_flat(
                        slug, self._now())
                    or self._reward_idle_and_flat(slug, self._now()))]
        if no_longer_releasable:
            raise AdapterError(
                "top-up release proof changed during preflight: "
                + ", ".join(sorted(no_longer_releasable)))
        if not replacements:
            # Still worth releasing: a dead market costs a REST refresh every
            # service cycle and reports `book_stale` every time.
            self._bind_markets(keep)
        else:
            self._bind_markets(keep + replacements)
        # Strictness was established immediately before committing the new
        # scope, with no order mutation in between. During the short socket
        # handoff a resting order can still fill or disappear. The complete
        # post-bind snapshots and fresh private state capture that change;
        # any positively-owned ambiguity then closes only its market's gate.
        # Unowned, conflicting or out-of-scope state remains terminal through
        # `_assert_runtime_scope`. Requiring every attributed GET again would
        # recreate the same account-wide failure after the safe preflight.
        await self._open_streams_for_active_market(
            strict_order_markets=frozenset())
        committed_at = self._now()
        timing_fields = self._top_up_timing_fields(
            committed_at, selection_source, cache_age)
        print(
            f"[top-up] at={committed_at.isoformat()} committed=yes "
            f"{timing_fields} "
            f"released={len(released)} kept={len(keep)} "
            f"added={len(replacements)} active={len(self.active_slugs)} "
            f"expired={len(expired)} closed={len(closed)} "
            f"reward_ended={len(reward_ended)} "
            f"reward_inactive={len(reward_inactive)} "
            f"economic_idle={len(economic_idle)} "
            f"reward_idle={len(reward_idle)} "
            f"released_slugs={','.join(sorted(released))}",
            flush=True,
        )
        return True

    def _safe_to_scan(self) -> bool:
        """True when a universe scan can run without abandoning live risk.

        A scan cancels every resting quote and stops draining both streams for
        its whole duration, so it may only run from a genuinely idle account.
        All three checks are whole-account: with several markets bound, an
        unresolved intent, a position or an authorized side on *any* slot makes
        the scan unsafe, and the primary market is not a proxy for the rest.
        """

        return (not self.ledger.unresolved_intents()
                and not self._has_open_position()
                and self._discovery_is_free())
