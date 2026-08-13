# Safety invariants

These are implementation requirements, not suggestions.

1. Live execution is off by default.
2. No live startup is allowed without a positive, shadow-validated maximum inventory age.
3. Every building order must fit both per-market and portfolio unmatched-loss budgets.
4. After the first fill, all risk-building orders for that market are cancelled.
5. After a fill, cancel every building order. Work a reducing quote at the active touch even when it realizes a small bounded loss; do not selectively close only winners.
6. If the position remains at its hard inventory-age limit, cancel and confirm the passive exit before sending a price-capped IOC. It must never drift to settlement by default.
7. A reward order is allowed only for the lifecycle phase applying at that instant.
8. Each side is independently eligible only when full-book cumulative depth reaches `targetSize` and the order is inside the resulting cutoff.
9. Published pools and pending earnings are never treated as cash or guaranteed revenue.
10. Unknown order, fill, position, book, or incentive state fails closed for new risk.
11. The private WebSocket is the primary live state feed; REST snapshots reconcile it after startup and reconnect.
12. State-changing requests require a durable pre-submit intent and idempotent fill booking.
13. Commissioning is pregame only, uses one exclusively held API key with
    maker-lab stopped, and is hard-locked to one named market and one contract
    per quote. Any later scale path is a separate authorization.
14. `cancel-all` and account-wide close operations are forbidden on a shared account.
15. A realized-P&L breaker does not replace open-exposure and worst-case-loss limits.
16. Before quoting a side, stressed full-book depth must show the entire proposed size can be closed inside its per-market loss and slippage budgets.
17. A WebSocket reconnect has no replay guarantee: new risk remains disabled
    until REST reconciliation and complete, fresh private snapshots succeed.
18. A create or cancel with an ambiguous response is never retried. Its market
    is quarantined until the venue state proves the outcome.
19. Rounded legacy position fields are not sufficient for risk. Exact
    `netPositionDecimal` state is required.
20. A clean HTTP cancel acknowledgement is not terminal proof. A fill may win
    the race, and the resulting inventory must still be exited.
21. Discord summaries must state SHADOW, DRY RUN, or LIVE in the title. Shadow
    fill signals and hypothetical exits are never labeled as exchange fills or
    venue P&L.
22. Pending, skipped, and unattributable rewards may be displayed as context
    but are excluded from accounted net and profitability gates.
23. Normal summaries use the status webhook; critical process, money, or
    exchange-state failures use the error webhook and must not block the market
    or private WebSocket event loop.
24. Commissioning stops adding risk at 06:00 `America/New_York`; process exit
    may occur later only while it cancels, flattens, and proves exact flat state.
25. The $15 hard stop uses marked account equity (`currentBalance` plus
    `assetNotional`). Buying power is never treated as P&L or marked equity.
26. A restart may mutate only persisted, identified orders/positions in the one
    configured market, and then only to recover flat. Unowned or cross-market
    state refuses without mutation.
