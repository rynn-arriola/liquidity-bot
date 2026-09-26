"""Execution-only parent-event guard. No order or position mutation authority.

Schedule and actual lifecycle are different evidence. The effective deadline
can only tighten once a parent event positively reports play or completion.
"""
from __future__ import annotations

import re
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable

from .adapters import AsyncReadOnlyGateway
from .errors import AdapterError
from .models import MarketMetadata


def event_phase(event: dict) -> str:
    """started / pregame / unknown; positive play beats contradictory copies."""
    sources = [event]
    nested = event.get("eventState")
    metadata = event.get("metadata")
    other = metadata.get("gameState") if isinstance(metadata, dict) else None
    malformed = any(value is not None and not isinstance(value, dict)
                    for value in (metadata, nested, other))
    sources.extend(s for s in (nested, other) if isinstance(s, dict))
    pregame = False
    started = False
    for source in sources:
        for field in ("live", "ended"):
            value = source.get(field)
            if value is True:
                started = True
            elif value is not None and value is not False:
                malformed = True
        period = source.get("period")
        if period is not None and not isinstance(period, str):
            malformed = True
            period = ""
        period = (period or "").strip().upper()
        if re.fullmatch(
                r"(?:S|Q|P|SET|PERIOD|INNING)[1-9][0-9]*|[1-9][0-9]*(?:H|Q|P)?"
                r"|HT|FT|OT|LIVE|IN_PLAY|FINISHED|ENDED", period):
            started = True
        elif period not in {"", "NS", "NOT_STARTED", "PRE_GAME", "PREGAME", "SCHEDULED"}:
            malformed = True
        score = source.get("score")
        if score is not None and not isinstance(score, str):
            malformed = True
        elif score and re.search(r"[1-9]", score):
            started = True
        pregame |= (period in {"NS", "NOT_STARTED", "PRE_GAME", "PREGAME", "SCHEDULED"}
                    or (source.get("live") is False and source.get("ended") is False))
    if started:
        return "started"
    return "pregame" if pregame and not malformed else "unknown"


