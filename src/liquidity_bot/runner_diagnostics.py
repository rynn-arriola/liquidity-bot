"""Diagnostic log lines for the commissioning runner.

Moved verbatim from ``live_commissioning.CommissioningRunner`` (pure
relocation, no behavior change). Methods run on the runner instance via
inheritance, so every ``self`` attribute and override resolves as before.
These only print local diagnostics; they never make trading decisions.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any

from .market_selector import RewardMarketSelection

DECISION_SCHEDULER_LOG_SECONDS = 60.0


class RunnerDiagnosticsMixin:

    # Calibration measurements (scripts/calibration_report.py). Both write only
    # to the optional market-quality archive; neither reads the API, gates,
    # delays or changes an order. A failure prints once and switches that
    # measurement off for this process instead of being swallowed silently.

    def _measurement_failed(self, component: str, exc: Exception) -> None:
        disabled = getattr(self, "_disabled_measurements", None)
        if disabled is None:
            disabled = self._disabled_measurements = set()
        if component not in disabled:
            disabled.add(component)
            print(f"[measurement-disabled] at={self._now().isoformat()} "
                  f"component={component} error={type(exc).__name__}: "
                  f"{str(exc)[:160]}", flush=True)

    def _sample_fill_markouts(self, now: datetime) -> None:
        """Record mid prices 30 s / 2 min / 10 min after each owned fill."""

        sink = getattr(self, "quality_sink", None)
        if sink is None or "fill_markout" in getattr(self, "_disabled_measurements", ()):
            return
        try:
            from .fill_markouts import FillMarkoutSampler
            from .market_quality_measurement import emit
            sampler = getattr(self, "_fill_markout_sampler", None)
            if sampler is None:
                sampler = self._fill_markout_sampler = FillMarkoutSampler()

            def book_for(slug):
                state = self._markets.get(slug)
                return None if state is None else (state.book, state.book_observed_at)

            sampler.poll(self.ledger.query, now, book_for,
                         lambda record: emit(sink, "fill_markout", lambda: record))
        except Exception as exc:
            self._measurement_failed("fill_markout", exc)

    def _note_entry_suppressed(self, slug: str, action: Any, now: datetime,
                               reason: str) -> None:
        """One record per market side per minute when a cap blocks an entry."""

        sink = getattr(self, "quality_sink", None)
        if sink is None or "entry_suppressed" in getattr(self, "_disabled_measurements", ()):
            return
        try:
            from .market_quality_measurement import emit
            seen = getattr(self, "_entry_suppressed_at", None)
            if seen is None:
                seen = self._entry_suppressed_at = {}
            key = (slug, action.side, reason)
            last = seen.get(key)
            if last is not None and 0 <= (now - last).total_seconds() < 60:
                return
            seen[key] = now
            state = self._markets.get(slug)
            start = (state.market.game_start
                     if state is not None and state.market is not None else None)
            working, reservations = self._flat_entry_orders_and_reservations()
            emit(sink, "entry_suppressed", lambda: {
                "at": now.isoformat(), "market": slug, "side": action.side,
                "price": action.price, "reason": reason,
                "plan_reason": action.reason,
                "cap": self._effective_entry_order_cap(now),
                "pending_entries": len(working) + reservations,
                "seconds_to_event": ((start - now).total_seconds()
                                     if start is not None else None)})
        except Exception as exc:
            self._measurement_failed("entry_suppressed", exc)

    def _log_selection_scan(self, selection: RewardMarketSelection) -> None:
        """Expose discovery headroom once for every successful full scan."""

        from .market_quality_measurement import emit
        from .selection_measurement import selection_measurement
        emit(self.quality_sink, "scan", lambda: json.loads(selection_measurement(
            selection, self.config, self._now())))

        elapsed = (
            f"{self._selection_scan_seconds_last:.1f}"
            if self._selection_scan_seconds_last is not None else "unknown")
        print(
            f"[selection-scan] at={self._now().isoformat()} "
            f"event_pages={getattr(selection, 'event_pages_used', 0)} "
            f"event_page_ceiling={getattr(self.gateway, 'event_max_pages', 0)} "
            f"event_markets={getattr(selection, 'catalog_markets', 0)} "
            f"executable={getattr(selection, 'executable_catalog_markets', 0)} "
            f"rewarded={getattr(selection, 'rewarded_executable_markets', 0)} "
            f"books_ranked={getattr(selection, 'books_evaluated', 0)} "
            f"viable={getattr(selection, 'viable_markets', 0)} "
            f"payout_est_ge_1={self._payout_floor_count(selection, 1.0)} "
            f"payout_est_ge_1_5={self._payout_floor_count(selection, 1.5)} "
            f"scan_seconds={elapsed}",
            flush=True,
        )
        # Preserve market/program/rank evidence for later payout comparison.
        # These bounded, local scan records add no request and feed no decision.
        try:
            for rank, item in enumerate(
                    (item for item in selection.opportunities if item.viable_sides),
                    start=1):
                print(
                    f"[payout-estimate] at={self._now().isoformat()} "
                    f"market={item.market.slug} program={item.period.program_id} "
                    f"period={item.period.period} rank={rank} "
                    f"sides={','.join(item.viable_sides)} "
                    f"remaining_usd={item.maximum_remaining_reward_total:.6f} "
                    "basis=current_share_remaining_only enforcement=none",
                    flush=True,
                )
        except Exception as exc:
            print(f"[payout-estimate] unavailable={type(exc).__name__}", flush=True)

        # One bounded local record of the evaluated slate. No network reads,
        # influence on selection, or propagation of optional logging failure.
        try:
            from .selection_measurement import selection_measurement
            print("[market-measurement] " + selection_measurement(
                selection, self.config, self._now()), flush=True)
        except Exception:
            # Even unavailable telemetry is optional; retain trading's result.
            pass

    def _log_maintenance_schedule_state(self, now: datetime) -> None:
        window = self._maintenance_decision.window
        signature: tuple[object, ...] = (
            self._maintenance_feed_state,
            self._maintenance_decision.phase,
            window.maintenance_id if window else None,
            window.status if window else None,
            self._maintenance_health_required_for,
        )
        if signature == self._maintenance_schedule_signature:
            return
        self._maintenance_schedule_signature = signature
        fields = [
            f"[maintenance-schedule] at={now.isoformat()}",
            f"feed={self._maintenance_feed_state.value}",
            f"phase={self._maintenance_decision.phase.value}",
            f"detail={self._maintenance_feed_detail}",
        ]
        if window is not None:
            fields.extend((
                f"id={window.maintenance_id}",
                f"status={window.status}",
                f"evacuate_at={window.evacuation_at.isoformat()}",
                f"scheduled_for={window.scheduled_for.isoformat()}",
                f"scheduled_until={window.scheduled_until.isoformat()}",
                f"resume_at={window.resume_at.isoformat() if window.resume_at else 'unknown'}",
            ))
        if self._maintenance_health_required_for is not None:
            fields.append(
                f"health_required_for={self._maintenance_health_required_for}")
        print(" ".join(fields), flush=True)

    def _log_decision_scheduler(self, now: datetime) -> None:
        last = self._scheduler_last_log_at
        if (last is not None
                and (now - last).total_seconds()
                < DECISION_SCHEDULER_LOG_SECONDS):
            return
        process_cpu_now = time.process_time()
        total_cpu_seconds = (
            max(0.0, process_cpu_now - self._scheduler_process_cpu_at_last_log)
            if self._scheduler_process_cpu_at_last_log is not None else 0.0)
        window_seconds = (
            max(0.0, (now - last).total_seconds())
            if last is not None else 0.0)
        cpu_cores = (
            total_cpu_seconds / window_seconds if window_seconds > 0 else 0.0)
        print(
            f"[decision-scheduler] at={now.isoformat()} "
            f"passes={self._scheduler_passes} "
            f"markets_seen={self._scheduler_markets_seen} "
            f"evaluated={self._scheduler_markets_evaluated} "
            f"skipped={self._scheduler_markets_skipped} "
            f"position_evaluated={self._scheduler_position_evaluations} "
            f"service_cpu_ms={self._scheduler_service_cpu_seconds * 1000:.1f} "
            f"decision_cpu_ms={self._scheduler_decision_cpu_seconds * 1000:.1f} "
            f"total_cpu_ms={total_cpu_seconds * 1000:.1f} "
            f"cpu_cores={cpu_cores:.3f} "
            f"event_loop_delay_p50={self._scheduler_percentile(self._scheduler_event_loop_delays, .50):.4f} "
            f"event_loop_delay_p95={self._scheduler_percentile(self._scheduler_event_loop_delays, .95):.4f} "
            f"decision_latency_p50={self._scheduler_percentile(self._scheduler_decision_latencies, .50):.3f} "
            f"decision_latency_p95={self._scheduler_percentile(self._scheduler_decision_latencies, .95):.3f} "
            f"action_latency_p50={self._scheduler_percentile(self._scheduler_action_latencies, .50):.3f} "
            f"action_latency_p95={self._scheduler_percentile(self._scheduler_action_latencies, .95):.3f}",
            flush=True,
        )
        self._scheduler_last_log_at = now
        self._scheduler_process_cpu_at_last_log = process_cpu_now
        self._scheduler_passes = 0
        self._scheduler_markets_seen = 0
        self._scheduler_markets_evaluated = 0
        self._scheduler_markets_skipped = 0
        self._scheduler_position_evaluations = 0
        self._scheduler_service_cpu_seconds = 0.0
        self._scheduler_decision_cpu_seconds = 0.0
        self._scheduler_event_loop_delays.clear()
        self._scheduler_decision_latencies.clear()
        self._scheduler_action_latencies.clear()

    async def _log_api_budget(self, now: datetime) -> None:
        """Emit limiter evidence without making a diagnostic API request."""

        read = getattr(self.gateway.limiter, "shared_telemetry", None)
        if callable(read):
            evidence = await read(reset=True)
        else:
            read = getattr(self.gateway.limiter, "telemetry", None)
            if not callable(read):
                return
            evidence = read(reset=True)
        categories = ",".join(
            f"{name}:{count}"
            for name, count in evidence.attempts_by_category
        ) or "none"
        failure_categories = ",".join(
            f"{name}:{count}"
            for name, count in evidence.failed_responses_by_category_status
        ) or "none"
        print(
            f"[api-budget] at={now.isoformat()} "
            f"slots={self.config.max_markets} "
            f"seconds={evidence.elapsed_seconds:.1f} "
            f"attempts={evidence.attempts} "
            f"rps={evidence.attempted_requests_per_second:.3f} "
            f"reads={evidence.read_attempts} "
            f"mutations={evidence.mutation_attempts} "
            f"failures={evidence.failed_responses} "
            f"rate_limited={evidence.rate_limited_responses} "
            f"wait_p95={evidence.wait_p95_seconds:.3f} "
            f"wait_p99={evidence.wait_p99_seconds:.3f} "
            f"wait_max={evidence.max_wait_seconds:.3f} "
            f"response_p95={evidence.response_p95_seconds:.3f} "
            f"response_p99={evidence.response_p99_seconds:.3f} "
            f"response_max={evidence.max_response_seconds:.3f} "
            f"waiting={evidence.waiting_now} "
            f"max_waiting={evidence.max_waiting} "
            f"cooldown={evidence.cooldown_remaining_seconds:.1f} "
            f"cooldown_safety={evidence.safety_cooldown_remaining_seconds:.1f} "
            f"cooldown_entry={evidence.entry_cooldown_remaining_seconds:.1f} "
            f"cooldown_background={evidence.background_cooldown_remaining_seconds:.1f} "
            f"cooldown_report={evidence.report_cooldown_remaining_seconds:.1f} "
            f"shared={int(evidence.shared_across_processes)} "
            f"categories={categories} "
            f"failure_categories={failure_categories} "
            f"account_read_cooldowns={','.join(f'{name}:{seconds:.1f}' for name, seconds in evidence.account_read_cooldowns if seconds > 0) or 'none'}",
            flush=True,
        )

    def _log_shutdown(self, market_slug: str, decision: str, **fields: Any) -> None:
        """Log one shutdown decision, the way `[action]` logs a live one.

        Shutdown builds its own ``PlanAction`` and calls ``driver.apply_one``
        directly, so none of it ever reached the `[action]` log. When the exit
        budget was exhausted on krovit-sukiho (2026-08-15) the alert said the
        budget was gone and nothing recorded where it went -- not the prices
        tried, not whether depth refused, not whether the market was skipped
        for a working order. Shutdown is when a position gets stranded
        overnight, so it is the last path that should be invisible.
        """

        detail = " ".join(
            f"{name}={value}" for name, value in fields.items()
            if value is not None)
        print(
            f"[shutdown] at={self._now().isoformat()} market={market_slug} "
            f"decision={decision}" + (f" {detail}" if detail else ""),
            flush=True,
        )

    def _log_book_rate_limit(self, slug: str) -> None:
        # One concise line per actual failed request, not per deferred owner.
        # HTTP failure bodies can contain gateway HTML and IP addresses.
        print(f"[book-rate-limit] at={self._now().isoformat()} market={slug} "
              "status=429 action=endpoint_backoff fresh_book_required=yes",
              flush=True)

    def _log_api_backpressure(
        self, component: str, delay: float, *, cause: str | None = None,
    ) -> None:
        """Record designed local deferral without calling it a venue error."""

        print(
            f"[api-backpressure] at={self._now().isoformat()} "
            f"component={component} delay={delay:.1f}s requests_sent=0 "
            f"cause={cause if isinstance(cause, str) and cause.startswith('LOCAL_') and cause.replace('_', '').isalnum() else 'unspecified'}",
            flush=True,
        )
