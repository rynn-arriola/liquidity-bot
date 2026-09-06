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
6. If the position remains at its hard inventory-age limit, cancel and confirm
   every working order before sending a price-capped IOC. Hard exit plans and
   applies one cancel at a time, building side first; confirmation grace starts
   only after submission and belongs to that exact order. The next cancel may
   be selected only after positive terminal proof. It must never drift to
   settlement by default.
7. A reward order is allowed only for the lifecycle phase applying at that instant.
8. Each side is independently eligible only when full-book cumulative depth reaches `targetSize` and the order is inside the resulting cutoff.
9. Published pools and pending earnings are never treated as cash or guaranteed revenue.
10. Unknown order, fill, position, book, or incentive state fails closed for new risk.
11. The private WebSocket is the primary live state feed; REST snapshots reconcile it after startup and reconnect.
12. State-changing requests require a durable pre-submit intent and idempotent fill booking.
13. Commissioning is pregame only and uses one exclusively held API key with
    maker-lab stopped. Size and breadth are restart-scoped operator inputs.
    Quote size is a whole value bounded to 5-30; the persisted live setting is
    10 and 5 is only the missing-file fallback. Breadth defaults to 20 and may
    be explicitly set only from 20 through the authorized canary ceiling of 32;
    32 remains a measured canary, not a durable default or proof that the
    whole slate can fall back to REST. A bare restart must return to 20. The
    six-hour selection horizon does not
    change. Early-exit loss (`0.8 × size`), stressed-exit entry cap (`4 × size`),
    per-market exposure (`9 × size`), portfolio exposure
    (`(slots + 10) × size`), HARD (`8 × size × slots / 20`),
    and the cent-rounded ET-day gross taker-fee telemetry reference
    (`$10 × size / 10 × slots / 24`) are computed properties. Taker-exit count
    and crossing the fee reference must not alter trading. Portfolio
    exposure funds every restart-configured slot-notional plus a ten-notional
    cushion. Breadth scales portfolio exposure, HARD and the fee observation;
    stressed-exit eligibility, per-market exposure and REST retain
    their quantity or policy meanings.

    The old "eight is the measured WebSocket ceiling" reasoning is retained in
    invariant 57 but was **disproved** -- 29 subscriptions were later sustained
    (FINDINGS section 9). Eight was a real measurement of one connection's
    cap, not of the account's.
14. `cancel-all` and account-wide close operations are forbidden on a shared account.
15. The marked-equity HARD stop does not replace open-exposure and worst-case-loss limits.
16. Before quoting a side, stressed full-book depth must show the entire proposed size can be closed inside its stressed-exit and slippage budgets.
17. A WebSocket reconnect has no replay guarantee: new risk remains disabled
    until REST reconciliation and complete, fresh private snapshots succeed.
18. A create with an ambiguous response is never retried. An ambiguous or
    acknowledged cancel may be re-issued only after a later authoritative
    `OPEN` read proves terminal cancellation has not happened; unknown or
    absent state never licenses a retry. Its market remains quarantined until
    the venue proves the outcome.
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
    Recoverable adapter failures and pending terminal cancel proof keep the
    process alive with new risk disabled; the latter does not consume a stream
    reconnect budget. Quoting resumes only after cancel/reconnect/reconcile succeeds. Process exit
    may occur later only after it cancels, flattens, and proves exact flat
    state, or through the bounded non-flat exit in invariant 32.
25. The sole money-loss authority is the **8 × size × slots / 20** adjusted
    marked-account-equity HARD stop
    (`currentBalance` plus every open position's Retail `cashValue`, less
    proven non-trading cash) is the emergency sensor. Every complete mark is
    bracketed by equal settled-cash reads; `assetNotional` is not used because
    production returned zero while open positions had nonzero `cashValue`. A
    missing/malformed position mark or moving cash fails closed. A marked
    breach blocks every new entry immediately. If
    inventory is open it is provisional: existing exits keep running, a
    recovered mark cannot clear until a breach-time position is positively
    closed, and the decision requires a complete reconciliation followed by a
    newer balance. A confirmed hard loss may stop while a different position
    remains open. Realized session P&L, per-market realized P&L, taker-exit
    count and taker fees may be reported but cannot block/cancel quotes, pause
    or stop trading, or alter exits. Buying power is never treated as P&L or
    marked equity. The derived HARD property cannot drift independently of the
    restart-scoped quote quantity and width.
26. A restart may mutate only persisted, identified orders/positions in the
    ledger-selected recovery markets inside configured capacity, and then only
    to reduce risk or recover flat. Unowned or out-of-scope state refuses
    without mutation. Invariants 91 and 93's exact unexecutable owners are
    monitored but never mutated until a valid book or open market status
    returns them to this ordinary recovery path.
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
    IOC is available; it never holds a position while silent. If every
    remaining nonzero owner already satisfies invariant 91 or 93, a fresh complete
    reconciliation proves no working/absent order or unresolved intent, and no
    executable price exists, shutdown may end promptly with explicit nonflat
    truth instead of repeating an impossible price search. Exit 3 and operator
    inspection remain required.
