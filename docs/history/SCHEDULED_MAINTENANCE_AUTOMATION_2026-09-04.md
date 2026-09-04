# Scheduled production-maintenance automation — 2026-09-04

## Operator decision

The bot should not require a laptop scheduler or manual trigger for announced
Polymarket US maintenance. It should stop adding risk, close positions before
Retail requests can become 503, remain dormant through the window and resume
one hour late only when the venue is healthy. The selected evacuation lead is
30 minutes.

## Official contract observed

The public Statuspage v2 endpoints expose upcoming, active, history and exact
scheduled-maintenance JSON without credentials. Only rows containing both
component ID `3xmdtkg3r8y5` and name `[Production] Polymarket US Exchange`
apply. Relevant fields are `id`, `status`, `scheduled_for`, `scheduled_until`,
`updated_at`, `components` and `incident_updates`.

Historical transitions are scheduled → in_progress → completed. The first
completed incident update is the positive completion clock. `started_at` has
matched notice creation and is not an actual-start clock. The 2026-09-02
production window completed early, while an earlier 2026-08-21 notice arrived
only six minutes before its scheduled start. Therefore a poller can act as soon
as official evidence exists but cannot promise the full selected lead when the
venue announces late.

At review time the exact production feed listed:

- `srky0lxrfrqr`: 2026-09-05 02:00–06:00 ET.
- `h4zlw8spx0sw`: 2026-09-06 02:00–06:00 ET.

## Implementation

`maintenance_schedule.py` performs bounded host-allowlisted public reads.
Startup reads bounded history once; minute polls read upcoming and active rows.
When a known row disappears, its exact endpoint must positively return it, so
absence never becomes completion. Verified windows are atomically written with
owner-only permissions. A cached boundary is evaluated from local time even
when the next feed read fails.

At the evacuation boundary, flat entry orders become undesired through the
existing entry-pause reconciler. A held position receives the existing hard
exit timing override: cancel building-side orders first, prove each exact order
terminal, then issue a bounded exact-position IOC using displayed depth. The
maintenance rule bypasses the early-loss preference because retaining exposure
into a known API outage contradicts the requested flatten, but it does not
weaken ownership, ambiguity, intent, depth, quantity or retry bounds.

The process stays alive. New risk remains blocked until one hour after the
later of the scheduled end or positive official completion. It then reuses the
existing three-cycle authenticated open-order/position/balance health gate;
normal stream freshness, reconciliation, public-UI participation and account
risk gates remain independently required.

## Verification

The focused maintenance/reconciler/live-runner group passed 501 tests. The
complete production-matched WSL suite passed 1,115 tests. A deliberate reversal
that changed the evacuation lead from 30 minutes to zero failed both the pure
boundary and cached-boundary runner regressions, then both passed after the
30-minute value was restored. Additional coverage proves malformed production
timestamps fail closed, an unavailable first read blocks entries without
inventing a forced exit, cached boundaries survive later feed failure and a
stale pre-boundary feed cannot start the post-hold health gate.

## Deployment and startup acceptance

Command 1 was authorized. The old runner stopped with two parked
short positions and two matching passive reducers; both filled naturally. At
2026-09-04 20:45:34Z an authenticated gate proved zero venue orders and zero
positions, while the ledger proved zero unresolved intents and zero
`UNKNOWN_ABSENT` or working rows. Deployment and restart could therefore
continue without a forced exit or safety-gate exception.

GitHub, the WSL checkout and `DEPLOYED_COMMIT` advanced to exact commit
`a348dbc1f`; the production checkout passed all 1,115 tests. A bare launcher
invocation initially selected its documented 20-slot default, so that process
was stopped flat before acceptance. The corrected explicit size-10/32 start
launched supervisor `3232223` and runner `3232232`. It logged the official feed
`HEALTHY` in `NORMAL`, wrote a mode-600 cache containing both announced IDs and
produced 29 authenticated orders across 18 markets with zero positions. The
first 140.3-second API window had 266 attempts and zero failures, 429s or
cooldown. One transient `UNKNOWN_ABSENT` during live repricing resolved through
ordinary positive proof; a later exact ledger sample had zero unresolved and
zero unknown rows.

Natural acceptance must still prove entry withdrawal, authenticated flatness
before the outage, no entries during the hold and health-gated recovery. No
fill, outage or maintenance condition should be induced.
