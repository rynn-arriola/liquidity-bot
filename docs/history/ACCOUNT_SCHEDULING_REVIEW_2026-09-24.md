# Account scheduling review — September 24, 2026

## Outcome and scope

Operator requested a deeper double-check before changes. Read-only review of
the deployed `f4f2bdb24` source, bounded existing logs and offline fake-venue
tests. No trading source/configuration change, restart, deployment, live fault
injection or new collector. The recommendation is incremental scheduling and
proof-consumer coordination, not a replacement engine or WebSocket-only mode.

Runner352901 was verified running at approximately18:55Z with size15/32 hybrid;
the loaded marker remains `f4f2bdb24`. Parity passed at18:52Z, including the
private recovery copy. No authenticated venue query was needed for this review:
current exposure is unverified, and the17:34:09Z release snapshot is historical.

## Corrected live evidence

Telemetry windows are selected by their ending timestamps. Durations below are
the sum of complete reported windows, including time before the first endpoint.
Counters measure admitted attempts, not independently captured IP-wide traffic.

| Window endpoints UTC | Seconds | All attempts/s | Reads/s | HTTP429 |
|---|---:|---:|---:|---:|
| Before,15:46:15–16:14:09 | 1762.8 | 3.3072 | 3.0962 | 40 |
| New,17:28:41–18:25:55 | 3530.3 | 3.1244 | 2.8842 | 10 |
| New,18:27:14–18:55:35 | 1779.9 | 2.8153 | 2.6636 | 10 |

Recent throttles: balance5, cash history2, execution history2, positions1.
The first new period had balance1, cash3, execution3, positions3. These are
account endpoints, not public book failures. Lower traffic does not guarantee
zero throttles. The earlier87.5% lower error-rate statement describes only the
first unpaired comparison; it is not the causal effect of this release. Market
activity and risk-derived capacity differed (earlier30 versus startup20 cap).

In the first new period, exact-order reads3302 were29.9% of11030 attempts.
All six account-read categories totaled9220/10182 reads (90.6%). This is a
target surface, not removable traffic. Exact-read reason counters included2030
positioned and1096 strict checks, not thousands of demonstrated duplicates.
In the recent period, exact-order reads fell to819 while total account reads
were4295/4741 (90.6%); changing position activity alone changes the load mix.

First new period proof telemetry:560 global decision checks,109 periodic,
101 scoped checks,84 state misses and17 missing-receipt misses. Recent:
393 global decision,44 periodic,20 scoped,14 state misses,6 missing misses.
Neither period reports a reuse hit. Counts of completed proofs and HTTP reads
are not interchangeable; counts do not prove that these calls were redundant.

All four comparable before-period timed IOC acknowledgments were5.05,5.29,
24.07,34.63s beyond their deadline, not uniformly24–35s. The first new period
contains **nine**, not eight, such acknowledgments at5.83–12.19s; the recent
period has four at4.73–9.52s. These measure acknowledgment relative to the
deadline, not first scheduling delay or final flat confirmation. They cannot
attribute every second to scheduler blocking or establish a matched speedup.
Position observation age on later reprices is position age, not reducer delay.

## Architecture already present

- Public streaming books and hybrid private hints already feed the serial
  trading owner. REST retains complete account authority and missing-event
  recovery. This review found no reason to change source authority.
- `RestFallbackRunner._service_urgent_positions` already services known due or
  uncovered positions before periodic work. Decision sweeps prioritize those
  positions again, including newly discovered fills. A new independent exit
  executor would duplicate ownership and introduce cancel/fill races.
- The shared governor already has safety, entry, background and report lanes.
  Account proofs and reducing mutations share safety capacity; calling a new
  feature an "urgent lane" alone does not remove their dependencies or load.
- Execution receipts already permit one-use, same-sweep, full-to-scoped reuse
  under original freshness, scope, event, mutation and ownership guards.
  Financial proofs are not shared through that mechanism. Zero live hits are
  consistent with the guarded path rejecting changed/missing receipts, not
  proof that widening a cache would be safe.
- Pure position/history reads already overlap; exact workers are bounded and
  their positive results are projected serially. More concurrency alone does
  not remove requests. A scoped check cannot authorize account-wide entries.
- Periodic balance checks already coalesce a sufficiently recent financial
  bracket. The latest release also skips doomed compounds on known endpoint
  cooldowns and defers optional earnings near urgent positions.

## Concrete remaining scheduling gap