33. Venue reward eligibility is necessary but not sufficient for entry. A live
    side must also pass current lifecycle, structure, spread, target
    depth/cutoff, freshness, exitability, exposure, ownership, and loss gates.
    The normalized 30-second reward/no-fill/stressed-exit model remains
    advisory telemetry while `enforce_reward_cost_gate` is false; its sign may
    not veto an otherwise authorized quote. The venue's $1 aggregate settlement
    threshold is reporting-only, never a per-market gate. Missing reward
    duration or enforced safety evidence means zero orders.
34. Pair-completion spread and maker rebate are zero in the commissioning entry
    forecast until attributable live evidence supports nonzero values. Published
    pool size is never substituted for the bot's proportional share.
35. Taker-exit count and gross owned taker fees are evidence, not trading
    authority. Neither may block or cancel quotes, pause or stop trading, or
    change a passive reducer, IOC exit, state monitor or pre-kickoff flatten.
    The fee observation is scoped to the ET risk day and equals the
    cent-rounded value of `$10 × (quote size / 10) × (slots / 24)` ($10 at
    size 10 / 24 slots). Durable same-day fills reconstruct its one-time notice
    after a restart; negative rebates never offset positive taker fees. The
    adjusted marked-equity HARD sensor is the sole money-loss authority.
36. Authenticated incentive earnings for the active market are sampled every 15
    minutes and stored in a table separate from executions. Paid, pending, and
    skipped observations do not alter inventory basis or trading P&L.
37. Automatic discovery enumerates active liquidity programs first, retrieves
    exact market metadata for only those reward slugs in batches of at most
    100, then applies open, tick, minimum-quantity, and proven six-hour pregame
    clock gates in memory. It must neither walk the account-wide active market
    catalog nor retrieve metadata once per reward slug. All reads share the 4
    requests/second governor; books are capped at 100 per pass and a rotating
    window eventually inspects lower-ranked eligible pools. Missing exact
    metadata cannot authorize entry.
38. A market rotation is allowed only after bot quotes are canceled and a
    whole-account REST snapshot proves zero working orders, zero nonzero
    positions, no unresolved intent, and known ownership. Only then may the
    active public/private subscription set be rebound. Incremental top-up is
    the later invariant-60/70 exception: it retains every healthy owner and
    releases only a positively safe slot.
39. An unsupported or undocumented incentive lifecycle is observable but
    cannot authorize an order. `daily` is documented but not yet implemented
    by the pregame executor; `tournament_with_cutoff` was observed live but has
    no documented timing/pool-allocation rule. Both remain read-only until
    explicitly modeled and tested.
40. New risk is limited to markets whose normalized `sportsMarketTypeV2` is
    exactly `SPORTS_MARKET_TYPE_MONEYLINE`. A two-side count is never accepted
    as a substitute because futures and props are also two-sided.
41. Fresh market selection keeps its 2-cent full-book threshold. In an already
    selected market, an exact one-tick live book quotes both touches so one
    side is not systematically left behind. A locked book still backs off to
    a non-crossing 2-cent window, and overlapping sides are never quoted.
    Runtime economics and the planner call one function for this, so the
    projection scores the price that is actually placed.
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
    authorize entry. Current structure, spread, target depth, applicable reward
    duration, and stressed exit depth remain mandatory. Positive expected value
    is recorded but is not mandatory while the cost gate is advisory.
46. Live score-seconds accrue only for an owned order observed working on a
    side that remains reward-eligible and otherwise authorized. Candidate
    score from a side with no order, or a side now blocked for cancellation,
    is never reported as attributable resting exposure.
47. Automatic selection returns an execution target only when at least one side
    clears every enforced reward and safety gate. Advisory expected value may
    rank or describe a side but may not exclude it. A zero-viable refresh keeps
    the existing active set; a zero-viable startup waits flat and periodically
    rescans rather than binding a monitoring-only market or exiting.
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
    HARD, unowned or cross-market state, unproven flat state, and every named
    critical remain terminal and are never restarted.
54. An empty viable universe is a wait, not a refusal. With no bound market the
    runner holds no orders, no position and no stream subscription, so waiting
    carries no risk. Only an operator stop or a genuine adapter failure ends
    the wait; `SIGTERM`/`SIGINT` handlers are installed before the wait begins.
55. Except for invariant 89's exact `0.01`–`0.09` residual band, inventory
    waits 900 seconds for a free passive exit before paying to cross.
    maker-lab's 112 reconstructed episodes resolved passively for 26%
    of episodes within 30 seconds but 46% by 900 seconds, while crossing costs
    a certain taker commission and their markout stayed positive at 60, 300
    and 900 seconds. One contract risks at most about $0.99 by waiting.
56. Kickoff overrides the inventory clock. A position within 300 seconds of
    the event start is flattened regardless of its age, because maker-lab
    disabled its forced exit on the same holding-is-cheaper evidence and the
    residue reached settlement for -$88.20, 86% of that session's loss.
    Waiting for a free exit is never permission to settle.
