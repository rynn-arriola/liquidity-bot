"""Pure time slicing and full-book exit counterfactuals."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from .models import BookSnapshot


NEW_YORK = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class ExitResult:
    complete: bool
    requested_qty: float
    closed_qty: float
    remaining_qty: float
    average_price: float | None
    gross_proceeds: float
    entry_cost: float
    taker_fee: float
    pnl: float
    worst_price: float | None


def split_reward_day_interval(start: datetime, end: datetime) -> list[tuple[str, float]]:
    """Split a timezone-aware interval across Polymarket's ET reward days."""

    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("reward intervals must be timezone-aware")
    if end < start:
        raise ValueError("reward interval end precedes start")
    if end == start:
        return []
    out: list[tuple[str, float]] = []
    cursor = start
    while cursor < end:
        local = cursor.astimezone(NEW_YORK)
        next_date = local.date() + timedelta(days=1)
        boundary_local = datetime.combine(next_date, time.min, tzinfo=NEW_YORK)
        boundary = boundary_local.astimezone(cursor.tzinfo)
        piece_end = min(end, boundary)
        out.append((local.date().isoformat(), (piece_end - cursor).total_seconds()))
        cursor = piece_end
    return out


def _exit_levels(
    book: BookSnapshot,
    entry_side: str,
    *,
    exclude_entry_level: bool,
) -> list[tuple[float, float]]:
    if entry_side == "bid":
        levels = sorted(book.levels("bid"), reverse=True)
    elif entry_side == "offer":
        levels = sorted(book.levels("offer"))
    else:
        raise ValueError("entry_side must be 'bid' or 'offer'")
    return levels[1:] if exclude_entry_level and levels else levels


def cross_exit(
    book: BookSnapshot,
    *,
    entry_side: str,
    entry_price: float,
    qty: float,
    depth_haircut: float = 1.0,
    taker_theta: float = 0.06,
    exclude_entry_level: bool = False,
) -> ExitResult:
    """Walk current depth for a counterfactual immediate close.

    ``depth_haircut`` stress-tests displayed liquidity. Candidate-entry checks
    can also exclude the entry touch: if our bid was filled, the queue ahead
    at that bid was necessarily consumed and cannot be counted as an exit.
    """

    entry_side = str(entry_side).lower()
    entry_price, qty = float(entry_price), float(qty)
    depth_haircut, taker_theta = float(depth_haircut), float(taker_theta)
    if entry_side not in {"bid", "offer"}:
        raise ValueError("entry_side must be 'bid' or 'offer'")
    if not 0 < entry_price < 1 or qty <= 0:
        raise ValueError("entry price and quantity are invalid")
    if not 0 <= depth_haircut <= 1 or taker_theta < 0:
        raise ValueError("depth haircut or fee coefficient is invalid")

    filled = notional = 0.0
    worst: float | None = None
    for price, displayed in _exit_levels(
            book, entry_side, exclude_entry_level=exclude_entry_level):
        available = displayed * depth_haircut
        take = min(qty - filled, available)
        if take <= 0:
            continue
        filled += take
        notional += take * price
        worst = price
        if filled >= qty - 1e-9:
            break
    average = notional / filled if filled else None
    if entry_side == "bid":
        proceeds = notional
        entry_cost = filled * entry_price
    else:
        proceeds = filled - notional
        entry_cost = filled * (1.0 - entry_price)
    fee = (taker_theta * filled * average * (1.0 - average)
           if filled and average is not None else 0.0)
    pnl = proceeds - entry_cost - fee
    return ExitResult(
        complete=filled >= qty - 1e-9,
        requested_qty=qty,
        closed_qty=filled,
        remaining_qty=max(0.0, qty - filled),
        average_price=average,
        gross_proceeds=proceeds,
        entry_cost=entry_cost,
        taker_fee=fee,
        pnl=pnl,
        worst_price=worst,
    )


def passive_close_pnl(entry_side: str, entry_price: float,
                      exit_price: float, qty: float) -> float:
    if entry_side == "bid":
        return qty * (exit_price - entry_price)
    if entry_side == "offer":
        return qty * (entry_price - exit_price)
    raise ValueError("entry_side must be 'bid' or 'offer'")
