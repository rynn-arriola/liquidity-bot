# Entry touch-reprice dwell — September 24, 2026

## Outcome

The reviewed quality-recovery and touch-churn repairs are pushed and staged in
the laptop WSL checkout at `ecea13b2f9eb249d14630026a810b2794a173cfb`.
They are **not loaded**: the operator explicitly required deployment without a
restart. Supervisor `4354` / runner `4368`, started September 25 at 00:09:20Z,
remained alive on loaded marker
`ef5a73f3d9a2afdf05962d9df1a79ec0cde70dce` throughout deployment.

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

## Activation boundary

No process stop, account mutation, marker change, or restart was performed.
The fixes will load only on a later explicitly authorized restart after the
ordinary account/ownership gates. Natural acceptance should compare
`resting_price_left_the_touch` cycle frequency and resting continuity; do not
induce book movement, fills, or faults.
