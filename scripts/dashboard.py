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
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _bot_era import ACCOUNT_WIDE_NOTE, BOT_ERA_START  # noqa: E402
from liquidity_bot.adapters import (  # noqa: E402
    AsyncReadOnlyGateway, shared_api_limiter)
from liquidity_bot.commissioning import CommissioningConfig  # noqa: E402
from liquidity_bot.pnl import economic_contract_cost  # noqa: E402
from liquidity_bot.sqlite_compat import active_fills_predicate  # noqa: E402
from reward_table import account_realized_today  # noqa: E402

CONFIG = CommissioningConfig.diagnostic_defaults()
MAX_MARKETS = CONFIG.max_markets
LOG = Path("logs/commissioning.log")
DB = "data/commissioning.db"
W = 70

# A live session on 2026-08-17 stalled for 1h56m with the process healthy the
# whole time. Ten minutes is comfortably past normal quiet and well short of
# that.
STALE_ACTION_SECONDS = 600.0


def _risk_caps(config: CommissioningConfig = CONFIG) -> tuple[float, float, float]:
    """Read scaled caps from one configured instance, never class defaults."""

    return (
        float(config.max_session_loss_usd),
        float(config.max_market_loss_usd),
        float(config.hard_stop_loss_usd),
    )

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


def _runner_started_at(now: datetime) -> datetime | None:
    """When the CURRENT runner process started, or None if it is not running.

    Everything in the log before this belongs to a process that is already
    dead. `commissioning.log` is append-only across restarts, so a fixed
    line window silently mixes the two.
    """

    try:
        out = subprocess.run(["ps", "-eo", "etimes,args"], capture_output=True,
                             text=True, timeout=20).stdout
    except Exception:
        return None
    for row in out.splitlines():
        if "liquidity_bot.live_commissioning" not in row or "grep" in row:
            continue
        try:
            return now - timedelta(seconds=int(row.split(None, 1)[0]))
        except Exception:
            return None
    return None


def _this_session(lines: list[str], since: datetime | None) -> list[str]:
    """The tail of `lines` written by the runner that is running now.

    Fixed windows like `lines[-400:]` read the previous session as current,
    and every health check built on one lied after the 2026-08-18 restart:
    a crash three hours dead still showed `risk stops 1`, and the stalled
    activity of the process that died showed as `STALLED` on a runner that
    was six seconds old and perfectly healthy. A dashboard that cries wolf
    about a fixed problem is worse than no dashboard.

    Cuts FORWARDS to the first timestamped line at or after the process
    start, rather than backwards to the last one before it. The difference
    is not cosmetic and the backwards version shipped broken: a session's
    final JSON block -- the one carrying `stop_reason` -- has no `at=` and
    trails the last timestamped line of the dead session, so scanning
    backwards kept it and `risk stops` still counted the old crash.
    Everything before the first line of the live session is another
    process's, timestamped or not.

    The 2s grace absorbs `etimes` integer-second rounding, which would
    otherwise clip the runner's own first lines.
    """

    if since is None:
        return lines
    cutoff = since - timedelta(seconds=2)
    for index, row in enumerate(lines):
        found = re.search(r"at=(\S+)", row)
        if not found:
            continue
        try:
            stamp = datetime.fromisoformat(found.group(1))
        except Exception:
            continue
        if stamp >= cutoff:
            return lines[index:]
    # A runner that has not logged a timestamped line yet has no session to
    # report. Empty is the honest answer: the checks then read "no [action]
    # in the recent log", not a stall inherited from a dead process.
    return []


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




