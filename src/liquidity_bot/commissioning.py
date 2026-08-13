"""Hard-locked authorization and 06:00 New York cutoff for first live tests.

This module does not connect to Polymarket. It turns the operator's explicit
risk acceptance into a narrow, reproducible commissioning configuration. The
actual runner must still pass exchange-state preflight before any mutation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


NEW_YORK = "America/New_York"


class CommissioningRefused(RuntimeError):
    """The requested experimental live session is not narrowly authorized."""


def next_six_am_new_york(now: datetime) -> datetime:
    """Return the next 06:00 New York cutoff as an aware datetime."""

    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    try:
        zone = ZoneInfo(NEW_YORK)
    except ZoneInfoNotFoundError as exc:
        raise CommissioningRefused(
            "America/New_York timezone data is unavailable") from exc
    local = now.astimezone(zone)
    cutoff = datetime.combine(local.date(), time(6), tzinfo=zone)
    if cutoff <= local:
        cutoff += timedelta(days=1)
    return cutoff


@dataclass(frozen=True)
class CommissioningConfig:
    """Non-scalable one-market/one-contract experimental authorization."""

    market_slug: str
    economic_risk_accepted: bool
    exclusive_api_key_confirmed: bool
    maker_lab_stopped_confirmed: bool
    arm_live: bool = False
    quote_size: Decimal = Decimal("1")
    max_markets: int = 1
    max_inventory_age_seconds: int = 30
    max_market_loss_usd: Decimal = Decimal("2")
    max_session_loss_usd: Decimal = Decimal("5")
    hard_stop_loss_usd: Decimal = Decimal("15")
    rest_requests_per_second: Decimal = Decimal("4")
    latest_stop_hour_et: int = 6
    max_session_hours: Decimal = Decimal("12")

    def blockers(self, *, now: datetime) -> tuple[str, ...]:
        problems: list[str] = []
        if not self.arm_live:
            problems.append("live arming flag is absent")
        if not self.economic_risk_accepted:
            problems.append("one-contract economic risk is not accepted")
        if not self.exclusive_api_key_confirmed:
            problems.append("exclusive API-key use is not confirmed")
        if not self.maker_lab_stopped_confirmed:
            problems.append("maker-lab is not confirmed stopped")
        if not self.market_slug.strip():
            problems.append("one explicit market slug is required")
        if self.quote_size != 1 or self.max_markets != 1:
            problems.append("commissioning is hard-locked to one market/contract")
        if self.latest_stop_hour_et != 6:
            problems.append("commissioning cutoff must be 06:00 New York")
        if (self.max_session_loss_usd != 5 or self.max_market_loss_usd != 2
                or self.hard_stop_loss_usd != 15):
            problems.append(
                "commissioning loss limits must remain $5 soft, $2 market, $15 hard")
        if self.hard_stop_loss_usd <= self.max_session_loss_usd:
            problems.append("hard stop must exceed the normal session stop")
        if self.rest_requests_per_second > 4:
            problems.append("commissioning REST budget cannot exceed 4 requests/second")
        cutoff = next_six_am_new_york(now)
        duration = Decimal(str((cutoff - now).total_seconds())) / Decimal("3600")
        if duration > self.max_session_hours:
            problems.append(
                "next 06:00 New York cutoff is more than 12 hours away")
        return tuple(problems)

    def assert_authorized(self, *, now: datetime) -> datetime:
        problems = self.blockers(now=now)
        if problems:
            raise CommissioningRefused("; ".join(problems))
        return next_six_am_new_york(now)