The outer loop services urgency, then awaits periodic financial work, then
periodic execution reconciliation, then reaches optional reporting and the
normal priority path. It does not re-run the urgent-position service between
the two mandatory periodic stages. A fill discovered or deadline crossed during
one stage can therefore wait for the next stage before priority is reconsidered.
This is a verified control-flow opportunity, **not** proof that it caused every
observed late acknowledgment. A single slow mandatory request can still delay
the serial owner after checkpoints are added.

Within a decision sweep, execution proof can also be followed by an equity
refresh before the action. Do not simply remove that refresh or classify all
history as reporting: the current financial bracket and execution backfill
protect entry authority and settled-loss accounting. Any separation of exit
requirements from financial requirements needs its own dependency proof.

## Offline verification

On the exact production Python environment, all28 tests in
`test_execution_proof_reuse` and `test_rest_throttle_exit_scheduling` pass.
These verify existing behavior; no new implementation was tested.

The existing fake-only overlap probe was rerun on current code. For both REST
and hybrid, a scoped check, following global check and financial refresh use
3+4+5 reads, respectively, with no order mutation. This demonstrates distinct
consumer paths in a controlled sequence, not12 removable reads. The scoped
result lacks global ownership authority; the financial result requires its own
consistent bracket. The existing scheduling regression verifies urgency before
periodic work but does not cover a newly urgent position between periodic stages.

## Recommended sequence and acceptance (not yet authorized)

1. Add bounded serial urgent checkpoints between completed periodic stages,
   draining pending private evidence through the existing single owner. Test a
   fill/deadline crossing during the preceding read, stop/rebind, stale proof,
   unresolved create/cancel, partial fills and multiple urgent owners. Preserve
   retry budgets and prohibit an exit-only pass from becoming a new entry.
   Do not interrupt a mutation or publish an incomplete snapshot.
2. Extend existing local-only timing attribution at compound-proof boundaries:
   requested/start/completion, consumer, original age, admission wait and
   cancel-terminal-to-reducer/IOC/flat stages. Reuse existing counters and add
   no API calls. Current aggregate logs cannot isolate every delay component.
3. Evaluate execution-proof sharing across adjacent consumers, starting with
   already complete global proof. Extend the existing receipt mechanism rather
   than adding a second authority/cache. Reject stale, changed-generation,
   incomplete, ambiguous, pending-event or post-mutation observations. Never
   promote scoped proof into global authority or delay exits to build a bigger
   account proof. Leave financial brackets/polling unchanged in the first phase.
4. Compare matched fake replay and live workload-normalized windows: endpoint
   reads per fill/cancel/decision, reuse reasons, reducer coverage, acknowledgment
   and flat tails, entry freshness/resting time, ambiguity and429 recovery.
   Require real read savings without worse safety/exit tails before expanding.

Tradeoff: checkpoints may defer routine work while exits are urgent, so bounded
service and unchanged fail-closed freshness are essential. Sharing creates
invalidation complexity and can legitimately save little in active trading.
No percentage saving or zero429 guarantee is established. Reject blanket longer
poll intervals, looser freshness, indiscriminate exact-read removal, automatic
WebSocket promotion and a parallel trading executor as first steps.

Preserve all entry/exit prices and deadlines, size15,32 slots, settled-loss
accounting, four$30 windows and pending-entry tiers. Teardown ownership invariant,
reward-period diagnostics and paused300-market coverage remain separate work.

## Historical root handoff preservation

The following accumulated index openings are preserved for their evidence links,
not as current status. The reviewed state and corrections above supersede them.

### Superseded STATUS.md opening

Current operator status — September24 12:15 ET: same `ab3f7f352` supervisor21730/runner21744 running hybrid size15/32. Authenticated16:15:30Z:30 flat pending entries plus four exact reducers/four positions; ledger zero unresolved/unknown/absent. All current position ages below six minutes; stored game clocks still future. Wider29.38-minute API sample:40 account429s,15 order404s, one earnings timeout,3.307 requests/s. Two timed IOC acknowledgments24.07/34.63s after deadline; those positions now absent. Entry equity proof was stale678s at12:03, recovered by12:08. Functioning now is not latency/rate-limit acceptance. No source/config/order mutation or restart by diagnosis. [Log review](HYBRID_LOG_REVIEW_2026-09-24.md). **Prepared September24 repair:** required-endpoint prechecks skip doomed compound reads; urgent exits defer optional earnings reporting. All2,185 tests pass. Not deployed/restarted; same runner verified16:30Z. Latest authenticated exposure remains16:15:30Z, not current proof. [Fix/handoff](ACCOUNT_COMPOUND_READ_RECOVERY_2026-09-24.md). **Command1 complete September24:** `f4f2bdb24` loaded; supervisor352883/runner352901 started17:27:04Z size15/32 hybrid. Six-minute acceptance: risk loss$12.728/$30 cap20; five windows2.185–3.326rps, one cash-history429 recovered. Authenticated17:34:09Z:20 orders, two positions exactly covered by reducers, zero unresolved/unknown and ledger agrees. [Release](ACCOUNT_COMPOUND_READ_RECOVERY_2026-09-24.md).

