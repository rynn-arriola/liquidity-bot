"""Bounded local risk evidence. No venue calls, raw errors or trading authority."""
from collections import OrderedDict

from .errors import AdapterError, ApiAdmissionDeferred
from .cash_activity import CashActivityUnavailable
from .read_backoff import is_transport_failure


_REASONS = {
    "settled cash changed during account equity snapshot": "cash_changed_positions",
    "settled cash changed during non-trading activity snapshot": "cash_changed_history",
    "ET risk day changed during non-trading activity snapshot": "day_rollover",
    "open-position basis is unavailable for account safety": "position_basis_unknown",
    "settled window account snapshot is stale": "snapshot_stale",
    "cash-activity boundary is unavailable": "cash_boundary_unknown",
    "completed cash activity changed after the account snapshot": "cash_activity_moved",
    "cash-activity pagination has no explicit eof": "cash_eof_missing",
    "cash-activity pagination ended without eof": "cash_eof_missing",
    "cash-activity pagination cursor repeated": "cash_cursor_repeated",
    "cash-activity scan did not reach the ET boundary": "cash_page_bound",
}
_GUARD_REASONS = {
    "session clock moved backwards": "clock_backwards",
    "session duration limit reached": "duration_limit",
    "marked account equity is unknown": "equity_unknown",
    "marked account equity is stale": "equity_stale",
}


class RiskDiagnostics:
    def __init__(self):
        self.last = OrderedDict()

    def emit(self, now, event, *, key, fields):
        """Internal constant labels only; bound both log rate and key storage."""
        previous = self.last.get((event, key))
        if previous is not None and 0 <= (now - previous).total_seconds() < 60:
            return
        self.last[(event, key)] = now
        self.last.move_to_end((event, key))
        while len(self.last) > 32:
            self.last.popitem(last=False)
        print(f"[risk-diagnostic] at={now.isoformat()} event={event} {fields}", flush=True)

    def refresh_failed(self, now, *, stage, error, previous_observed_at):
        # Compare exact local messages but never print arbitrary exception text,
        # operation URLs, server bodies, error codes or exception class names.
        reason = _REASONS.get(str(error), "read_or_validation_failed")
        if (isinstance(error, CashActivityUnavailable)
                and str(error).startswith("unclassified completed cash transfer:")):
            reason = "cash_transfer_unclassified"
        category = ("local_deferred" if isinstance(error, ApiAdmissionDeferred)
                    else "transport" if is_transport_failure(error)
                    else "cash_validation" if isinstance(error, CashActivityUnavailable)
                    else "adapter" if isinstance(error, AdapterError) else "local_error")
        status = getattr(error, "status_code", None)
        if type(status) is not int or not 100 <= status <= 599:
            status = "unknown"
        age = ("unknown" if previous_observed_at is None else
               f"{(now - previous_observed_at).total_seconds():.3f}")
        self.emit(now, "refresh_failed", key=(stage, reason, category, status),
                  fields=f"stage={stage} reason={reason} category={category} status={status} previous_age_s={age}")

    def entry_blocked(self, runner, now, market):
        from datetime import date
        from .reward_calendar import risk_day

        today = date.fromisoformat(risk_day(now))
        reasons = []
        guard = runner.risk_guard
        if guard is None:
            reasons.append("guard_unknown")
        else:
            reasons.extend(_GUARD_REASONS.get(value, "critical_or_unknown_guard")
                           for value in guard.blockers(now=now))
            if guard.hard_stop_reached(now=now):
                reasons.append("hard_limit")
        checks = (
            (not runner._window_entry_ready(now), "window_not_ready"),
            (runner._provisional_hard_stop_at is not None, "provisional_hard"),
            (runner._baseline_day != today, "baseline_day"),
            (runner._nontrading_cash is None, "cash_unknown"),
            (runner._nontrading_cash_day != today, "cash_day"),
            (runner._manual_entry_paused, "manual_pause"),
            (runner._hard_stop_active(), "hard_hold"),
            (runner._window_risk is not None and runner._window_risk.provisional, "window_provisional"),
            (runner._transport_recovery_component is not None, "transport_recovery"),
            (runner._market_fault_pause_until is not None, "market_fault_pause"),
            (runner._scheduled_maintenance_pause_active(), "scheduled_maintenance"),
            (runner._ui_availability_initialized and runner._ui_entry_paused, "ui_pause"),
            (not runner.config.strategy.allows_entry(market, now), "event_lifecycle"),
            (runner._live_entry_blocker(market.slug, now) is not None, "live_entry_gate"),
        )
        reasons.extend(label for failed, label in checks if failed)
        labels = tuple(reasons) or ("unclassified",)
        self.emit(now, "entry_blocked", key=labels, fields="reasons=" + ",".join(labels))
