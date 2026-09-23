# Account-proof architecture review — September 23, 2026

## Outcome and authority

Read-only architecture review requested after the operator questioned hybrid's
lack of measured REST savings. No execution/configuration change or restart.
Recommendation: consolidate account proof consumers incrementally; do not
replace the working runner with a WebSocket-only engine or slow safety polling.
Implementation requires the operator's next authorization.

## Current evidence

Loaded release321728fc9, supervisor7592/runner7606, auto15/32 hybrid remained
running. Authenticated19:26:58Z:25 orders,21 flat-market entries and four exact
reducers covering +15/+12/+15/-6 positions; unresolved/unknown zero. Local
working orders26 versus venue25 were separate, non-atomic observations during
live trading, not a claim of exact ledger parity or proof of a defect.

Eleven completed telemetry windows ending19:14:04–19:25:45Z cover761.2 seconds
(the first starts before19:14). They contain2,363 admitted attempts,2,195 reads
(2.8836/s),168 mutations and one429. These are admission counters, not exact
wire/IP-wide traffic, and not a matched REST-versus-hybrid benchmark.

| Read category | Attempts | Per second |
|---|---:|---:|
| Exact order | 556 | 0.7304 |
| Open-order lists | 325 | 0.4269 |
| Positions | 341 | 0.447976 |
| Execution history | 343 | 0.4506 |
| Balances | 240 | 0.3153 |
| Cash history | 160 | 0.2102 |
| All other reads | 230 | 0.3022 |

Account categories total1,965/2,195 reads,89.5%. This is the target surface,
NOT the removable share. Essential proofs, pagination and mutation races remain.
One reducer timed exit in the logs reached IOC acknowledgement4.35s after its
deadline and then flat proof; that single episode does not establish comparative
latency. Existing controlled tests prove particular saved reads and earlier
wakeup only, not better live aggregate performance.

## Complete flow and findings

Public WebSocket books feed reward discovery/ranking and selected-market
decisions; changing that market scanner is not the primary account-load remedy.
Private hybrid events are serialized into the ledger or treated as hints.
REST still supplies complete execution/account authority, ownership recovery
and financial consistency. The shared governor bounds all lanes.

1. `rest_fallback._rest_poll_due` selects the one-second active cadence when
   ANY position or unresolved intent exists (five seconds otherwise). This is
   a due interval after completion, not a promise of one finished poll/second.
   `RestFallbackRunner._rest_reconcile` can reconcile all working orders and
   dirties all active markets. Hybrid substantive hints can request more proof.
2. `_reconcile_position_state` scopes exact order reads but still collects
   account-wide orders/positions/history; it intentionally does not renew the
   global gate/cadence. A later global proof can repeat overlapping work.
3. `_refresh_balance_candidate` collects balance/positions/balance, cash
   history and a final balance. Periodic balance coalescing already exists,
   but this bracket is not shared with execution reconciliation. Settled-loss
   arithmetic uses ledger matched realized P&L, while its publication/freshness
   depends on this financial proof. Do not simply remove the bracket.
4. `AccountSnapshotReader` already concurrently collects pure read results,
   then the serial owner projects them. Its single-flight guard rejects a
   second collector; it is not a request-joining coordinator shared across
   execution, capital/risk and exit consumers.
5. Two-list flat-order proof, terminal private-order shortcuts, bounded exact
   read concurrency, history overlap/deep checkpoints, balance coalescing and
   scoped reducer admission already exist. Recommending them as new work would
   overstate the opportunity. Exact-order reason telemetry is still missing.

At startup/recovery/shutdown, full proof and exact ambiguous-order retrieval
remain intentional. Unknown creates must not be retried; absent orders cannot
be inferred canceled. Private reconnect/data gaps invalidate affected authority.
Background workers never adopt identities or mutate the ledger. A single
serial writer owns intents, projection and final post-admission mutation checks.

## Recommended architecture and phases

Extend the existing account observation boundary rather than create a second
account engine or another poller. One coordinator accepts requests specifying
consumer/reason, required fields, scope, generation/revision, maximum age,
ordering constraints and urgency. Compatible consumers join one in-flight read
cycle; incompatible or invalidated consumers receive new proof. Immutable
results include per-field provenance, completeness, timestamps and coverage.
Only the serial owner applies them and issues narrowly scoped authority.

