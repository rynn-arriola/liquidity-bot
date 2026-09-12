# Whole-bot review — September 12, 2026

## Scope and status

This is the review phase requested before the next responsiveness change. It
covers the active trading lifecycle, its reporting/operations consumers, and
the relevant official Retail contracts. It is not a claim that every line of
historical research tooling or every unused Institutional endpoint has been
independently verified. No trading source, configuration, source selection,
order, position, deployment or running process was changed by this review.

Reviewed baseline: `f6342ff7`, with loaded trading source marker `b826ff11`.
Parity passed for Windows/GitHub/laptop WSL; the offline PC remains pending.
The recovery ledger copy was online-backed-up and verified by parity, not
activated. Runner `649372` and supervisor `649355` remain the live REST owner,
size 10 / 32. A bounded authenticated report-lane read at **13:11:14Z** found
**29 orders, zero nonzero positions**: all 29 were flat-market entry orders.
These are sequential observations at that time, not ongoing exposure proof.

## Confirmed findings to fix before the performance refactor

### R1 — P1: WebSocket-mode reconciliation hides conflicting fill identities

Location: `src/liquidity_bot/live_commissioning.py`, `_rest_reconcile`,
the per-execution `except LedgerConflict` around line 2509.

REST mode re-raises this conflict, but WebSocket mode continues. The catch was
originally for harmless schema/outcome enrichment. `ExecutionLedger.apply_execution`
now handles that enrichment and identical replay without raising; changed
price, quantity, fee, intent, market or event time is a genuine conflict.
Suppressing it can leave the history/ledger disagreement hidden while renewing
the account gate. This is especially relevant when private service returns.

Offline reproduction: record execution `review-conflict` at price 0.40; return
the same identity from history at 0.80; reconcile in ordinary WebSocket mode.
Result: `rejected=false`, `rest_reconciled=true`. This proves the code path,
not that a conflicting fill occurred on the live account.

Recommendation: propagate immutable fill conflicts in both modes. Preserve
identical replay and outcome-enrichment behavior; the existing
`test_replaying_a_fill_after_a_schema_change_is_not_a_conflict` must still pass.
The cost is a correct safety hold when evidence truly disagrees.

### R2 — P1: Slow account refresh renews an old equity mark as fresh

Location: `live_commissioning.py`, `_refresh_balance`, around lines 4365–4377.

The account snapshot retains its original observation time. After cash-history
and final balance reads, however, the risk guard is stamped with completion
time. Equal fiat cash does not prove that the intervening position valuation
has stayed current. The 15-second risk-age gate therefore understates age.

Offline reproduction: advance the fixture clock 25 seconds during cash-history
collection while the account cash bracket remains equal. Result: source age
25 seconds, risk-guard age zero, `can_add_new_risk=true`. Other independent gates
can still prevent an actual order; this does not prove a live HARD miss.

Recommendation: carry the oldest contributing observation time into freshness,
publish only after the bracket succeeds, and reject/expire over-age entry
authority. Preserve ET rollover, cash-flow exclusion, provisional/confirmed
HARD persistence and the previous good mark on failed collection. Completion
time is useful latency telemetry, not a replacement for data age.

### R3 — P1: Entry safety checks can expire during API admission

Locations: `live_session.py`, `ControlledActionDriver.apply_one`, and
`live_orders.py`, `AsyncTradingGateway.create/_mutation`.

The driver checks the risk guard and entry cap before awaiting the shared
limiter. The final callback rechecks event evidence; the REST/source wrappers
also check their own proof/generation. They do not repeat the whole entry
authorization, including the shorter book/equity age bounds, after admission.

Offline reproduction through fixture preflight, normal planner, real driver,
executor and gateway: a fake limiter advances time 16 seconds before sending.
One fake create occurs although `risk_guard.can_add_new_risk(now)=false` at
send time. No live mutation was made. Long admission is exceptional under the
current governor, but the boundary must remain safe under delay and future
scheduler changes.

