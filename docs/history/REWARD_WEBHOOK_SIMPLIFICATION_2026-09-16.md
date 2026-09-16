# Reward webhook simplification — September 16, 2026 ET

## Operator decision and problem

The Discord reward webhook should report the actual settled closed-trade result,
not a second account-marked column or a mostly pending finalized-net column. The
old six-column table was 64 characters wide and wrapped into two physical lines
on Discord mobile, making dates and values appear disconnected.

## Reviewed behavior

The Discord embed now shows:

- one `Trading` value sourced only from the existing chronological
  `settled_trading_by_day` replay;
- venue-reported paid and pending liquidity rewards;
- the existing resting-hours and average-sides coverage in a shorter table.

Account-marked P&L, interval-estimate symbols and finalized net are removed only
from the Discord reward embed. Their calculation and dashboard/CLI consumers are
unchanged. The webhook still reads the same ledger and venue inputs, adds no API
request, and cannot place, cancel or reprice an order. Trading, risk, capacity,
selection and the running process are outside this change.

Settled trading includes each non-reversed recorded fill, matches opposite
inventory chronologically, charges observed fees on their event day and excludes
unmatched open inventory. Missing values remain `--`; zero remains numeric.

## Value audit and layout proof

A read-only replay of the live ledger produced the same seven daily values shown
by the prior report. The unusual September 12 `+$191.03` is supported by recorded
opposite fills: one large imported/manual round trip realized `+$230.4549` after
fees, with other trading reducing the day's total. It is not a deposit, reward or
column-alignment error.

The daily row is now 42 characters instead of 64. A production-data preview fits
each date, Trading, Paid and Pending value on one line. The embed description is
two lines: total settled Trading P&L, then paid/pending reward totals. The footer
only defines Trading and the current-day marker.

## Verification and release state

The focused reward/layout suite passes all 42 tests. A deliberate reversal that
fed account-marked values into the Trading column failed the settled-source
regression, and the restored implementation passed. The full production virtual
environment passes all 1,740 tests in 208.415 seconds. Empty reward data, missing
settled data, zero/positive/negative values, current-day partial rows, seven-day
capacity, Discord field limits and dashboard isolation are covered.

Deployment is report-only and must not restart the trader. Final commit, WSL
deployment hash and scheduled/manual post evidence are recorded after release.