1. Instrument why each proof/exact read is required and what it serves, using
   existing request paths with no diagnostic API polling. Build deterministic
   replay cases for current REST/hybrid decisions and load before optimizing.
2. Coalesce compatible execution/exit observations and requests. Keep existing
   deadlines and fallback cadence initially. Prove fewer calls on identical
   traces before expanding sharing to financial data.
3. Prioritize dirty positioned owners and deadlines, avoiding unnecessary
   peer exact-order sweeps where positive scoped evidence allows. Retain
   bounded full-account audits for manual/external activity and silent loss.
   Unattributed hints, unowned orders and global ambiguity remain global.
4. Integrate capital/risk consumers only with preserved cash-bracket ordering,
   transfer handling and settled-loss authority. A cached position read outside
   the bracket cannot replace one inside it. Reporting consumes published
   observations and must not initiate duplicate trading-critical reads.

WebSocket events accelerate known changes; REST anchors initial state, audits
and recovery. Silence does not prove freshness, and a transport connection is
not application completeness. Never auto-promote a hint to a complete account
snapshot. Keep explicit source modes and a rollback path; no database policy
migration, risk reset, quote-size/filter/expiry or loss-rule change is proposed.

Alternatives: REST-only remains a simpler baseline but retains repeated proof
cost; WebSocket-only lacks established full current stream authority; merely
slowing polling risks late exits; increasing concurrency/governor can increase
bursts without removing work. Shared proof coordination targets unnecessary
work with lower conceptual risk than a total runner rewrite, but introduces
cache invalidation and ordering risks that must be tested first.

## Acceptance and edge cases

On identical replayed fills/books/manual actions, preserve orders, exact reducer
quantity, risk decisions and deadlines while reducing admitted/wire reads by
reason. Test event-during-read and rate admission, partial fills/cancel races,
duplicate/out-of-order/missing events, reconnect generations, unowned orders,
unknown creates/404s, busts, pagination, transfer cash races,429/503/timeouts,
window rollover, recovery, shutdown/cancellation and rollback. Independent
subscriber cancellation must not cancel another consumer's safety proof.

Compare live matched workload periods by positions, working orders, fills and
expiry churn: total/category REST, fill-observed-to-correct-reducer p50/p95,
cancel-confirm-to-replacement, hard-deadline overruns, uncovered exposure,
unknown-state duration and resting time. Do not accept lower reads achieved by
fewer active markets or slower safety response. Require reversal regressions,
full tests and final edge review before an authorized restart. No numeric saving
or permanent zero429 guarantee is established by this review.

## Official contract check