### Superseded FINDINGS.md opening

Findings index — September23 extended REST sample:stable2.7785 reads/s versus current2.7897 over1,272.9s; latest10-minute slice2.9849, no demonstrated aggregate reduction. Exact-order reads latest0.8696/s versus stable0.6097. Current eight429s span trade/cash history,balance,positions; public books zero. New logging proves18:19:50 cash_changed_positions -> equity_unknown -> entry cancellations without429; coincident fill observed. Original16:29 trigger remains unproved. GTD/refill issue persists; no strategy/code change. [Evidence](ENTRY_CHURN_DIAGNOSIS_2026-09-23.md). **September23 architecture:** separate global/scoped execution and financial proofs overlap; current reader is not a cross-consumer coordinator. Account reads89.5% of2,195 reads/761.2s; removable share and live speed benefit unproved. [Review/design](ACCOUNT_PROOF_ARCHITECTURE_REVIEW_2026-09-23.md). Follow-up fake scoped/global/financial sequence:12 reads in each mode despite unchanged state; not12 removable reads.156 baseline tests passed. Prefer serial same-cycle proof receipts;10–20% remains an unproved target. **Prepared first phase:** compatible full-to-scoped fake sequence saves three reads in REST/hybrid; all2,162 tests pass and three deliberate reversals fail as expected. Live hit rate/savings unknown; financial/global reads remain mandatory. [Evidence](EXECUTION_PROOF_REUSE_2026-09-23.md). Follow-up timing review: ten timing/admission methods AST-identical;188 regression tests pass with reuse disabled and enabled (376 executions),295 focused rerun passes. No source change; natural live acceptance remains pending. Command1 loaded `ab3f7f352`; seven live windows1.916–3.491rps, two account429s/two order404s recovered. At20:35:18Z venue/ledger29/29, one exact-covered+3 position, zero unresolved/unknown. Active transitions refused reuse; live saving remains unproved. Prior teardown invariant is separate. **September23 22:00 ET reward supply collapse verified:** fresh raw six-symbol incentive response confirms explicit closed/end02:00Z fields for affected day-of and daily-event programs; UTR control remains active. Stored before/after scans and804-record offline replay agree. Daily records are present but closed, not missing.31 installed reward tests pass; venue closure reason/replacement schedule unknown. Same live runner never stopped. [Evidence](REWARD_PROGRAM_CLOSURE_2026-09-23.md). **22:46 ET recovery:** venue replacements ending20260924 are active; normal22:42 scan214 applicable/42 viable, authenticated16 orders/four exact-covered positions. Website still listed old IDs without status/end; no restart or strategy change. Earlier counts are historical. **September24 06:42 ET:** live runner survives official06–08ET maintenance in recovery pause, minute probes return503/stable0/3; current exposure unknown, no stopped/crash claim. [Evidence](MAINTENANCE_STATUS_2026-09-24.md). **08:22 ET update:** APIs recovered/health3of3 at08:01; fresh venue flat/order-free12:22:54Z. Follow-up official08–08:30 maintenance remains verifying/loaded MAINTENANCE, earliest08:45 conditional. Prior06:42 unknown exposure is superseded. [Follow-up](MAINTENANCE_STATUS_2026-09-24.md). **08:41 ET:** official maintenance completed; venue0 orders/0 positions, running RESUME_HOLD until08:45:07.375ET. No automatic trading-resumption claim yet. **09:17 ET:** automatic recovery now trading, authenticated29 entries/zero positions;13:18 ledger zero unresolved/unknown. Later account429s09:09–09:14 recovered in latest three windows; no all-run-clean claim. **September24 noon review:**27 completed API windows/1762.8s show40 account429s and3.307rps; timed IOC ACKs24.07/34.63s late, current affected positions cleared. No causal attribution to hybrid or Cloudflare from these summaries. Current16:15:30Z four exact-covered positions/30 entries and no ledger ambiguity; sustained performance remains unaccepted. [Log review](HYBRID_LOG_REVIEW_2026-09-24.md). **September24 confirmed local inefficiency:** known required-endpoint cooldown was discovered only after exact-order walks/equity brackets; failing-before regressions reproduce this. Prepared no-lease precheck preserves ambiguous-order repair and defers optional earnings near urgent exits.2,185 tests pass; live savings/429 elimination unproved. [Evidence](ACCOUNT_COMPOUND_READ_RECOVERY_2026-09-24.md). **Live acceptance:** `f4f2bdb24` started17:27:04Z. Five windows ran2.185–3.326rps; one cash-history429 recovered. Both natural positions had exact reducers at17:34:09Z, zero ambiguity. This proves safe initial operation, not sustained savings or429 elimination. [Release](ACCOUNT_COMPOUND_READ_RECOVERY_2026-09-24.md).

