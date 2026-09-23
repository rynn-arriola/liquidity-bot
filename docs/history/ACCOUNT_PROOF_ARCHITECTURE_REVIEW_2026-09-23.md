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
