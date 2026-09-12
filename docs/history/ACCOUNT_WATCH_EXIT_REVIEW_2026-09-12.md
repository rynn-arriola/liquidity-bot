# September 12 account watch, exit follow-up and serial-owner audit

## Authority and release boundary

The operator requested all pending issues after the whole-bot/readiness review.
Prepare, test and push relevant repairs; the existing no-deploy/no-restart
instruction still controls. The laptop remains the only live owner. No manual
order, position, ledger, source-selection or risk mutation was performed.

Earlier prepared repairs remain included, not replaced: [whole-bot R1–R5](WHOLE_BOT_FIXES_2026-09-12.md)
and [readiness A/B plus the profiled exchange-ID index](READINESS_PERFORMANCE_FIXES_2026-09-12.md).
The isolated 117,626-intent index benchmark was ~2.9 seconds to under 1 ms for
127 identical lookups; it is not an end-to-end/live latency claim.

## Repairs completed in this follow-up

1. Incremental candidate collection now has a checkpoint before each composed
   REST request/page/retry, plus an end-of-scan checkpoint. During REST scans,
   one single-flight, complete position read watches for quantity changes.
   Its immutable result is only a wakeup: it cannot project a fill, prove
   flatness, release capacity or authorize entry/exit. Changed/stale/failed
   observations revoke entry proof and return control to ordinary strict
   reconciliation and position service. Partial candidate results are discarded.
2. In WebSocket mode those checkpoints drain private events in serial order;
   new/changed positions and confirmed position-owner cancellations interrupt
   discovery even before the inventory deadline. Malformed private data revokes
   the gate and requests same-source rebind. There is no REST position watcher
   in WebSocket mode and no automatic source switch.
3. The watcher cannot inherit the catalog callback or its lower-priority lane.
   It uses the existing shared safety admission, one result in flight, and the
   configured 5/1-second intervals after completion. Disabling bulk/polling
   optimization retains the existing five-second cadence. It is cancelled and
   joined on every scan exit. The unchanged full reconciliation still precedes
   any mutation; a watch is not a replacement for order/history/balance proof.
4. History normalization, concurrent exact-order reads and the private candidate
   previously called the ledger's identity resolver inside workers. Its unique
   unresolved-intent adoption branch can write. History/order workers now capture
   immutable identity inputs and resolve them on the serial owner after reads
   join. A failed/cancelled/obsolete account batch cannot adopt an identity.
   Private candidates collect without a resolver; retained events are projected
   by the owner before candidate agreement compares established bindings.
5. Conflicting duplicate position pages no longer silently overwrite a previous
   quantity. Conflicting duplicate fill IDs within one history batch raise the
   same critical immutable-identity failure rather than hiding it before ledger
   application. Exact duplicates and optional trade/outcome enrichment remain.
6. Cache revalidation and the top-up loop no longer downgrade ledger/invariant
   failures to a cache fallback or ordinary retry. Recoverable watch/API failure
   still pauses/retries; positive integrity contradictions remain terminal.
7. Bounded, local `[execution-timing]` JSON records position observation/change/
   flatness, positive CANCEL-intent resolution and acknowledged position actions.
   Fields distinguish observation-to-action, cancel-confirmation-to-replacement
   and available deadline offsets. Initial/restarted positions are labelled
   `position_initial`; these are not exchange-fill delivery measurements.
   Missing/future anchors stay null, identical replays do not reset clocks,
   source changes do not reset episodes, and positive flat observation clears
   them. Timing failures cannot break an actual IOC or become strategy inputs.

## Verification and limits

- Real 32-candidate selector with deterministic one-second fake book responses:
  the old path completed all 32 reads before returning; the watched path aborted
  after one read when the position observation changed. No position projection,
  order mutation or partial selection commit occurred in the watcher. This is
  simulation evidence, not a one-second live fill-to-exit promise.
- Focused collection/source/snapshot/timing runs passed during development.
  Deliberate reversals produced 27 expected failing assertions/subtests across
  watch interruption, source generations, worker ownership, pagination,
  critical escalation and timing. Separate cadence and malformed-private-gate
  reversals each produced one additional expected failure: 29 detections total.
  Every reversal was restored before final verification.
- Final complete suite: **1,525 tests passed in 144.500 seconds**, including
  26 added tests. All 12 changed/new source/test file hashes match the isolated
  tested copy. Earlier intermediate complete runs passed 1,523 and 1,524 tests
  before the final disabled-cadence and malformed-private-gate audit additions.
- Native isolated test checkout uses production dependencies, but no live
  credentials, trading API or writable production ledger. No runtime migration
  or new production process was introduced by testing.

## Dedicated final edge-case audit

- Success/failure/boundaries: empty/unchanged/nonzero/long/short/missing positions,
  duplicate pagination, future/slow observation, zero/full 30-entry scope and
  real 32-candidate selection are covered. No absence is used as cancel proof.
- Timing/races: stop/source-generation/scope changes interrupt the next scan
  checkpoint; cancellation joins the single watcher and every snapshot worker.
  Existing task-held private event ordering is settled before top-up. New
  private fills interrupt even with a distant deadline; malformed events cannot
  refresh stale authority. Final release/entry checks remain unchanged.
