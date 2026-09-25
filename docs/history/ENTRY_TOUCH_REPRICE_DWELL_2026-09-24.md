# Entry touch-reprice dwell — September 24, 2026

## Outcome

The reviewed quality-recovery and touch-churn repairs were pushed at
`ecea13b2f9eb249d14630026a810b2794a173cfb`, documented in release head
`a4703cbe8f42f70fb4b5bac2dde93628e4c28e20`, and loaded by Command1 on the
laptop WSL runtime. Supervisor `37031` / runner `37045` started September 25 at
03:33:38Z in size 15 / 32-slot hybrid mode.

## Incident and scope

Live logs showed one flat entry offer repeatedly cancel and return at the same
price while the touch oscillated by one tick. On
`aec-setkameua-khavit-pontym-2026-09-25`, the bot placed at 0.56, cancelled,
returned to 0.56, then repeated the cycle at intervals of only a few seconds.
The existing priority replan was working; the defect was that every transient
one-tick touch change immediately destroyed resting time.

This release does not widen the desired quote or change market ranking,
selection, risk, size, capacity, entry eligibility, exit timing, or REST/private
authority. It adds a ten-second continuous confirmation only when an existing
flat entry is exactly one tick **less aggressive** than the new desired touch.
If the touch returns, the clock resets and the order remains. If the move
persists, the ordinary cancel/reprice occurs at the deadline.

The dwell never applies to:

- reducers, IOC exits, or any positioned market;
- a resting entry that became more aggressive;
- a move larger than one tick;
- a quantity mismatch, duplicate, ineligible side, paused account, unknown
  execution state, or stale/invalid book.

The runner now schedules the earliest reward-eligibility or touch-confirmation
deadline, so a quiet stream cannot leave a confirmed stale quote indefinitely.
The earlier 60-second recovery after a 70%-quality cancellation is retained.

## Verification

- Focused reconciler, quality and reducer-continuity coverage passed.
- A regression reproduces the one-tick less-aggressive oscillation, proves the
  ten-second boundary, and proves that a returning touch starts a fresh clock.
- Edge coverage proves immediate handling of more-aggressive, multi-tick and
  wrong-size orders and unchanged position/reducer behavior.
- The pre-deployment complete production-environment suite passed 2,263 tests;
  the final scheduler integration regression passed separately.
- The exact staged WSL checkout passed all 2,266 tests in 250.108 seconds.
- All 184 normalized Python source/test files match between Windows and WSL.
- WSL tracked files are clean; only the existing untracked `DEPLOYED_COMMIT`
  marker and editable-install metadata remain.

## Activation and live acceptance

The old runner stopped cleanly with terminal `flat=true`. Authenticated gates
at 03:31:13Z and 03:33:38Z each proved zero venue orders, positions, unresolved
intents, unknown orders, working ledger orders and ledger positions. Windows,
GitHub and WSL were all at `a4703cbe8`; the inactive peer was offline and left
pending, while parity created a hash-verified private ledger recovery copy.

The fresh scan found 17 viable markets and the bot built to 28 venue orders.
After natural fills, authenticated 03:41:18Z state showed 24 orders across 16
markets and four positions, each covered by an exact reducer. Unresolved and
unknown counts were zero and the ledger agreed. The current risk row remained
loss $0 / hard $30 / cap 30, based on matched fills and excluding open orders
and positions. Five completed API windows ran from 2.309 to 3.359 requests per
second with zero 429s or cooldown; the only failures were normal transient
individual-order 404 publication reads. No filter, ranking, size, risk, entry,
exit or account-authority rule changed. Natural touch-cycle and quality-recovery
comparison remains pending; do not induce book movement, fills, or faults.
