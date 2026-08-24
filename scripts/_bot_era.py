"""Where this bot's own history starts, and why every money figure needs it.

The account is older than the bot. maker-lab ran on it first and earned reward
on it, so anything read account-wide -- the venue's earnings endpoint, marked
equity, funding history -- mixes two different systems and flatters or damns
this one for work it did not do.

Measured from the ledger, which holds only this bot's orders:

    first order  2026-08-14T04:21:56Z
    first fill   2026-08-14T11:48:39Z

Reward dated before 2026-08-14 is maker-lab's: **$86.90** of it, against about
$90 of this bot's. Attributing that to the liquidity bot overstates its
performance by roughly a factor of two, which has happened twice in analysis --
once reporting a reward-to-loss ratio of 3.5x that was really about 2x.

Reward dates use Eastern Time. The first bot order at 04:21:56Z was 00:21:56
ET on the same date, so this ownership boundary remains unchanged.

## What can and cannot be scoped

Scopable, because the source carries a date or belongs to the bot alone:

  * venue reward earnings -- filter on the `date` field
  * ledger fills, orders, intents -- the ledger begins with this bot

**Not scopable:** marked equity and funding history are account-level facts
with no per-system attribution. A trading figure derived as
`equity - funding - reward` is therefore ACCOUNT-WIDE however the reward term
is filtered, and must be labelled that way rather than quietly presented as the
bot's. Say which one a number is, every time.
"""

from __future__ import annotations

BOT_ERA_START = "2026-08-14"

BOT_ERA_NOTE = "liquidity-bot only, from 2026-08-14"
ACCOUNT_WIDE_NOTE = "ACCOUNT-WIDE, includes pre-bot maker-lab"


def in_bot_era(date: str) -> bool:
    """True if a venue date string belongs to this bot's history.

    Dates are venue ET `YYYY-MM-DD`; anything shorter or malformed is excluded
    rather than guessed at, because a bad parse here silently credits this
    bot with another system's earnings.
    """

    return len(str(date)) >= 10 and str(date)[:10] >= BOT_ERA_START