- Ownership: no-ID adoption executes only on the owner after the applicable
  complete batch; private candidate events retain positive facts for owner
  projection on promotion or abort. Unknown create is never retried, exact
  terminal proof is still required and immutable conflicts remain critical.
- API/load: no rate, timeout, retry-count or source-selection tuning. The REST
  scan watch adds bounded position reads during a previously blind catalog
  window; it shares 4/s with trading and can consume some catalog throughput.
  Existing safety proof is not weakened to compensate. No full account watch,
  second ledger writer or permanent inactive WebSocket was introduced.
- Recovery/shutdown/restart/rollback: worker cleanup is tested; all earlier
  strict exit/HARD/maintenance/teardown regressions remain. This patch has no
  schema or risk configuration change. Restart resets measurement only, not
  authoritative deadlines or loss state. Old source can read the ledger, but
  rolling back restores the repaired gaps and is not automatically safe.
- Business policy: size 10 / 32, manual sources, max 30 flat pending REST entries
  excluding all position-market orders/reducers/exits, $90 HARD, current ET caps,
  70% entry quality, reward selection, IOC quantities/prices and three-attempt/
  30-second rounds are unchanged. No induced live fill, outage or loss.
- Measurement isolation: bounded 256-market timing maps, no API request, no
  persisted strategy state, null unknowns and an actual IOC regression with a
  failing timing sink. Log coverage is normal owner projection/action service;
  it is not a complete exchange timestamp/acknowledgement audit or proof that
  every emergency/manual close has a timing row.
- Final source/test diff, whitespace and secret-boundary review performed.
  Healthy WebSocket recovery and full-width live latency still need natural
  acceptance after an authorized deployment; no code guarantees liquidity.

## Live evidence, unchanged old runner — not acceptance of this patch

Runner 649372 / supervisor 649355 still use REST size 10 / 32 and loaded marker
`b826ff11e`; WSL HEAD is `f9f9d8c4`. The operator's hold prevents loading this
patch and the already-pushed readiness/index fixes.

The earlier -6.03 `aec-setkameua-morvas-senmyk-2026-09-12` position closed through
the bot's own recovery: owned BUY_LONG fill 6.03 at 0.33 at
**14:27:02.981074Z**, flat release logged 14:27:42Z, authenticated positions empty
at **14:31:03.402713Z**. Earlier 0.26 IOCs at 14:20:49.219, 14:21:03.250 and
14:21:10.912Z were positively EXPIRED with zero fill, then the existing retired
round retried. A later displayed 0.26 offer cannot prove earlier executable
depth. The cause of those unfilled venue outcomes is not established; no price
cap/attempt escalation is justified. Eventual closure is not healthy latency.

The bounded private-wire check at **14:30:53–14:31:38Z** connected with initial
and final Pong but received **zero application frames**, zero decode failures.
Earlier order-only recovery was not reproduced in this probe. REST stays
selected. The [official changelog](https://docs.polymarket.us/changelog) was
reopened; the live status fetch failed, so this does not establish a declared
outage or maintenance end. This external stream limitation cannot be repaired
by pretending an initial balance/position subscription is healthy.

At **15:04:12–15:04:20Z**, authenticated reads found 30 orders: 27 flat pending
orders and three position-market orders. Four positions were present:
`stolud-lopvla` +10 with no order, `zhuole-nebdmy` -7.51, `chlpaq-marcap` +10,
and `varlep-nicmel` -10, each of the latter three with one order. This is a
bounded sequential observation, not an atomic exposure snapshot or flat proof.
The +10 fill occurred at 15:02:54.159381Z and was recorded at 15:04:07.516549Z
(93.36 seconds later), during a 57.6-second candidate scan followed by top-up
commit at 15:05:09Z. No new reducer was recorded for that owner by the 15:04:50Z
ledger check. This directly illustrates the loaded scan blind window; do not
equate the prepared simulation with a live fix. Subsequently at 15:07:31Z the
old runner logged a history API timeout during strategy-state recovery, so
current latency cannot be attributed exclusively to CPU or catalog scheduling.

Final authenticated read at **15:12:37–15:12:41Z** found 26 orders: **24 flat
pending entries and two position-market orders**, with the same four positions.
Both `stolud-lopvla` +10 and `zhuole-nebdmy` -7.51 had no working order in that
read. Logs continued execution-state recovery/quarantine on the unchanged old
runner. The operator was asked whether to authorize the safe release workflow;
no answer/approval is inferred. This unresolved live state must be prominent in
the handoff, not hidden behind the passing offline suite.

## Handoff still required

Obtain explicit deployment/restart authority, then normal stop/flat/ownership,
parity, exact reviewed-file/deployed-suite and startup gates. Repeat authenticated
exposure checks and observe natural reducer/IOC recovery and entry cap. Compare
like-for-like full-width transition timing/load, retaining missing-data coverage;
no near-WebSocket-speed or better-P&L claim is established offline. Continue
external WebSocket/support follow-up without changing source automatically.
