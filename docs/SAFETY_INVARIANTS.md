# Safety invariants

These are implementation requirements, not suggestions.

1. Live execution is off by default.
2. No live startup is allowed without a positive, shadow-validated maximum inventory age.
3. Every building order must fit both per-market and portfolio unmatched-loss budgets.
4. After the first fill, all risk-building orders for that market are cancelled.
5. After a fill, cancel every building order and work the exact reducing quote
   at the active touch, with **no cost-basis floor**. A floor only fills when
   the market comes back, so it exits winners and strands losers until they
   settle: maker-lab ran one and reached settlement 79% losers by construction,
   for -$88.20 in one session. Emergency loss realization stays reserved for
   the bounded hard-timeout path, not the passive reducer.
6. If the position remains at its hard inventory-age limit, cancel and confirm the passive exit before sending a price-capped IOC. It must never drift to settlement by default.
7. A reward order is allowed only for the lifecycle phase applying at that instant.
8. Each side is independently eligible only when full-book cumulative depth reaches `targetSize` and the order is inside the resulting cutoff.
9. Published pools and pending earnings are never treated as cash or guaranteed revenue.
10. Unknown order, fill, position, book, or incentive state fails closed for new risk.
11. The private WebSocket is the primary live state feed; REST snapshots reconcile it after startup and reconnect.
12. State-changing requests require a durable pre-submit intent and idempotent fill booking.
13. Commissioning is pregame only and uses one exclusively held API key with
    maker-lab stopped. Size and breadth are **5 contracts per quote by default
    across at most 20 concurrently bound markets**. Quote size is a whole,
    restart-scoped value bounded to 5-10; it returned 10 -> 5 on 2026-08-24.
    Breadth and the six-hour selection horizon did not change. Early-exit loss
    (`0.8 × size`), per-market exposure (`9 × size`) and the session execution-
    fee cap (`0.30 × size`) are computed properties, not independent knobs, so
    they cannot drift from quantity. The account loss stops deliberately do
    not move -- they bound the account, not the position.

    The old "eight is the measured WebSocket ceiling" reasoning is retained in
    invariant 57 but was **disproved** -- 29 subscriptions were later sustained
    (FINDINGS section 9). Eight was a real measurement of one connection's
    cap, not of the account's.
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
25. Losses are stopped at a single **$40** threshold -- `max_market_loss_usd`
    and `max_session_loss_usd` both sit there so exactly one can fire. The
    **$45** hard stop exists only because the emergency ceiling must sit above
    the normal stop, and is measured on marked account equity
    (`currentBalance` plus `assetNotional`). Buying power is never treated as
    P&L or marked equity. (Corrected 2026-08-16: this read $30/$35, which had
    not been true for some time. Verified against `CommissioningConfig`, which
    validates both at startup and refuses to arm if changed.)
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
35. A session stops maker quoting after 20 confirmed taker exits or $0.25 of
    session execution fees, whichever comes first, then cancels, reconciles and
    proves flat. The budget exists to collect exit evidence; it is a ceiling,
    not a target.
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
41. A building quote keeps at least 2 cents between our bid and our offer. A
    book already leaving that much is quoted at the touch, unquoted; a tighter
    book is priced back symmetrically until exactly 2 cents remain. Overlapping
    sides are never quoted. Runtime economics and the planner call one function
    for this, so the projection scores the price that is actually placed.
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
57. **Superseded 2026-08-16 — kept because it was used as grounds not to
    investigate.** The live limits are 20 markets and currently 5 contracts per quote
    (invariant 13). The per-connection reasoning below is still correct; what
    was wrong is treating one connection's cap as the account's. Connections
    are independent and each carries its own quota, and 29 subscriptions were
    later sustained (FINDINGS section 9).

    ~~Commissioning quotes at most eight markets at once and one contract per
    quote.~~ Eight is measured, not chosen: the private WebSocket accepts 10
    subscriptions per connection, and positions must be scoped one market per
    subscription because `positionSubscription` omits `marketSlug`. Orders ride
    a single subscription carrying every bound slug, because order events
    always include `marketSlug` and never need scope to be attributed, so the
    budget is 1 orders + 8 positions + 1 balance. Splitting orders per market
    too would need 17 and the venue drops the overflow *asynchronously*, so
    the stream must refuse to start above 10 rather than run half-blind.
    Public market data needs one subscription for any number of markets.
