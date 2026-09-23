"""Local host supervision. No credentials, venue calls or writable ledger.

Windows invokes tick periodically. The same POSIX control lock serializes tick,
operator start and operator stop. A runner still acquires its existing exclusive
execution lease and performs normal authenticated ownership/recovery proof.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import time
import uuid

from .account_source import read_object

STATE = "host_recovery.json"
CONTROL_LOCK = "host_control.lock"
MAX_RECOVERIES = 3


def read_state(root: Path) -> dict:
    path = root / "data" / STATE
    if not path.exists():
        return {"status": "disarmed"}
    data = read_object(path)
    if (type(data.get("version")) is not int or data.get("version") != 1
            or data.get("status") not in {
                "armed", "recovering", "disarmed", "attention", "recovered"}):
        raise ValueError("invalid host recovery state; automatic launch refused")
    return data


def write_state(root: Path, state: dict) -> None:
    path = root / "data" / STATE
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
    try:
        with temp.open("x", encoding="utf-8") as handle:
            os.chmod(temp, 0o600)
            json.dump(state, handle, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
        if os.name == "posix":
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        temp.unlink(missing_ok=True)


@contextmanager
def control_lock(root: Path, *, blocking: bool = True):
    import fcntl
    path = root / "data" / CONTROL_LOCK
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        flags = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
        fcntl.flock(handle.fileno(), flags)
        yield


def arm(root: Path, target: str, size: str, slots: str, source: str) -> dict:
    from .quote_size import parse_quote_size
    from .slot_capacity import parse_slot_capacity
    if (not target or any(c.isspace() for c in target)
            or target.startswith("-")):
        raise ValueError("invalid recovery target")
    if source not in {"saved", "rest", "rest-fallback", "websocket", "hybrid"}:
        raise ValueError("invalid recovery account source")
    state = {
        "version": 1, "status": "armed", "token": uuid.uuid4().hex,
        "target": target, "size": str(parse_quote_size(size)),
        "slots": str(parse_slot_capacity(slots)), "source": source,
        "attempts": 0, "updated_at": time.time(),
    }
    write_state(root, state)
    return state


def finish(root: Path, status: str, token: str | None = None) -> None:
    if status == "disarmed" and token is None:
        # A damaged recovery file must never prevent an operator stop.
        write_state(root, {"version": 1, "status": "disarmed",
                           "updated_at": time.time()})
        return
    state = read_state(root)
    if token is not None and state.get("token") != token:
        return  # An old child's exit must never disarm a newer manual launch.
    if token is not None and state.get("status") == "disarmed":
        return
    state.update(version=1, status=status, updated_at=time.time())
    write_state(root, state)


def process_present() -> bool:
    # All trading processes on this sole live host count, even another checkout.
    result = subprocess.run(["ps", "-eo", "comm=,args="], check=True,
                            capture_output=True, text=True, timeout=5)
    for line in result.stdout.splitlines():
        fields = line.strip().split(None, 1)
        if len(fields) != 2:
            continue
        name, args = fields
        if (name.startswith("python") and (
                "liquidity_bot.live_commissioning" in args or "run_live.py" in args)):
            return True
        if name in {"bash", "sh"} and "supervise_commissioning.sh" in args:
            return True
    return False


def tick(root: Path, *, present=process_present, popen=subprocess.Popen,
         clock=time.time) -> str:
    """Called under control_lock; launch only the existing recovery executor."""
    state = read_state(root)
    if state.get("status") not in {"armed", "recovering"}:
        return state.get("status", "disarmed")
    if present():
        return "process_present"
    now = clock()
    if now < state.get("next_attempt_at", 0):
        return "backoff"
    attempts = state.get("attempts", 0)
    if type(attempts) is not int or attempts < 0 or attempts >= MAX_RECOVERIES:
        finish(root, "attention", state.get("token"))
        return "attention"
    # Validate stored arguments using the same normal-launch validators.
    from .quote_size import parse_quote_size
    from .slot_capacity import parse_slot_capacity
    size = str(parse_quote_size(state["size"]))
    slots = str(parse_slot_capacity(state["slots"]))
    target, source = state["target"], state["source"]
    if (not isinstance(target, str) or not target or target.startswith("-")
            or any(c.isspace() for c in target)
            or source not in {"saved", "rest", "rest-fallback", "websocket", "hybrid"}):
        raise ValueError("invalid saved recovery settings")
    # A successfully completed operator source switch outlives its process.
    # Pending requests never update this file and cannot select a source here.
    selection = root / "data" / "account-source-selection.json"
    if selection.exists():
        from .account_source import startup_mode
        startup_mode(None, root / "data")  # Refuse malformed saved selection.
        source = "saved"
    token = state.get("token")
    if not isinstance(token, str) or len(token) != 32:
        raise ValueError("invalid recovery generation")
    state.update(status="recovering", attempts=attempts + 1,
                 next_attempt_at=now + 120, updated_at=now)
    write_state(root, state)  # Persist the attempt before any child can exist.
    (root / "logs").mkdir(exist_ok=True)
    with (root / "logs" / "supervisor.log").open("ab") as log:
        popen(["bash", "scripts/supervise_commissioning.sh", target, size,
               slots, source, "recovery-only", token], cwd=root,
              stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
              start_new_session=True, close_fds=True)
    return "recovery_started"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["tick", "arm", "disarm", "finish", "check"])
    parser.add_argument("values", nargs="*")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    # Shell launch/stop already hold descriptor 9 across process changes.
    parser.add_argument("--lock-held", action="store_true")
    args = parser.parse_args()

    def execute():
        if args.command == "tick":
            print(tick(args.root))
        elif args.command == "arm":
            if len(args.values) != 4:
                raise ValueError("arm requires target, size, slots, source")
            print(arm(args.root, *args.values)["token"])
        elif args.command == "disarm":
            finish(args.root, "disarmed")
        elif args.command == "finish":
            if len(args.values) != 2 or args.values[0] not in {
                    "attention", "recovered", "disarmed"}:
                raise ValueError("finish requires status and token")
            finish(args.root, *args.values)
        else:
            state = read_state(args.root)
            if (len(args.values) != 1 or state.get("token") != args.values[0]
                    or state.get("status") not in {"armed", "recovering"}):
                raise ValueError("host launch disarmed or superseded")
    if args.lock_held:
        execute()
    else:
        try:
            # Windows checks must not queue behind a slow start/stop forever.
            with control_lock(args.root, blocking=args.command != "tick"):
                execute()
        except BlockingIOError:
            if args.command != "tick":
                raise
            print("control_busy")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
