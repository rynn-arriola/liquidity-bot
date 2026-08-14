# Safety invariants

These are implementation requirements, not suggestions.

1. Live execution is off by default.
2. No live startup is allowed without a positive, shadow-validated maximum inventory age.
3. Every building order must fit both per-market and portfolio unmatched-loss budgets.
4. After the first fill, all risk-building orders for that market are cancelled.
5. After a fill, cancel every building order. Work the exact reducing quote at
   the active touch only when that price is no worse than inventory basis;
   otherwise rest at the break-even basis price. Emergency loss realization is
   reserved for the bounded hard-timeout path, not the passive reducer.
6. If the position remains at its hard inventory-age limit, cancel and confirm the passive exit before sending a price-capped IOC. It must never drift to settlement by default.
7. A reward order is allowed only for the lifecycle phase applying at that instant.
8. Each side is independently eligible only when full-book cumulative depth reaches `targetSize` and the order is inside the resulting cutoff.
9. Published pools and pending earnings are never treated as cash or guaranteed revenue.
10. Unknown order, fill, position, book, or incentive state fails closed for new risk.
11. The private WebSocket is the primary live state feed; REST snapshots reconcile it after startup and reconnect.
12. State-changing requests require a durable pre-submit intent and idempotent fill booking.
13. Commissioning is pregame only, uses one exclusively held API key with
    maker-lab stopped, and is hard-locked to one active market at a time and
    one contract per quote. Read-only reward discovery may inspect many
    markets; any later concurrent-exposure path is a separate authorization.
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
24. Commissioning runs until operator stop or a terminal safety/loss condition.
    Recoverable adapter failures keep the process alive with new risk disabled;
    quoting resumes only after cancel/reconnect/reconcile succeeds. Process exit
    may occur later only after it cancels, flattens, and proves exact flat
    state, or through the bounded non-flat exit in invariant 32.
25. The $15 hard stop uses marked account equity (`currentBalance` plus
    `assetNotional`). Buying power is never treated as P&L or marked equity.
26. A restart may mutate only persisted, identified orders/positions in one
    ledger-selected recovery market, and then only to recover flat. Unowned or cross-market
    state refuses without mutation.
27. `SIGTERM` and `SIGINT` are graceful operator-stop requests. They enter
    market-scoped cancel/flatten/reconcile rather than terminate immediately.
28. Only explicitly classified transport/adapter failures and transient stale
    or unknown exchange state may recover in-process. Ownership, market
    identity, exact-position, planning, and unknown future strategy failures
    are terminal for maker quoting. They enter cancel/flatten shutdown rather
    than being retried as if they were transport errors.
29. Hard-exit IOC recovery is bounded to three submissions within 30 seconds,
    with at most 10 seconds to prove each submitted IOC terminal. A ceiling or
    unexitable-inventory condition ends maker quoting and escalates to the
    cancel/flatten path; IOC submissions may not loop without a bound.
30. Repeated recoverable strategy/action failures use exponential backoff capped
    at 30 seconds through the shared 4 requests/second REST governor.
31. Shutdown owns a flatten budget separate from the quoting loop's, also
    bounded to three submissions. An exhausted runtime exit counter must never
    stop the terminal path from attempting a flatten, because refusing to try
    is what leaves unmatched inventory to reach settlement.
32. A shutdown that cannot prove flat repeats its critical alert every 300
    seconds and ends the process non-flat after a 900-second window, reporting
    exit 3 for immediate operator inspection. Until that deadline it keeps
    reconciling and uses any remaining exit attempt when a safe exact-position
    IOC is available; it never holds a position while silent.
33. Venue reward eligibility is necessary but not sufficient for entry. A live
    side must also pass a normalized competing-score projection, a conservative
    per-side pool allocation, a 50% reward haircut, and a 2x
    probability-weighted stressed-exit-cost margin. The venue's $1 aggregate
    settlement threshold is reporting-only, never a per-market gate. Missing
    duration or economic evidence means zero orders, not an override sentinel.
34. Pair-completion spread and maker rebate are zero in the commissioning entry
    forecast until attributable live evidence supports nonzero values. Published
    pool size is never substituted for the bot's proportional share.
35. The first confirmed taker exit in a commissioning session is terminal for
    maker quoting. The process cancels, reconciles, proves flat, and does not
    start another fee-paying cycle; positive execution fees also have a $0.02
    session ceiling.
36. Authenticated incentive earnings for the active market are sampled every 15
    minutes and stored in a table separate from executions. Paid, pending, and
    skipped observations do not alter inventory basis or trading P&L.
37. Automatic discovery bulk-loads the active sports catalog, applies open,
    tick, minimum-quantity, and proven six-hour pregame clock gates in memory,
    then requests incentives only for those symbols. It must never retrieve
    metadata once per reward slug. All reads share the 4 requests/second
    governor; books are capped at 25 per pass and a rotating window eventually
    inspects lower-ranked eligible pools.
