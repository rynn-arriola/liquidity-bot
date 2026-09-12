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

## Authorized force-stop, deployment and restart (18:00Z)

The operator subsequently explicitly requested force-stop and restart, saying
they would manually cancel orders and close positions. This overrides the prior
no-restart decision for this release only; it creates no standing force policy.
The exact old supervisor 1216388 and runner 1216411 were verified, then stopped
through the supported force script with zero grace. Both processes were absent.
No graceful final-flat record was obtained and none is claimed.

Authenticated 17:53:37Z proof still found two orders and zero positions, so
restart was withheld. At 17:54:26Z the venue was order-free and flat, but the
ledger retained a working order and acknowledged PLACE. Under the exclusive
stopped-runner lease, the ordinary REST reconciliation path confirmed both
exact exchange orders terminal, matching client/market ownership. It cleared
the local records with zero venue mutations and no direct SQL correction.
The 17:55:30Z and final 18:00:15.862025Z authenticated gates proved zero venue
orders/positions, working ledger orders/positions, unresolved intents and
unknown orders.

Clean production checkout fast-forwarded to reviewed/pushed `a8888b5e5`.
All 186 normalized deployed source/test/script/web/config hashes matched;
the complete exact native checkout suite passed all 1,544 tests in 141.609s.
Only afterward was DEPLOYED_COMMIT updated. Parity matched Windows, GitHub,
WSL and marker; the stopped-ledger backup was verified at 378,671,104 bytes,
SHA-256 prefix `86dcefa2e070`. The offline desktop remains explicitly pending.

The fresh gate preceded `start_commissioning.sh auto 10 32 rest`. Supervisor
1336728 and runner 1336746 started with unchanged size/slots/manual REST,
$90 HARD and max 30 flat pending entries excluding positioned-market orders.
The first selection scan at 18:01:20Z completed in 51.5 seconds and found
35 viable markets. Live acceptance follows below; startup is not evidence of
sustained API capacity, natural exit timing or WebSocket-equivalent speed.

### Bounded live acceptance

At 18:02:01Z one market's unprovable pregame state entered normal local
recovery; the runner continued and began quoting at 18:02:14Z. First two
completed API windows through 18:04:22Z: 735 attempts, 11 `order_retrieve@404`
failures, zero rate limits/cooldown, maximum admission wait 0.720s. A startup
response took 18.871s; this is not a claim that every request is fast.

A natural +10 Volana/Shevik fill was observed at 18:04:14.562227Z and its
SELL reducer acknowledged at 18:04:14.671428Z, 0.109201s after observation.
This measures observed-position-to-acknowledgement, not venue-fill detection.
Normal cancel-confirm/repricing followed. The non-atomic 18:04:12-18:04:19Z
snapshot crossed this transition and showed two pending/one unknown record;
none was manually cleared. The 18:05:10.780870-18:05:20.515111Z authenticated
snapshot showed 30 orders: 29 flat pending entries plus one SELL-10 remaining
at 0.51 for the +10 position, zero unresolved intents and zero unknown orders.
The exit was working, not yet filled; natural hard-deadline/flat acceptance is
not established by this five-minute startup observation. No induced trade,
source switch, risk change or second restart was performed.
