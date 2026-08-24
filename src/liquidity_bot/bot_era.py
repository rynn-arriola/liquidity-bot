"""Where this bot's own history starts, and why every money figure needs it.

The account is older than the bot. maker-lab traded and earned reward on it
first, so anything read account-wide -- the venue's earnings endpoint, marked
equity, funding history -- mixes two systems and flatters or damns this one for
work it did not do.

Measured from the ledger, which holds only this bot's orders:

    first order  2026-08-14T04:21:56Z
    first fill   2026-08-14T11:48:39Z

Verified against the venue 2026-08-20: PAID reward dated before this boundary
is **$86.51** and belongs to maker-lab, against **$54.44** PAID plus $35.73
PENDING for this bot. Counting the earlier figure here roughly doubles the
bot's apparent result, which has happened twice in analysis.

Reward dates use Eastern Time, per the venue's earnings API. The first bot
order at 2026-08-14T04:21:56Z was 00:21:56 ET on the same date, so the bot-era
boundary remains 2026-08-14 and a plain compare against the venue's date field
is correct for this ownership filter.

## What can and cannot be scoped

Scopable, because the source carries a date or belongs to the bot alone:

  * venue reward earnings -- filter on the ``date`` field
  * ledger fills, orders, intents -- the ledger begins with this bot

**Not scopable:** marked equity and funding are account-level facts with no
per-system attribution. Any figure derived as ``equity - funding - reward`` is
ACCOUNT-WIDE however the reward term is filtered, and must be labelled so.

This mirrors ``scripts/_bot_era.py``, which serves the analysis scripts. The
constant is duplicated here because the runner cannot import from ``scripts/``,
and a reward figure on the live summary needs the same boundary the reports do.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Iterable

# The bot's first order falls on this date in ET, matching the venue field.
BOT_ERA_START_DATE = "2026-08-14"


def is_bot_era(date_value: Any) -> bool:
    """True when a venue reward row belongs to this bot rather than maker-lab."""

    return str(date_value)[:10] >= BOT_ERA_START_DATE


def bot_era_rewards_by_status(rows: Iterable[Any]) -> dict[str, Decimal]:
    """Total reward by status for this bot's era only.

    Status is upper-cased because the venue has emitted both cases. A row that
    will not parse is skipped rather than raising: this feeds the summary, and
    anything that exists only to be reported must not be able to take the
    runner down -- BUGFIXES records that failure twice, once where a reporting
    metric nearly stopped shutdown proving flat.
    """

    totals: dict[str, Decimal] = {}
    for row in rows:
        try:
            if not is_bot_era(getattr(row, "date", "")):
                continue
            status = str(getattr(row, "status", "")).upper()
            amount = Decimal(str(getattr(row, "reward", "0")))
        except (TypeError, ValueError, ArithmeticError):
            continue
        totals[status] = totals.get(status, Decimal("0")) + amount
    return totals