57. **Superseded 2026-08-16 — kept because it was used as grounds not to
    investigate.** The durable breadth baseline is 20 markets, with one
    restart-scoped 22-slot canary authorized, and live size is 13 contracts per
    quote (invariant 13). The per-connection reasoning below is still correct; what
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
    operator noticed. Once a flat, order-free and ambiguity-free retirement
    reaches the ten-round abandonment bound, its book refresh also stops even
    before kickoff: the planner can never run for it again, so another book
    cannot serve an entry or exit. Any position, working/unknown-absent order
    or unresolved intent keeps the refresh alive.
63. Retirement is bounded in three directions. Retirement is opt-in by name,
    so an unrecognised critical still fails closed to terminal. A market that
    fails ten rounds is abandoned and never planned again --
    shutdown makes the final attempt -- because retirement resets the exit
    budget and would otherwise replan forever, including after kickoff. More
    distinct retirements than `max_markets` is systemic, not unlucky, and stops
    the session.
64. Stream visibility and order ambiguity have different scope. A private
    reconnect, or a both-stream recovery after a failed active-set bind, may
    resume unaffected markets only after complete REST order and position
    snapshots plus fresh private state. An unresolved intent,
    `UNKNOWN_ABSENT` row, or failed order read positively owned by one market
    keeps that market's execution gate closed until terminal proof; it does not
    make unrelated owners unknown or require their individual reads on a
    scoped/freshness pass. Background terminal repair rotates bounded batches
    of five attributable owners; their GET-by-ID reads may overlap, but every
    request remains governed by the shared 4 req/s limiter, so one NotFound
    retry ladder cannot serialize the other owners. Complete account snapshots
    remain mandatory on their normal cadence. Absence is never cancellation.
    Missing, conflicting, unowned, or out-of-scope ownership remains
    account-wide terminal. Normal active-set changes remain account-wide
    strict. Incremental top-up is a bounded exception only after immediately
    preceding complete account snapshots, strict reads of every owner being
    released and a scope assertion succeed while the old active tuple and both
    old streams remain intact, with no order mutation between proof and commit.
    A lagging individual read belonging to a retained, positively-owned market
    bars only that owner; it cannot block release proof for a different slot.
    Its post-bind reconcile still requires
    complete account order/position snapshots and fresh private state, while a
    newly ambiguous positively-owned order closes only its owner's market gate.
    A failed pre-commit proof may not change scope, close a stream or cancel an
    order. Other scoped order reads are a recovery-only exception after global
    cancellation has already been requested. Private and both-stream reconnect
    loops are bounded, but exhaustion never proves flat: only shutdown's
    independent whole-account proof may authorize restartable exit `5`.
    Recovery must start the replacement private listener before issuing its
    quarantine cancels, then apply buffered private events before classifying
    owners as ambiguous. Shutdown likewise drains buffered terminal events
    before REST reconciliation. This preserves positive cancel/fill proof
    without treating a missing REST row as terminal. Heartbeat-silence recovery
    may preserve resting orders only under the frozen decision loop: overlap-
    start only stale shards, require each replacement's balance delivery, swap
    them, apply scope-checked buffered events, refresh REST balance, and complete
    whole-account REST order and
    position reconciliation, and prove every shard live. A heartbeat that
    arrives during handoff uses the same REST/liveness proof. Any failed step
    falls back to global quarantine except a confirmed HTTP 429 read: while the
    decision loop stays frozen it receives a slower bounded retry, with private
    events drained and replacement liveness checked before every wait. No
    mutation uses that retry. Non-429, stale, stopped or exhausted proof still
    quarantines. An empty scoped order subscription need not invent an EOF;
    complete REST proves empty state. A filtered shard snapshot is complete only
    for its market scope and cannot clear another shard.
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
67. Reward attribution and daily risk authority use midnight-to-midnight
    `America/New_York` and must honor DST. Changing the risk reset instant is a
    live-risk policy change, not an accounting side effect. The UTC-to-ET
    cutover must inherit the exact baseline already being enforced rather than
    stamp current equity and forgive the day's drawdown; only the following ET
    midnight may establish a fresh baseline.
68. The retired queue-depletion dataset remains observation only. Historical
    `WOULD_QUEUE_RESET` rows, stream sessions and reports cannot be read by the
    planner, reconciler, scheduler, risk policy or mutation gateway. The live
    runner no longer opens its separate public-trade socket or appends tracker
    rows. Preserved collector code and data are audit evidence only and may not
    be reactivated without a new reviewed measurement decision.
69. Venue maintenance may pause a runner but may not relax execution truth.
    Only a positively classified transport failure, WebSocket disconnect or
    HTTP 5xx may enter the maintenance coordinator; NotFound, HTTP 429,
    malformed response/schema, ownership conflict and runtime invariants retain
    their existing scoped or terminal behavior. Maintenance probes are
    read-only complete authenticated open-order, position and balance reads
    and are never applied as evidence that an absent order was cancelled.
    Recovery requires three consecutive complete probe cycles one minute apart;
    any failure resets the streak. Quoting remains blocked until the original
    component recovery subsequently completes its cancellation, whole-account
    reconciliation, fresh-stream and private-state gates. Startup uses the
    same in-process pause rather than spending the supervisor's fast-crash
    budget. Operator stop remains prompt, no pause proves flatness, and the
    active market tuple and restart-configured capacity must remain unchanged.
