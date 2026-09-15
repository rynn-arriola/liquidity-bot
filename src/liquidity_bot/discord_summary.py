"""One-glance Discord summary designed for reward-aware liquidity provision."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from .capital_guidance import calculate_capital_guidance
from typing import Any


GREEN = 0x2ECC71
RED = 0xE74C3C
ORANGE = 0xE67E22
# Deliberately distinct at a glance. The complaint this answers is not that
# alerts were missing, it is that a recovering retry and a fatal condition
# looked identical -- both plain text, both opening "Liquidity Bot".
GRAY = 0x95A5A6


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
    resting_side_hours_today: float = 0.0
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
    hard_stop_loss_usd: Decimal = Decimal("0")
    # Account-wide marked TRADING P&L against the ET opening equity, after
    # proven rewards/deposits/withdrawals are removed. It includes the current
    # cashValue of every open venue position; Retail assetNotional is not used.
    today_pnl_usd: Decimal | None = None
    # Optional decomposition of the marked total. It is available only when
    # the day began flat and every current open position reports cost.
    today_realized_pnl_usd: Decimal | None = None
    today_unrealized_pnl_usd: Decimal | None = None
    today_pnl_as_of: datetime | None = None
    # The hard stop's own adjusted equity delta. Negative is a loss. Proven
    # non-trading cash is removed before the guard observes equity, so this can
    # intentionally differ from `today_pnl_usd`. None preserves compatibility
    # for offline/legacy callers that do not run the live equity guard. Window
    # mode instead uses confirmed closing results and None stays unavailable.
    hard_stop_pnl_usd: Decimal | None = None
    hard_stop_window: str | None = None
    pending_entry_cap: int | None = None
    pending_entry_count: int | None = None
    hard_stop_state: str | None = None
    # Lifetime result for THIS bot: ledger trading, which is bot-scoped by
    # construction because the ledger holds only this bot's fills. Settled
    # positions are invisible to it, so it understates losses -- see FINDINGS.
    overall_pnl_usd: Decimal = Decimal("0")
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
    # Read-only operating-capital guidance. These are existing runner values,
    # not additional venue reads. A zero quote size is the compatibility
    # sentinel for shadow and older callers, which omit the capital card.
    quote_size: Decimal = Decimal("0")
    portfolio_exposure_cap_usd: Decimal = Decimal("0")
    weekly_reserve_usd: Decimal = Decimal("0")
    settled_cash_usd: Decimal | None = None
    buying_power_usd: Decimal | None = None
    capital_state_proven: bool = False
    capital_unavailable_reason: str | None = None
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
        if min(self.score_seconds, self.resting_side_hours_today,
               self.max_inventory_age_seconds,
               self.rest_requests_per_second) < 0:
            raise ValueError("summary measurements cannot be negative")
        for value in (
                self.trading_realized_usd, self.open_marked_usd,
                self.attributable_paid_rewards_usd, self.pending_rewards_usd,
                self.hard_stop_loss_usd,
                self.quote_size, self.portfolio_exposure_cap_usd,
                self.weekly_reserve_usd):
            _money(value)
        for value in (
                self.today_realized_pnl_usd,
                self.today_unrealized_pnl_usd):
            if value is not None:
                _money(value)
        if ((self.today_realized_pnl_usd is None)
                != (self.today_unrealized_pnl_usd is None)):
            raise ValueError(
                "today realized and unrealized P&L must be supplied together")
        if (self.today_realized_pnl_usd is not None
                and (_money(self.today_realized_pnl_usd)
                     + _money(self.today_unrealized_pnl_usd)
                     != _money(self.today_pnl_usd))):
            raise ValueError("today P&L breakdown does not reconcile")
        if self.today_pnl_as_of is not None and (
                self.today_pnl_as_of.tzinfo is None
                or self.today_pnl_as_of.utcoffset() is None):
            raise ValueError("today_pnl_as_of must be timezone-aware")


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

    Keep actual account trading, pending/paid separation, market ratios and
    blockers, the guard's own HARD input, and proven withdrawal guidance.
    Methodology and incomplete lifetime estimates stay out of this routine
    card; detailed diagnostics remain in the logs and audit reports.
    """

    now = summary.observed_at
    uptime = max(0.0, (now - summary.session_started_at).total_seconds())
    paid = _money(summary.attributable_paid_rewards_usd)
    pending = _money(summary.pending_rewards_usd)
    # The headline is the account-wide trading mark: closed trading plus the
    # current open-position mark, with proven non-trading cash removed.
    trading_today = (_money(summary.today_pnl_usd)
                     if summary.today_pnl_usd is not None else None)
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
    # Universe supply can exceed the bot's authorized capacity. Only supply
    # that could occupy a slot is unmet capacity; the full universe remains
    # visible in the separate "Viable now" row.
    unmet = (max(
        0,
        min(summary.viable_markets, summary.slot_capacity)
        - summary.resting_markets,
    ) if summary.slot_capacity else max(
        0, summary.viable_markets - summary.resting_markets))
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
            f"**{state}**\n"
            "**SHADOW MEASUREMENT — NO EXCHANGE ORDERS**\n"
            f"Eligible score-hours this run {summary.score_seconds / 3600:,.2f}\n")
    else:
        headline = (
            f"**{state}**\n"
            + (f"**TRADING TODAY  ${trading_today:+,.2f}**\n"
               if summary.today_pnl_usd is not None
               else "**TRADING TODAY  UNAVAILABLE**\n")
            + (f"Closed ${_money(summary.today_realized_pnl_usd):+,.2f} · Open mark ${_money(summary.today_unrealized_pnl_usd):+,.2f}\n"
               if summary.today_realized_pnl_usd is not None
               and summary.today_unrealized_pnl_usd is not None
               else "Closed / open breakdown: UNAVAILABLE\n"))
    # The embed's left strip, which is the first thing read on a phone. It
    # carried operational state alone, so "am I making money today" was not
    # answerable at a glance.
    #
    # P&L takes the strip ONLY when the state chose green -- i.e. nothing
    # operational wants attention. A stuck slot or an unquoted viable market
    # keeps its orange, and an integrity failure keeps its red, because those
    # cost reward and a green strip on a profitable day would hide them. That
    # is the same defect as a stop meter reading safe while the stop is about
    # to fire: the colour must never be more reassuring than the state.
    #
    # So: red = losing today OR broken; orange = look at the slots; green =
    # healthy and up. The state icon in the title disambiguates red.
    if color == GREEN and trading_today is not None:
        color = GREEN if trading_today >= 0 else RED

    description = warning + headline

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
        + _row("  Stuck", broken)
        + (_row("  re-quoting", cycling) if cycling else "")
        + (_row("  awaiting rotation", waiting) if waiting else "")
        + "".join(f"      {reason[:15]:<15}{count:>4}\n"
                  for reason, count in summary.idle_market_reasons[:3])
        + ("".join(f"      {'other':<15}"
                   f"{sum(c for _, c in summary.idle_market_reasons[3:]):>4}\n")
           if len(summary.idle_market_reasons) > 3 else "")
        + (_row("  free, no supply", free) if summary.slot_capacity else "")
        + _row("Eligible sides", summary.reward_eligible_sides)
        + _row("Resting side-hours", f"{summary.resting_side_hours_today:,.2f}"))
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

    hard_cap = _money(summary.hard_stop_loss_usd)
    # The HARD meter must read exactly what its own stop fires on. Live callers
    # provide the adjusted equity delta; the fallback keeps historical/offline
    # summaries compatible without changing their display.
    hard_stop_pnl = (
        summary.today_pnl_usd
        if summary.hard_stop_pnl_usd is None and summary.hard_stop_window is None
        else summary.hard_stop_pnl_usd)
    hard_loss = (max(Decimal("0"), -_money(hard_stop_pnl))
                 if hard_stop_pnl is not None else None)
    risk_text = (
        _row(position_label, summary.open_positions)
        + _row("Working orders", summary.working_orders)
        + _row("Oldest age", f"{summary.max_inventory_age_seconds:.1f}s"))
    if hard_cap > 0 and hard_loss is None:
        risk_text += "\nHARD     UNAVAILABLE\n"
    elif hard_cap > 0:
        risk_text += (
            "\nHARD     " + _meter(float(hard_loss), float(hard_cap))
            + f"  ${float(hard_loss):,.2f} / ${float(hard_cap):,.2f}\n")
    if summary.hard_stop_window is not None:
        risk_text += _row("Risk window", summary.hard_stop_window)
        remaining = (max(Decimal("0"), hard_cap - hard_loss)
                     if hard_loss is not None else None)
        risk_text += _row("Budget left", (
            f"${remaining:,.2f} ({min(Decimal('100'), remaining / hard_cap * 100):.0f}%)"
            if remaining is not None and hard_cap > 0 else "unavailable"))
    if summary.pending_entry_cap is not None:
        count = "unknown" if summary.pending_entry_count is None else str(summary.pending_entry_count)
        risk_text += _row("Pending entries", f"{count} / {summary.pending_entry_cap}")
    if summary.hard_stop_state is not None:
        risk_text += _row("Risk status", summary.hard_stop_state)

    reward_text = (_row("PAID attributed", f"{float(paid):,.2f}")
                   + _row("Pending", f"{float(pending):,.2f}"))

    capital_text = ""
    if summary.quote_size > 0 and summary.slot_capacity > 0:
        guidance = calculate_capital_guidance(
            quote_size=summary.quote_size,
            slot_capacity=summary.slot_capacity,
            portfolio_exposure_cap_usd=summary.portfolio_exposure_cap_usd,
            weekly_reserve_usd=summary.weekly_reserve_usd,
            settled_cash_usd=summary.settled_cash_usd,
            buying_power_usd=summary.buying_power_usd,
            state_proven=summary.capital_state_proven,
            unavailable_reason=summary.capital_unavailable_reason,
        )
        capital_text = (
            _row("Settled cash",
                   f"${guidance.settled_cash_usd:,.2f}"
                   if guidance.settled_cash_usd is not None else "UNKNOWN")
            + _row("Buying power",
                   f"${guidance.buying_power_usd:,.2f}"
                   if guidance.buying_power_usd is not None else "UNKNOWN")
            + _row("Retained target", f"${guidance.comfortable_balance_usd:,.2f}")
            + _row("Free to withdraw",
                   f"${guidance.free_to_withdraw_usd:,.2f}"
                   if guidance.free_to_withdraw_usd is not None
                   else "UNAVAILABLE"))
        if guidance.unavailable_reason:
            reason = guidance.unavailable_reason
            if reason.startswith("order proof pending"):
                reason = "Order confirmation pending"
            capital_text += f"  {reason[:45]}\n"

    fence = "```text\n"
    fields = [
        {"name": "Position and stop loss", "inline": False,
         "value": fence + risk_text + "```"},
        {"name": "Slots", "inline": False,
         "value": fence + slots_text + "```"},
        {"name": "Rewards · lifetime", "inline": False,
         "value": fence + reward_text + "```"},
    ]
    if capital_text:
        fields.append({
            "name": "Capital guidance", "inline": False,
            "value": fence + capital_text + "```",
        })
    fields.append({"name": "Health", "inline": False,
                   "value": fence + health_text + "```"})
    return {"embeds": [{
        "title": f"Liquidity Bot · {summary.mode.replace('_', ' ')}",
        "description": description,
        "color": color,
        "fields": fields,
        "footer": {"text": (
            f"Uptime {uptime / 3600:,.1f}h" if summary.mode == "SHADOW" else
            "Account trading · ET day · "
            f"Mark age {_age((now - summary.today_pnl_as_of).total_seconds()) if summary.today_pnl_as_of is not None else 'UNKNOWN'} · "
            f"Uptime {uptime / 3600:,.1f}h")},
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
        colour, title = RED, "Liquidity Bot · Action required"
    else:
        colour, title = GRAY, "Liquidity Bot · Retrying"
    fields = [
        {"name": "Component", "value": f"`{component}`"[:1024], "inline": True},
        {"name": "Attempt", "value": str(attempt), "inline": True},
    ]
    if market:
        fields.append({"name": "Market", "value": f"`{market}`"[:1024],
                       "inline": False})
    return {"embeds": [{
        "title": title[:256],
        "description": error[:4000],
        "color": colour,
        "fields": fields[:25],
        "timestamp": moment.isoformat(),
    }]}