### Superseded NEXT.md opening

Open work — RUNNING on `ab3f7f352`, auto size15/32 hybrid since20:25:35Z. Command1 gates,2,162 tests,237 hashes and restart passed. At20:35:18Z venue/ledger had29 matching orders, one+3 position with exact SELL3 reducer, zero unresolved/unknown. Continue natural `[execution-proof]` hit/miss, reducer latency and endpoint429 observation; acceptance saw only conservative misses, so no live savings claim. Two account-read429s recovered. Prior-run stream teardown hit `working order has no owned place intent` before eventual authenticated flat/clear proof; investigate separately without changing this release. [Evidence](EXECUTION_PROOF_REUSE_2026-09-23.md). **September23 reward closure follow-up:** venue ended affected programs at22:00 ET; cause/replacement schedule needs venue confirmation. Recommend refining existing-data period-rejection diagnostics (closed/not-started/outside-window/live-only/missing), including misleading `usable_markets.py` wording; no trading filter bypass or new REST needed. Diagnosis completed, implementation not requested. [Evidence](REWARD_PROGRAM_CLOSURE_2026-09-23.md). **22:46 ET update:** replacement programs now visible and normal scan recovered42 viable; closure cause is now a rollover/publication-gap question, not an ongoing four-market shortage. No restart needed for observed recovery; website/API old-ID discrepancy remains. **September24 maintenance:** natural health-gated resume remains pending;08:15ET earliest only if official completion08:00 and existing gates pass. No restart authorized/required by current status check; exposure unavailable during503. [Evidence](MAINTENANCE_STATUS_2026-09-24.md). **08:22 ET supersedes:** account now authenticated flat/clear of venue orders; follow-up maintenance verifying through08:30, earliest08:45 conditional. Observe normal resumption and stale pregame owner replacement; do not bypass guards or infer restart authority. **08:41 ET supersedes:** official maintenance completed; loaded RESUME_HOLD ends08:45:07.375ET, venue flat/order-free. Natural trading resumption remains unverified. **09:17 ET resumption accepted:** fresh29 entries/zero positions, no restart. Keep account429 recurrence and touch-repricing/resting-time follow-up open; separate peer parity SSH stalled and was stopped without touching bot. **September24 noon priority:** investigate balance/positions429 recovery and deadline service ordering:40 throttles/29.38min, equity proof stale678s, two IOC ACKs24–35s late. Preserve source/risk/exits; diagnosis only, no repair/restart authority inferred. Secondary earnings timeout/reducer repricing follow-up. [Log review](HYBRID_LOG_REVIEW_2026-09-24.md). **September24 scoped repair now prepared:** operator authorized implementation; compound-read prechecks and urgent-exit reporting deferral pass all2,185 tests. Await separate deployment/restart authority, then verify manifest/environment plus fresh safety gates and natural recovery/exit/API acceptance. No claim all429 or deadline delays solved. [Handoff](ACCOUNT_COMPOUND_READ_RECOVERY_2026-09-24.md). **Post-release observation:** `f4f2bdb24` is live and initially accepted. Continue natural endpoint-deferral/429 and timed-exit measurement; do not infer sustained savings from six minutes. The pre-existing shutdown stream-teardown invariant reproduced during graceful stop but final authenticated flat/clear proof passed; investigate separately. [Release](ACCOUNT_COMPOUND_READ_RECOVERY_2026-09-24.md).
