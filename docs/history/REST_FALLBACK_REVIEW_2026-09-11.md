# Temporary REST fallback: end-to-end review and repair, September 11

## Authorization and scope

The operator requested an end-to-end comparison against the original WebSocket
path, then authorized repairing the related issues, limiting temporary mode to
30 pending entry orders, completing edge-case review, and restarting afterward.
Quote size 10, 32 reward-selected market slots, $90 HARD, lower loss tiers,
overnight rules, live-event protection and exact position exits remain unchanged.
The temporary ceiling excludes position-market orders and reducers, counts
unresolved entry reservations, and cannot relax any smaller policy ceiling.
The original WebSocket mode retains its existing entry capacity and remains the
default. Stable tags and diagnostic/trading credentials are untouched.

## Findings and narrow repairs

1. **False account-risk cancellations after slow account proof.** The decision
   sweep captured `now`, awaited a full REST reconciliation, then used that old
   time to decide whether equity needed refreshing. In the offline reproduction,
   equity was five seconds old before a 16-second poll; the refresh was skipped,
   the planner saw 21-second-old equity and canceled a healthy quote with
   `side_no_longer_desired:account_new_risk_paused`. The shared sweep now reads
   the current clock after reconciliation. Actual stale/failed equity still
   blocks entry and cancels unsafe resting entries; freshness is not fabricated.
2. **Serial full-account reads exhausted proof freshness.** Forty successful
   order reads at simulated 0.8 seconds each rejected two consecutive proofs.
   Temporary mode now overlaps at most four independent exact-order reads under
   the unchanged shared API governor. Every owned working/ambiguous order is
   still checked, including reducers and owners above the entry ceiling. No
   mutation is parallelized or automatically retried. Any failed read fails
   proof; cancellation joins all workers, and invariants remain terminal.
   Expired complete proof is local retryable admission backpressure, not a
   fabricated venue-maintenance claim or permanent startup refusal.
3. **Two-page history truncation was insufficient without private fills.** A
   201-fill fixture returned only 200 fills and discarded its continuation.
   REST mode uses durable recorded fill IDs as its restart anchor; an empty
   ledger instead walks retained history to EOF within the existing page bound.
   Descending polls retain at least the original two pages
   and continue until a whole already-proven fill page or EOF. Unknown/malformed
   account legs, missing EOF/continuation, repeated cursors and page exhaustion
   cannot certify coverage. History IDs advance only after successful ledger
   application; a long successful initial backfill remains positive history
   even when current account proof expires, allowing the next fresh proof to
   catch up without repeating the full initial walk. No history response proves
   order cancellation, flatness or absence of older/pruned fills.
4. **A startup server outage could become a permanent refusal.** Per-market
   startup dropped every candidate after incentive HTTP 500s and replaced the
   transient cause with `CommissioningRefused`. It now preserves a typed
   transient cause for normal in-process recovery. Schema/ownership/configuration
   refusals are not relaxed. This is an inherited defect, not introduced by REST.

The original path remains serial with its existing bounded history backfill.
Only the correct post-await clock and transient startup classification also
repair the shared path. Strategy/reconciler, execution ledger, risk arithmetic,
price selection and API rate limits were not changed.

## Evidence before deployment

- Compared implementation against `v1.2.0`; reviewed source binding, scheduler,
  account proof, fill/backfill, cancellation, reduction, event lifecycle, risk,
  maintenance, shutdown, supervisor and restart paths.
- Baseline full suite: 1,257 tests / 126.741 seconds, passing.
- Live old-runner scheduler at 12:15:04Z reported decision p50/p95
  211.355/226.900 seconds. Logs contained repeated account-risk-paused cancels.
  This supports a latency concern; it does not prove every cancel had one cause
  or establish a realized monetary/reward impact.
- First repaired full suite: 1,279 tests / 129.988 seconds, passing.
- Reversing six principal repairs produced ten expected failing regressions
  (eight assertions, two expected old-path exceptions), then source was restored.
- Two final strict-history edge regressions also failed under an isolated
  legacy-history reversal. No production source was changed for that reversal.
- Graceful stop completed at 12:30:22Z, `operator_sigterm`, `flat=true`.
  Authenticated venue/ledger gate at 12:31:43Z proved zero orders, positions,
  unresolved intents, unknown orders and ledger working orders.

## Dedicated edge-case audit

- Success/empty/full: original versus temporary startup; empty retained history;
  29/30 entry boundaries; 40 owned orders still completely reconciled.
- Risk/business: original mode unchanged; 30 maximum across ET clock/day bands;
  tighter 25/20/15/10/0 caps; unresolved reservations; reducer exclusion;
  higher-ranked pairs retained; $90 HARD authority and transfer exclusion.
- Timing/races: clock reread after long proof; 30-second boundary and negative
  age; post-admission create/reducer expiry; old queued book rejection; parent
  cancellation and independently cancelled child both drain all read workers.
- Partial/unknown: exact fills and cancel ACK-versus-terminal behavior; all
  unreadable owners still attempted; no unknown-create retry or ownership bypass;
  malformed history, failed positions/history and ledger conflicts revoke proof.
- Recovery/shutdown: transient startup failure recovers; invalid startup still
  refuses; operator stop interrupts waits; maintenance stays entry-only unless
  its existing flatten rule applies; exact reducers and the IOC budget persist.