58. The authorized scope is the active set, never "any market". An owned order
    in a second selected market is legitimate; a `venue:` prefixed row or any
    market outside the set remains a terminal ownership violation, including
    inside the set. Widening this further would stop the bot noticing that
    something else is trading the shared account.
59. Every market carries its own REST cadence and its own book freshness. The
    decision clock is re-read per market, because issuing eight mutations
    through a 4 requests/second governor takes real time and a market whose
    book aged meanwhile must report `book_stale` and be skipped rather than
    quote on data that is no longer fresh.
60. Rotation replaces slots, not the whole set. A market that still qualifies
    keeps its state and its accrual; only markets that stopped qualifying are
    dropped, and only while flat and order-free. Cancelling seven earning
    markets to replace one would violate invariant 51 seven times over.
61. Shutdown and quarantine retire every market that could hold risk, not just
    the active set. A slug dropped by rotation can still own a working order,
    a nonzero position or an unresolved intent, and flat is only proven when
    the whole account is flat.
62. A condition that describes one market's inventory or exit retires that
    market; it never stops the session. A retired market keeps its streams,
    book refresh and reconciler so its exit is still worked, and the
    pre-kickoff flatten still applies, but no side of it may be quoted while
    the retirement remains in force. An `unresolved_mutation_timeout`
    retirement is purely an ambiguity container: it releases its quote bar
    only after positive terminal proof, no unresolved intent, no working order
    and a flat position. Book, plan, action and exit retirements retain their
    cooldown/abandonment behavior. Ending the session for a per-market
    condition exits 4, which the supervisor never restarts, so one thin book
    took down seven healthy markets and left the process dead until an
    operator noticed.
63. Retirement is bounded in three directions. Retirement is opt-in by name,
    so an unrecognised critical still fails closed to terminal. A market that
    fails ten rounds is abandoned and never planned again --
    shutdown makes the final attempt -- because retirement resets the exit
    budget and would otherwise replan forever, including after kickoff. More
    distinct retirements than `max_markets` is systemic, not unlucky, and stops
    the session.
64. Stream visibility and order ambiguity have different scope. A private
    reconnect may resume unaffected markets only after complete REST order and
    position snapshots plus fresh private state. An unresolved intent,
    `UNKNOWN_ABSENT` row, or failed order read positively owned by one market
    keeps that market's execution gate closed until terminal proof; it does not
    make unrelated owners unknown or require their individual reads on a
    scoped/freshness pass. Background terminal repair rotates bounded batches
    of five attributable owners; their GET-by-ID reads may overlap, but every
    request remains governed by the shared 4 req/s limiter, so one NotFound
    retry ladder cannot serialize the other owners. Complete account snapshots
    remain mandatory on their normal cadence. Absence is never cancellation.
    Missing,
    conflicting, unowned, or out-of-scope ownership remains account-wide
    terminal. A bounded reconnect failure never proves flat: only shutdown's
    independent whole-account proof may authorize restartable exit `5`.
65. A CANCEL-confirmed owner may be prioritized ahead of the full market slate
    only after a terminal order state resolves its intent. Priority changes
    scheduling, never authorization: ownership, unresolved-intent, private/REST
    execution freshness, equity freshness, book freshness, economics, loss and
    exit gates remain identical to a normal decision. If priority service
    yields for REST, every ready private and market event is applied before the
    decision and the derived market economics are refreshed against that
    drained state. Rebind discards owners no longer active, and duplicate
    terminal notices cannot create duplicate priority work.
66. Prospective quote observation is reporting, never authorization. It may
    describe only a non-reducing post-only PLACE and must be written in the
    same durable transaction as that intent, keyed by its exact client order
    ID before venue mutation. Reducers, IOC exits and cancels cannot carry an
    entry observation. A missing or malformed observation is counted as a
    coverage gap and must not veto, delay, reorder or cancel an otherwise valid
    action. No observation field, including league, may feed selection,
    reconciliation, scheduler priority, slot capacity or risk policy until a
    separately reviewed rule passes a prospective held-out cohort and first
    runs in would-refuse shadow mode.
67. Reward attribution and risk authority use explicitly different calendars.
    The venue earnings `date` is midnight-to-midnight `America/New_York`, so a
    daily trading/reward join must bucket ledger instants into that ET day and
    must honor DST. Reward totals are timezone-independent. Session and hard
    stops remain baselined on the durable UTC day: changing their reset instant
    can grant a fresh loss budget and is a live-risk policy change, never an
    incidental consequence of correcting a report.