def _embed(*, now, failures, health, today_pnl, total_pnl, quoted, idle_bound,
           free, positions, orders, fills_today, fills_all, session_loss,
           session_cap, viable, tight, rewarded, reward_paid, reward_pending,
           uptime):
    """A Discord embed in the house style the runner already uses.

    Deliberately NOT a second format. `discord_summary.py` established the
    vocabulary -- a colour-coded side-stripe, an `##` headline, emoji field
    names and monospace text bodies -- and a parallel ANSI message would have
    been a third thing to read.
    """

    from liquidity_bot.discord_summary import GREEN, ORANGE, RED

    if failures:
        colour, icon = RED, "\U0001f6d1"
        state = "NOT WORKING \u00b7 " + ", ".join(failures)
    elif quoted is None:
        colour, icon = ORANGE, "\U0001f7e0"
        state = "WORKING \u00b7 venue state unavailable"
    elif idle_bound:
        colour, icon = ORANGE, "\U0001f7e0"
        state = "WORKING \u00b7 {} slot(s) wasted".format(len(idle_bound))
    else:
        colour, icon = GREEN, "\U0001f7e2"
        state = "WORKING"

    def signed(value):
        # Discord does NOT render ANSI inside embeds -- it works in ordinary
        # message content but embed descriptions and fields strip it. Verified
        # 2026-08-18: the payload carried correct 0;32 / 0;31 escapes and still
        # displayed plain, so the approach is wrong rather than the codes.
        #
        # A coloured circle is the reliable substitute: it renders in embeds,
        # on mobile, and in clients that strip formatting. Sign and currency
        # stay alongside it so the number reads correctly without the emoji.
        if value is None:
            return "n/a"
        number = float(value)
        mark = "\U0001f7e2" if number >= 0 else "\U0001f534"
        sign = "+" if number >= 0 else "-"
        return "{} {}${:,.2f}".format(mark, sign, abs(number))

    def money(value):
        return "n/a" if value is None else "${:+,.2f}".format(float(value))

    def plain(value):
        return "n/a" if value is None else "${:,.2f}".format(float(value))

    def meter(used, cap, width=12):
        # ANSI does not render inside a Discord text fence, but block
        # characters do, so the terminal's visual cue survives here.
        cap = max(float(cap), 1e-9)
        filled = max(0, min(width, int(round(width * float(used) / cap))))
        return "\u2588" * filled + "\u2591" * (width - filled)

    def row(label, value, width=10):
        return "{:<19}{:>{w}}\n".format(label, value, w=width)

    description = (
        "## {} {}\n".format(icon, state)
        + "**TODAY** {}   \u00b7   **TOTAL** {}\n".format(
            signed(today_pnl), signed(total_pnl))
        + "_up {}_\n".format(uptime))
    if total_pnl is not None:
        description += ("> _TOTAL is account-wide since inception, so it "
                        "includes pre-bot trading._\n")

    health_text = "".join(
        "{:<14}{:>6}\n".format(name[:14], level) for level, name, _ in health)

    if quoted is None:
        slots_text = (row("quoting", "unknown", 8)
                      + row("bound, not quoting", "unknown", 8)
                      + row("empty", "unknown", 8))
    else:
        slots_text = (row("quoting", len(quoted), 5)
                      + row("bound, not quoting", len(idle_bound), 5)
                      + row("empty", free, 5))
    if viable is not None:
        slots_text += (row("viable now", viable, 5)
                       + row("rewarded <2h", rewarded, 5)
                       + row("refused, tight", tight, 5))

    headroom = session_cap - session_loss
    risk_text = (row("positions", "unknown" if positions is None else positions)
                 + row("resting orders", "unknown" if orders is None else orders)
                 + row("trades today", fills_today)
                 + row("trades lifetime", "{:,}".format(fills_all))
                 + "\n"
                 + "used      {}  {}\n".format(
                     meter(session_loss, session_cap), plain(session_loss))
                 + "remaining {}  {}\n".format(
                     meter(headroom, session_cap), plain(headroom))
                 + "stop at   {}\n".format(plain(session_cap)))

    reward_text = (row("paid", plain(reward_paid))
                   + row("pending", plain(reward_pending)))

    fence = "```text\n"
    fields = [
        {"name": "\U0001f6e0\ufe0f Health",
         "value": fence + health_text + "```", "inline": True},
        {"name": f"\U0001f4ca Slots (of {MAX_MARKETS})",
         "value": fence + slots_text + "```", "inline": True},
        {"name": "\u26a0\ufe0f Position & stop loss",
         "value": fence + risk_text + "```", "inline": False},
        {"name": "\U0001f381 Rewards",
         "value": fence + reward_text + "```", "inline": False},
    ]
    if idle_bound:
        fields.append({
            "name": "\U0001f7e0 Bound but not quoting",
            "value": fence + "".join(s[:44] + "\n" for s in idle_bound[:5])
                     + "```",
            "inline": False})
    return {"embeds": [{
        "color": colour,
        "description": description,
        "fields": fields,
        "footer": {"text": now.strftime("dashboard \u00b7 %Y-%m-%d %H:%M:%SZ")},
    }]}


def _signed(value) -> str:
    """Green above zero, red below. The only two colours that matter here."""
    if value is None:
        return paint("d", "       n/a")
    number = float(value)
    return paint("g" if number >= 0 else "r", f"{number:+10,.2f}")


