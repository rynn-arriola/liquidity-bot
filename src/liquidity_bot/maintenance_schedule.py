"""Official Polymarket US maintenance schedule evidence.

The public Statuspage feed is operational scheduling evidence only.  It can
remove entry authority and establish an evacuation deadline, but it never
proves an order cancelled, a position flat, or the authenticated venue healthy.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from enum import Enum
import gzip
import json
import os
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.error import HTTPError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen


STATUS_HOST = "status.polymarketexchange.com"
MAINTENANCE_HISTORY_URL = (
    f"https://{STATUS_HOST}/api/v2/scheduled-maintenances.json")
MAINTENANCE_UPCOMING_URL = (
    f"https://{STATUS_HOST}/api/v2/scheduled-maintenances/upcoming.json")
MAINTENANCE_ACTIVE_URL = (
    f"https://{STATUS_HOST}/api/v2/scheduled-maintenances/active.json")
PRODUCTION_COMPONENT_ID = "3xmdtkg3r8y5"
PRODUCTION_COMPONENT_NAME = "[Production] Polymarket US Exchange"
MAINTENANCE_PROBE_TIMEOUT_SECONDS = 5.0
MAINTENANCE_PROBE_MAX_BYTES = 1024 * 1024
MAINTENANCE_EVACUATION_LEAD_SECONDS = 30 * 60
MAINTENANCE_RESUME_DELAY_SECONDS = 60 * 60
MAINTENANCE_SCHEDULE_POLL_SECONDS = 60.0
MAINTENANCE_CACHE_PATH = Path("data/polymarket-maintenance-schedule.json")
STATUSPAGE_RECORD_ABSENT_AFTER_END = "record_absent_after_end"


class MaintenanceFeedState(str, Enum):
    HEALTHY = "HEALTHY"
    UNKNOWN = "UNKNOWN"


class MaintenancePhase(str, Enum):
    NORMAL = "NORMAL"
    EVACUATE = "EVACUATE"
    MAINTENANCE = "MAINTENANCE"
    RESUME_HOLD = "RESUME_HOLD"


@dataclass(frozen=True)
class MaintenanceWindow:
    maintenance_id: str
    name: str
    status: str
    scheduled_for: datetime
    scheduled_until: datetime
    updated_at: datetime
    completed_at: datetime | None = None

    def __post_init__(self) -> None:
        if (not self.maintenance_id.strip()
                or len(self.maintenance_id) > 128):
            raise ValueError("maintenance id is invalid")
        if not self.name.strip() or len(self.name) > 512:
            raise ValueError("maintenance name is invalid")
        if not self.status.strip() or len(self.status) > 64:
            raise ValueError("maintenance status is invalid")
        for name, value in (
                ("scheduled_for", self.scheduled_for),
                ("scheduled_until", self.scheduled_until),
                ("updated_at", self.updated_at)):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{name} must be timezone-aware")
        if (self.completed_at is not None
                and (self.completed_at.tzinfo is None
                     or self.completed_at.utcoffset() is None)):
            raise ValueError("completed_at must be timezone-aware")
        if self.scheduled_until <= self.scheduled_for:
            raise ValueError("maintenance end must follow its start")

    @property
    def evacuation_at(self) -> datetime:
        return self.scheduled_for - timedelta(
            seconds=MAINTENANCE_EVACUATION_LEAD_SECONDS)

    @property
    def resume_at(self) -> datetime | None:
        if (self.status not in {
                "completed", STATUSPAGE_RECORD_ABSENT_AFTER_END}
                or self.completed_at is None):
            return None
        return max(self.scheduled_until, self.completed_at) + timedelta(
            seconds=MAINTENANCE_RESUME_DELAY_SECONDS)


@dataclass(frozen=True)
class MaintenanceScheduleProbe:
    state: MaintenanceFeedState
    windows: tuple[MaintenanceWindow, ...]
    detail: str
    observed_at: datetime


@dataclass(frozen=True)
class MaintenanceDecision:
    phase: MaintenancePhase
    window: MaintenanceWindow | None = None

    @property
    def paused(self) -> bool:
        return self.phase is not MaintenancePhase.NORMAL

    @property
    def evacuating(self) -> bool:
        return self.phase is MaintenancePhase.EVACUATE


def _aware_datetime(value: object, name: str) -> datetime:
    raw = str(value or "").strip()
    if raw.endswith("Z"):
        raw = f"{raw[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(f"{name} is not an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return value


def _is_production_window(row: Mapping[str, Any]) -> bool:
    components = row.get("components")
    if not isinstance(components, list):
        return False
    for raw_component in components:
        if not isinstance(raw_component, Mapping):
            continue
        if (str(raw_component.get("id") or "") == PRODUCTION_COMPONENT_ID
                and str(raw_component.get("name") or "")
                == PRODUCTION_COMPONENT_NAME):
            return True
    return False


def _parse_window(raw: object) -> MaintenanceWindow | None:
    row = _mapping(raw, "scheduled maintenance")
    if not _is_production_window(row):
        return None
    maintenance_id = str(row.get("id") or "").strip()
    name = str(row.get("name") or "").strip()
    status = str(row.get("status") or "").strip().lower()
    if not maintenance_id or len(maintenance_id) > 128:
        raise ValueError("production maintenance id is invalid")
    if not name or len(name) > 512:
        raise ValueError("production maintenance name is invalid")
    if not status or len(status) > 64:
        raise ValueError("production maintenance status is invalid")
    scheduled_for = _aware_datetime(
        row.get("scheduled_for"), "scheduled_for")
    scheduled_until = _aware_datetime(
        row.get("scheduled_until"), "scheduled_until")
    updated_at = _aware_datetime(row.get("updated_at"), "updated_at")
    completion_updates: list[datetime] = []
    updates = row.get("incident_updates")
    if isinstance(updates, list):
        for raw_update in updates:
            if not isinstance(raw_update, Mapping):
                continue
            if str(raw_update.get("status") or "").strip().lower() != "completed":
                continue
            completion_updates.append(_aware_datetime(
                raw_update.get("created_at"),
                "completed incident update created_at"))
    completed_at = min(completion_updates) if completion_updates else None
    if status == "completed" and completed_at is None:
        # Some Statuspage list responses omit update detail.  ``updated_at``
        # is positive completion-state evidence, and using it can only extend
        # the operator's one-hour hold if it reflects a later edit.
        completed_at = updated_at
    return MaintenanceWindow(
        maintenance_id=maintenance_id,
        name=name,
        status=status,
        scheduled_for=scheduled_for,
        scheduled_until=scheduled_until,
        updated_at=updated_at,
        completed_at=completed_at,
    )


def _read_json(url: str) -> Mapping[str, Any]:
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "Cache-Control": "no-cache",
            "User-Agent": "liquidity-bot-maintenance-schedule/1.0",
        },
        method="GET",
    )
    with urlopen(
            request, timeout=MAINTENANCE_PROBE_TIMEOUT_SECONDS) as response:
        final_host = (urlparse(response.geturl()).hostname or "").lower()
        if final_host != STATUS_HOST:
            raise ValueError("unexpected maintenance-feed redirect")
        if int(response.status) != 200:
            raise ValueError(
                f"maintenance feed returned HTTP {int(response.status)}")
        encoding = str(response.headers.get("Content-Encoding", "")).lower()
        if encoding == "gzip":
            with gzip.GzipFile(fileobj=response) as compressed:
                raw = compressed.read(MAINTENANCE_PROBE_MAX_BYTES + 1)
        else:
            raw = response.read(MAINTENANCE_PROBE_MAX_BYTES + 1)
    if len(raw) > MAINTENANCE_PROBE_MAX_BYTES:
        raise ValueError("maintenance feed response is too large")
    decoded = json.loads(raw.decode("utf-8"))
    return _mapping(decoded, "maintenance feed")


def _parse_payload(payload: Mapping[str, Any]) -> tuple[MaintenanceWindow, ...]:
    rows = payload.get("scheduled_maintenances")
    if not isinstance(rows, list) or len(rows) > 256:
        raise ValueError("scheduled_maintenances must be a bounded array")
    windows: list[MaintenanceWindow] = []
    for raw in rows:
        parsed = _parse_window(raw)
        if parsed is not None:
            windows.append(parsed)
    return tuple(windows)


def probe_polymarket_us_maintenance_schedule(
    known_windows: Iterable[MaintenanceWindow] = (),
    *,
    include_history: bool = False,
    now: datetime | None = None,
) -> MaintenanceScheduleProbe:
    """Read official production maintenance windows without credentials."""

    observed_at = now or datetime.now(timezone.utc)
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("maintenance observation time must be timezone-aware")
    observed_at = observed_at.astimezone(timezone.utc)
    known = {window.maintenance_id: window for window in known_windows}
    try:
        history: dict[str, MaintenanceWindow] | None = None
        if include_history:
            history = {
                window.maintenance_id: window
                for window in _parse_payload(_read_json(
                    MAINTENANCE_HISTORY_URL))
            }
            remote = dict(history)
        else:
            remote: dict[str, MaintenanceWindow] = {}
            for url in (MAINTENANCE_UPCOMING_URL, MAINTENANCE_ACTIVE_URL):
                remote.update({
                    window.maintenance_id: window
                    for window in _parse_payload(_read_json(url))
                })
        # A completed item disappears from upcoming/active. Re-read every
        # still-relevant known item by exact id. Statuspage can also delete an
        # item after its scheduled end: in that narrow case, an exact 404 plus
        # a successful complete-history read prove the record absent (normal
        # polls also require successful upcoming and active reads). Preserve
        # the planned end as the conservative completion boundary so the
        # ordinary one-hour hold and authenticated recovery checks still
        # apply. Any disappearance before the scheduled end, non-404 detail
        # failure, or failed history read remains UNKNOWN.
        cutoff = observed_at - timedelta(days=2)
        for maintenance_id, prior in known.items():
            if prior.scheduled_until < cutoff:
                continue
            if maintenance_id in remote:
                continue
            detail_url = (
                f"https://{STATUS_HOST}/api/v2/scheduled-maintenances/"
                f"{quote(maintenance_id, safe='')}.json")
            try:
                payload = _read_json(detail_url)
                parsed = _parse_window(payload.get("scheduled_maintenance"))
                if (parsed is None
                        or parsed.maintenance_id != maintenance_id):
                    raise ValueError(
                        "exact maintenance detail lost production identity")
            except HTTPError as exc:
                if exc.code != 404:
                    raise
                if history is None:
                    history = {
                        window.maintenance_id: window
                        for window in _parse_payload(_read_json(
                            MAINTENANCE_HISTORY_URL))
                    }
                parsed = history.get(maintenance_id)
            if parsed is not None:
                remote[maintenance_id] = parsed
                continue
            if observed_at < prior.scheduled_until:
                raise ValueError(
                    "known maintenance disappeared before scheduled end")
            if (prior.status == "completed"
                    and prior.completed_at is not None):
                remote[maintenance_id] = prior
                continue
            remote[maintenance_id] = replace(
                prior,
                status=STATUSPAGE_RECORD_ABSENT_AFTER_END,
                completed_at=prior.scheduled_until,
            )
        windows = tuple(sorted(
            remote.values(), key=lambda item: (
                item.scheduled_for, item.maintenance_id)))
        return MaintenanceScheduleProbe(
            MaintenanceFeedState.HEALTHY, windows,
            "official_statuspage", observed_at)
    except Exception as exc:
        return MaintenanceScheduleProbe(
            MaintenanceFeedState.UNKNOWN, tuple(known.values()),
            type(exc).__name__, observed_at)


def maintenance_decision(
    windows: Iterable[MaintenanceWindow], now: datetime,
) -> MaintenanceDecision:
    """Select the most urgent current production maintenance constraint."""

    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("maintenance decision time must be timezone-aware")
    now = now.astimezone(timezone.utc)
    decisions: list[MaintenanceDecision] = []
    for window in windows:
        if now < window.evacuation_at:
            continue
        resume_at = window.resume_at
        if resume_at is not None:
            if now < resume_at:
                decisions.append(MaintenanceDecision(
                    MaintenancePhase.RESUME_HOLD, window))
            continue
        phase = (
            MaintenancePhase.EVACUATE
            if now < window.scheduled_for
            else MaintenancePhase.MAINTENANCE
        )
        decisions.append(MaintenanceDecision(phase, window))
    if not decisions:
        return MaintenanceDecision(MaintenancePhase.NORMAL)
    priority = {
        MaintenancePhase.MAINTENANCE: 0,
        MaintenancePhase.EVACUATE: 1,
        MaintenancePhase.RESUME_HOLD: 2,
        MaintenancePhase.NORMAL: 3,
    }
    return min(decisions, key=lambda item: (
        priority[item.phase],
        item.window.scheduled_for if item.window else now))


def load_maintenance_cache(
    path: Path = MAINTENANCE_CACHE_PATH,
) -> tuple[MaintenanceWindow, ...]:
    """Load only previously verified scheduling evidence; corruption is empty."""

    try:
        document = _mapping(
            json.loads(Path(path).read_text(encoding="utf-8")),
            "maintenance cache")
        if set(document) != {"schema_version", "observed_at", "windows"}:
            raise ValueError("maintenance cache keys are invalid")
        if document["schema_version"] != 1:
            raise ValueError("maintenance cache schema is unsupported")
        _aware_datetime(document["observed_at"], "observed_at")
        rows = document["windows"]
        if not isinstance(rows, list) or len(rows) > 256:
            raise ValueError("maintenance cache windows are invalid")
        windows: list[MaintenanceWindow] = []
        for row in rows:
            item = _mapping(row, "maintenance cache window")
            if set(item) != {
                    "maintenance_id", "name", "status", "scheduled_for",
                    "scheduled_until", "updated_at", "completed_at"}:
                raise ValueError("maintenance cache window keys are invalid")
            completed = item["completed_at"]
            windows.append(MaintenanceWindow(
                maintenance_id=str(item["maintenance_id"]),
                name=str(item["name"]),
                status=str(item["status"]),
                scheduled_for=_aware_datetime(
                    item["scheduled_for"], "scheduled_for"),
                scheduled_until=_aware_datetime(
                    item["scheduled_until"], "scheduled_until"),
                updated_at=_aware_datetime(item["updated_at"], "updated_at"),
                completed_at=(
                    _aware_datetime(completed, "completed_at")
                    if completed is not None else None),
            ))
        return tuple(windows)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, TypeError):
        return ()


def write_maintenance_cache(
    windows: Iterable[MaintenanceWindow], observed_at: datetime,
    path: Path = MAINTENANCE_CACHE_PATH,
) -> None:
    """Atomically retain verified windows across process/supervisor restarts."""

    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("cache observation time must be timezone-aware")

    def stamp(value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    payload = {
        "schema_version": 1,
        "observed_at": stamp(observed_at),
        "windows": [{
            "maintenance_id": item.maintenance_id,
            "name": item.name,
            "status": item.status,
            "scheduled_for": stamp(item.scheduled_for),
            "scheduled_until": stamp(item.scheduled_until),
            "updated_at": stamp(item.updated_at),
            "completed_at": (
                stamp(item.completed_at) if item.completed_at else None),
        } for item in windows],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(0o600)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
