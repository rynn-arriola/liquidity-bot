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
                ("reward_eligible_sides", self.reward_eligible_sides),
                ("entry_fills", self.entry_fills),
                ("exit_samples", self.exit_samples),
                ("open_positions", self.open_positions),
                ("working_orders", self.working_orders),
                ("unresolved_intents", self.unresolved_intents),
                ("unknown_orders", self.unknown_orders),
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
                self.session_loss_limit_usd, self.hard_stop_loss_usd):
            _money(value)


def build_liquidity_summary(summary: LiquiditySummary) -> dict[str, Any]:
    """Build a Discord embed without claiming pending rewards as income."""

    now = summary.observed_at
    uptime = max(0.0, (now - summary.session_started_at).total_seconds())
    trading = _money(summary.trading_realized_usd)
    marked = _money(summary.open_marked_usd)
    paid = _money(summary.attributable_paid_rewards_usd)
    pending = _money(summary.pending_rewards_usd)
    overall = trading + marked + paid
    integrity_ok = not summary.integrity_problems
    ready = summary.readiness_status in {
        "TRIAL_ELIGIBLE_NOT_SCALE", "SCALE_ELIGIBLE"}
    if not integrity_ok or summary.readiness_status == "DO_NOT_START_TRIAL":
        color, icon = RED, "🛑"
    elif ready:
        color, icon = GREEN, "🟢"
    else:
        color, icon = ORANGE, "🟠"

    warning = ""
    if not integrity_ok:
        problems = "\n".join(
            f"> • {item[:180]}" for item in summary.integrity_problems[:5])
        warning = "> 🚨 **INTEGRITY FAILURE — new risk must remain halted**\n" + problems + "\n\n"
    if summary.mode == "SHADOW":
        headline = (
            "## SHADOW MEASUREMENT — NO EXCHANGE ORDERS\n"
            f"_eligible score-seconds {summary.score_seconds:,.2f} · no venue "
            "trading P&L is claimed_\n")
    else:
        headline = (
            f"## ACCOUNTED NET  ${overall:+,.2f}\n"
            f"_{summary.pnl_label} ${trading:+,.2f} · open mark ${marked:+,.2f} · "
            f"attributable PAID rewards ${paid:+,.2f}_\n")
    description = (
        warning + headline
        + (f"> Pending rewards **${pending:,.2f}** are shown but not counted.\n"
           if pending else "")
        + f"`{summary.readiness_status}`")
    entry_label = "Fill signals" if summary.mode == "SHADOW" else "Entry fills"
    position_label = "Replays open" if summary.mode == "SHADOW" else "Positions open"
    fields = [
        {
            "name": "🎁 Liquidity rewards",
            "value": (
                "```text\n"
                f"Markets active   {summary.active_markets:>10}\n"
                f"Eligible sides   {summary.reward_eligible_sides:>10}\n"
                f"Score-seconds    {summary.score_seconds:>10,.2f}\n"
                f"PAID attributed  {float(paid):>10,.2f}\n"
                "```"),
            "inline": True,
        },
        {
            "name": "📦 Inventory and exits",
            "value": (
                "```text\n"
                f"{entry_label:<16}{summary.entry_fills:>10}\n"
                f"Exit samples     {summary.exit_samples:>10}\n"
                f"Exit complete    {100 * summary.exit_completion_rate:>9.1f}%\n"
                f"{position_label:<16}{summary.open_positions:>10}\n"
                f"Oldest age       {summary.max_inventory_age_seconds:>9.1f}s\n"
                "```"),
            "inline": True,
        },
        {
            "name": "🛡️ Risk and exchange truth",
            "value": (
                "```text\n"
                f"Working orders   {summary.working_orders:>10}\n"
                f"Unresolved       {summary.unresolved_intents:>10}\n"
                f"Unknown orders   {summary.unknown_orders:>10}\n"
                f"Session cap      {float(_money(summary.session_loss_limit_usd)):>10.2f}\n"
                + (f"HARD STOP        {float(_money(summary.hard_stop_loss_usd)):>10.2f}\n"
                   if _money(summary.hard_stop_loss_usd) > 0 else "")
                + "```"),
            "inline": False,
        },
        {
            "name": "🏥 Still alive?",
            "value": (
                "```text\n"
                f"Uptime           {uptime / 3600:>9.2f}h\n"
                f"REST rate        {summary.rest_requests_per_second:>8.2f}/s\n"
                f"Market stream    {_age(summary.market_stream_age_seconds):>10}\n"
                f"Private stream   {_age(summary.private_stream_age_seconds):>10}\n"
                f"Ledger on disk   {_bytes(summary.ledger_bytes):>10}\n"
                "```"),
            "inline": False,
        },
    ]
    if summary.mode == "SHADOW":
        fields[-1]["value"] = fields[-1]["value"].replace(
            f"Private stream   {_age(summary.private_stream_age_seconds):>10}",
            f"Private stream   {'N/A shadow':>10}")
    return {"embeds": [{
        "title": (f"{icon} Liquidity Bot — {summary.mode.replace('_', ' ')} — "
                  f"{summary.readiness_status}"),
        "description": description,
        "color": color,
        "fields": fields,
        "footer": {"text": (
            "Pending/unattributable rewards excluded • exact private state required")},
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