def _pnl_strip(today, total) -> str:
    left = "ET DAY " + _signed(today)
    right = "TOTAL  " + _signed(total)
    return left + " " * max(2, W - 6 - 17 - 17) + right


FUNDING_CACHE = Path("data/funding_cache.json")


async def _funding(gateway) -> tuple[Decimal, Decimal, bool]:
    """Net funding in and reward cash out. Cached, because it barely changes.

    A truncated walk is not an approximation, it is a wrong answer: eight pages
    found $400 of $4,408 and produced a "net +41.45" for an account nowhere
    near it. So a walk is used only when it reaches the end of history, and the
    result is cached; otherwise the last good cached value is reused and the
    caller is told whether it is trustworthy.
    """

    import json
    cached = None
    if FUNDING_CACHE.exists():
        try:
            raw = json.loads(FUNDING_CACHE.read_text())
            cached = (Decimal(raw["funding"]), Decimal(raw["reward_cash"]))
        except Exception:
            cached = None

    deposits = withdrawals = credits = reward_cash = Decimal(0)
    cursor, complete = None, False
    try:
        for _ in range(40):
            params = {"limit": 100}
            if cursor:
                params["cursor"] = cursor
            raw = await gateway._call(
                "GET /v1/portfolio/activities",
                lambda params=params: gateway.client.portfolio.activities(params))
            for activity in (raw.get("activities") or []):
                change = activity.get("accountBalanceChange")
                if not isinstance(change, dict):
                    continue
                if "COMPLETED" not in str(change.get("status", "")):
                    continue
                amount = Decimal(str((change.get("amount") or {}).get("value") or 0))
                text = str(change.get("description", "")).lower()
                if "reward" in text:
                    reward_cash += amount
                elif "withdraw" in text:
                    withdrawals += amount
                elif "deposit" in text:
                    deposits += amount
                else:
                    credits += amount
            cursor = str(raw.get("nextCursor") or "").strip() or None
            if not cursor or raw.get("eof"):
                complete = True
                break
    except Exception:
        complete = False

    if complete:
        funding = deposits - withdrawals + credits
        try:
            FUNDING_CACHE.parent.mkdir(parents=True, exist_ok=True)
            FUNDING_CACHE.write_text(json.dumps(
                {"funding": str(funding), "reward_cash": str(reward_cash)}))
        except Exception:
            pass
        return funding, reward_cash, True
    if cached:
        return cached[0], cached[1], True
    return Decimal(0), Decimal(0), False


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--markets", action="store_true")
    parser.add_argument("--pnl", action="store_true")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--color", action="store_true")
    parser.add_argument("--discord", action="store_true")
    parser.add_argument("--no-color", action="store_true")
    # Discord renders ANSI inside an ```ansi fence, and piping to it
    # is not a tty, so colour has to be forced rather than detected.
    args = parser.parse_args()
    if args.all:
        args.markets = args.pnl = True
    if args.no_color or (not args.color and not sys.stdout.isatty()):
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

    # Scoped to the running process. See `_this_session`.
    session = _this_session(lines, _runner_started_at(now))
    age = _last_action_age(now, session)
    places = sum(1 for row in session if "PLACE_POST_ONLY" in row)
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
                                  # FAILED is resolved, not outstanding. A
                                  # failed intent is a mutation we know did
                                  # not happen; counting it as unresolved
                                  # reported a permanent phantom that no
                                  # action could ever clear.
                                  "WHERE state NOT IN ('RECONCILED','FAILED')"
                                  ).fetchone()[0]
        active_fills = active_fills_predicate(conn)
        fills_all = conn.execute(
            "SELECT COUNT(*) FROM execution_fills "
            f"WHERE {active_fills}").fetchone()[0]
        fills_today = conn.execute(
            "SELECT COUNT(*) FROM execution_fills "
            f"WHERE {active_fills} AND event_at LIKE ?",
            (now.strftime("%Y-%m-%d") + "%",)).fetchone()[0]
        conn.close()
    except Exception as exc:
        health.append(("warn", "ledger", f"unreadable: {type(exc).__name__}"))

    blocked = sum(1 for row in session if "rotation blocked" in row)
    if absent > 0:
        health.append(("bad", "rotation",
                       paint("r", f"{absent} orders UNKNOWN_ABSENT \u2014 BLOCKED")))
        failures.append("rotation blocked")
    elif blocked:
        health.append(("warn", "rotation", f"{blocked} blocked events in log"))
    else:
        health.append(("ok", "rotation",
                       f"unblocked \u00b7 {absent} absent \u00b7 {unresolved} unresolved"))

    stops = sum(1 for row in session if "risk_stop:" in row)
    health.append(("warn" if stops else "ok", "risk stops",
                   f"{stops} in the recent log" if stops else "none"))

    # None is materially different from zero. During a venue outage zero would
    # claim the account is flat and order-free even though the read that could
    # prove that state did not complete.
    orders: int | None = None
    positions: int | None = None
    quoted: set[str] | None = None
    equity: Decimal | None = None
    today_pnl: Decimal | None = None
    venue_ok = False
    try:
        from liquidity_bot.account_snapshots import AsyncAccountSnapshotGateway
        from liquidity_bot.probe import client_from_environment
        gateway = AsyncReadOnlyGateway(
            client_from_environment(),
            limiter=shared_api_limiter(),
            default_traffic_class="background",
        )
        account = AsyncAccountSnapshotGateway(gateway)
        live = await account.fetch_open_orders(observed_at=now)
        account_today = await account_realized_today(DB, gateway, now=now)
        if (account_today.marked_value is None
                or account_today.account_equity is None
                or account_today.position_count is None):
            raise RuntimeError(account_today.reason)
        # Publish the snapshot atomically only after every account component
        # succeeds. A later-read failure must not leak a partial order count.
        orders = len(live)
        quoted = {o.market_slug for o in live}
        positions = account_today.position_count
        equity = account_today.account_equity
        today_pnl = account_today.marked_value
        venue_ok = True
    except Exception as exc:
        health.append(("warn", "venue", f"unreachable: {type(exc).__name__}"))

    total_pnl = None
    if venue_ok:
        try:
            funding, _cash, trusted = await _funding(gateway)
            if trusted:
                total_pnl = equity - funding
        except Exception:
            pass

    state = ("\u2715 NOT WORKING \u00b7 " + ", ".join(failures)) if failures \
        else "\u25cf WORKING"
    colour = "r" if failures else "g"
    print("\u2554" + "\u2550" * (W - 2) + "\u2557")
    print("\u2551  " + paint("b", "LIQUIDITY BOT")
          + " " * max(1, W - 20 - len(now.strftime('%Y-%m-%d %H:%MZ')))
          + now.strftime("%Y-%m-%d %H:%MZ") + "  \u2551")
    print("\u2551  " + paint(colour, state).ljust(W - 6 + len(paint(colour, "")) )
          + "  \u2551")
    print("╠" + "═" * (W - 2) + "╣")
    print("║  " + _pnl_strip(today_pnl, total_pnl) + "  ║")
    print("\u255a" + "\u2550" * (W - 2) + "\u255d")
    print("")
    for level, name, detail in sorted(
            health, key=lambda h: {"bad": 0, "warn": 1, "ok": 2, "idle": 3}[h[0]]):
        print(f"  {dot(level)} {name:<12} {detail}")
    print("")
    return await _sections(args, now, lines, gateway if venue_ok else None,
                           orders, quoted, positions, equity,
                           fills_today, fills_all, failures,
                           health, today_pnl, total_pnl)