70. Reward authorization loss may reclaim a slot only from positive,
    owner-specific proof. The runner must remember the exact program ID that
    authorized entry. A later successful incentive response must still contain
    that exact row and prove either its explicit `ends_at` elapsed or that it no
    longer applies to freshly-read event metadata (for example, kickoff moved
    back outside the six-hour day-of window). A missing row, different program
    or failed refresh is not proof. Even after confirmation, a nonzero
    position, working order, `UNKNOWN_ABSENT` order or unresolved intent retains
    the market in the active ownership scope. Incremental replacement preserves
    every healthy venue order, rechecks release safety at the commit boundary,
    obeys the near-flatten-deadline guard and cannot exceed the validated
    restart-configured capacity.
    Reward projection must intersect the nominal day-of clock with explicit
    program bounds so selection and runtime authorization value the same
    interval.
71. The hourly seven-day reward report is operationally isolated from trading.
    It runs as a separate process, opens the execution ledger with SQLite
    `mode=ro`, and has no order, book, discovery or mutation call. For its
    account trading values it may make only bounded account-wide reads: one
    earnings request; up to four historical cash-only pages; and, for today's
    independent snapshot, two equal balance reads bracketing one complete
    position snapshot plus up to four current-day cash-only pages. All cash
    walks must reach explicit EOF and share one report-local 1 request/sec
    limiter. Cash changes are assigned by completion `updateTime`; feed order
    is only creation order and may not prove an ET boundary.
    Today's displayed value is current cash plus every open position's
    `cashValue`, minus ET opening equity and completed non-trading cash. When
    the day began flat and current basis is complete it is decomposed into
    closed trading (cash change plus open basis) and open unrealized
    (`cashValue - basis`); otherwise only the exact total is shown. The current
    ET row and its net use this marked value. A completed historical day uses
    adjacent native ET equity
    boundaries only when both prove flat and all non-trading cash is removed;
    otherwise it is visibly prefixed `~` and remains the balanced-pair
    estimate. An immutable historical scan may ignore completions after its
    fixed `through` boundary but must still reach EOF; the current bracketed
    snapshot may not. Any moving balance, absent position `cashValue`, unproven
    boundary, unknown cash transfer, pagination defect, page exhaustion or day
    rollover must render `Unavailable`, never a partial number. A nonblocking
    file lock prevents overlap and a 90-second timeout bounds runtime. Its
    dedicated Discord webhook is loaded only from the server-side shared
    credentials file, is excluded from repr/log output, and may never be
    committed. Discord output is capped at seven ET days and uses two narrow
    fixed-width monospaced tables whose row widths are regression-tested; it
    must not depend on pipe-table rendering or color. Presence and validation
    of the report-only webhook are required by the hourly process alone. The
    same completed run may atomically publish one owner-only seven-day JSON
    snapshot for the TV dashboard. That cache must preserve ET dates, account
    trading, finalized gain, pending context, merged side-hours and pending-safe
    net semantics. Overall P&L is lifetime ledger realised P&L plus bot-era PAID
    gain; pending is excluded. The live runner may additionally publish an
    atomic owner-only local cache of its latest already-proven account-wide
    marked trading total and, only when the day began flat and basis is
    complete, its closed/open breakdown. The cache excludes proven rewards,
    deposits, withdrawals and transfers, expires at ET rollover and cannot
    affect trading if its write fails. The TV service may only validate/read
    either cache:
    it has no venue client, credential load or fallback account request.
    A missing or malformed value may not block trading secret loading. Report
    failure cannot stop, restart or alter the runner.
72. Liquidity-duration and score accounting are telemetry, never trading
    authority. LIVE `Resting side-hours` must use the same durable-order
    interval calculation as the seven-day reward table: merge overlap per
    `(market, side)`, split at midnight-to-midnight `America/New_York`, and run
    from a SQLite read-only connection off the event loop. SHADOW remains
    explicitly run-scoped and may render modeled score-hours. Daily reward
    score may still be persisted as additive increments, but it is not a
    substitute for resting duration. A duration/score read, write, conversion
    or reporting failure may not authorize, select, cancel, replace, delay or
    resize an order, change the restart-configured capacity, alter a
    reward/economics gate
    or modify any risk baseline. It adds no venue REST request or WebSocket
    subscription. Resting side-hours and score-hours both sum concurrent sides
    and may exceed 24. A backward wall-clock sample must add zero score, retain
    the last accepted timestamp as a high-water mark and remain diagnostic
    only; it may not escape into per-market service recovery or retirement.
73. Operator stop is a mutation boundary, not merely an outer-loop condition.
    A stop delivered while a multi-market decision sweep is in progress must
    prevent the next owner from entering the ordinary plan/apply path. The
    existing shutdown routine alone owns subsequent cancellation and
    flattening; prompt stop may not skip its positive whole-account flat proof.
