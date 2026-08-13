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