async def _sections(args, now, lines, gateway, orders, quoted, positions,
                    equity, fills_today, fills_all, failures,
                    health=(), today_pnl=None, total_pnl=None) -> int:
    viable = None
    tight = rewarded = 0
    if args.markets and gateway is not None:
        try:
            from liquidity_bot.market_selector import RewardMarketSelector
            policy = CONFIG.reward_selection_policy()
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
    venue_available = quoted is not None
    idle_bound = sorted(touched - quoted) if venue_available else []

    print(box_top("SLOTS"))
    if not venue_available:
        free = None
        print(line(paint("y", "venue data unavailable — slot counts unknown")))
        print(line())
        print(line(f"{dot('warn')} quoting              unknown"))
        print(line(f"{dot('warn')} bound, not quoting   unknown"))
        print(line(f"{dot('warn')} empty                unknown"))
    else:
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
    if positions is None or orders is None:
        print(line(f"positions  unknown      resting  unknown"
                   f"        trades  {fills_today} today"))
    else:
        print(line(f"positions  {positions} " + ("(flat)" if positions == 0 else "")
                   + f"      resting  {orders}        trades  {fills_today} today"))
    print(line(" " * 46 + f"{fills_all:,} lifetime"))
    print(line())
    session_cap, market_cap, hard_cap = _risk_caps()
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
        active_fills = active_fills_predicate(conn)
        for slug, intent, price, qty in conn.execute(
                "SELECT market_slug,intent,price,qty FROM execution_fills "
                f"WHERE {active_fills} AND event_at LIKE ?",
                (now.strftime("%Y-%m-%d") + "%",)):
            q, p_ = Decimal(str(qty)), Decimal(str(price))
            book = books[slug]
            if intent == "ORDER_INTENT_BUY_LONG":
                book["lq"] += q
                book["lc"] += q * economic_contract_cost(intent, p_)
            elif intent == "ORDER_INTENT_BUY_SHORT":
                book["sq"] += q
                book["sc"] += q * economic_contract_cost(intent, p_)
        conn.close()
        # NET across every market, winners included. This summed only the
        # losing markets and discarded the winning ones, which is a gross
        # loss total and not what any stop reads: `runtime_safety.blockers`
        # trips on `-realized_pnl >= max_session_loss`, a net figure.
        #
        # Measured 2026-08-18: the bar showed $36.34 of a $40 cap -- "$3.66
        # left" -- on a day whose net realised was -$7.88, so roughly $32 was
        # actually left. It overstated by 4.6x in the direction that makes a
        # healthy bot look one fill from a hard stop, which is exactly the
        # reading that gets a working bot shut off by hand.
        #
        # `worst_market` stays per-market and negative-only, because that IS
        # the shape of its stop: `any(value <= -max_market_loss for value in
        # realized_by_market.values())`.
        net = 0.0
        for book in books.values():
            pairs = min(book["lq"], book["sq"])
            if pairs <= 0:
                continue
            lc = book["lc"] / book["lq"] * pairs if book["lq"] else Decimal(0)
            sc = book["sc"] / book["sq"] * pairs if book["sq"] else Decimal(0)
            realised = float(pairs - lc - sc)
            net += realised
            if realised < 0:
                worst_market = max(worst_market, -realised)
        # A profitable day consumes none of the stop, never a negative bar.
        session_loss = max(0.0, -net)
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


    if args.discord:
        try:
            from liquidity_bot.notifications import DiscordNotifier
            from liquidity_bot.probe import load_runtime_secrets
            reward_paid = reward_pending = None
            if gateway is not None:
                try:
                    paid = pending = Decimal(0)
                    for entry in await gateway.fetch_earnings():
                        amount = Decimal(str(getattr(entry, "reward", 0) or 0))
                        status = str(getattr(entry, "status", "")).upper()
                        if status == "PAID":
                            paid += amount
                        elif status == "PENDING":
                            pending += amount
                    reward_paid, reward_pending = paid, pending
                except Exception:
                    pass
            DiscordNotifier(load_runtime_secrets()).send_payload(
                _embed(now=now, failures=failures, health=health,
                       today_pnl=today_pnl, total_pnl=total_pnl,
                       quoted=quoted, idle_bound=idle_bound, free=free,
                       positions=positions, orders=orders,
                       fills_today=fills_today, fills_all=fills_all,
                       session_loss=session_loss, session_cap=session_cap,
                       viable=viable, tight=tight, rewarded=rewarded,
                       reward_paid=reward_paid, reward_pending=reward_pending,
                       uptime=_uptime()),
                channel="summary")
            print(paint("d", "  posted to Discord"))
        except Exception as exc:
            print(paint("y", "  Discord post failed: {}: {}".format(
                type(exc).__name__, exc)))

    if args.pnl and gateway is not None:
        await _pnl(now, gateway, equity)
    elif args.pnl:
        print(paint("y", "  P&L skipped: venue unreachable"))
    if not args.markets:
        print(paint("d", "  add --markets for slot supply, --pnl for P&L"))
    return 1 if failures else 0