74. The realized session-loss sensor must measure trading cash, not raw settled
    cash. Completed rewards, deposits, withdrawals, bonuses and transfers must
    be positively classified and removed from current cash change before the
    $80 session threshold is evaluated; open inventory remains added back at
    exact basis. Startup must prove the adjustment before an action driver can
    build risk. Runtime uses only the five proven cash activity types, requires
    completion-time classification and explicit EOF within four pages, and
    brackets the walk with equal settled-cash reads through the shared REST
    governor. Unknown transfer text, malformed pagination, page exhaustion,
    moving cash or ET-day rollover may not fall back to raw cash or renew risk
    freshness. The existing stale-equity gate must disable new risk while the
    adjustment is unproved. The same proven non-trading adjustment is removed
    from the separate account-wide marked-equity sensor so a reward, deposit or
    withdrawal cannot mask or fabricate a trading drawdown.
    Discord, the hourly reward report and both dashboards must use the same
    marked-trading equation for the current ET day. Closed realized P&L alone,
    raw settled-cash change, and `currentBalance + assetNotional` are not valid
    substitutes. A carried position without a proven opening basis may retain
    the exact marked total but must render the closed/open split unavailable.
75. An unavailable account read is not zero exposure. Operator reporting must
    publish venue orders, positions, balance and quoted markets only after the
    complete account snapshot succeeds. A first or later read failure must
    render every dependent venue field `unknown`/`unavailable`, must never say
    `flat`, and must not derive empty-slot arithmetic from default counters.
    Reporting availability cannot alter runner, ledger, selector or risk state.
    A routine summary may defer at most 60 seconds only when fresh balance is
    blocked by current order ambiguity; rechecks are ledger-only, persistent
    ambiguity remains unavailable, and shutdown/critical summaries never wait.
76. Explicit closed-market lifecycle proof may reclaim a slot only through the
    incremental top-up transaction. A cached exact market row may schedule the
    scan, but the exact owner must be freshly read as closed, inactive or a
    non-open explicit status after complete account reconciliation and before
    tuple/stream commit. Missing status, failed read or a reopened market is no
    proof. Nonzero position, working or `UNKNOWN_ABSENT` order, or unresolved
    intent retains the owner. Retained orders are not cancelled, the near-exit
    deadline guard remains, and the active tuple cannot exceed the validated
    restart capacity.
77. Venue execution history is positive retained evidence only. It may be
    reset or pruned, so an empty activity result can never prove no execution,
    cancellation or flatness. Execution ID and trade ID are separate durable
    identities. A linked `TRADE_STATE_BUSTED` fill must be marked reversed
    exactly once and excluded from position basis, known-fill totals, P&L,
    fees and reports. Trade/market identity conflict or reactivation after
    BUSTED fails closed. A newer complete authoritative position already
    contains the reversal and must not be arithmetically reversed again.
78. Buying power may constrain only a flat market's new post-only entry. The
    required notional is `price * quantity`, and a successful submission must
    reserve cached capacity for the remainder of that configured-capacity
    decision sweep.
    Rejection backoff is structured, bounded and market-local. A reducer,
    cancel or IOC exit may never be blocked or delayed by buying power or a
    prior entry rejection. Buying power is never equity, P&L or flat proof.
79. REST account freshness cannot certify WebSocket liveness. Every private
    shard owns an independent clock advanced only by a decoded application
    message or a positively awaited WebSocket protocol Pong; the market
    connection retains its independent last-message clock. A private transport
    probe that fails, times out or becomes unavailable enters the existing
    fail-closed stream recovery. A connection with neither form of positive
    proof for 180 seconds enters bounded scoped recovery. If both stream
    families are stale they are rebuilt together. A single-family
    private recovery may preserve orders only under invariant 64's overlap and
    whole-account proof; otherwise it quarantines. REST requests use a
    15-second timeout. Only confirmed 429 reads may honor bounded
    `Retry-After`; ambiguous mutations remain durable and never auto-retry.
80. Changelog monitoring is operationally isolated. It may read only the
    public RSS feed and notify the existing error channel; it has no API key,
    execution-ledger access, REST-governor share, WebSocket subscription or
    runner mutation path. First run establishes a no-spam baseline, failed
    notification preserves unseen state for retry, and a nonblocking lock plus
    timeout prevents overlap. Its failure cannot stop, restart, delay or alter
    trading.
81. Spread-scalp measurement is a stopped, separate observation process with its own
    SQLite database and no order, portfolio, balance or incentive-mutation
    path. It may open the commissioning ledger only with SQLite `mode=ro` to
    copy the current queue labels. Its combined public book/trade scope cannot
    exceed 100 markets; catalog reads are capped locally at 1 request/second
    and normally occur only every 15 minutes. A pair is successful only after
    the full original quantity fills on both fixed-price sides using
    conservative public-trade/queue evidence. Partial/one-side/no-fill states,
    decode failures, drops, reconnects and stale exit books remain explicit.
    None may select, delay, cancel, replace, resize or otherwise affect the
    liquidity runner at any configured capacity. A measurement restart restores durable unfinished first-
    leg episodes; it never turns them into venue positions.
    An exact fixed-GTC trial is a separately labeled pure-volume cohort capped
    at 20 markets inside the same 100-market public scope. It receives five
    minutes of observation before hypothetical placement and remains subscribed
    through positive settlement so offline both-fill or settlement termination
    cannot inherit a rotation gap. It never creates, cancels or reprices a venue
    order. Pre-placement features
    may use only books at or before placement and trades strictly before it.
    A later scope loss, stream error or interrupted interval excludes that path
    from strict results. Retained terminal checks remain read-only, share the
    existing 1 request/second limiter and are bounded to five markets per
    15-minute refresh.
