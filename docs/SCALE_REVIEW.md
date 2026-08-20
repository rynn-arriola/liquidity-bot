# Final scale review — 2026-08-13 ET

## Verdict

**Do not restart maker-lab and do not scale.** The evidence gate remains red.
The operator has separately accepted the economic risk of one non-scalable,
one-contract commissioning session to collect live reward/fill/exit evidence.

## Why the prior strategy lost

The maker-lab evidence says the 2026-08-12 session lost $147.94 and attributes
$132.56 of the $148.18 ledger loss (89%) to unpaired positions reaching
settlement. Short-horizon markouts were positive; the central failure was
unfinished two-sided trades, amplified by larger quote size, more markets, no
session loss breaker, and a reverted exit setting. A separate $40.09 balance
versus ledger gap remains unresolved, so its reported P&L is not reliable
enough to justify sizing.

The clean design directly addresses that mechanism: cancel building risk on a
first fill, work a reducing quote at the active touch even at a bounded loss,
then cancel-confirm and use a stressed-depth, price-capped IOC at a measured
hard timeout. Unknown state produces a critical halt rather than silently
letting inventory ride to settlement.

## What is proven

- Reward lifecycle, target cutoff, liquidity-program filtering, and
  `discountFactor ^ ticks * size` scoring are deterministic and tested.
- Current rewarded pregame markets can be discovered and observed read-only.
- A full catalog/reward join found only 97 rewarded six-hour moneylines; 83 of
  91 applicable pregame moneyline books were below the 2-cent entry threshold,
  and none of the 91 passed the conservative reward-versus-exit model.
- A prior 45-second read-only run saw 57 full books, 114 candidate
  observations, 548.7018 hypothetical score-seconds, no reconnects, and zero
  orders.
- Durable intent/order/fill/position state handles crashes after remote create,
  ambiguous timeouts, duplicate fills, partial fills, stale snapshots,
  reconnects, and cancel/fill races without float inventory arithmetic or
  duplicate automatic create attempts.
- Four 4-hour logical dry runs resolved 48/48 fills at each candidate timeout
  (15/30/60/120 seconds) with no healthy-path criticals.
- All REST attempts use one shared 4 requests/second governor per process;
  continuous books/trades use WebSockets. A trial requires exclusive ownership
  of the one API key with maker-lab stopped.
- The market-scoped kill-switch behavior is unit-tested and never calls
  account-wide cancel-all. Its required supervised exchange test is not done.

## What is not proven

- This bot has no attributable fill-hazard sample yet. Its temporary
  first-fill prior comes from 301 maker-lab order lifetimes in the 86 markets
  paid on 2026-08-10 (114 first fills over 372,259.527 exposure-seconds), not
  from liquidity-bot executions.
- No timeout has been selected from evidence.
- The existing $32.06 paid reward belongs to prior account activity and is not
  attributable to this strategy. Pending and skipped rewards do not count.
- There are not three complete shadow days or 100 selected-horizon exits.
- Exclusive key ownership and manual supervision have not been recorded, and
  the kill switch has not been tested against the exchange under supervision.
- There are no controlled live sessions, independent attributable paid events,
  or cent-accurate live P&L reconciliations for this bot.
- The 2-cent entry and 1.5-cent pull thresholds are supported by maker-lab's
  historical spread buckets, but have not yet produced a liquidity-bot live
  fill/exit/reward sample.
- The one-contract runner and detached supervisor are mechanically tested, but
  have not yet produced controlled live evidence at the time of this review.

## Current trial blockers

- Three complete shadow collection days.
- At least 100 exit samples for one selected timeout.
- At least 99% stressed completion and loss-budget pass rates.
- Exclusive use of the existing API key with maker-lab stopped for the entire
  session.
- A controlled exchange kill-switch result from the one-contract commissioning
  session.
- A final whole-account flat/open-order snapshot and fresh private stream at
  startup. These are runtime gates, not assumptions.

## Scale rule

The operator-authorized commissioning exception is, **as configured and
running** (corrected 2026-08-16; this read one market, one contract, a
30-second timeout and $2/$5/$15 caps): up to 20 concurrently bound markets, 10
contracts per quote, a 900-second inventory timeout with a 300-second
pre-kickoff flatten, a $40 per-market soft cap, a $40 session soft cap, a $45
emergency hard stop, and graceful operator stop. Do
not add a market or contract until the scale gate sees three attributable
paid reward days, 20 independent paid events, five clean sessions, positive
conservative net after actual exit costs, and a ledger/exchange difference no
larger than $0.01. Change only one dimension at a time.