async def _pnl(now, gateway, equity) -> None:
    """Daily reward and trading, week subtotals, and never-reset totals.

    Reward per day comes from the venue by EARNING date, so it is recoverable
    historically. Trading per day is not -- it needs equity at each past
    midnight, which cannot be queried retroactively -- so days before the first
    snapshot print `n/a` rather than a fabricated number.

    Venue reward dates are midnight-to-midnight America/New_York. Totals are
    timezone-independent; any daily trading comparison must use that same
    boundary rather than UTC.
    """

    import json

    daily = collections.defaultdict(
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

    # Equity at each past midnight, from the appended snapshot series. Only
    # days with BOTH ends present can be priced; the rest stay n/a.
    equity_by_day: dict[str, Decimal] = {}
    path = Path("data/accounting_snapshots.json")
    if path.exists():
        try:
            for entry in json.loads(path.read_text()):
                day = str(entry.get("at", ""))[:10]
                equity_by_day[day] = Decimal(str(entry.get("equity", "0")))
        except Exception:
            equity_by_day = {}

    # From the bot's first order, not from the account's first reward. This
    # read 2026-08-10 and so folded in maker-lab's three pre-bot days.
    days = sorted(d for d in daily if d >= BOT_ERA_START)
    if not days:
        return
    start = datetime.strptime(days[0], "%Y-%m-%d")
    start -= timedelta(days=start.weekday())
    span = [(start + timedelta(days=n)).strftime("%Y-%m-%d")
            for n in range((now.date() - start.date()).days + 1)]


    # Funding: the only term that needs a history walk, so it is confined to
    # --pnl and bounded at eight pages (measured enough to reach every deposit).
    # MUST reach eof. An eight-page walk found only the most recent deposit
    # ($400 of $4,408) and produced a "net +41.45" for an account that is
    # nowhere near it. A partial funding total does not make the derived P&L
    # approximate, it makes it wrong, so an incomplete walk prints nothing
    # derived at all.
    deposits = withdrawals = credits = cash_reward = Decimal(0)
    cursor = None
    complete = False
    try:
        for _ in range(30):
            params = {"limit": 100}
            if cursor:
                params["cursor"] = cursor
            raw = await gateway._call(
                "GET /v1/portfolio/activities",
                lambda params=params: gateway.client.portfolio.activities(params))
            for activity in (raw.get("activities") or []):
                change = activity.get("accountBalanceChange")
                if not isinstance(change, dict):
                    continue
                if "COMPLETED" not in str(change.get("status", "")):
                    continue
                amount = Decimal(str((change.get("amount") or {}).get("value") or 0))
                text = str(change.get("description", "")).lower()
                if "reward" in text:
                    cash_reward += amount
                elif "withdraw" in text:
                    withdrawals += amount
                elif "deposit" in text:
                    deposits += amount
                else:
                    credits += amount
            cursor = str(raw.get("nextCursor") or "").strip() or None
            if not cursor or raw.get("eof"):
                complete = True
                break
    except Exception:
        complete = False

    funding = deposits - withdrawals + credits
    trading_pnl = equity - funding - cash_reward
    # Summed here rather than referenced as an undefined `grand`, which is
    # what this printed before -- the section raised NameError and no totals
    # were ever shown. `daily` is built from the venue's earnings above.
    pending_total = sum(
        (value["pending"] for value in daily.values()), Decimal(0))
    print(box_top("TOTALS"))
    print(line(paint("d", ACCOUNT_WIDE_NOTE)))
    print(line(f"{'equity now':<28}{float(equity):>12,.2f}"))
    print(line(f"{'net funding in':<28}{float(funding):>12,.2f}  "
               + paint("d", f"in {float(deposits):,.0f} · out "
                            f"{float(withdrawals):,.0f}")))
    # Cash actually received, from the transfer history -- not the venue's
    # PAID total. They agree to $0.24 (see FINDINGS), but this line says
    # "cash", and `trading` below is derived from this same number, so using
    # the other one would make the arithmetic on screen not add up.
    print(line(f"{'reward received (cash)':<28}{float(cash_reward):>12,.2f}  "
               + dot("ok")))
    print(line(f"{'reward still pending':<28}{float(pending_total):>12,.2f}  "
               + dot("warn")))
    if complete:
        print(line(f"{'trading':<28}{float(trading_pnl):>12,.2f}"))
        print(rule())
        on_cash = equity - funding
        print(line(paint("b", f"{'net on cash received':<28}"
                              f"{float(on_cash):>12,.2f}")))
        print(line(paint("b", f"{'net if all pending pays':<28}"
                              f"{float(on_cash + pending_total):>12,.2f}")))
    else:
        print(rule())
        print(line(paint("y", "funding history incomplete — trading and net")))
        print(line(paint("y", "suppressed rather than shown wrong")))
    print(box_bottom())
    print(paint("d", "  reward dates use midnight-to-midnight ET; totals are "
                     "timezone-independent"))


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