Recommendation: a shared, synchronous final-send validator using the current
clock and source generation. Recheck entry proof, book/equity/event age, pause,
clock band and pending-entry allowance immediately before network I/O. A local
veto is exact NOT-SENT/FAILED, never UNCERTAIN. Keep cancels available and give
reducers their separate exact-position/freshness authority; do not apply the
flat-entry cap to them.

### R4 — P1: General stream-task teardown can discard received private evidence

Locations: `live_commissioning.py`, task cancellation before top-up/rotation
around lines 9923/9987 and final shutdown around 10156.

`next_event()` removes an event from its queue. If its task completes before
these branches cancel/gather tasks, the returned event is discarded. A later
queue drain cannot recover it. The explicit source-handoff path already takes
the held result first, but the older general lifecycle branches do not all
use that pattern.

Offline reproduction through `runner.run`: let the private task take a uniquely
tagged frame, then request stop at the cooperative yield. At shutdown the
marker was neither observed nor still queued. The reproduction used a harmless
heartbeat marker; an order/fill event follows the same task/result path.
Complete REST/history may repair state later, but that is not permission to
discard positive evidence, especially while the venue is unavailable.

Recommendation: unify bounded teardown: settle the reader task, project any
successfully returned private event once, then drain in wire order. Retain
positive events before a subsequent error; do not restart streams merely to
drain on shutdown. Test stop, rotation, top-up, reconnect and source handoff.

## Additional improvements and limits

- **R5 — P2, legacy diagnostic accounting:** `scripts/accounting.py:318`
  labels `delta_equity + delta_fill_spend - delta_paid_reward` as derived
  settlements without subtracting deposits/withdrawals or separating changes
  in open marks. A deposit alone changes that displayed settlement number.
  This is not the current Discord reward-table or HARD calculation. Remove
  the causal label or reconcile all cash flows and open marks before using it;
  make the canonical account-marked report the clear default.
- **Cash-type forward compatibility:** the official Portfolio contract lists
  `ACTIVITY_TYPE_LIQUIDITY_PROGRAM` and `ACTIVITY_TYPE_TAKER_FEE_REBATE`;
  `cash_activity.CASH_ACTIVITY_TYPES` does not request them. A bounded live
  query for those two types returned zero rows with `eof=true`. No current
  accounting error is established. Support a positively documented liquidity
  credit if it appears under that dedicated type; do not automatically treat
  a trading-fee rebate as unrelated cash. Preserve double-count protection.
- **Scheduling:** REST proof and catalog work are still awaited inline. The
  5/1-second poll interval starts after completion, not at the fill. A new fill
  can occur while the bot still believes it is flat. Account requests/s alone
  cannot establish detection or exit latency.
- **Do not background the existing selector unchanged:** its EventAwareGateway
  records event-start facts in the ledger/caches. A nominal read can therefore
  write authoritative state. Separate observation collection from projection
  before introducing an independent worker.
- **Capital display:** `free_to_withdraw` is conservative operating-capital
  guidance capped by proven cash/buying power, not a venue guarantee that a
  withdrawal will be accepted. Missing proof must stay unknown. Current
  reporting correctly avoids filling that gap with an invented number.
- **Documentation drift:** old instructions still call the source temporary,
  describe superseded fee/count stops or older refresh cadence, and coexist
  with later decisions. Retire superseded directions into history as part of
  implementation; do not reintroduce them as trading rules.

## Whole-system coverage map

