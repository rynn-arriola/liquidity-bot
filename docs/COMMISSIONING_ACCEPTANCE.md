# Commissioning prelaunch acceptance — 2026-08-13

No mutation or Discord endpoint was called during these checks.

## Deterministic verification

- 158 unit/integration tests passed under Python 3.14 on Windows.
- A fake venue accepted only an exact one-contract, post-only payload.
- Fake 06:00 New York cutoff completed flat with zero new orders.
- A $15 marked-equity drawdown selected the named emergency hard-stop path.
- A persisted owned order restarted in recovery-only mode, was canceled, and
  exited flat without adding risk.
- An unowned exchange order refused startup and made no mutation.

## Bounded real read-only verification

Observed at `2026-08-13T04:53:19Z` through the shared 4 requests/second gate:

- 19,179 sports-market catalog rows normalized;
- 584 pregame markets started after the 06:00 New York cutoff and were inside
  the 36-hour discovery window;
- 37 had a current applicable liquidity program;
- 5 passed one-contract tick/minimum, both-sided reward eligibility, and
  stressed full-depth exit checks;
- account open orders: 0;
- account nonzero positions: 0;
- authenticated marked-balance schema: valid;
- public market WebSocket full book: received;
- private WebSocket account-balance snapshot: received.

Selected prelaunch candidate:
`aec-wnba-atl-conn-2026-08-13`. At the observation it had an active `early`
liquidity period, a $200 published pool, 10,000-contract target, $0.01 tick,
$0.01 minimum quantity, $0.81/$0.82 touch, both one-contract sides eligible,
and approximately -$0.0196/-$0.0185 stressed one-contract exit P&L. The event
clock was `2026-08-13T23:00:00Z`, after the shutdown cutoff.

Pool and eligibility are not guaranteed payout or profitability. The live
runner revalidates market, incentive, book, account, balance, and private-stream
state immediately before its first order.

The first server start attempt at approximately `2026-08-13T05:01Z` refused
before order entry because the market metadata endpoint returned the observed
open value `MARKET_STATUS_OPEN` while validation recognized only the book
endpoint's `MARKET_STATE_OPEN`. The accepted open set was narrowed to exactly
those two observed values and covered by a regression test; no arbitrary status
or substring matching was introduced.

The next bounded attempt placed one exact one-contract post-only order and then
canceled it as eligibility/state changed. The exchange proved zero open orders
and zero positions, but its terminal retrieve response omitted `updateTime` and
reported `lastTransactTime`. The ledger correctly rejected terminal content
reusing the original creation clock and remained in recovery. The operator
process was stopped only after independent REST checks proved canceled, no open
orders, and flat. Normalization now consumes the observed terminal clock, and
startup directly retrieves every persisted exchange ID before snapshots; a
restart with any prior place intent is recovery-only and cannot resume risk.

A subsequent fresh-ledger attempt placed one one-contract quote, reconciled its
order response, and then hit an empty-transition bug: the same loop pass tried
to take the oldest timestamp after REST had cleared the final unresolved
intent. It raised `ValueError`, entered shutdown, canceled the quote, proved
zero open orders/positions, and exited without restart. The loop now treats an
empty post-reconciliation set as success and proceeds, with a regression test
and persistent traceback output for future caught failures.

The next fresh-ledger attempt placed two exact one-contract quotes and exited
flat on `critical:book_stale`. The market WebSocket is change-driven and had no
book change inside the five-second decision limit. A quiet feed now receives a
REST full-book refresh at three seconds through the shared 4 requests/second
governor; a newer WebSocket book suppresses that fallback call.

The next attempt remained mechanically healthy until the private freshness
window expired: after its initial authenticated balance snapshot, no account
state changed, so the change-driven private stream was silent. It canceled two
one-contract quotes and exited flat on `critical:execution_state_unknown`.
While that socket remains connected, the runner now performs a complete REST
order/position reconciliation every ten seconds. REST and private timestamps
remain distinct; either recent proof can maintain known state, and both aging
out still disables risk.

The following attempt remained healthy beyond the private threshold and had
two exact one-contract post-only quotes, but later stopped flat on another book
freshness edge. The decision reused its pre-await clock and stamped REST books
at request start; under live API latency, later account work could exhaust the
five-second budget. Books are now stamped at response receipt, refreshed last,
and evaluated against a newly read clock. Any intent reconciliation starts a
fresh loop before a new quote decision.

That attempt stopped at the one-minute incentive refresh boundary because the
book-due comparison still used the clock captured before the incentive calls.
The runner now rereads time immediately before deciding whether the final book
refresh is due; upstream REST latency therefore cannot hide an aged book.

One more attempt exposed a scheduler edge: a due check could see a WebSocket
book just under three seconds old, skip REST, and then schedule three seconds
from the check time, allowing the five-second limit to arrive first. The next
deadline is now anchored to the book's actual receipt timestamp.