82. Current-book economic refusal may reclaim a pregame slot only after both
    sides remain continuously blocked for 600 seconds by a named, reviewed
    replaceable reason while the owner is flat, working-order-free,
    `UNKNOWN_ABSENT`-free and unresolved-intent-free. An authorized side,
    unknown/data/lifecycle/retirement blocker or execution ownership resets or
    prevents the dwell. Replacement runs only on the normal 900-second scan,
    releases oldest-first, and must have one distinct viable replacement per
    optional owner before release. It may not cancel retained orders, lower the
    active count when replacement supply is short, bypass complete pre-commit
    account reconciliation or exceed the validated restart capacity.
83. Post-flat refill priority may shorten only avoidable scheduler delay. It
    requires a transition from positively held near-deadline inventory to no
    remaining near-deadline position, observed through the normal private
    projector or a complete REST position snapshot, plus mandatory empty,
    expired, closed or reward-ended capacity. It may yield the ordinary full-
    slate service sweep but never priority cancel recovery, reducer/exit work,
    the near-deadline guard, release revalidation, complete account proof or
    stream freshness. A full-universe selection no older than 120 seconds may
    seed candidates only after fresh bulk metadata and incentive reads plus a
    fresh full book and every normal selector gate for each candidate; stale,
    failed or insufficient validation falls back to the bounded complete scan.
    The urgent path may not accelerate invariant 82's economic-idle eviction.
    A failed attempt clears only the request it consumed and establishes one
    retry-not-before deadline honored by every ordinary and event-driven top-up
    trigger; a newer post-flat transition must survive. Timing
    telemetry is evidence only and cannot authorize a slot or order.
84. A response-backed mutation rejection is not a transport ambiguity. An
    authenticated HTTP 403 may move that exact durable intent to `FAILED`; it
    does not prove any existing order terminal or any position flat. A
    permission/location denial is account-wide for new entry, so the normal
    action loop must stop later entry slots before their API call and admit at
    most one periodic entry probe. Cancels and reducers bypass that circuit;
    if their own request is denied, only risk-reducing mutations receive a
    short bounded cooldown. Only success in the matching scope clears its
    circuit. Timeout, connection loss, 5xx, malformed success, missing order
    ID and absence remain ambiguous. Legacy repair may close an old uncertain
    intent only when its durable error contains the exact reviewed response
    proof; near-matches remain unresolved. Shutdown keeps its independent
    risk-reduction and positive-flatness contract.
85. A bound market reaching its positively observed kickoff is an expected
    lifecycle transition, not a retryable runtime failure. It may request one
    incremental top-up, but startup must still reject already-started
    candidates and no request may bypass near-deadline inventory priority,
    flat/order/ambiguity proof, complete account reconciliation, fresh
    replacement viability, release revalidation, stream handoff or the
    validated restart ceiling. Retained orders are never cancelled for
    discovery. A failed refill establishes the same bounded retry-not-before
    deadline for ordinary, lifecycle, reward-end and post-flat triggers, and
    lifecycle timing telemetry is evidence only rather than entry
    authorization.
86. Retained executable value is a prospective shadow, never an entry gate.
    The 60%, 70% and 80% labels are written only on a building post-only PLACE
    observation in the same pre-mutation intent transaction. They may not
    refuse, delay, reorder, cancel, resize or select an order. Replacement and
    reward-opportunity fields reuse only the most recent already-fetched
    selector snapshot and therefore add no venue read or socket; evidence
    older than the normal selection interval must be labeled stale and cannot
    be claimed as a spare. A missing/malformed observation still follows
    invariant 66. No retained-value policy may be proposed until the current
    size/capacity cohort compares avoided realized trading loss against
    projected and paid reward surrendered, recent-candidate availability,
    unknown replacement evidence and no-candidate capacity on a frozen
    held-out interval, followed by explicit operator authorization.
87. Host API admission may slow discovery but may not turn rate-limit recovery
    into an exit deadline. Every cooperating WSL process consumes one aggregate
    4/second clock. Background selection has its own 2/second lane; invariant
    94 gives reporting a separate opportunistic lane; maker entry and cached
    revalidation have an independent 4/second entry lane; account proof,
    cancels, passive reducers and IOC exits use safety. A 429
    cools only its originating class, safety's own cooldown is at most one
    second, and no mutation retries. Non-safety reads publish cooldown and fail
    promptly instead of sleeping inline; later non-safety work with more than
    one second remaining defers before network IO. A deferred entry is exactly
    `FAILED`, not uncertain, causes no market quarantine and gates later entry
    plans for the remaining cooldown before they create another intent. File
    locking/state IO must run off the event loop and no process or asyncio lock may be held
    while waiting. The first operator stop interrupts existing admission and
    read-retry sleeps; a mutation interrupted before network IO must close its
    durable intent as exact `FAILED`, while later shutdown safety calls remain
    available. An authenticated executable that cannot join POSIX coordination
    must fail closed rather than create another host budget.
