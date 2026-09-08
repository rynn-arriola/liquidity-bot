# Persistent recovery and daily HARD evacuation — September 7–8, 2026

## Authorization and live state

The operator approved the [recovery audit recommendations](RECOVERY_STOP_AUDIT_2026-09-07.md),
then required $90 HARD to pause rather than shut down, cancel pending orders,
properly exit every position and resume the next ET day. The existing provisional
open-position cohort confirmation is retained, as stated during implementation.
Once confirmed, evacuation is immediate, not the ordinary 900-second wait.
The latest instruction explicitly forbids restart until requested. This task
prepares, verifies and pushes source only; no deployment or live mutation.

At **September 8 04:09:33.655036Z / 00:09 ET**, one bounded authenticated gate
using the existing opportunistic report lane found **30 orders across 22 markets,
zero positions, zero unknown orders and zero unresolved intents**, matching 30
local working rows. Runner **816489**, supervisor **816479**, remain the same
size-10/32 processes started September 7 11:22:45 ET. Loaded marker remains
`b089b3a6e`. This is point-in-time proof, not a promise of later exposure.

## Changes

- Additive `execution_hard_stop_holds` records the confirmed breached meter day
  and release time. Persist before cancellation/evacuation. Same-day equity
  recovery or restart cannot clear it. A breach confirmed after midnight belongs
  to its original baseline day; normal rollover may then proceed without
  forgiving the unfinished evacuation.
- Confirmed HARD blocks at both frame and action-driver entry gates, wakes
  owners, cancels pending entries and sends held positions through the existing
  sequential cancel-confirm-exact-IOC path. Like scheduled evacuation, it is an
  immediate flatten deadline with displayed-depth fallback, not an early-exit
  preference subject to the $8 waiting cap. Unpriceable/closed/unknown state
  still cannot authorize an unsafe order or prove flat.
- Release requires a later ET date, current adjusted baseline/cash proof,
  eligible fresh risk, connected/reconciled account state, a post-hold current-day
  reconciliation followed by adjusted equity, and zero positions, working/absent
  orders or unresolved intents. Manual, scheduled/UI maintenance, quality,
  pending-order and other entry gates remain independently authoritative.
  The newer equity snapshot must itself contain no position: a late fill seen
  after the reconciliation cannot be ignored merely because the ledger is flat.
- Restart with a persisted HARD hold and identified owned unfinished state uses
  fresh-private-state `HARD_RECOVERY`, not bounded recovery-only shutdown. All
  existing scope/ownership and delivered-startup-alert preflight conditions
  still apply before this path can later resume entry.
- Startup does not publish a raw unadjusted equity sample. If cash classification
  fails after a withdrawal, recovery cannot persist a false trading-loss latch.
- Ten retirement rounds make only an empty/order-free/ambiguity-free owner
  dormant. Held or pending-order owners keep their streams, evidence, reserved
  slot/exposure and slow 60-second exit rounds. Rate-budget reset never clears
  the pending IOC marker; terminal reconciliation authorizes another exact
  remaining-quantity attempt. Per-round three-attempt/30-second caps remain.
- Typed 429/admission deferral shares persistent transport/5xx recovery without
  being described as maintenance evidence. Twelve transient reconnect failures,
  including mixed 503/429, enter three-complete-read health hold, then retry the
  existing stream/quarantine/reconciliation gates. Unknown schema/invariant
  failures do not become an outage. Temporary startup availability has no
  lifetime attempt ceiling; proof/configuration refusals remain refusals.
- More than configured capacity in recent named stale-book/execution-state
  retirements pauses entry and selection for 30 minutes, then requires fresh
  post-cooldown account/stream proof. Owner quarantine remains intact; normal
  selection must independently validate replacements. Unknown fault cascades
  still terminate trading.
- Refusal/non-flat/integrity exits and three fast crashes keep a lightweight
  supervisor in `ATTENTION_REQUIRED`: one alert, local heartbeat, no blind
  relaunch. Manual clean stop ends the supervisor. This is visibility, not
  automatic repair of credentials, ownership, code corruption or venue bugs.
  Recovery logs show reason, health streak, last successful read and next probe.

## Verification

New offline coverage includes exact long/short and tiny residual evacuation,
builder-before-reducer cancellation, no IOC before known state, partial-fill
remaining quantity, preservation of pending IOC across re-arm, same-day restart
with recovered equity, next-day stale/nonflat/unknown refusal, retained manual
pause, owned unfinished startup, failed raw-cash proof, 75 temporary startup
failures followed by success, mixed throttles beyond the short budget, a virtual
three-hour outage ending only at explicit manual stop, 32-slot cascade scope,
post-cooldown proof and real supervisor-shell execution using inert fixtures.

Seventeen new focused tests pass. Existing midnight confirmation regressions
now assert a latch on the original breached day instead of terminal exit.
The complete pinned Python 3.12 WSL review suite passes **1,190 tests**.
No test invokes a live trader or induces a venue failure.

Deliberately reversing persistence, midnight proof, exit abandonment, transient
classification, startup waiting, cascade pause, immediate evacuation and
supervisor attention produced 16 failing subcases and two expected errors.
Separate reversals of IOC-marker preservation and held-startup mode produced
five failing subcases, proving they were not masked by the evacuation reversal.
Restoring the raw startup equity sample independently failed its withdrawal
regression. Removing the newer-position-snapshot guard independently failed
the late-fill release regression. All changes were restored before the final
full suite.

## Dedicated final edge review

Reviewed success/failure, empty/full ownership, equality at HARD, long/short/tiny
positions, partial IOC completion, cancel/entry races, stale/unknown proof,
cross-midnight confirmation, restart, manual stop, scheduled maintenance,
storage failure, API load and capacity. Durable write failure cannot release
entry; previous ambiguous mutations remain unresolved; existing authority never
expands beyond owned configured scope. No quantity, quality threshold, reward
priority, pending-order cap, money-loss amount or ordinary 900-second timer
changed. Early evacuation is specifically the newly authorized confirmed-HARD
behavior. Recovery does not fabricate flatness, reset losses or retry unknown
creates. Long recovery cannot guarantee liquidity or API availability.

Remaining limits: arbitrary integrity faults need operator attention; unavailable
venue mutations cannot be completed while unavailable; inactive/empty books
retain their existing monitored wait. An alive supervisor alone never means
orders or positions are actively managed. Natural outage/HARD/midnight behavior
is **not live-accepted** until deployment and subsequent unforced observation.

## Handoff

Wait for explicit deployment/restart authorization. Use the normal single-owner
graceful-stop, flat/known-state proof, reviewed deploy, hash/native-suite and fresh
start gates. Do not deploy source under the current process. Old rollback code
does not honor the new durable hold table: rollback requires explicit risk-policy
review, never merely a git reset. Preserve the additive ledger history and use
online backups. Remote backup-PC parity remains pending while offline.
