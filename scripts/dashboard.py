#!/usr/bin/env python3
"""One screen that answers "is the bot working, and am I making money".

Read-only in every direction: the ledger is opened `mode=ro`, only read
endpoints are called, and nothing here writes to the venue or the runner. Safe
to run against a live session.

Built 2026-08-18 after a day in which four separate faults all looked identical
from outside -- process up, supervisor content, log ticking -- and every one was
found by the operator rather than by the bot. Each health line below is a
failure that actually happened.

The thing it gets right that a glance at open orders does not: **order count at
any instant is noise.** The runner places, cancels and re-places continuously,
so a snapshot catches it mid-cycle and reads anywhere from 0 to 7 for a
perfectly healthy bot -- two readings seconds apart genuinely disagreed on
2026-08-18. What separates working from stuck is the AGE OF THE LAST ACTION.

    python3 scripts/dashboard.py              # health + slots + risk (3 calls)
    python3 scripts/dashboard.py --markets    # + selection funnel (~50 calls)
    python3 scripts/dashboard.py --pnl        # + daily P&L and totals (~25)
    python3 scripts/dashboard.py --all --no-color

`--markets` and `--pnl` share the one 4 req/s governor with the live runner, so
do not loop them. The default is cheap enough to run any time.
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, "src")
sys.path.insert(0, "/root/liquidity-bot/src")

MAX_MARKETS = 20
LOG = Path("logs/commissioning.log")
DB = "data/commissioning.db"
W = 70

# A live session on 2026-08-17 stalled for 1h56m with the process healthy the
# whole time. Ten minutes is comfortably past normal quiet and well short of
# that.
STALE_ACTION_SECONDS = 600.0

C = {"g": "\033[32m", "y": "\033[33m", "r": "\033[31m",
     "d": "\033[90m", "b": "\033[1m", "x": "\033[0m"}


def paint(code: str, text: str) -> str:
    return f"{C[code]}{text}{C['x']}" if C["x"] else text


def dot(level: str) -> str:
    return {"ok": paint("g", "\u25cf"), "warn": paint("y", "\u25d0"),
            "bad": paint("r", "\u2715"), "idle": paint("d", "\u25cb")}[level]


def bar(value: float, total: float, width: int = 24, level: str = "ok") -> str:
    total = max(total, 1e-9)
    filled = max(0, min(width, int(round(width * value / total))))
    return paint(level, "\u2588" * filled) + paint("d", "\u2591" * (width - filled))


def rule(title: str = "") -> str:
    if not title:
        return "\u2502  " + paint("d", "\u2500" * (W - 6))
    pad = W - 8 - len(title)
    return "\u2502  " + paint("d", "\u2500" * max(0, pad) + " " + title + " ")


def box_top(title: str) -> str:
    return ("\u250c\u2500 " + paint("b", title) + " "
            + "\u2500" * max(0, W - 6 - len(title)) + "\u2510")


def box_bottom() -> str:
    return "\u2514" + "\u2500" * (W - 2) + "\u2518"


def line(text: str = "") -> str:
    return "\u2502  " + text


def _proc(pattern: str) -> int:
    try:
        out = subprocess.run(["ps", "-eo", "args"], capture_output=True,
                             text=True, timeout=20).stdout
    except Exception:
        return -1
    return sum(1 for row in out.splitlines()
               if pattern in row and "grep" not in row)


def _tail(limit: int = 6000) -> list[str]:
    if not LOG.exists():
        return []
    try:
        with LOG.open("rb") as handle:
            handle.seek(0, 2)
            handle.seek(max(0, handle.tell() - 4_000_000))
            return handle.read().decode("utf-8", "replace").splitlines()[-limit:]
    except Exception:
        return []


def _uptime() -> str:
    try:
        out = subprocess.run(["ps", "-eo", "etime,args"], capture_output=True,
                             text=True, timeout=20).stdout
    except Exception:
        return "?"
    for row in out.splitlines():
        if "liquidity_bot.live_commissioning" in row and "grep" not in row:
            return row.strip().split()[0]
    return "-"


def _last_action_age(now: datetime, lines: list[str]) -> float | None:
    for row in reversed(lines):
        if "[action]" not in row:
            continue
        found = re.search(r"at=(\S+)", row)
        if found:
            try:
                return (now - datetime.fromisoformat(found.group(1))).total_seconds()
            except Exception:
                return None
    return None


def _money(value) -> Decimal:
    if isinstance(value, dict):
        value = value.get("value", 0)
    try:
        return Decimal(str(value or 0))
    except Exception:
        return Decimal(0)


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--markets", action="store_true")
    parser.add_argument("--pnl", action="store_true")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--no-color", action="store_true")
    args = parser.parse_args()
    if args.all:
        args.markets = args.pnl = True
    if args.no_color or not sys.stdout.isatty():
        for key in C:
            C[key] = ""

    now = datetime.now(timezone.utc)
    lines = _tail()
    failures: list[str] = []
    health: list[tuple[str, str, str]] = []

    runner = _proc("liquidity_bot.live_commissioning")
    supervisor = _proc("supervise_commissioning.sh")
    if runner > 0:
        health.append(("ok", "runner",
                       f"up {_uptime()} \u00b7 supervisor "
                       + ("attached" if supervisor else
                          paint("y", "MISSING"))))
    else:
        health.append(("bad", "runner", paint("r", "NOT RUNNING")))
        failures.append("runner down")

    age = _last_action_age(now, lines)
    places = sum(1 for row in lines[-2000:] if "PLACE_POST_ONLY" in row)
    if age is None:
        health.append(("warn", "activity", "no [action] in the recent log"))
    elif age > STALE_ACTION_SECONDS:
        health.append(("bad", "activity",
                       paint("r", f"last action {age/60:.0f} min ago \u2014 STALLED")))
        failures.append("stalled")
    else:
        health.append(("ok", "activity",
                       f"last action {age:.0f}s ago \u00b7 {places} recent placements"))

    absent = unresolved = fills_today = fills_all = -1
    try:
        conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        absent = conn.execute("SELECT COUNT(*) FROM execution_orders "
                              "WHERE status='UNKNOWN_ABSENT'").fetchone()[0]
        unresolved = conn.execute("SELECT COUNT(*) FROM execution_intents "
                                  "WHERE state NOT IN ('RECONCILED')").fetchone()[0]
        fills_all = conn.execute("SELECT COUNT(*) FROM execution_fills").fetchone()[0]
        fills_today = conn.execute(
            "SELECT COUNT(*) FROM execution_fills WHERE event_at LIKE ?",
            (now.strftime("%Y-%m-%d") + "%",)).fetchone()[0]
        conn.close()
    except Exception as exc:
        health.append(("warn", "ledger", f"unreadable: {type(exc).__name__}"))

    blocked = sum(1 for row in lines[-400:] if "rotation blocked" in row)
    if absent > 0:
        health.append(("bad", "rotation",
                       paint("r", f"{absent} orders UNKNOWN_ABSENT \u2014 BLOCKED")))
        failures.append("rotation blocked")
    elif blocked:
        health.append(("warn", "rotation", f"{blocked} blocked events in log"))
    else:
        health.append(("ok", "rotation",
                       f"unblocked \u00b7 {absent} absent \u00b7 {unresolved} unresolved"))

    stops = sum(1 for row in lines[-400:] if "risk_stop:" in row)
    health.append(("warn" if stops else "ok", "risk stops",
                   f"{stops} in the recent log" if stops else "none"))

    orders = positions = 0
    quoted: set[str] = set()
    equity = Decimal(0)
    venue_ok = False
    try:
        from liquidity_bot.account_snapshots import AsyncAccountSnapshotGateway
        from liquidity_bot.adapters import AsyncReadOnlyGateway
        from liquidity_bot.probe import client_from_environment
        gateway = AsyncReadOnlyGateway(client_from_environment())
        account = AsyncAccountSnapshotGateway(gateway)
        live = await account.fetch_open_orders(observed_at=now)
        orders = len(live)
        quoted = {o.market_slug for o in live}
        held = await account.fetch_positions(observed_at=now)
        positions = sum(1 for p in held
                        if abs(float(getattr(p, "net_qty", 0) or 0)) > 1e-9)
        balance = await account.fetch_balance(observed_at=now)
        equity = (_money(balance.current_balance)
                  + _money(balance.asset_notional))
        venue_ok = True
    except Exception as exc:
        health.append(("warn", "venue", f"unreachable: {type(exc).__name__}"))

    state = ("\u2715 NOT WORKING \u00b7 " + ", ".join(failures)) if failures \
        else "\u25cf WORKING"
    colour = "r" if failures else "g"
    print("\u2554" + "\u2550" * (W - 2) + "\u2557")
    print("\u2551  " + paint("b", "LIQUIDITY BOT")
          + " " * max(1, W - 20 - len(now.strftime('%Y-%m-%d %H:%MZ')))
          + now.strftime("%Y-%m-%d %H:%MZ") + "  \u2551")
    print("\u2551  " + paint(colour, state).ljust(W - 6 + len(paint(colour, "")) )
          + "  \u2551")
    print("\u255a" + "\u2550" * (W - 2) + "\u255d")
    print("")
    for level, name, detail in sorted(
            health, key=lambda h: {"bad": 0, "warn": 1, "ok": 2, "idle": 3}[h[0]]):
        print(f"  {dot(level)} {name:<12} {detail}")
    print("")
    return await _sections(args, now, lines, gateway if venue_ok else None,
                           orders, quoted, positions, equity,
                           fills_today, fills_all, failures)


async def _sections(args, now, lines, gateway, orders, quoted, positions,
                    equity, fills_today, fills_all, failures) -> int:
    viable = None
    tight = rewarded = 0
    if args.markets and gateway is not None:
        try:
            from liquidity_bot.market_selector import (
                RewardMarketSelector, RewardSelectionPolicy)
            policy = RewardSelectionPolicy()
            selection = await RewardMarketSelector(
                gateway, policy=policy).select(now=now)
            viable = len([o for o in selection.opportunities if o.viable_sides])
            tight = selection.catalog_spread_filtered_markets
            rewarded = selection.rewarded_executable_markets
        except Exception:
            viable = None

    # Bound-but-not-quoting: which markets the runner is working right now is
    # in-memory state, so it is inferred from recent log activity. A market it
    # touched in the last few minutes is bound; if it holds no venue order, the
    # slot is held and producing nothing -- the state that wasted two hours on
    # 2026-08-17 while looking perfectly healthy.
    recent_cut = (now - timedelta(minutes=5)).isoformat()
    touched: set[str] = set()
    for row in lines[-2500:]:
        found = re.search(r"at=(\S+).*?market=([a-z0-9-]+)", row)
        if found and found.group(1) >= recent_cut:
            touched.add(found.group(2))
    idle_bound = sorted(touched - set(quoted))

    print(box_top("SLOTS"))
    print(line(bar(len(quoted), MAX_MARKETS, 40,
                   "y" if idle_bound else "g")
               + f"   {len(quoted)} / {MAX_MARKETS}"))
    print(line())
    print(line(f"{dot('ok')} quoting              {len(quoted):<4} resting at the venue"))
    print(line(f"{dot('warn' if idle_bound else 'idle')} bound, not quoting   "
               f"{len(idle_bound):<4} " + (paint('y', 'wasted slots')
                                           if idle_bound else 'none')))
    free = max(0, MAX_MARKETS - len(quoted) - len(idle_bound))
    print(line(f"{dot('idle')} empty                {free:<4} nothing qualifies"))
    for slug in idle_bound[:5]:
        print(line("     " + paint("y", slug[:58])))
    if viable is not None:
        print(line())
        print(line(paint("d", f"supply   {rewarded} rewarded within 2h \u00b7 "
                              f"{tight} refused (book under 2c) \u00b7 "
                              f"{viable} viable")))
    print(box_bottom())
    print("")

    print(box_top("POSITION & RISK"))
    print(line(f"positions  {positions} " + ("(flat)" if not positions else "")
               + f"      resting  {orders}        trades  {fills_today} today"))
    print(line(" " * 46 + f"{fills_all:,} lifetime"))
    print(line())
    try:
        from liquidity_bot.commissioning import CommissioningConfig
        session_cap = float(CommissioningConfig.max_session_loss_usd)
        market_cap = float(CommissioningConfig.max_market_loss_usd)
        hard_cap = float(CommissioningConfig.hard_stop_loss_usd)
    except Exception:
        session_cap = market_cap = 40.0
        hard_cap = 45.0
    # Realised P&L for today, matched-pair priced: one YES plus one NO settles
    # at exactly 1.0000, so a completed pair realises `1.00 - paid`. This is an
    # INDEPENDENT estimate, not what the runner's stop reads -- the stop uses
    # `realized_pnl` minus a baseline it re-snapshots at startup and daily, and
    # that baseline lives in the process. Shown so the headroom is directional;
    # never treat it as the exact trigger distance.
    session_loss = worst_market = 0.0
    try:
        conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        books: dict[str, dict[str, Decimal]] = collections.defaultdict(
            lambda: {"lq": Decimal(0), "lc": Decimal(0),
                     "sq": Decimal(0), "sc": Decimal(0)})
        for slug, intent, price, qty in conn.execute(
                "SELECT market_slug,intent,price,qty FROM execution_fills "
                "WHERE event_at LIKE ?", (now.strftime("%Y-%m-%d") + "%",)):
            q, p_ = Decimal(str(qty)), Decimal(str(price))
            book = books[slug]
            if intent == "ORDER_INTENT_BUY_LONG":
                book["lq"] += q
                book["lc"] += q * p_
            elif intent == "ORDER_INTENT_BUY_SHORT":
                book["sq"] += q
                book["sc"] += q * p_
        conn.close()
        for book in books.values():
            pairs = min(book["lq"], book["sq"])
            if pairs <= 0:
                continue
            lc = book["lc"] / book["lq"] * pairs if book["lq"] else Decimal(0)
            sc = book["sc"] / book["sq"] * pairs if book["sq"] else Decimal(0)
            realised = float(pairs - lc - sc)
            if realised < 0:
                session_loss += -realised
                worst_market = max(worst_market, -realised)
    except Exception:
        session_loss = worst_market = 0.0
    for label, used, cap in (("session loss", session_loss, session_cap),
                             ("worst market", worst_market, market_cap),
                             ("hard stop", session_loss, hard_cap)):
        level = "g" if used < cap * 0.5 else ("y" if used < cap * 0.8 else "r")
        print(line(f"{label:<14}{bar(used, cap, 24, level)}   "
                   f"${used:,.2f} / ${cap:,.0f}"
                   + paint("d", f"   ${cap - used:,.2f} left")))
    print(line(paint("d", "  today's realised loss, matched-pair priced — the runner's")))
    print(line(paint("d", "  own stop re-baselines daily, so treat this as directional")))
    print(box_bottom())
    print("")

    if args.pnl and gateway is not None:
        await _pnl(now, gateway, equity)
    elif args.pnl:
        print(paint("y", "  P&L skipped: venue unreachable"))
    if not args.markets:
        print(paint("d", "  add --markets for slot supply, --pnl for P&L"))
    return 1 if failures else 0


async def _pnl(now, gateway, equity) -> None:
    """Daily reward and trading, plus never-reset totals.

    Reward per day comes from the venue by EARNING date, so it is recoverable
    historically. Trading per day is not: it needs equity at each past
    midnight, which cannot be queried retroactively, so days before the first
    snapshot print `n/a` rather than a fabricated number. From the first
    snapshot onward it is exact.
    """

    daily: dict[str, dict[str, Decimal]] = collections.defaultdict(
        lambda: {"paid": Decimal(0), "pending": Decimal(0)})
    try:
        for row in await gateway.fetch_earnings():
            day = str(getattr(row, "date", ""))[:10]
            if not day:
                continue
            status = str(getattr(row, "status", "")).upper()
            amount = Decimal(str(getattr(row, "reward", 0) or 0))
            if status == "PAID":
                daily[day]["paid"] += amount
            elif status == "PENDING":
                daily[day]["pending"] += amount
    except Exception as exc:
        print(paint("y", f"  reward history unavailable: {type(exc).__name__}"))
        return

    snaps: dict[str, Decimal] = {}
    path = Path("data/accounting_snapshots.json")
    if path.exists():
        try:
            import json
            for entry in json.loads(path.read_text()):
                snaps[str(entry.get("at", ""))[:10]] = Decimal(
                    str(entry.get("equity", "0")))
        except Exception:
            snaps = {}

    days = sorted(d for d in daily if d >= "2026-08-10")
    if not days:
        return
    print(box_top("DAILY P&L"))
    print(line(paint("d", "day              paid   pending    trading      net")))
    weeks: dict[str, list[str]] = collections.OrderedDict()
    for day in days:
        stamp = datetime.strptime(day, "%Y-%m-%d")
        monday = (stamp - timedelta(days=stamp.weekday())).strftime("%Y-%m-%d")
        weeks.setdefault(monday, []).append(day)

    for monday, group in weeks.items():
        print(rule("wk " + monday[5:]))
        totals = {"paid": Decimal(0), "pending": Decimal(0)}
        for day in group:
            stamp = datetime.strptime(day, "%Y-%m-%d")
            paid = daily[day]["paid"]
            pending = daily[day]["pending"]
            totals["paid"] += paid
            totals["pending"] += pending
            mark = dot("ok") if paid and not pending else (
                dot("warn") if pending else dot("idle"))
            note = ("pending" if pending else
                    ("paid" if paid else "no activity"))
            print(line(f"{stamp.strftime('%a %m-%d')}  "
                       f"{float(paid):>8.2f}  {float(pending):>8.2f}  "
                       f"{'n/a':>9}  {'n/a':>7}   {mark} {paint('d', note)}"))
        print(rule())
        print(line(f"{'week':<10}{float(totals['paid']):>10.2f}  "
                   f"{float(totals['pending']):>8.2f}"))
    print(box_bottom())
    print("")

    paid_total = sum(v["paid"] for v in daily.values())
    pending_total = sum(v["pending"] for v in daily.values())
    print(box_top("TOTALS"))
    print(line(f"{'equity now':<26}{float(equity):>12,.2f}"))
    print(line(f"{'reward received (cash)':<26}{float(paid_total):>12,.2f}  "
               + dot("ok")))
    print(line(f"{'reward pending':<26}{float(pending_total):>12,.2f}  "
               + dot("warn")))
    print(line(paint("d", "  trading needs the funding history; "
                          "run scripts/accounting.py")))
    print(box_bottom())


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