| Area | Reviewed integration and conclusion |
|---|---|
| Startup/configuration | Launcher, supervisor, source selection, size/capacity parsing, runtime lease and preflight; persisted operator settings and single live owner must remain authoritative. |
| Venue normalization | Retail SDK/raw response handling, pagination, Decimal quantity preference, side/intent mapping, execution states/busts and exact-order identity; do not rewrite proven YES-frame mapping from a generic example. |
| Selection/rewards | Event-based discovery, eligibility, exact parent-event proof, ranking, book cutoff/score, program-level measurement; improve scheduling without changing eligibility or reward priority. |
| Entries | One-tick retained-market touch behavior, fresh-selection spread, 70% quality gate, stressed exit, exposure and pending cap; R3 concerns when checks occur, not their configured values. |
| Position lifecycle | Builder cancellation, touch reducer, exact residual quantity, 900-second clock, tiny residual grace, pregame/live-event override, sequential cancel/IOC confirmation and 3-attempt/30-second budget. No recommendation to weaken exit proof or reset clocks. |
| Risk/accounting | Equity/cash brackets, durable ET baselines and cash-flow exclusion, loss ratchet, sole HARD evacuation/pause/next-day recovery; R2 affects freshness. |
| Transport/ownership | REST and WebSocket gates, source handoff, immutable reader results, generation/scope proof, durable intents, exact confirmation, duplicate fills; R1/R3/R4 affect common boundaries. |
| Maintenance/recovery | Official window parsing, retained cache/deleted-record handling, one-hour resume hold, UI pause, health recovery, per-market inactive/settlement waits and ownership reservations. No software can close through an unavailable mutation service or absent liquidity. |
| Reports/measurements | Account-marked Discord reward table, nearby-midnight estimate boundaries, paid/pending separation, summary/capital guidance, ledger diagnostic scripts and dashboard caches; R5 is a separate legacy command. |
| Operator interface/security | Read-only dashboard route allowlist, local binding, read-only SQLite, bounded asynchronous notifications, credentials parsed as data, parity/backup and stop/restart gates. Research/scalp modules remain outside live execution; no recommendation to activate them. |

Coverage combines targeted source tracing, official-contract comparison and the
complete existing suite. Historical analysis modules were inventoried and their
tests exercised; this is not a fresh mathematical audit of every research model.

## Official API comparison and opportunities