88. ET-day rollover must establish its durable opening mark before scanning
    non-trading cash whose boundary and day-equality proof depend on that mark.
    A raw balance/position candidate may be published only to create the new
    baseline; it cannot authorize entry. New risk additionally requires a
    complete non-trading-cash adjustment explicitly tagged to the same current
    ET day and the final fresh adjusted-equity observation. A cash-history
    failure therefore leaves entry blocked without retrying against yesterday's
    boundary or allowing stale prior-day cash evidence to cross midnight.
89. Tiny-position acceleration is quantity-bounded and exit-only. An exact
    venue-tradable position from `0.01` through `0.09` contracts may switch
    from passive reduction to cashout only when its durable position-open clock
    reaches 300 seconds. A restart may not reset that clock; a larger old
    position that shrinks into the band is immediately due. Unknown or
    non-aligned venue minimums fail safe. Every working order is cancelled one
    at a time, building side first, and exact terminal proof is required before
    the next cancel or exact-position IOC. Fresh displayed depth may be used
    only after the normal stressed-depth attempt; insufficient displayed depth
    remains recoverable and cannot claim flat. Slot release still requires
    positive flat/order/ambiguity proof. Positions at `0.10` or above keep the
    900-second clock, and this rule may not change entry selection, capacity,
    risk limits or API cadence.
90. Decision scheduling may skip only a flat market whose decision inputs and
    owned timers are unchanged, except invariants 91 and 93's exact
    unexecutable owners.
    Every other nonzero position is serviced and planned every outer pass,
    including retired risk, so reducers, the durable inventory clock and
    pre-kickoff flatten are unconditional. Exact book,
    fill, position, order/intention resolution and successful-mutation changes
    wake only their owner; global entry-authority transitions wake the active
    slate. Buying power remains an apply-time gate and may not dirty the slate.
    Reward dwell, contract/book refresh and kickoff deadlines remain exact,
    selector order and mid-sweep freshness margins are preserved, and private
    events remain in wire order. The scheduler may only choose which markets
    enter the existing service/reconciler/driver path; it has no mutation,
    pricing, quantity, selection, risk or venue authority of its own.
91. One post-kickoff recovery position may enter settlement wait only after a
    valid Retail book wrapper positively contains neither bids nor offers and
    the persisted ledger proves exact bot-owned basis equal to the current
    nonzero position, no working or `UNKNOWN_ABSENT` order, no unresolved
    intent, known whole-account risk and membership in the configured active
    scope. Transport, 5xx, schema, identity, missing-market and pregame failures
    never qualify. The owner is permanently barred from quoting, reserves one
    configured slot and its full basis-derived portfolio exposure, remains in
    private/account reconciliation scope and receives a read-only book probe no
    faster than every 60 seconds. It may not manufacture flatness, settlement,
    a price or a mutation. A valid book immediately restores the unchanged
    reducer/IOC recovery path; authoritative zero position releases the slot;
    any ambiguity or ownership drift removes the exception and returns to
    ordinary fail-closed recovery. Unrelated markets may use only the remaining
    configured slots.
92. Public Polymarket US application maintenance removes new-risk authority but
    cannot remove risk-reduction authority or manufacture venue truth. Only the
    exact official product-scoped maintenance/trading-paused evidence may enter
    this gate; generic text, public status, Retail API success and probe failure
    are insufficient to claim either maintenance or recovery. A flat owner's
    entry quotes become undesired through the unchanged reconciler, while every
    nonzero position remains unconditional and retains passive reduction,
    cancel-confirm sequencing, IOC deadlines and pre-kickoff flattening. An
    unknown observation never clears a pause; recovery requires three
    consecutive healthy official-page observations. A persistent operator
    pause may independently block entry, but operator resume clears only that
    latch and may not override UI maintenance. The public probe carries no
    credential, makes no venue API call or mutation, is bounded and runs off
    the event loop. Neither signal may prove flatness, cancellation, ownership,
    settlement, account health or permission to restart.
93. A freshly fetched exact market row that is closed, inactive or carries an
    explicit non-open status is lifecycle evidence, not a failed incentive
    read. A flat, working/absent-order-free and unresolved-intent-free owner may
    only be released through invariant 76's complete incremental top-up
    transaction and fresh exact pre-commit recheck; startup still rejects an
    ordinary non-open candidate. A nonzero owner may enter inactive-market wait
    only when it remains in the configured active scope, whole-account risk is
    known, persisted bot-owned basis exactly matches the position, and no
    working/absent order or unresolved intent exists. It remains barred from
    quoting, reserves one configured slot and its full portfolio exposure,
    retains private/account monitoring, and probes fresh contract metadata no
    faster than the normal 60-second cadence. No book or order mutation can
    override a non-open contract. Fresh open metadata returns the owner to the
    unchanged reducer/IOC path; authoritative flat proof requests slot release;
    any ambiguity removes the wait exception and returns to ordinary fail-closed
    recovery. Transport, schema, missing-market and missing-incentive failures
    never prove closure or authorize replacement. After twelve consecutive
    unknown contract/incentive refresh failures, read retries back off from one
    minute to a bounded fifteen-minute ceiling without releasing ownership.