class EventAwareGateway(AsyncReadOnlyGateway):
    """Batch parent checks with 30-second freshness and bounded failure retry.

    Only verified nested market membership can establish identity. A primary
    market's conventional slug is a lookup hint, never identity proof. All reads
    use the inherited shared governor; failure invalidates entry freshness while
    durable positive-start evidence and position exits remain available.
    """
    def __init__(self, client: Any, *, ledger=None,
                 now: Callable[[], datetime] | None = None, **kwargs):
        super().__init__(client, **kwargs)
        self._event_now = now or (lambda: datetime.now(timezone.utc))
        self._event_ledger = ledger
        self._started = ledger.event_starts() if ledger is not None else {}
        self._parent = {s: v[0] for s, v in self._started.items()}
        self._event_status: dict[str, tuple[datetime, str]] = {}
        self._event_members: dict[str, set[str]] = {}
        self._event_retry_at: datetime | None = None

    def _consume_events(self, events: list[dict], at: datetime) -> None:
        new_starts = {}
        for event in events:
            if not isinstance(event, dict) or not isinstance(event.get("markets"), list):
                raise AdapterError("event membership is unavailable")
            event_slug = event.get("slug")
            if not isinstance(event_slug, str) or not event_slug.strip():
                raise AdapterError("event slug is unavailable")
            phase = event_phase(event)
            self._event_status[event_slug] = (at, phase)
            self._event_members[event_slug] = set()
            for row in event["markets"]:
                slug = row.get("slug") if isinstance(row, dict) else None
                if not isinstance(slug, str) or not slug:
                    continue
                old = self._parent.get(slug)
                if old is not None and old != event_slug:
                    raise AdapterError("market has conflicting parent events")
                self._parent[slug] = event_slug
                self._event_members[event_slug].add(slug)
                if phase == "started" and slug not in self._started:
                    new_starts[slug] = (event_slug, at)
        if new_starts:
            if self._event_ledger is not None:
                self._event_ledger.record_event_starts(new_starts)
            self._started.update(new_starts)

    async def _call(self, label, operation, *args, **kwargs):
        response = await super()._call(label, operation, *args, **kwargs)
        if label == "events.list(upcoming-sports)":
            if not isinstance(response, dict) or not isinstance(response.get("events"), list):
                raise AdapterError("event response is unavailable")
            self._consume_events(response["events"], self._event_now())
        return response

    def apply_event_status(self, market: MarketMetadata) -> MarketMetadata:
        parent = self._parent.get(market.slug)
        evidence = self._event_status.get(parent)
        start = self._started.get(market.slug)
        clock = market.game_start
        if start is not None:
            clock = min(clock, start[1]) if clock is not None else start[1]
        return replace(market, game_start=clock, event_guard_required=True,
            event_slug=parent, event_checked_at=evidence[0] if evidence else None,
            event_not_started=bool(evidence and evidence[1] == "pregame" and start is None
                                   and market.slug in self._event_members.get(parent, set())),
            event_started_at=start[1] if start else None)

    def event_entry_allowed(self, slug: str) -> bool:
        """Zero-I/O recheck immediately after mutation admission waits."""
        parent = self._parent.get(slug)
        evidence = self._event_status.get(parent)
        return bool(slug not in self._started and evidence
                    and evidence[1] == "pregame"
                    and slug in self._event_members.get(parent, set())
                    and 0 <= (self._event_now() - evidence[0]).total_seconds() < 30)

    async def refresh_event_states(self, slugs: Iterable[str], *, force=False) -> None:
        now = self._event_now()
        if not force and self._event_retry_at is not None and now < self._event_retry_at:
            return
        requested = set(slugs)
        parents = set()
        for slug in requested:
            if slug in self._started:
                continue
            parent = self._parent.get(slug)
            if parent is None:
                # Accepted only if the returned event contains this exact slug.
                parent = slug.removeprefix("aec-")
            evidence = self._event_status.get(parent)
            if force or evidence is None or not 0 <= (now-evidence[0]).total_seconds() < 30:
                parents.add(parent)
        if not parents:
            return
        # Invalidate before awaiting: a failed or incomplete response cannot
        # keep a formerly healthy event authorizing new entries.
        for parent in parents:
            self._event_status.pop(parent, None)
        verified = set()
        try:
            for offset in range(0, len(parents), 100):
                batch = sorted(parents)[offset:offset+100]
                params = {"slug": batch, "limit": len(batch), "offset": 0}
                raw = await self._call("events.list(lifecycle-batch)",
                    lambda params=params: self.client.events.list(params))
                if not isinstance(raw, dict) or not isinstance(raw.get("events"), list):
                    raise AdapterError("event lifecycle batch is unavailable")
                events = raw["events"]
                returned = [e.get("slug") for e in events if isinstance(e, dict)]
                if (len(returned) != len(events)
                        or any(not isinstance(s, str) or not s for s in returned)
                        or len(returned) != len(set(returned)) or not set(returned).issubset(batch)):
                    raise AdapterError("event lifecycle batch is incomplete or mismatched")
                self._consume_events(events, self._event_now())
                verified.update(returned)
                if set(returned) != set(batch):
                    raise AdapterError("event lifecycle batch has missing events")
            if any(s not in self._started and
                   s not in self._event_members.get(self._parent.get(s), set())
                   for s in requested):
                raise AdapterError("exact market event membership is unavailable")
        except Exception:
            for parent in parents - verified:
                self._event_status.pop(parent, None)
            self._event_retry_at = self._event_now() + timedelta(seconds=10)
            raise
        self._event_retry_at = None

    async def list_upcoming_sports_markets(self, **kwargs):
        markets = await super().list_upcoming_sports_markets(**kwargs)
        return tuple(self.apply_event_status(m) for m in markets)

    async def fetch_market(self, slug: str):
        market = await super().fetch_market(slug)
        try:
            await self.refresh_event_states([slug])
        except AdapterError:
            # Valid contracts must remain available to position reductions.
            # Missing lifecycle evidence still blocks every entry below.
            pass
        return self.apply_event_status(market)

    async def fetch_markets_by_slugs(self, slugs):
        slugs = tuple(slugs)
        markets = await super().fetch_markets_by_slugs(slugs)
        reason = None
        try:
            await self.refresh_event_states(slugs)
        except AdapterError as exc:
            reason = f"{type(exc).__name__}:{exc}"
        self._log_unfresh_event_evidence(slugs, reason)
        return {s: self.apply_event_status(m) for s, m in markets.items()}

    def _log_unfresh_event_evidence(self, slugs, reason) -> None:
        """Diagnostic only: say why a contract batch lacks fresh event proof.

        Measured 2026-09-26: whole eight-market batches repeatedly failed with
        "event pregame state is unavailable" while this refresh swallowed its
        reason. Behavior is unchanged; entries still require fresh evidence.
        """

        now = self._event_now()
        unfresh = [s for s in slugs
                   if s not in self._started and not self.event_entry_allowed(s)]
        if not unfresh:
            return
        last = getattr(self, "_event_unfresh_logged_at", None)
        if last is not None and 0 <= (now - last).total_seconds() < 60:
            return
        self._event_unfresh_logged_at = now
        if reason is None:
            retry_at = self._event_retry_at
            reason = (f"retry_window_until={retry_at.isoformat()}"
                      if retry_at is not None and now < retry_at
                      else "no_fresh_pregame_evidence")
        sample = ",".join(unfresh[:3])
        print(f"[event-evidence-unfresh] at={now.isoformat()} markets={len(unfresh)}/"
              f"{len(slugs)} reason={reason[:240]} sample={sample}", flush=True)
