# Daily execution-fee pause — 2026-08-30

## Trigger and decision

The size-13 runner ended at 2026-08-31 02:28 UTC under the old
`$3.90` session execution-fee ceiling. Its durable owned fills contained
`$4.00` of gross positive taker fees and `$1.21` of maker rebates (`$2.79`
net). The account had no open orders and only two 0.01-contract dust
positions when checked afterward.

The operator selected two policy changes:

1. the gross owned taker-fee budget is `$9.75` at quote size 13 and scales as
   `0.75 × quote size`, rounded to cents, funding about 50 peak-fee exits at
   every size; and
2. reaching this cost budget pauses new trading for the ET risk day instead
   of terminating the bot. Monitoring, cancellation, passive reduction, IOC
   exits and the pre-kickoff flatten remain available. A new ET day may resume
   only after a fresh account/risk-day baseline and all normal entry gates.

The existing normal realized-loss stop already uses the same daily in-process
pause model. The explicit account-wide entry bit now also guarantees its
resting building quotes are immediately undesired instead of waiting through
reward hysteresis. Its three-consecutive-loss-day terminal breaker and the
separate emergency marked-equity hard stop were not weakened by this decision.

## Implementation

- `CommissioningConfig.max_daily_execution_fees_usd` derives the cent-rounded
  budget as `quote_size × 0.75`.
- The runner sums positive fees only from non-reversed TAKER fills whose client
  order is owned by a durable PLACE intent, bounded to the current
  `America/New_York` risk day. Negative maker/rebate values cannot reset the
  gross safety budget.
- Same-day fills are the authority, so restarting the process cannot grant a
  second budget.
- The market frame carries an explicit account-wide `new_risk_allowed` bit.
  When false, flat-state building orders are cancelled immediately without
  waiting through reward-eligibility hysteresis. Inventory planning ignores
  this bit and retains every reducing path.
- At ET rollover, the fee latch clears, but entry remains blocked until the
  durable risk-day baseline and a fresh equity observation belong to the new
  day.
- Discord sends at most one critical fee-pause notice per runner per ET day.
  The terminal
  `4 × quote size` taker-exit evidence count remains independent.

## Edge-case audit

Covered in permanent tests:

- exact threshold and partial-fill summation;
- positive gross fees are not reduced by negative fee/rebate rows;
- maker, manual/unowned and reversed fills are excluded;
- prior-ET-day fills do not consume the new day;
- same-day restart reconstructs the pause from durable fills;
- ET midnight clears the fee latch but does not authorize entry from a stale
  prior-day baseline;
- account pause immediately cancels both building sides;
- a held position still receives its passive reducing quote;
- one alert per ET day;
- the independent session taker-exit count remains terminal.

No venue request, API retry or shared-rate-limit behavior was added.

## Verification and deployment

- Focused commissioning/reconciler/live-runner suite: 418 tests passed.
- Seven targeted boundary tests passed after restoration.
- Three deliberate reversals failed exactly: restoring the old `$3.90`
  size-13 calibration failed the size assertion; bypassing the account entry
  gate failed the immediate-cancel test while the reducer test remained green;
  restoring the terminal fee result failed the non-terminating pause test.
- Complete local WSL suite: 934 tests passed in 92.876 seconds.
- Final diff/edge review found no new venue call, retry, shared-rate change,
  ownership relaxation, exit suppression, secret/database addition or
  capacity coupling.
- Commit `be03d64764cadb567cdbe586d69749bd2884b5c4` was pushed to GitHub,
  fast-forwarded into the WSL runtime from the matching Windows checkout and
  passed all 934 tests there in 69.284 seconds. `DEPLOYED_COMMIT`, WSL HEAD,
  Windows HEAD and GitHub then matched exactly.
- A fresh authenticated 03:25 UTC start gate proved zero venue orders, zero
  positions, zero `UNKNOWN_ABSENT` orders and zero unresolved intents. Exactly
  one size-13/22-slot supervisor and runner pair started.
- Startup reconstructed `$9.9600` of same-day gross owned taker fees. The old
  `$3.90` budget was session-scoped, so five sessions accumulated `$1.26`,
  `$1.27`, `$1.35`, `$2.08` and `$4.00`; the final `$4.00` checkpoint covered
  only its own session. The new runner emitted
  `[execution-fee-pause]`, remained alive, placed no new orders and an
  authenticated 03:28 UTC acceptance again proved zero orders, zero positions,
  zero absent and zero unresolved rows. This naturally accepts same-day
  reconstruction, non-termination and entry suppression. Automatic next-day
  re-eligibility remains pending the ET midnight boundary and fresh gates.

## Follow-up calibration

The original `$6.00` day calibration was replaced before the following
eligible-day run. A taker fee peaks at `0.015 × quote size`, so `0.75 × size`
funds 50 peak-fee exits at every authorized size and closely matches the
practical capacity of the independent `4 × size` count guard at the selected
size 13 (52 exits). The old `0.30 × size`
session policy funded only 20 peak-fee exits regardless of size; changing only
the scope would have preserved that contradiction. The ET-day scope,
non-terminal pause, durable reconstruction and gross-fee definition are
unchanged.

The operator stopped the runner before this calibration change. The graceful
stop ended with `operator_sigterm` and a fresh authenticated dashboard read
proved zero venue orders, zero positions, zero absent and zero unresolved
rows. All 360 focused commissioning/live-runner tests passed, then the complete
934-test suite passed. A deliberate reversal to the prior formula failed the
permanent size-13 assertion with `$6.00 != $9.75`, proving that the scalable
calibration is regression-sensitive.
