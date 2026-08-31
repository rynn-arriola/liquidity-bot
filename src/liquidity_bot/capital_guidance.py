"""Conservative, read-only operating-capital guidance.

The calculation deliberately does not mutate strategy configuration or call
the venue.  It turns the already-proven balance snapshot and the existing
portfolio exposure budget into an operator-facing withdrawal ceiling.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_DOWN


CENT = Decimal("0.01")


def _decimal(value: Decimal | str | int | float, name: str) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{name} is not decimal") from exc
    if not result.is_finite():
        raise ValueError(f"{name} must be finite")
    return result


@dataclass(frozen=True)
class CapitalGuidance:
    """One conservative capital recommendation for the current configuration."""

    quote_size: Decimal
    slot_capacity: int
    slot_footprint_usd: Decimal
    cushion_usd: Decimal
    weekly_reserve_usd: Decimal
    comfortable_balance_usd: Decimal
    settled_cash_usd: Decimal | None
    buying_power_usd: Decimal | None
    free_to_withdraw_usd: Decimal | None
    unavailable_reason: str | None = None


def calculate_capital_guidance(
    *,
    quote_size: Decimal | str | int | float,
    slot_capacity: int,
    portfolio_exposure_cap_usd: Decimal | str | int | float,
    weekly_reserve_usd: Decimal | str | int | float,
    settled_cash_usd: Decimal | str | int | float | None,
    buying_power_usd: Decimal | str | int | float | None,
    state_proven: bool,
    unavailable_reason: str | None = None,
) -> CapitalGuidance:
    """Return capital to preserve and a conservative withdrawal ceiling.

    ``comfortable_balance_usd`` is never below either the configured
    portfolio exposure cap or one worst-case notional per authorized slot.
    Withdrawable cash is capped twice: settled cash must remain above that
    comfortable balance, and the venue must report the money as buying power
    rather than money reserved by working orders.

    Dynamic account evidence fails closed.  Missing, negative, non-finite or
    unresolved evidence returns no withdrawal amount instead of guessing.
    """

    size = _decimal(quote_size, "quote_size")
    cap = _decimal(portfolio_exposure_cap_usd, "portfolio_exposure_cap_usd")
    weekly = _decimal(weekly_reserve_usd, "weekly_reserve_usd")
    if size <= 0:
        raise ValueError("quote_size must be positive")
    if isinstance(slot_capacity, bool) or not isinstance(slot_capacity, int):
        raise ValueError("slot_capacity must be an integer")
    if slot_capacity <= 0:
        raise ValueError("slot_capacity must be positive")
    if cap <= 0:
        raise ValueError("portfolio_exposure_cap_usd must be positive")
    if weekly < 0:
        raise ValueError("weekly_reserve_usd cannot be negative")

    footprint = size * slot_capacity
    exposure_reserve = max(cap, footprint)
    cushion = exposure_reserve - footprint
    comfortable = exposure_reserve + weekly

    cash: Decimal | None = None
    power: Decimal | None = None
    reason = unavailable_reason.strip() if unavailable_reason else None
    if settled_cash_usd is not None:
        try:
            cash = _decimal(settled_cash_usd, "settled_cash_usd")
        except ValueError:
            reason = reason or "balance evidence is invalid"
    if buying_power_usd is not None:
        try:
            power = _decimal(buying_power_usd, "buying_power_usd")
        except ValueError:
            reason = reason or "balance evidence is invalid"

    if not state_proven:
        reason = reason or "account state is not proven"
    elif cash is None or power is None:
        reason = reason or "balance evidence is unavailable"
    elif cash < 0 or power < 0:
        reason = reason or "balance evidence is invalid"

    free: Decimal | None = None
    if reason is None and cash is not None and power is not None:
        # ROUND_DOWN is toward zero; the value is non-negative here.  Never
        # round a recommendation up by a fraction of a cent.
        free = max(
            Decimal("0"), min(power, cash - comfortable),
        ).quantize(CENT, rounding=ROUND_DOWN)

    return CapitalGuidance(
        quote_size=size,
        slot_capacity=slot_capacity,
        slot_footprint_usd=footprint,
        cushion_usd=cushion,
        weekly_reserve_usd=weekly,
        comfortable_balance_usd=comfortable,
        settled_cash_usd=cash,
        buying_power_usd=power,
        free_to_withdraw_usd=free,
        unavailable_reason=reason,
    )
