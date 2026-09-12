# REST read amplification and early exit scheduling — September 12

## Scope and evidence

The operator authorized the targeted API/exit-delay fixes **without restarting**
the bot. Preserve source selection, shared 4 requests/second, size 10 / 32,
30 flat pending REST entries (positions/reducers excluded), $90 HARD and all
existing trade, cash-adjustment, lifecycle and ownership rules. Prepare source
outside the running WSL checkout; deployment/live acceptance remain pending.

The preceding [live review](ACCOUNT_PERFORMANCE_RELEASE_2026-09-12.md)
observed four actual HTTP 429s in 8,798 attempts through 17:11:59Z: three
balance reads and one cash-history read. A later window ending 17:18:16Z
contained another cash-history 429. Low interval RPS is not proof against an
endpoint/rolling limit. The available telemetry identifies rejected read classes,
not the exact venue quota or a complete causal explanation for every rejection.
Natural exits did reach flat; Kormyk's first IOC acknowledgement was 6.652s
beyond its deadline, and Bulale/Buiedu's at 17:14:27.971397Z was 15.695s late,
followed by flat at 17:14:31.256433Z. Acknowledgement is not execution.

Official [changelog](https://docs.polymarket.us/changelog) and
[rate guidance](https://docs.polymarket.us/trader-guide/rate-limits) were reopened.
The linked live status page failed to load; this is not outage/recovery proof.
No higher rate, alternate credential or automatic source switch is introduced.

## Local mechanisms and prepared repair

1. Periodic balance work used an independent timer even when a decision had
   just completed the same full balance/positions/cash-activity bracket.
   Coalesce only an already-published mark younger than five seconds, using
   its original observation time and next due time; a skip at age four seconds
   leaves one second until due, not another five. ET rollover forces a fresh
   bracket even when the prior day's mark is younger than five seconds.
   Never cache a partial bracket, transfer
   adjustment, failed response or newly labelled timestamp.
2. A failed equity refresh was retried for every subsequent decision owner,
   unlike the existing once-per-sweep execution-proof failure handling.
   Remember that failure for the current sweep only. Later sweeps can recover;
   stale equity continues to remove entry authority, and cancellation/reduction
   remains under the ordinary gates. The unchanged bracket still classifies
   all non-trading cash before publishing a HARD observation.
3. REST position priority was inside the decision sweep, after periodic work
   and full-slate service. An early REST-only phase now services due/uncovered
   positions through the same service/reconciliation/equity/planner/executor
   path first. Unknown intents, owner backoff and exact unexecutable waits do
   not acquire redundant service calls. A position-only guard rechecks flatness
   across awaits so a closed owner cannot acquire an extra entry opportunity.
   WebSocket has no extra exit phase. No parallel mutation owner is added.

These remove proven local amplification/delay paths. They do not guarantee no
future 429, exact-deadline fills, faster venue publication or private-WebSocket
equivalent latency. A request already in flight cannot be made instantaneous.

## Verification and dedicated edge-case audit

Focused fake-venue regression exercises 32 flat owners: one failed bracket per
sweep instead of 32, with stale equity still refusing entry and next-sweep
recovery allowed. Deliberately removing the failure latch, fresh-bracket
coalescing and early-phase call produced three expected failures; fixes restored.
Two additional deliberate reversals reproduced next-due drift and skipped ET
rollover; restored code passed both. Tests cover original data age, clock
reversal, due-boundary equality, flat and
WebSocket no-op paths, flatness during service/proof/equity, stop during service,
multiple positive/negative owners, exact partial quantity, isolated waits,
unknown intents and owner backoff. Existing full-width real fake-executor tests
retain sequential cancel-confirm-three-IOC behavior and positive final flatness.

The initial complete WSL/Python 3.12 review suite passed 1,543 tests in
211.894 seconds. Final cadence/rollover review added one test and retained the
original next-due timestamp; all 11 focused tests pass. The final complete run
executed 1,544 tests in 211.484 seconds: all code tests passed; its only failure
was the documentation budget check, which sampled the in-progress handoff at
121/101/101 lines. Those three indexes were compacted without dropping policy
or evidence. All 14 documentation/new-regression tests then passed in 27.399
seconds against the corrected handoff, and the standalone documentation audit
and final diff check passed. Do not describe the intermediate full run as zero
failures. Production checkout is not used
as the test working directory; the interpreter alone is reused, with all test
clients fake and all writable fixture ledgers isolated.

Authenticated 17:28:07.787310–17:28:21.319819Z observation of the unchanged
live runner: 31 orders = 30 flat entries plus one BUY-10 remaining at 0.42 for
-10 Shyant/Smymyk, zero unresolved intents/unknown orders. Non-atomic reads
are timestamped; they are not live acceptance of the uninstalled repair.

Final review found no additional code defect. At 17:32Z the same runner and
supervisor remained alive with source marker `2f7ba0222`; no tracked production
file changed. This is a prepared release only. GitHub receives the reviewed
patch and handoff; parity must leave the live checkout behind when source
changes require deployment. Do not deploy, restart, switch source or change
the $90 HARD limit under this task's authorization.

Audit boundaries: no schema or saved policy changes; no new endpoint or SDK;
no mutation retry or terminal proof shortcut; no read worker writes; no loss
baseline/ET-day/cap reset; no IOC price, quantity, three-attempt or 30-second
authority change. Restart/rollback adds no migration: old behavior can return
only through the normal reviewed stopped-safe deployment gates, never live file
replacement. Complete source-switch, recovery and shutdown regressions remain
required. Position service may consume existing reads earlier; actual busy-run
API/exit acceptance is deferred until an independently authorized deployment.
