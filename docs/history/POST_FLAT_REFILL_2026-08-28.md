# Post-flat mandatory slot refill — 2026-08-28

## Incident evidence

At 21:09Z the authenticated venue showed 24 resting orders across 14 markets,
while recent selection scans showed 29 viable markets and later rose to 34.
The low raw count was therefore not a general supply shortage. One market had
retired at 21:00:22Z with a 10-contract position after its hard-exit retry
window; other bound owners were expired, not open, empty-book or economically
idle. The exit market positively released flat at 21:08:53Z.

The near-deadline selection guard behaved correctly while inventory existed:
the measured full scan took 32-45 seconds and may not interrupt the pre-kickoff
flatten window. The availability defect began only after flat proof. The run
loop still finished its ordinary serial 20-market service path before checking
incremental top-up, so `[top-up-plan]` did not appear until 21:11:17Z. The
transaction released five owners, kept fifteen, added five and committed
`active=20` at 21:11:37Z. New replacements began placing at 21:13:07Z. This
separates unavoidable exit-safety delay from avoidable post-flat scheduler
delay.

## Repair

Both the normal private projector and complete REST position reconciliation
compare the pre-apply and post-apply near-deadline position state. A transition
from at least one urgent position to none requests one immediate top-up only
when mandatory capacity is empty, expired, positively closed or positively
reward-ended. If another urgent position remains, the request is not created.

The request yields only the ordinary full-slate service sweep. Explicit
priority cancel recovery still runs, the top-up's near-deadline check remains,
and all existing complete-account, strict release-owner, fresh closed-metadata,
scope, stream and post-bind proofs remain unchanged. A request whose evidence
changes cannot preempt. Completion clears only the request it consumed, so a
new position transition discovered by the socket-handoff REST snapshot is not
erased.

A full-universe selection no older than 120 seconds can seed replacement
candidates. It is never trusted directly. The selector performs one bulk market
read, one batched incentive read and a fresh full-book read for each bounded
candidate, then reapplies structure, six-hour horizon, program, spread, target,
price and reward viability gates. The runner overfetches at most twice the
needed count, never above 20 candidates. Stale, failed or insufficient cache
validation falls back to the unchanged bounded full selector. There is no
background poll and no new mutation path. Optional economic-idle eviction stays
on the operator-approved 900-second cadence.

## Operator detection

Search the production log for these exact fields:

- `[top-up-request] ... reason=post_flat underfilled_slots=N`
- `[top-up-cache] ... outcome=validated|fallback`
- `[top-up-plan] ... trigger=post_flat selection_source=...`
- `[top-up] ... committed=yes ... active=20`
- `post_flat_wait_seconds`
- `post_flat_underfilled_slot_minutes`

The first timing value measures detection-to-plan/commit latency. Slot-minutes
multiply that time by the mandatory missing-slot count, measuring avoidable
reward-capacity loss after flat proof. Neither metric is trading authority.

## Verification and operating state

Sixty-seven focused selector/top-up tests pass. Edge coverage includes another
urgent position remaining, explicit priority service, early economic-idle
refusal, fresh cache validation, stale-cache full-scan fallback and preserving
a newer request. Deliberately removing the post-flat authorization made
`test_final_urgent_position_flat_requests_immediate_top_up` fail; restoring it
passed. The complete local suite passed 766 tests.

At 21:51Z authenticated venue proof showed zero positions and orders. The old
runner stopped gracefully and its 29 stale `UNKNOWN_ABSENT` rows reconciled to
zero. Commit `2ece3c3` matched production hashes and passed all 766 server tests
before the authorized size-10 restart. At 22:00Z runner PID 51989 had 20 bound,
18 quoting, 34 resting orders, zero positions/absent/unresolved rows and no
new-run recovery, quarantine or risk stop. Natural post-flat acceptance still
requires a real qualifying close; never induce a position merely to test it.