38. A market rotation is allowed only after bot quotes are canceled and a
    whole-account REST snapshot proves zero working orders, zero nonzero
    positions, no unresolved intent, and known ownership. Only then may the
    singleton public/private stream subscription be rebound.
39. An unsupported or undocumented incentive lifecycle is observable but
    cannot authorize an order. `daily` is documented but not yet implemented
    by the pregame executor; `tournament_with_cutoff` was observed live but has
    no documented timing/pool-allocation rule. Both remain read-only until
    explicitly modeled and tested.
40. New risk is limited to markets whose normalized `sportsMarketTypeV2` is
    exactly `SPORTS_MARKET_TYPE_MONEYLINE`. A two-side count is never accepted
    as a substitute because futures and props are also two-sided.
41. A flat account may create a building quote only at a fresh full-book spread
    of at least 2 cents. A resting building quote may remain down to 1.5 cents
    and is canceled below that hysteresis boundary. Selection and runtime use
    the same thresholds; stale catalog BBO cannot authorize an order.
42. Spread and structure gates apply only to building risk. Once inventory is
    nonzero, passive reduction and bounded hard exit remain available at every
    spread and market structure so the gate cannot strand a position.
43. Fill risk uses the maximum 95% Wilson upper bound among applicable
    calibration cells, never a product of conditional effects and never a
    lower point estimate. An unknown league falls back to global and all other
    applicable cells.
44. A quote's projected reward is counted only in the no-fill branch. Stressed
    unwind loss and the additional cost-margin copy are counted in the fill
    branch. The selector and live runner must use the same calibrated model and
    horizon.
45. Historical paid reward yield may reorder current candidates but cannot
    authorize entry. Current structure, spread, target depth, reward projection,
    stressed exit depth, and positive expected value remain mandatory.
46. Live score-seconds accrue only for an owned order observed working on a
    side that remains reward-eligible and economically authorized. Candidate
    score from a side with no order, or a side now blocked for cancellation,
    is never reported as attributable resting exposure.
47. Automatic selection returns an execution target only when at least one side
    has positive lower-bound expected value. A zero-viable refresh keeps the
    existing singleton stream scope and cannot rebind to a monitoring-only
    market; a zero-viable startup refuses before order entry.
48. Catalog BBO is advisory. A malformed, locked, or crossed catalog quote is
    normalized to unavailable metadata and cannot reject otherwise valid
    market metadata. Only a fresh full book may authorize or reject entry.
49. Runtime invariant failures raised while computing reward economics retain
    their stable critical code and enter terminal shutdown; they must not be
    collapsed into a generic runner failure.
50. Live score-seconds are credited at the resting order's own price, not the
    touch. Score decays as `discount_factor ** ticks_from_best`, so a drifted
    quote earns a fraction of the touch score and one outside the cutoff earns
    nothing. Where more than one owned order exists on a side, the price
    furthest from the touch is used.
51. Discovery may run only while accrual is already zero: no owned quote
    resting and no economically authorized side in the bound market. A scan
    cancels quotes and stops draining both streams for its duration, and
    reward is a time integral over resting orders, so scanning while quoting
    spends measured score-seconds on an unmeasured chance of a better market.
    The selection interval still bounds how often a scan may run; this only
    defers it to a moment when it costs nothing. A market that stops being
    executable already forces the interval due, so a dead market is replaced
    as soon as its quotes are retired.
52. A zero-viable scan is an idle result, not a discovery failure. If the bound
    market expired during the scan, the runner clears its incentive so no side
    can quote and stays dormant on the normal selection interval. It must not
    escalate into the fast error-retry cadence, which would rescan the whole
    universe every 60 seconds and starve book and account freshness.
53. Staying down is itself a failure mode. An unexpected runtime failure that
    proved the whole account flat exits `5` and is restarted, because a fresh
    process re-runs the entire flat and ownership preflight before any order.
    Loss stops, unowned or cross-market state, unproven flat state, and every
    named critical remain terminal and are never restarted.
54. An empty viable universe is a wait, not a refusal. With no bound market the
    runner holds no orders, no position and no stream subscription, so waiting
    carries no risk. Only an operator stop or a genuine adapter failure ends
    the wait; `SIGTERM`/`SIGINT` handlers are installed before the wait begins.
55. Inventory waits 900 seconds for a free passive exit before paying to
    cross. maker-lab's 112 reconstructed episodes resolved passively for 26%
    of episodes within 30 seconds but 46% by 900 seconds, while crossing costs
    a certain taker commission and their markout stayed positive at 60, 300
    and 900 seconds. One contract risks at most about $0.99 by waiting.
56. Kickoff overrides the inventory clock. A position within 300 seconds of
    the event start is flattened regardless of its age, because maker-lab
    disabled its forced exit on the same holding-is-cheaper evidence and the
    residue reached settlement for -$88.20, 86% of that session's loss.
    Waiting for a free exit is never permission to settle.