Reviewed the current [Retail private WebSocket documentation](https://docs.polymarket.us/api-reference/websocket/private),
[rate limits](https://docs.polymarket.us/api-reference/rate-limits) and
[changelog](https://docs.polymarket.us/changelog). Retail documents private
order/position/balance messages; it does not establish a complete resumable
order replay contract sufficient to discard REST recovery. September19
CreateDropCopySubscription/resume-token guidance concerns Institutional gRPC,
not this Retail transport. Public20/s per IP and authenticated20/s per key
documentation does not explain the observed429 threshold. Live status-page
fetch failed during this review, so no new operational venue-health claim.

## Follow-up: narrower best first option

The operator requested deeper comparison before implementation. Prefer a
**serial, decision-cycle proof coordinator with bounded proof receipts** as the
first implementation, not a generic shared TTL cache or an independent account
service. This refines rather than replaces the eventual shared-proof boundary.

Most current callers execute sequentially in the one owner loop. A conventional
single-flight utility that only merges simultaneous requests cannot remove much
of that sequential repetition. Existing read concurrency and cached execution
history already help inside one reconcile. The missing integration is across
compatible consumers, while preserving what each completed proof actually
certifies. A scoped proof must never be relabeled as full entry authority.

### New offline evidence

Ignored diagnostic `.local-tools/probe_account_proof_overlap.py` reuses the
existing fake client/temporary-ledger fixtures. It seeds30 flat entry orders and
one separate15-contract position, advances six fake seconds, invokes a scoped
position proof, the following due global decision proof and a financial refresh.
No venue credentials, production database or real network are used; assertions
confirm unchanged position quantity and zero create/cancel calls.

| Explicitly invoked consumer | Reads in REST | Reads in hybrid |
|---|---:|---:|
| Scoped position proof | orders1 + positions1 + trade history1 | same |
| Following global proof | orders2 + positions1 + trade history1 | same |
| Financial bracket | balances3 + positions1 + cash history1 | same |
| Total | 12 | 12 |

This demonstrates repeat collection across these real methods on a constructed
unchanged scenario. It does not establish live sequence frequency, safe removable
count, optimized speed or overall saving. Fixture pagination/risk configuration
is not the full live cash-history workload. Trade and cash histories use distinct
requirements even when the client method is shared; they cannot be deduplicated
by endpoint name alone. Likewise, three balances and two order lists have
intentional ordering/race functions, not automatically redundant reads.

Re-ran `test_hybrid_rest_comparison`, `test_hybrid_preplan`,
`test_rest_throttle_exit_scheduling`, `test_account_source` in the installed WSL
environment:156 tests passed in30.081s. These validate the existing baseline and
its safeguards, NOT an implemented new coordinator. Expected fake-error logs
and temporary-directory ResourceWarnings were emitted. The separate overlap
probe passed one test covering both modes in2.080s.

### Chosen boundaries

- One serial owner schedules proof requirements and remains the only ledger
  projector/mutation owner. No extra background poller or decision writer.
- Start with no-behavior-change reason/coverage telemetry and bounded receipts
  from already completed execution proofs. Reuse only an explicitly permitted
  result with unchanged relevant revision, scope, generation and freshness.
  Complete global proof may cover a narrower consumer only after its exact
  evidence requirements are checked; scoped proof cannot renew global readiness.
- Urgent reducers/cancels/exits must not wait for a larger combined batch,
  financial reporting or peer-order proofs merely to save calls. If the broader
  consumer cannot share the urgent proof safely, perform the necessary extra
  reads. No intentional batching delay and no longer polling/freshness limit.
- Keep financial brackets separate in Phase1. Later fusion must place position
  reads within their required balance bracket and retain transfer/history,
  midnight/window-boundary and oldest-observation rules. Do not drop capital
  safety because loss arithmetic itself is settled-fill based.
- Invalidate receipts on relevant fills, mutation intents, ambiguous identity,
  pending private observations, source/scope change, expiry and failed proof.
  Recheck after rate admission immediately before POST. Reuse cannot extend
  timestamps, certify an absent order as canceled or retry an unknown create.
- Keep full startup/recovery/shutdown audits, periodic completeness checks and
  REST fallback unchanged initially. Reducing their frequency is not this plan.
- Roll out one independently reversible consumer at a time. Preserve the
  current source modes, no new durable schema, and all market/risk rules.

### Alternative comparison and estimate correction

Generic endpoint cache: rejected first step; age alone misses fills and
cross-read ordering, and sequential callers do not benefit from single-flight
alone. Longer REST intervals: rejected; could slow missing-event detection.
WebSocket-only promotion: rejected without demonstrated complete live source
authority and recovery evidence. More concurrency: already bounded/implemented;
it changes wait distribution rather than removing reads. Whole-engine rewrite:
unnecessary expansion before smaller measured improvements.

The previously communicated10–20% range is an **engineering target only**, not
an evidence-backed forecast. None of the new measurements establishes that
percentage. Phase1 may save less; mutation-heavy periods legitimately invalidate
receipts. Quantify actual savings from instrumented reasons and matched replay,
then require non-worse reducer/exit tails, unknown-state recovery and resting
coverage before deployment. No guarantee against venue429s.

Bot process7592/7606 remained running during review; no new venue exposure query
was needed for the fake-only comparison, so19:26:58Z remains the last exposure
check cited here. No source/configuration change, deployment of trading code,
restart, new collector or implementation authorization. Official Retail private
WebSocket/rate-limit pages were rechecked; no new contract justifies relaxing
account authority. Documentation-only handoff records the refined proposal.