The [complete index](https://docs.polymarket.us/llms.txt) was used to inventory
Retail, Python/TypeScript SDK, Institutional, incentive, fee and partner pages.
Detailed review focused on APIs the bot consumes or could plausibly substitute.
Unused combos/RFQ, tags, search, series and player/league catalog routes were
cataloged, not treated as authorized strategy expansion.

- [Authentication](https://docs.polymarket.us/api-reference/authentication):
  SDK Ed25519 header signing and the documented timestamp window match the
  integration. A new key is not evidence that missing private data is fixed.
- [Private WebSocket](https://docs.polymarket.us/api-reference/websocket/private):
  preserve raw Retail decoding and tested aliases. The overview uses snake-case
  examples while the detailed page/SDK use camelCase shapes. Do not replace a
  positively working subscription with another page's generic shape. The
  position update contract alone is not a guarantee of an initial snapshot.
- [Markets WebSocket](https://docs.polymarket.us/api-reference/websocket/markets):
  keep public full books in both account modes. Lightweight BBO cannot supply
  the depth required for reward cutoff and exact stressed exit decisions.
- [Orders overview](https://docs.polymarket.us/api-reference/orders/overview)
  and its Retail OpenAPI schema: synchronous submission can wait for execution;
  asynchronous acceptance still needs exact confirmation. There is no basis
  to blindly retry an ambiguous create.
- [Batch cancels](https://docs.polymarket.us/api-reference/orders/cancel-multiple-orders):
  returned IDs echo the request rather than prove cancellation. Batch-create
  and modify likewise do not eliminate per-order outcome reconciliation.
  Do not adopt batching as the immediate latency fix.
- [Close position](https://docs.polymarket.us/api-reference/orders/close-position-order):
  the documented market-wide close is not a substitute for this bot's exact
  owned quantity, all-working-orders-terminal and bounded IOC rules.
- [Portfolio](https://docs.polymarket.us/api-reference/portfolio/overview):
  decimal positions, complete pagination and explicit busted-trade handling
  matter in both modes; a current position snapshot is not complete fill history.
- [Earnings](https://docs.polymarket.us/api-reference/incentives/get-your-incentive-earnings):
  keep ET earning date and payment status separate from current account cash.
  Some public incentive schema/server references point into the Institutional
  surface; do not silently change the verified Retail gateway.
- [Liquidity incentives](https://docs.polymarket.us/incentives/liquidity):
  per-side time-normalized scores and raw-depth cutoff support the implemented
  scoring model; estimates are not paid amounts. Payment can lag beyond one day.
- [Fees](https://docs.polymarket.us/fees): taker coefficient 0.06 and maker
  coefficient -0.0125 match current assumptions. Actual recorded fees, including
  rounding/partial fills, remain accounting authority over stress estimates.
- [Rate limits](https://docs.polymarket.us/api-reference/rate-limits): published
  Retail limits are 20/s; the host's stricter aggregate 4/s remains intentional.
  The documented latency stopgap is distinct from an HTTP 429. Faster-looking
  Institutional FIX/gRPC/drop-copy options are not assumed available to this
  Retail account.
- [Changelog](https://docs.polymarket.us/changelog): latest listed maintenance
  notice is September 11, 04:00–07:00 ET; newer sports types do not authorize
  expanding this bot beyond its supported moneylines. The linked live status
  page could not be fetched successfully by the web tool, so this review makes
  no fresh platform-outage/resolution assertion.

## Recommended implementation order

1. Repair R1–R4 with focused regressions, deliberate reversal failures and
   cross-mode coverage. Correct the misleading legacy diagnostic separately.
2. Keep one ledger writer, planner, executor and risk policy. Extract pure
   account/candidate collection; retain ordered serial projection. Do not
   duplicate the strategy into REST and WebSocket implementations.
3. Introduce a bounded, single-flight account watch and interruptible catalog
   work. Give urgent position service priority between units of background
   work. Budget requests through the existing host governor, without changing
   the configured rate, slots, order cap or business thresholds.
4. Measure response receipt → projection → cancel/reducer decision → send →
   terminal confirmation at normal 32-owner capacity. Compare median/p95/max
   and deadline misses, not only poll interval or average RPS.
5. Retain manual verified mode selection, no automatic switching and no
   permanently running inactive private probe. Deploy/restart only after an
   independently authorized, reviewed operational workflow.

## Restored-WebSocket acceptance requirements

Both modes must pass the same trading-policy/ownership scenarios. Required
cases are complete and partial snapshots; raw-list and nested payloads;
duplicate versus conflicting fills; delayed/error frames after valid events;
partial fills, sign flips and manual closes; cancellation acknowledgement before
terminal proof; fills during admission, top-up, shutdown and handoff; stale or
changed generation/scope; cash bracket delays and ET/DST boundaries; persistent
HARD/entry ratchet; unknown creates with no duplicate retry; empty/thin books;
and every cap excluding position-market reducers/exits.

Switching must preserve position age, durable ownership, IOC attempt budgets,
HARD and same-day loss tiers. An urgent exit or incomplete/disagreeing target
proof aborts promotion. A failed candidate must not discard its positive fill
evidence. A quiet healthy connection is not equivalent to complete account
proof, and Pongs alone cannot enable WebSocket trading.

Existing source-switch tests are valuable but did not catch R1–R4. Passing
simulation is not proof that the venue now provides a complete usable private
feed. Natural restored-feed acceptance remains pending; do not induce fills,
faults or a source switch to obtain it.

## Verification record

The source was copied into an isolated native WSL directory, without live
credentials, databases or runtime state, using the installed Python environment.
The four reproductions above used fake clients only. The first complete-suite
attempt ran 1,469 tests with one staging error: the isolated copy omitted
`web/tv-dashboard/index.html`. That was a review-copy omission, not a product
defect; the missing web assets were copied and the complete suite rerun.
The complete rerun passed **1,469 tests in 134.516 seconds**. This confirms the
unchanged baseline tests, not the absence of the newly reproduced gaps.

No production repair is claimed here. R1–R5 and the responsiveness implementation
remain open work; this report is the evidence and scope for that next phase.
