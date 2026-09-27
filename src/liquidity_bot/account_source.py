"""Operator-only account-source control. No credentials or venue mutations."""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


def normalize_mode(value: str) -> str:
    if value == "rest-fallback":
        return "rest"
    if value not in {"rest", "websocket", "hybrid"}:
        raise ValueError("account source must be rest, websocket or hybrid")
    return value


def read_object(path: Path) -> dict:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate account-source field")
            result[key] = value
        return result
    if path.stat().st_size > 16384:
        raise ValueError("account-source control exceeds size bound")
    value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique)
    if not isinstance(value, dict):
        raise ValueError("account-source control must be an object")
    return value


def atomic_write(path: Path, value: dict, *, exclusive=False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                               0o600), "w", encoding="utf-8") as handle:
            json.dump(value, handle, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        if exclusive:
            # Publish the already complete inode without replacing a request
            # submitted by another operator. The runner never sees partial JSON.
            os.link(temporary, path)
        else:
            os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@dataclass(frozen=True)
class SwitchRequest:
    request_id: str
    run_id: str
    generation: int
    source: str
    target: str
    created_at: datetime
    expires_at: datetime

    @classmethod
    def parse(cls, raw: dict):
        if set(raw) != set(cls.__dataclass_fields__):
            raise ValueError("invalid account-source request fields")
        for key in ("request_id", "run_id"):
            if str(uuid.UUID(raw[key])) != raw[key]:
                raise ValueError("invalid account-source request identity")
        if type(raw["generation"]) is not int or raw["generation"] < 0:
            raise ValueError("invalid account-source generation")
        created = datetime.fromisoformat(raw["created_at"])
        expires = datetime.fromisoformat(raw["expires_at"])
        if created.utcoffset() is None or expires.utcoffset() is None:
            raise ValueError("account-source request requires timezone")
        if not 0 < (expires - created).total_seconds() <= 300:
            raise ValueError("account-source request lifetime exceeds five minutes")
        return cls(raw["request_id"], raw["run_id"], raw["generation"],
                   normalize_mode(raw["source"]), normalize_mode(raw["target"]),
                   created, expires)

    def valid_for(self, control, now: datetime) -> bool:
        return ("hybrid" not in {self.source, self.target}
                and self.run_id == control.run_id
                and self.generation == control.generation
                and self.source == control.selected
                and self.target != self.source
                and self.created_at <= now < self.expires_at)


class AccountSourceControl:
    """Single-run request identity; only successful selections survive restart."""

    def __init__(self, selected: str, directory: Path):
        self.selected = normalize_mode(selected)
        self.directory = Path(directory)
        self.run_id = str(uuid.UUID(os.environ["LIQUIDITY_ACCOUNT_SOURCE_RUN_ID"])) if (
            "LIQUIDITY_ACCOUNT_SOURCE_RUN_ID" in os.environ) else str(uuid.uuid4())
        self.generation = 0
        self.request = None
        self.state = "initializing"
        self.reason = "startup"
        self.last_request_id = None

    @property
    def request_path(self):
        return self.directory / "account-source-request.json"

    def publish(self, now: datetime):
        atomic_write(self.directory / "account-source-status.json", {
            "run_id": self.run_id, "generation": self.generation,
            "selected": self.selected, "state": self.state,
            "reason": self.reason, "request_id": self.last_request_id,
            "target": self.request.target if self.request else None,
            "observed_at": now.isoformat(), "pid": os.getpid(),
        })

    def take_request(self, now: datetime):
        if self.request is not None or not self.request_path.exists():
            return None
        # Rename atomically: a concurrent operator cannot overwrite the item
        # being read or cause us to unlink their subsequent request.
        claimed = self.request_path.with_name("account-source-claimed.json")
        os.replace(self.request_path, claimed)
        try:
            request = SwitchRequest.parse(read_object(claimed))
            if not request.valid_for(self, now):
                raise ValueError("stale, wrong-run or wrong-source request")
            if request.request_id == self.last_request_id:
                raise ValueError("duplicate account-source request")
            self.last_request_id = request.request_id
            self.request = request
            self.state, self.reason = "verifying", "operator_request"
            return request
        finally:
            claimed.unlink(missing_ok=True)

    def persist_selected(self, mode: str):
        atomic_write(self.directory / "account-source-selection.json",
                     {"mode": normalize_mode(mode), "run_id": self.run_id})


@dataclass(frozen=True)
class AccountSourcePolicy:
    default_mode: str = "websocket"
    verification_seconds: int = 45
    read_concurrency: int = 4
    rest_pending_entry_ceiling: int = 30

    def __post_init__(self):
        normalize_mode(self.default_mode)
        for field, low, high in (("verification_seconds", 15, 60),
                                 ("read_concurrency", 1, 4),
                                 ("rest_pending_entry_ceiling", 1, 50)):
            value = getattr(self, field)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"account-source {field} must be {low}-{high}")


def load_source_policy(path: Path):
    raw = read_object(path)
    if set(raw) != set(AccountSourcePolicy.__dataclass_fields__):
        raise ValueError("invalid account-source policy fields")
    return AccountSourcePolicy(**raw)


def startup_mode(explicit: str | None, directory=Path("data"), *, default="websocket") -> str:
    if explicit is not None:
        return normalize_mode(explicit)
    path = Path(directory) / "account-source-selection.json"
    if not path.exists():
        return normalize_mode(default)
    raw = read_object(path)
    if set(raw) not in ({"mode"}, {"mode", "run_id"}):
        raise ValueError("invalid saved account source")
    return normalize_mode(raw["mode"])


def main():
    import argparse
    from datetime import timedelta
    parser = argparse.ArgumentParser(description="Request a verified account-source switch")
    parser.add_argument("action", choices=("status", "rest", "websocket", "hybrid", "relaunch-ready"))
    parser.add_argument("--directory", type=Path, default=Path("data"))
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    if args.action == "relaunch-ready":
        try:
            status = read_object(args.directory / "account-source-status.json")
            selected = read_object(args.directory / "account-source-selection.json")
            ready = (status["run_id"] == selected.get("run_id")
                     == os.environ.get("LIQUIDITY_ACCOUNT_SOURCE_RUN_ID")
                     and status["selected"] == normalize_mode(selected["mode"]))
        except (OSError, ValueError, TypeError, KeyError):
            ready = False
        raise SystemExit(0 if ready else 1)
    status = read_object(args.directory / "account-source-status.json")
    if args.action == "status":
        print(json.dumps(status, indent=2))
        return
    age = (now - datetime.fromisoformat(status["observed_at"])).total_seconds()
    if not 0 <= age <= 60 or status["state"] not in {"active", "unavailable", "failed"}:
        parser.error("runner status is stale or a switch is already pending")
    if args.action == status["selected"]:
        parser.error("that source is already selected")
    if "hybrid" in {args.action, status["selected"]}:
        parser.error("hybrid selection is restart-scoped; use reviewed stop/start gates")
    raw = {
        "request_id": str(uuid.uuid4()), "run_id": status["run_id"],
        "generation": status["generation"], "source": status["selected"],
        "target": args.action, "created_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=300)).isoformat(),
    }
    SwitchRequest.parse(raw)
    # Atomic exclusive publication prevents partial reads and lost requests.
    path = args.directory / "account-source-request.json"
    atomic_write(path, raw, exclusive=True)
    print("Switch requested; verification must finish before the source changes.")


if __name__ == "__main__":
    main()