- Load: four reads in flight maximum, unchanged aggregate admission and request
  set; initial history may cost more pages, subsequent known overlap stays
  bounded. Slow/unavailable APIs can still pause trading. No synchronous report
  or diagnostic task is added to the live decision path.
- Restart/rollback: preserve the same ledger and original credential source;
  history uses durable fill anchors but fresh account proof is still required;
  no stable tag movement,
  database rewrite, risk reset or automatic source switch.

## Limits and live acceptance

REST polling is not an instantaneous private execution stream. It cannot prove
that a successful venue response is internally up to date, recover pruned
history, guarantee unseen late changes below retained overlap, guarantee exit
liquidity, or guarantee a zero-delay reaction to fills. Fail-closed pauses remain
intentional. No test suite proves survival of every possible error.

The exact Retail [activities contract](https://docs.polymarket.us/api-reference/portfolio/get-activities),
reopened September 11, documents `cursor`, `nextCursor`, `eof` and descending
timestamp sort; no undocumented start-time filter is used. The tests simulate
responses and never induce live fills, maintenance, cancellations or losses.

Final suite, deployment hashes, post-deployment native tests, fresh restart gate
and live acceptance are recorded below as they complete. Until then the bot is
stopped; this document is not itself a deployment or trading authorization.

## Final source gate

The final review suite passed **1,281 tests in 126.454 seconds**. It includes
24 new edge regressions plus the existing 29 temporary-mode tests. Twelve
reversal detections established that the new tests reject removed repairs.
Final source review retained all safety gates and found no further actionable
policy drift. Runtime deployment and live acceptance remain separate gates.

## Production-sized rehearsal follow-up

Commit `665b72f42` was pushed and deployed while stopped; all 171 tracked
source/test/script/web/config hashes matched. Its native full suite passed
1,281 tests in 94.401 seconds. No bot restart had occurred.

An isolated rehearsal used an online copy of the production ledger, disabled
all create/cancel calls, and the 0.25/s reporting lane. It was interrupted after
several minutes while paging history; no production ledger or venue mutation
occurred. Requiring the entire old retained history on every process start was
an avoidable cold-start/load regression. The follow-up seeds known history from
durable execution IDs; it still requires at least two pages and continues across
unseen fill bursts until known-page overlap or EOF. It does not reset inventory,
assume complete historical P&L, or authorize entries without current proof.
An empty ledger still requires EOF. One added restart-anchor regression and its
reversal cover this follow-up; final re-verification is recorded below.

At **12:39:35Z**, the follow-up completed the same isolated production-ledger
REST proof in **14.828 seconds**, even on the deliberately slow reporting lane:
zero working orders/positions, complete proof true, no error, no production
ledger write and no venue mutation. This validates flat-start account decoding
and retained overlap, not sustained 30-entry timing or fill latency.

Final follow-up source gate: **1,282 tests passed in 129.591 seconds**,
including 25 new review regressions. The durable-anchor reversal failed as
expected, bringing the repair/edge reversal detections to thirteen. No source
or configuration changes remain outside the reviewed diff.

## Final deployment and restart

- Final code commit **`64dcefdc1`** pushed before stopped-runtime deployment;
  all **171** tracked source/test/script/web/config hashes matched and
  `DEPLOYED_COMMIT` names the same full commit.
- Native full suite: **1,282 tests / 96.347 seconds**, passing. Review suite:
  **1,282 / 129.591 seconds**, passing; thirteen reversal detections.
- Final authenticated start gate at **12:45:10Z** again proved zero venue orders,
  positions, unknown orders, unresolved intents and ledger working orders.
- Restart requested with `auto 10 32 rest-fallback`; current live acceptance
  follows below. The temporary cap applies to flat-entry orders/reservations,
  not 30 markets or a restriction on exact position exits.

## Initial live acceptance

Supervisor **36080** / runner **36088** are running the final commit in temporary
mode. Startup logged `max_pending_entries=30`, four bounded order reads and
`effective=30`; scheduled maintenance was NORMAL at 12:47:07Z. New orders began
at **12:47:28Z**. Authenticated evidence at 12:48:28Z found 23 orders on 23
markets, zero positions, zero unresolved/unknown state, matching 23 ledger orders.

The first completed rebuilt-slate API window at **12:49:08Z** contained **544
attempts / 221.2 seconds**, 514 reads and **30 creates**, **zero failures and zero
429s**, 2.459 requests/second, response p99 0.372s, and maximum read waiters four
(the deliberately bounded concurrency). No create/cancel churn appeared during
this initial rebuild. Decision latency p50/p95 was **73.731/129.938 seconds**;
action latency p50/p95 **0.389/1.970 seconds**. This is initial startup/slate
evidence, not an apples-to-apples steady-state performance comparison or proof
of instantaneous fill response. Sustained natural fill/exit latency and later
maintenance/HARD recovery remain operational observations, not induced tests.

At **12:49:53Z / 08:49 ET**, authenticated venue and ledger matched **30
working orders across 30 markets, zero open positions, zero unknown orders and
zero unresolved intents**. The requested pending-entry ceiling is reached;
this is not a restriction on position reducers or a guarantee of future reward.
