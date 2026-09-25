# September25 overnight fix check

Read-only check around10:47–10:53Z (06:47–06:53ET). No trading code,
configuration, orders or process were changed. Supervisor37031/runner37045
remained alive on release`a4703cbe8`, size15/32 hybrid, after starting03:33:38Z.
Parity confirmed clean Windows/GitHub/WSL tracked state and the loaded marker;
the private online ledger recovery copy was verified. Peer checks were skipped.

## Current maintenance state

Official Statuspage summary JSON identifies production maintenance
`gtdr2zbjsld4`, September25 06:00–08:00ET, still in progress at this check.
The bot logged scheduled evacuation/new-entry pause at09:30:00Z (05:30ET),
then maintenance at10:00Z. It did not crash or start this pause because of429.
Positions reads started returning503 by10:20:52Z. Public-stream recovery
entered its persistent health hold at10:27:16Z and open-order health probes
continued roughly once a minute, failing503. A separate bounded authenticated
open-orders check also returned503; current venue orders/positions are unknown.
Do not substitute the local ledger for fresh exposure proof or promise08:15
resumption before official completion, configured buffer and healthy account
proof. No restart is indicated merely to bypass maintenance.

Sources checked: [changelog](https://docs.polymarket.us/changelog) (latest
entry September21) and [official status summary](https://status.polymarketexchange.com/api/v2/summary.json).
The web reader could not load Statuspage; a direct HTTPS GET retrieved it.

## Overnight outcomes

- Completed API-budget windows from this run:55,787 attempts over24,575.9s,
  2.270 attempts/s, zero429,52,667 reads and3,120 mutations. This includes
  quieter/maintenance-held time and is not a workload-matched improvement
  against the first35-minute3.026/s sample.
- Failure categories:131 individual-order404,287 individual-order unknown,
  12 balance unknown,12 open-orders unknown,4 earnings unknown,1 event-contract
  batch unknown,7 positions503,1 positions504 and5 open-orders503. Counters
  stop at completed telemetry windows and exclude later recovery probes.
  Unknown category is not evidence of429; the287 failures need classification.
- Quality cancellations33. Twenty-three subsequent same-side entry returns
  were observed, all at least75s after their preceding quality cancellation;
  none violated the60s minimum. This supports suppression of rapid re-entry,
  but elapsed time alone does not prove every second of book health.
- Touch-reprice cancellations96. No matched control or suppressed-dwell count
  establishes the10s dwell's resting-time improvement yet.
- Twenty-six timed IOC acknowledgements occurred1.799–8.416s after logged
  deadlines, median4.999s. An acknowledgement is not proof of final closure.
  The earlier24.25s recovered passive-reducer gap and63.1s refill delay remain
  open latency follow-ups; latest lifecycle refill was44.2s at09:06Z.
- A separate overnight bound-stream/order-read-backoff recovery began05:50:10Z;
  three successful account health probes reached the gate at05:52:12Z, though
  that attempt still encountered local order-read backoff. Later trading
  actions prove eventual continuation without a process restart. This is
  separate from the scheduled06:00ET maintenance and merits investigation.
- Risk cap correctly stepped to25 at00:46ET when matched-fill window loss
  reached$6.9022, then reset to30/loss0 at06:00ET. Hard limit stayed$30;
  open orders/positions were explicitly excluded. These are window values,
  not a claim about the complete day's trading P&L.

## Remaining work

After maintenance, verify authenticated exposure and natural recovery; classify
the unknown order-read failures/recovery; retain the reducer/refill latency and
matched touch-churn follow-ups. No business-rule changes are authorized by this
status request. Use the existing logs rather than adding more API polling.

## September25 13:08Z recheck and priorities

Same supervisor37031/runner37045, no restart. Official status summary now shows
all systems operational and no active maintenance/incidents. Runner account
health probes proved zero orders/positions at11:48–11:50Z. Official completion
set resume eligibility12:15:13.896Z; entry pause cleared12:15:41.881Z, after the
configured buffer and health gate. Automatic maintenance recovery worked.

Authenticated13:08:49Z sample:34 venue orders, comprising30 flat entries and
four exact reducers for four positions (+15,+15,-13.47,+8); zero unresolved
intents and unknown orders. These are sequential live reads, not an atomic
snapshot: local working count32 differed from the earlier venue order read.
The preceding13:07:55Z sample saw25 entries/three position orders/four positions
because Mosvas/Tebyar filled during the read sequence. Its reducer was
acknowledged13:07:48.915Z and later partial-filled; fresh13:08 proof shows exact
BUY13.47 remaining. Do not call that first sequential sample a sustained
uncovered-position defect. Transient local quantity -15→-30→-15 was corrected
before the15-contract reducer decision; examine fill/snapshot ordering before
classifying that as a true doubled venue position.

Completed post-resumption API windows through13:08:08Z:9,998 attempts across
3,180.8s,3.143/s,zero429;65 failures were individual-order404.396 touch-reprice
cancellations,68 quality cancellations and52 quality re-entries (minimum64s,
none<60s). This is much more touch activity than the prior overnight interval,
but different books/workloads prevent causal attribution to the10s dwell.
Latest lifecycle refill53.2s; decision p95 in latest two windows16.99/23.80s.
Do not compare the active3.143/s directly to the maintenance-inclusive2.270/s
as proof of regression. Hybrid is receiving private order notifications and
REST remains complete authority; proof reuse logs show real skipped reads.

Recommended order of work, diagnosis only unless separately authorized:

1. Position safety/timing: recover exact reducer-gap causes, final closure
   timing and partial-fill/snapshot sequencing without weakening proof.
2. Entry repricing churn: trace396 cancellations against actual book/tick/side
   changes, distinguishing required adverse moves from avoidable replacements.
   This is the clearest current REST/resting-time optimization candidate.
3. Overnight recovery: classify287 unknown-status order-read failures and the
   public-book startup/backoff episode; do not mislabel them as429.
4. Refill scheduling:53–63s delays still warrant tracing after position and
   churn work. Keep300 coverage and strategy changes deferred.

No trading source/config changes, order mutations or restart for this recheck.
