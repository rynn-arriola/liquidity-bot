# Daily execution-fee pause — 2026-08-30

## Trigger and decision

The size-13 runner ended at 2026-08-31 02:28 UTC under the old
`$3.90` session execution-fee ceiling. Its durable owned fills contained
`$4.00` of gross positive taker fees and `$1.21` of maker rebates (`$2.79`
net). The account had no open orders and only two 0.01-contract dust
positions when checked afterward.

The operator selected two policy changes:

1. the gross owned taker-fee budget is `$6.00` at quote size 13 and scales
   proportionally with quote size, rounded to cents; and
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
  budget as `quote_size × 6 / 13`.
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
  capacity coupling. Push, deployment and exact-runtime verification are
  pending below this checkpoint.
- The bot remains stopped; this change does not authorize a restart.
