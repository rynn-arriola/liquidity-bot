# End-to-end review repairs — September 8, 2026

## Authorization and safe stop

After the source-only review of `3bc2c69ea`, the operator requested: **Fix all
then restart the bot.** This authorizes the four reviewed safety fixes and the
previously prepared persistent recovery / confirmed-HARD evacuation release.
It does not change strategy, size 10 / 32, the enabled 70% entry-quality trial,
reward priority, $90 HARD, entry-cap tiers, or normal inventory-exit timing.

Supervisor 816479 was stopped before SIGTERM to runner 816489. Its final record
at **11:03:04.587596Z** states `operator_sigterm`, `flat=true`. Both processes
were absent. Authenticated post-stop proof at **11:03:52.567618Z** found zero
orders, positions, unresolved intents, unknown orders and local working rows.
Parity's stopped-runtime online ledger backup at **11:03:17.348725Z** passed
integrity and copied size/hash verification: 297,426,944 bytes, SHA-256 prefix
`022fb47b320b`. It remains a private recovery copy, never active authority.
Windows/GitHub matched `3bc2c69ea`; clean WSL HEAD `4aab5bc83` required deployment.
The backup PC was offline/DNS-unavailable and remains pending, not overwritten.

## Review findings and implementation

1. **Provisional HARD state was lost on restart.** With the actual $90 setting,
   a $94 marked loss became provisional; recovery to $89 while the original
   position remained open still blocked the original runner, but a second
   managed-position startup authorized new-risk frames. An additive singleton
   table now preserves original risk date, breach time and the position cohort.
   Restart loads it before baseline adoption; midnight restores the breached
   baseline instead of creating a forgiving new one. Missing baseline fails
   closed. Cohort additions are unioned durably; post-close recovered evidence
   clears it, and confirmation atomically replaces it with the durable daily
   hold. Current account proof, not stored positions alone, remains authority.
2. **Discord delivery delayed safety actions.** The actual run loop awaited the
   HARD announcement ahead of its cancel sweep. A runtime-only dispatcher now
   queues notices without waiting for HTTP; startup still requires actual
   delivery. One worker prioritizes critical messages, retries them at most
   three times, snapshots report payloads, and bounds queued work at 128 plus
   one in flight. Critical work can evict queued noncritical work. Overflow,
   transport failure and undelivered shutdown work are logged without secret
   text. Shutdown safety work runs before a maximum two-second queue drain.
   This bounds memory and trading delay; it cannot guarantee Discord delivery
   during outage or process termination, and never calls a queued notice sent.
3. **Uncertain exposure disappeared from accounting.** Unknown-basis shorts
   previously counted as zero worst loss. Both signs now reserve full unit
   loss. UNKNOWN_ABSENT orders remain financial and flat-entry-count
   reservations until terminal proof. Unmatched unresolved PLACE intents
   reserve possible remaining notional, subtracting known fills and avoiding
   duplicate row-backed reservations. Position-market orders still do not
   consume entry-order slots; exits remain available. Absent-only owners stay
   in shutdown/risk scope. Missing owned PLACE provenance fails closed rather
   than guessing which outcome an order represents.
4. **IOC final boundary omitted direction validation.** The driver now rejects
   a bid against a long or an offer against a short before executor submission,
   in addition to exact quantity and terminal-cancellation checks. Current
   normal planning already selected the correct direction: this closes a final
   safety defense, not a proven history of wrong-side venue orders.

These reproductions establish code defects, not the cause of historical trading
losses or an estimate of future reward benefit.

## Verification and dedicated edge-case audit

Twelve new regressions cover real-run-loop notification ordering, both inventory
signs, unresolved PLACE, absent/cancel deduplication, terminal release, held-owner
entry-cap exclusion, recovered-mark managed startup, positive post-close release,
original baseline restoration across midnight, cohort union/atomic confirmation,
missing baseline refusal, correct and incorrect IOC sides, queue saturation and
priority, immutable report capture, transport retries, and bounded shutdown.

Temporarily reverting the runner and IOC fixes produced **nine expected failures
in twelve tests**. Restoring them passed all twelve. Existing scoped-recovery
fixtures required durable owned PLACE provenance; these fixtures were repaired,
not the ownership checks weakened. The three previously failing scoped recovery
tests passed. The complete final pinned Python 3.12 review suite passed **1,202
tests in 118.701 seconds**. Deployment verification is recorded below.

Final review covers success/failure, empty/full and timing boundaries, restart,
ET rollover, partial and unknown exposure, cancellation races, shutdown, scoped
ownership, notification load and rollback. No REST request or subscription is
added. Exposure reservations are conservative under ambiguity and may reduce
entry availability temporarily. Queue saturation cannot block trading or grow
memory without bound; critical delivery failures remain visible in local logs.
Persistent state writes precede entry-release decisions; confirmation/cleanup
is atomic. No recovery code fabricates flatness or retries an unknown create.
The broader persistent-recovery/confirmed-HARD change set retains its separate
[dedicated audit and reversal evidence](PERSISTENT_RECOVERY_2026-09-08.md).

Rollback to older executable code would ignore the new provisional/confirmed
hold tables and is therefore not an automatic or policy-neutral rollback.
Retain the database/history and require explicit risk review and safe stop.

## Deployment and natural acceptance

Pending final complete suite, push, normalized server hashes, native complete
suite, repeated fresh start gate and size-10/32 restart acceptance. Do not induce
losses, fills, maintenance or Discord failures to test the live bot. Successful
initial quoting is not natural acceptance of future outage/HARD/midnight paths.