94. Read-only analytics, reward reports and shadow measurements are
    opportunistic API work. They must use the shared WSL coordinator's report
    lane, wait until at least one aggregate request interval was unused, make
    no more than one request every four seconds and publish only their own
    cooldown. They may finish later within their reporting interval and may
    never delay the background-discovery, entry or safety lane clocks. The
    cached five-minute Discord summary and TV dashboard snapshot add no venue
    request. Cancels, reducers, exits, account proof, reconciliation and
    ambiguity recovery must never be demoted to report traffic.
95. Automatic selection may not depend on an unbounded account-wide market or
    incentive enumeration. Discovery must page active/open sports events only
    inside the six-hour strategy horizon plus the reviewed one-day event-clock
    skew buffer, fail closed at the explicit event-page ceiling, and apply the
    authoritative market-level lifecycle, `game_start`, V2-moneyline, tick and
    minimum-quantity gates before any symbol-filtered incentive fan-out. Event
    time is a discovery hint and cannot widen the strategy horizon. A missing,
    malformed, truncated or failed event/incentive response authorizes no new
    risk. This routing may reduce API work only; it cannot alter quote price,
    quantity, reward period, economics, ranking, ownership, risk or exit
    authority.
96. Loss-scaled order capacity is a new-risk ceiling driven only by the same
    complete adjusted ET-day marked-equity evidence as HARD. At 50%, 70%, 80%,
    90% and 95% HARD utilization, no more than 30, 25, 20, 15 and 10
    flat-market entry orders respectively may be working or unresolved. The
    tightest tier reached is durable for that ET date and cannot loosen after a
    mark recovery or process restart; only the following ET day restores full
    configured two-sided capacity. A tier transition blocks a new entry at the
    mutation boundary and drains only positively owned overflow orders, one
    cancel per driver pass, lowest-ranked market first and preserving complete
    pairs where possible. Unknown state cannot authorize a cap-driven cancel.
    Any nonzero position excludes that market's orders from the count, and
    passive reducers, IOC exits, recovery and shutdown must never be blocked,
    cancelled or counted by this ceiling. It adds no venue read or stream and
    does not replace exposure, stressed-exit, buying-power or HARD enforcement;
    HARD at 100% remains the sole loss stop.
97. A separate deterministic clock ceiling permits no more than 30 working or
    unresolved flat-market entry orders from 00:00 inclusive through 09:00
    exclusive `America/New_York`, including DST, and no more than 40 during the
    rest of the ET day. The effective ceiling is the smallest of the clock
    band, current durable loss-scaled ceiling and two orders per configured
    slot. Reaching 09:00 changes only the clock band and cannot reset or widen
    the loss ratchet. The schedule and loss tiers load once at startup from a
    strictly validated policy; an unreadable or invalid policy refuses before
    credentials, database or venue access and cannot hot-reload.
    It uses invariant 96's reservations, ownership proof, one-cancel driver,
    ranking/pair preservation and mutation-boundary recheck. Nonzero-position
    markets, reducers, IOC exits, recovery and shutdown remain excluded.
98. Official production maintenance scheduling is a tightening-only clock.
    The runner polls only the bounded public Statuspage schedule for the exact
    `[Production] Polymarket US Exchange` component, retains verified windows
    in an owner-only atomic cache and re-evaluates cached boundaries even when
    a later poll fails. Thirty minutes before `scheduled_for`, flat entry
    orders become undesired and every exact bot-owned position enters the
    existing sequential cancel-confirm and bounded exact-position IOC path;
    ownership, ambiguity, displayed-depth and mutation-intent rules remain
    unchanged. Absence alone is not completion, `started_at` is not an
    execution clock, and an unknown observation cannot clear a hold. A cached
    exact row may be classified locally as `record_absent_after_end` only once
    `scheduled_until` has passed, its exact endpoint returns 404 and a complete
    history read succeeds without the ID; ordinary polls must also complete
    both current-list reads. Before the planned end, or after any non-404,
    failed or malformed read, the state remains unknown. This classification
    does not claim official completion: it uses `scheduled_until` as the
    boundary, while any later official completed update replaces it and extends
    the hold. New risk remains blocked through maintenance and until one hour
    after the later of that boundary or the first official completed update,
    followed by three complete authenticated order/position/balance reads.
    Normal stream, reconciliation, UI-participation and account-risk gates
    must still pass.
    The process stays alive, operator stop remains prompt, and no schedule,
    cache, public status or health read may prove cancellation or flatness.
99. A completed cash transfer may enter adjusted trading equity as positive
    non-trading cash only through an already reviewed plain-text class or the
    exact structured version-1 `kind=remediation`, `action=goodwill`,
    `scope=isv` shape. That amount is subtracted from account movement and can
    never buy back loss capacity or mask HARD. A missing, malformed or nearby
    structured value remains unknown and expires new-risk authority; it may
    not be guessed from amount, timing or operator interpretation.
