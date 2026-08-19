"""One-glance Discord summary designed for reward-aware liquidity provision."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any


GREEN = 0x2ECC71
RED = 0xE74C3C
ORANGE = 0xE67E22
# Deliberately distinct at a glance. The complaint this answers is not that
# alerts were missing, it is that a recovering retry and a fatal condition
# looked identical -- both plain text, both opening "Liquidity Bot".
GRAY = 0x95A5A6
NOTE_SEPARATOR = chr(10) + chr(10) + "_"


def _money(value: Decimal | str | int | float) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("summary money value is not decimal") from exc
    if not result.is_finite():
        raise ValueError("summary money value must be finite")
    return result


def _bytes(value: int) -> str:
    """Human-readable size.

    The ledger is never rebuilt -- it is the audit trail -- so it only ever
    grows, and nothing else on the droplet warns before the disk fills.
    """

    size = float(max(0, value))
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:,.0f}{unit}" if unit == "B" else f"{size:,.1f}{unit}"
        size /= 1024
    return f"{size:,.1f}TB"


def _age(value: float | None) -> str:
    if value is None:
        return "UNKNOWN"
    if value < 0:
        return "CLOCK ERROR"
    return f"{value:.1f}s"


@dataclass(frozen=True)
class LiquiditySummary:
    mode: str
    readiness_status: str
    observed_at: datetime
    session_started_at: datetime
    active_markets: int = 0
    # Managed is not earning. Reward is paid for RESTING, so the count that
    # maps to income is how many of the managed markets actually hold an
    # order. Measured 2026-08-16: 20 managed against 9 resting.
    resting_markets: int = 0
    # Why the rest are not resting, most common first. The question this
    # answers is "are we at full capacity, or down ten markets" -- a count of
    # what is working is only half of that.
    idle_market_reasons: tuple[tuple[str, int], ...] = ()
    reward_eligible_sides: int = 0
    score_seconds: float = 0.0
    entry_fills: int = 0
    exit_samples: int = 0
    exit_completion_rate: float = 0.0
    open_positions: int = 0
    working_orders: int = 0
    max_inventory_age_seconds: float = 0.0
    unresolved_intents: int = 0
    unknown_orders: int = 0
    trading_realized_usd: Decimal = Decimal("0")
    open_marked_usd: Decimal = Decimal("0")
    attributable_paid_rewards_usd: Decimal = Decimal("0")
    pending_rewards_usd: Decimal = Decimal("0")
    session_loss_limit_usd: Decimal = Decimal("0")
    hard_stop_loss_usd: Decimal = Decimal("0")
    # The number the session stop actually reads, not marked equity. Negative
    # is a loss. `trading_realized_usd` above is the equity delta and moves
    # with every unrealised tick -- drawing the stop meter from it would show
    # the bot approaching a limit that nothing is approaching. See
    # `runtime_safety.blockers`, which trips on `-realized_pnl`.
    session_realized_pnl_usd: Decimal = Decimal("0")
    # Worst single market, same sign convention. The per-market stop fires
    # independently of the session one, so a meter for the session alone can
    # read comfortable while a market is one fill from stopping.
    worst_market_realized_pnl_usd: Decimal = Decimal("0")
    # Supply, so the reader can tell "3 markets because 3 is all there is"
    # from "3 markets because something is broken". Without these the slots
    # field reports a ratio against capacity and every quiet evening reads
    # as a fault. `slot_capacity` doubles as the sentinel: 0 means the caller
    # supplied no supply data, and the wording falls back to the old form.
    slot_capacity: int = 0
    viable_markets: int = 0
    rewarded_markets: int = 0
    spread_filtered_markets: int = 0
    selection_failures: int = 0
    # Idle slots held only because their market has already started. Not
    # faults -- a started market can never quote again and is just waiting
    # for the next rotation sweep -- so they are reported apart from the
    # count that means "something is wrong".
    awaiting_rotation_markets: int = 0
    # Bound, holding no order, but placed or cancelled seconds ago -- between
    # a cancel and its re-place. Not a fault, and counting it as one made the
    # alarm fire on ordinary churn.
    cycling_markets: int = 0
    pnl_label: str = "trading"
    rest_requests_per_second: float = 0.0
    market_stream_age_seconds: float | None = None
    private_stream_age_seconds: float | None = None
    # Ledger file plus its WAL sidecar. WAL is not a rounding error here: it
    # can carry a large uncommitted tail, and reporting the .db alone would
    # understate the disk actually in use.
    ledger_bytes: int = 0
    integrity_problems: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.mode not in {"SHADOW", "DRY_RUN", "LIVE"}:
            raise ValueError("summary mode must be SHADOW, DRY_RUN, or LIVE")
        for name, value in (
                ("observed_at", self.observed_at),
                ("session_started_at", self.session_started_at)):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{name} must be timezone-aware")
        for name, value in (
                ("active_markets", self.active_markets),
                ("resting_markets", self.resting_markets),
                ("reward_eligible_sides", self.reward_eligible_sides),
                ("entry_fills", self.entry_fills),
                ("exit_samples", self.exit_samples),
                ("open_positions", self.open_positions),
                ("working_orders", self.working_orders),
                ("unresolved_intents", self.unresolved_intents),
                ("unknown_orders", self.unknown_orders),
                ("slot_capacity", self.slot_capacity),
                ("viable_markets", self.viable_markets),
                ("rewarded_markets", self.rewarded_markets),
                ("spread_filtered_markets", self.spread_filtered_markets),
                ("selection_failures", self.selection_failures),
                ("awaiting_rotation_markets", self.awaiting_rotation_markets),
                ("cycling_markets", self.cycling_markets),
                ("ledger_bytes", self.ledger_bytes)):
            if value < 0:
                raise ValueError(f"{name} cannot be negative")
        if not 0 <= self.exit_completion_rate <= 1:
            raise ValueError("exit completion rate must be within [0, 1]")
        if min(self.score_seconds, self.max_inventory_age_seconds,
               self.rest_requests_per_second) < 0:
            raise ValueError("summary measurements cannot be negative")
        for value in (
                self.trading_realized_usd, self.open_marked_usd,
                self.attributable_paid_rewards_usd, self.pending_rewards_usd,
                self.session_loss_limit_usd, self.hard_stop_loss_usd,
                self.session_realized_pnl_usd,
                self.worst_market_realized_pnl_usd):
            _money(value)


def _signed(value: Decimal) -> str:
    """A signed dollar amount with a colour the client cannot strip.

    Discord does **not** render ANSI inside an embed -- it works in ordinary
    message content, but embed descriptions and field values strip it.
    Verified 2026-08-18: the payload carried correct 0;32 / 0;31 escapes and
    still displayed plain, so the approach was wrong rather than the codes.

    A coloured circle survives everywhere the embed does, including mobile.
    The sign and currency stay alongside it, so the number still reads
    correctly to anyone who cannot see the emoji at all.
    """

    number = float(_money(value))
    mark = "\U0001f7e2" if number >= 0 else "\U0001f534"
    return f"{mark} {'+' if number >= 0 else '-'}${abs(number):,.2f}"


def _meter(used: float, cap: float, width: int = 12) -> str:
    """A progress bar in block characters, which a text fence does render."""

    cap = max(float(cap), 1e-9)
    filled = max(0, min(width, int(round(width * float(used) / cap))))
    return "█" * filled + "░" * (width - filled)


def _row(label: str, value: Any, width: int = 10) -> str:
    return f"{label:<19}{value:>{width}}\n"


def build_liquidity_summary(summary: LiquiditySummary) -> dict[str, Any]:
    """Build a Discord embed without claiming pending rewards as income.

    This is the runner's five-minute status message. Since 2026-08-18 it
    carries the card layout that `scripts/dashboard.py --discord` introduced
    -- state headline, health, slots, and a stop-loss meter -- so the message
    that arrives on its own looks like the one an operator asks for.

    They are not the same function and should not be merged into one. The
    dashboard runs outside the process and can see things the runner cannot
    (its own liveness, log history, account-wide P&L from `accounting.py`);
    the runner can see things the dashboard would have to pay venue calls
    for. Shared *style*, separate data.

    What survived the redesign, because each cost something to learn:
    ACCOUNTED NET excludes pending rewards; markets resting is a ratio, since
    9 alone does not say what it is 9 of; the idle breakdown always accounts
    for every market; and the ledger's size on disk is shown, because nothing
    else warns before it fills.
    """

    now = summary.observed_at
    uptime = max(0.0, (now - summary.session_started_at).total_seconds())
    trading = _money(summary.trading_realized_usd)
    marked = _money(summary.open_marked_usd)
    paid = _money(summary.attributable_paid_rewards_usd)
    pending = _money(summary.pending_rewards_usd)
    overall = trading + marked + paid
    integrity_ok = not summary.integrity_problems
    idle = max(0, summary.active_markets - summary.resting_markets)

    # State reads from what the bot is doing, not only from readiness. A bot
    # that is "eligible" while holding no orders at all is not working, and
    # the old title said SCALE_ELIGIBLE either way.
    # Idle slots are a fault only if there was something to put in them.
    # `unmet` is the count that actually means "should be quoting and is not";
    # any other idle slot is the universe being quiet, which is the normal
    # evening state and must not read as a problem. Measured 2026-08-18:
    # 3 of 20 slots filled, and all three of the three viable markets were
    # quoted -- 88% of rewarded markets had books tighter than 2c and were
    # refused before a book was ever read.
    # Three different things get called "an idle slot", and only two of them
    # are faults. Keep them apart:
    #
    #   idle   a slot BOUND to a market that is resting no order. The slot is
    #          occupied and earning nothing -- it cannot be given to another
    #          market until it is released. This is the wasted one.
    #   unmet  a market that IS viable and is not being quoted at all. Supply
    #          exists and is going uncollected.
    #   free   capacity with nothing viable to put in it. Not a fault, and on
    #          a quiet evening it is most of the board.
    unmet = max(0, summary.viable_markets - summary.resting_markets)
    free = max(0, summary.slot_capacity - summary.active_markets)
    # Only slots that are actually stuck. A market that has kicked off is
    # idle for a known, harmless reason and is excluded, so this count keeps
    # meaning "look at this".
    waiting = min(idle, max(0, summary.awaiting_rotation_markets))
    cycling = min(idle - waiting, max(0, summary.cycling_markets))
    broken = max(0, idle - waiting - cycling)
    if not integrity_ok or summary.readiness_status == "DO_NOT_START_TRIAL":
        color, icon = RED, "\U0001f6d1"
        state = "NOT WORKING"
    elif broken:
        # Lead with this. It is the one an operator can act on, and the count
        # that answers "are the other slots working?".
        color, icon = ORANGE, "\U0001f7e0"
        state = f"WORKING · {broken} slot(s) STUCK"
    elif unmet and summary.slot_capacity:
        color, icon = ORANGE, "\U0001f7e0"
        state = f"WORKING · {unmet} viable market(s) not quoted"
    elif free and summary.slot_capacity:
        color, icon = GREEN, "\U0001f7e2"
        state = (f"WORKING · all {summary.resting_markets} viable market(s) "
                 f"quoted, {free} slot(s) free")
    else:
        color, icon = GREEN, "\U0001f7e2"
        state = "WORKING"

    warning = ""
    if not integrity_ok:
        problems = "\n".join(
            f"> • {item[:180]}" for item in summary.integrity_problems[:5])
        warning = ("> \U0001f6a8 **INTEGRITY FAILURE — new risk must remain "
                   "halted**\n" + problems + "\n\n")

    if summary.mode == "SHADOW":
        headline = (
            f"## {icon} SHADOW MEASUREMENT — NO EXCHANGE ORDERS\n"
            f"_eligible score-seconds {summary.score_seconds:,.2f} · no venue "
            "trading P&L is claimed_\n")
    else:
        headline = (
            f"## {icon} {state}\n"
            f"**ACCOUNTED NET  ${overall:+,.2f}**   ·   {_signed(overall)}\n"
            f"_{summary.pnl_label} ${trading:+,.2f} · open mark "
            f"${marked:+,.2f} · attributable PAID rewards ${paid:+,.2f} "
            f"· up {uptime / 3600:,.1f}h_\n")
    description = (
        warning + headline
        + (f"> Pending rewards **${pending:,.2f}** are shown but not counted.\n"
           if pending else "")
        + f"`{summary.readiness_status}`")

    entry_label = "Fill signals" if summary.mode == "SHADOW" else "Entry fills"
    position_label = "Replays open" if summary.mode == "SHADOW" else "Positions"

    health_text = (
        _row("Market stream", _age(summary.market_stream_age_seconds))
        + _row("Private stream",
               "N/A shadow" if summary.mode == "SHADOW"
               else _age(summary.private_stream_age_seconds))
        + _row("Unresolved", summary.unresolved_intents)
        + _row("Unknown orders", summary.unknown_orders)
        + _row("REST rate", f"{summary.rest_requests_per_second:.2f}/s")
        + _row("Ledger on disk", _bytes(summary.ledger_bytes)))

    # The ratio, and then why the rest are idle. Show the top three and fold
    # the remainder into `other`, so the numbers always account for every idle
    # market -- truncating silently made them fail to sum to active - resting,
    # on the one field whose job is answering "are we at capacity".
    slots_text = (
        _row("Quoting", f"{summary.resting_markets}/{summary.slot_capacity}"
             if summary.slot_capacity
             else f"{summary.resting_markets}/{summary.active_markets}")
        # Indented so the three read as a partition of the remaining slots,
        # and no sub-count can be mistaken for the alarm. The flat list did
        # exactly that: an operator read "Free, none viable 8" as "8 slots
        # not working" while the stuck count directly above it said 0.
        #
        # `stuck` is the only one of the three that means act. A bound market
        # resting nothing holds a slot no other market can use, so it is the
        # one that costs reward.
        + _row("  stuck, look at it", broken)
        + (_row("  re-quoting", cycling) if cycling else "")
        + (_row("  awaiting rotation", waiting) if waiting else "")
        + "".join(f"      {reason[:15]:<15}{count:>4}\n"
                  for reason, count in summary.idle_market_reasons[:3])
        + ("".join(f"      {'other':<15}"
                   f"{sum(c for _, c in summary.idle_market_reasons[3:]):>4}\n")
           if len(summary.idle_market_reasons) > 3 else "")
        + (_row("  free, no supply", free) if summary.slot_capacity else "")
        + _row("Eligible sides", summary.reward_eligible_sides)
        + _row("Score-seconds", f"{summary.score_seconds:,.2f}"))
    if summary.slot_capacity:
        # The funnel, so "why only three?" is answerable from the message
        # itself rather than needing `scripts/usable_markets.py` on the
        # droplet. Of these, only "lost to errors" means something is wrong;
        # the rest are deliberate economic refusals.
        slots_text += (
            _row("Viable now", summary.viable_markets)
            + _row("Rewarded in window", summary.rewarded_markets)
            + _row("Refused, tight book", summary.spread_filtered_markets)
            + _row("Lost to errors", summary.selection_failures))

    session_cap = _money(summary.session_loss_limit_usd)
    hard_cap = _money(summary.hard_stop_loss_usd)
    session_loss = max(Decimal("0"), -_money(summary.session_realized_pnl_usd))
    worst_loss = max(Decimal("0"), -_money(summary.worst_market_realized_pnl_usd))
    risk_text = (
        _row(position_label, summary.open_positions)
        + _row("Working orders", summary.working_orders)
        + _row(entry_label, summary.entry_fills)
        + _row("Exit samples", summary.exit_samples)
        + _row("Exit complete", f"{100 * summary.exit_completion_rate:.1f}%")
        + _row("Oldest age", f"{summary.max_inventory_age_seconds:.1f}s"))
    if session_cap > 0:
        risk_text += (
            "\nsession  " + _meter(float(session_loss), float(session_cap))
            + f"  ${float(session_loss):,.2f} / ${float(session_cap):,.2f}\n")
        if summary.worst_market_realized_pnl_usd:
            risk_text += (
                "worst    " + _meter(float(worst_loss), float(session_cap))
                + f"  ${float(worst_loss):,.2f}\n")
        if hard_cap > 0:
            risk_text += (
                "HARD     " + _meter(float(session_loss), float(hard_cap))
                + f"  ${float(session_loss):,.2f} / ${float(hard_cap):,.2f}\n")

    reward_text = (_row("PAID attributed", f"{float(paid):,.2f}")
                   + _row("Pending", f"{float(pending):,.2f}"))

    fence = "```text\n"
    fields = [
        {"name": "\U0001f6e0️ Health", "inline": True,
         "value": fence + health_text + "```"},
        {"name": "\U0001f4ca Slots and reward eligibility", "inline": True,
         "value": fence + slots_text + "```"},
        {"name": "⚠️ Position and stop loss", "inline": False,
         "value": fence + risk_text + "```"},
        {"name": "\U0001f381 Rewards", "inline": False,
         "value": fence + reward_text + "```"},
    ]
    return {"embeds": [{
        "title": (f"{icon} Liquidity Bot — {summary.mode.replace('_', ' ')} "
                  f"— {summary.readiness_status}"),
        "description": description,
        "color": color,
        "fields": fields,
        # No footer. It had grown to a single unreadable line of twenty
        # diagnostics -- catalog counts, scan timings, fill-risk drivers --
        # none of which are decisions. They are printed to the log as
        # `[selection]` instead, where they can be read and grepped.
        "timestamp": now.isoformat(),
    }]}


def build_runtime_alert(
    *,
    component: str,
    error: str,
    attempt: int,
    critical: bool,
    market: str | None = None,
    host: str | None = None,
    commit: str | None = None,
    now: datetime | None = None,
) -> dict:
    """One error embed, styled so severity is readable without parsing text.

    Colour and title carry the severity; the body carries the detail. A
    recovering retry is grey and says so, a fatal condition is red and says
    what it wants from the operator. Previously both went as plain text
    beginning "Liquidity Bot", which is why they were indistinguishable.
    """

    moment = now or datetime.now(timezone.utc)
    if critical:
        colour, title, note = (
            RED, "🛑 Needs attention",
            "New risk is disabled until this clears.")
    else:
        colour, title, note = (
            GRAY, "🔁 Recovering on its own", "No action needed. Retrying.")
    fields = [
        {"name": "Component", "value": f"`{component}`"[:1024], "inline": True},
        {"name": "Attempt", "value": str(attempt), "inline": True},
    ]
    if market:
        fields.append({"name": "Market", "value": f"`{market}`"[:1024],
                       "inline": False})
    footer = " · ".join(part for part in (
        "liquidity-bot", host, commit) if part)
    return {"embeds": [{
        "title": title[:256],
        "description": (error + NOTE_SEPARATOR + note + "_")[:4000],
        "color": colour,
        "fields": fields[:25],
        "footer": {"text": footer[:2048]},
        "timestamp": moment.isoformat(),
    }]}
