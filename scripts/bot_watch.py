#!/usr/bin/env python3
"""Push a phone alert (ntfy) when a bot starts, stops or crashes.

Runs every minute from cron in the live bot directory. It looks at the process list
from the outside, so it also catches a dead supervisor, a kill, an out-of-memory
exit or the Endgame watchdog's restart. Read-only apart from the alert and its own
state file (data/bot-watch.json).

    bot_watch.py            check once (cron)
    bot_watch.py --test     send a test alert
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from liquidity_bot.ntfy import send  # noqa: E402

STATE = Path("data/bot-watch.json")
CRON_MARK = "# liquidity-bot bot watch"
BOTS = {
    "Liquidity Trader": "liquidity_bot.live_commissioning",
    "Endgame Trader": "late_favorite_trader.py run",
}


def running(ps: str) -> dict[str, bool]:
    return {name: needle in ps for name, needle in BOTS.items()}


def _tail(path: Path, lines: int = 80) -> list[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]
    except OSError:
        return []


def stop_reason(name: str) -> tuple[str, bool]:
    """(reason, crashed?) from the bot's own log, best effort."""
    if name == "Endgame Trader":
        tail = _tail(Path("logs/late-favorite-trader.log"))
        if any("Timeout (" in line for line in tail):
            return "watchdog: a cycle got stuck (auto-restarts within a minute)", True
        if any(line.startswith("Traceback") for line in tail):
            return "crashed with an error (auto-restarts within a minute)", True
        return "stopped (restart or kill)", False
    tail = _tail(Path("logs/commissioning.log"), 400)
    for line in reversed(tail):
        if '"stop_reason"' in line:
            reason = line.split(":", 1)[-1].strip().strip('",')
            return (f"stopped by operator ({reason})", False) if "operator" in reason else (
                f"stopped: {reason}", True)
    return "stopped (no stop record found)", True


def describe_start(name: str) -> str:
    if name == "Endgame Trader":
        try:
            policy = json.loads((ROOT / "config" / "late_favorite_policy.json").read_text(encoding="utf-8"))
            mode = "LIVE (real money)" if policy.get("enabled") and policy.get("live_orders") else "practice"
            return (f"{mode} · ${policy.get('risk_per_trade_usd')}/trade · daily stop "
                    f"${policy.get('daily_loss_stop_usd')}")
        except Exception:
            return "started"
    try:
        policy = json.loads(Path("config/strategy_policy.json").read_text(encoding="utf-8"))
        parts = [n for n, on in (("pregame", policy.get("pregame_enabled", True)),
                                 ("live", policy.get("live_enabled", False))) if on]
        return "farming " + " + ".join(parts or ["(none)"])
    except Exception:
        return "started"


def check(ps: str | None = None, now: datetime | None = None, notify=send) -> list[str]:
    now = now or datetime.now(timezone.utc)
    if ps is None:
        ps = subprocess.run(["ps", "-eo", "args="], capture_output=True, text=True, timeout=10).stdout
    try:
        state = json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    sent = []
    first_run = not state
    for name, up in running(ps).items():
        before = state.get(name, {}).get("up")
        if before is None or before == up:
            state.setdefault(name, {"up": up, "since": now.isoformat()})
            continue
        if up:
            title, body, priority, tags = f"{name} started", describe_start(name), 3, "green_circle"
        else:
            reason, crashed = stop_reason(name)
            title = f"{name} {'CRASHED' if crashed else 'stopped'}"
            body, priority, tags = reason, (5 if crashed else 4), ("rotating_light" if crashed else "red_circle")
        notify(title, body, priority=priority, tags=tags)
        sent.append(title)
        state[name] = {"up": up, "since": now.isoformat()}
    if first_run:
        status = ", ".join(f"{n} {'ON' if s['up'] else 'OFF'}" for n, s in state.items())
        notify("Bot alerts on", f"Watching both bots. Now: {status}", priority=2, tags="eyes")
        sent.append("Bot alerts on")
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    tmp.replace(STATE)
    return sent


def cron_line(root: Path, code: Path = ROOT) -> str:
    return (f"* * * * * cd {root} && flock -n data/bot-watch.lock timeout 50 "
            f"{root}/.venv/bin/python -B {code}/scripts/bot_watch.py >> logs/bot-watch.log 2>&1 {CRON_MARK}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--test", action="store_true")
    a = ap.parse_args(argv)
    if a.test:
        ok = send("Test alert", "Bot alerts are working.", priority=3, tags="white_check_mark")
        print(f"test sent={ok}")
        return 0 if ok else 1
    sent = check()
    if sent:
        print(f"[bot-watch] at={datetime.now(timezone.utc).isoformat()} sent={sent}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
