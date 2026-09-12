"""Bounded evidence-only transition timing. Never consumed by execution."""
from collections import OrderedDict
import json


def elapsed(at, anchor):
    if anchor is None:
        return None
    value = (at - anchor).total_seconds()
    return round(value, 6) if value >= 0 else None


class ExecutionTimings:
    def __init__(self, emit=None, limit=256):
        self.emit = emit or (lambda row: print(
            "[execution-timing] " + json.dumps(row, sort_keys=True), flush=True))
        self.limit = limit
        self.positions = OrderedDict()
        self.cancels = OrderedDict()

    def _keep(self, mapping, key, value):
        mapping[key] = value
        mapping.move_to_end(key)
        while len(mapping) > self.limit:
            mapping.popitem(last=False)

    def _emit(self, event, slug, at, source, **fields):
        self.emit(dict(event=event, market=slug, at=at.isoformat(),
                       source=source, **fields))

    def observe(self, quantities, *, at, source, complete=False):
        slugs = set(quantities)
        if complete:
            slugs.update(self.positions)
        for slug in sorted(slugs):
            qty = quantities.get(slug, 0)
            old = self.positions.get(slug)
            if old is not None and old[0] == qty:
                continue
            if old is None and qty == 0:
                continue
            if old is not None and at < old[1]:
                continue
            if old is None or qty * old[0] < 0:
                self.cancels.pop(slug, None)
            event = ("flat_observed" if qty == 0 else
                     "position_initial" if old is None else "position_changed")
            self._emit(event, slug, at, source, quantity=str(qty),
                       previous_observation_age_seconds=elapsed(at, old[1]) if old else None)
            if qty == 0:
                self.positions.pop(slug, None)
                self.cancels.pop(slug, None)
            else:
                self._keep(self.positions, slug, (qty, at, event))

    def cancel_confirmed(self, slug, *, at, source):
        self._keep(self.cancels, slug, at)
        self._emit("cancel_terminal_observed", slug, at, source)

    def action(self, action, result, *, position, at, source, deadline=None):
        submitted = getattr(result, "submitted", None)
        cancelled = getattr(result, "cancel_intent_id", None)
        if position == 0 or (submitted is None and cancelled is None):
            return
        slug = action.market_slug
        old = self.positions.get(slug)
        cancel = self.cancels.get(slug)
        kind = getattr(action.kind, "value", str(action.kind))
        self._emit("position_action_acknowledged", slug, at, source,
                   action=kind, reason=action.reason, quantity=str(position),
                   observation_kind=old[2] if old else None,
                   position_observation_age_seconds=elapsed(at, old[1]) if old else None,
                   cancel_confirmation_age_seconds=elapsed(at, cancel) if submitted else None,
                   deadline_offset_seconds=(round((at - deadline).total_seconds(), 6)
                       if kind == "place_ioc" and deadline is not None else None))
        if submitted is not None:
            self.cancels.pop(slug, None)
