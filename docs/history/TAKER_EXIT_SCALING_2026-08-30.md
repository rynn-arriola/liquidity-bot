# Size-scaled taker-exit session count — 2026-08-30

## Trigger and operator decision

The size-13, 22-slot runner stopped at 2026-08-30T11:43:58Z with
`commissioning_taker_exit_limit`. Authenticated read-only verification found
three nonzero positions and three bot-owned resting reducing orders. The ledger
had no unresolved intents, absent orders or foreign working ownership, so this
was the configured session ceiling rather than an ownership, API, VPN or
Cloudflare failure.

The stopped session ran from 06:15:43Z through 11:43:58Z. It recorded exactly
20 distinct owned taker-exit orders across 26 partial-fill rows, $1.35 of fees
and approximately -$11.1404 of realized P&L attributable to those taker fills.
The fixed count was still the size-5 value even though every other
quantity-linked commissioning control had moved to size 13.

The operator chose to preserve the size-5 ratio of four taker-exit orders per
configured contract. `CommissioningConfig.max_taker_exit_orders` is therefore
now computed as `4 × quote_size`: 20 at size 5, 52 at live size 13 and 120 at
the authorized maximum size 30.

## Independent dollar ceiling

The session execution-fee limit remains independently scaled as
`0.30 × quote_size`, or $3.90 at size 13. It is deliberately not increased a
second time to guarantee all 52 exits: unusually expensive exits may exhaust
the dollar ceiling before the count ceiling. At the stopped session's observed
average fee, 52 exits would project to $3.51, still below $3.90; that projection
is context, not a guarantee or a target.

Scaling the count increases the amount of adverse exit behavior a session can
observe before this particular stop. The realized-session and marked-equity
loss sensors, per-market stop, early-cross refusal, exposure controls, kickoff
flatten, mutation certainty and fee ceiling remain independent and unchanged.

## Slot-dependent control audit

No other value was increased for the 22-slot restart. The earlier bounded
canary explicitly kept the `30 × quote_size` portfolio cap: 22 fully used
slot-notionals still fit inside it with an eight-notional, 36% cushion. The
following controls do not acquire a larger safety budget merely because the
scheduler has two more owners:

- the 4 requests/second REST governor;
- the $390 portfolio and $117 per-market exposure caps at size 13;
- the $52 per-market and $104 account loss thresholds;
- the 900-second inventory and 300-second pre-kickoff deadlines;
- three hard-exit attempts, mutation certainty and shutdown proof;
- book, account, stream and ownership freshness requirements.

Increasing those values would broaden capital or failure exposure rather than
normalize a size-derived count, and the operator did not authorize it.

## Edge-case review

- The single quote-size parser still accepts only finite whole values 5-30, so
  the computed count has no rounding ambiguity in an authorized runner.
- Authorization locks the multiplier at four; constructing a different
  multiplier fails before live arming.
- The runner still counts distinct owned client orders, not partial-fill rows.
  Maker fills, manual/imported identifiers and reversed fills remain excluded.
- The fee ceiling still sums every positive partial-fill fee, so splitting a
  fill cannot evade the dollar guard.
- Equality remains fail-closed: the 52nd confirmed owned taker-exit order at
  size 13 returns `commissioning_taker_exit_limit`.
- The counter remains scoped to the current process session and starts from
  the runner's `started_at`; no historical fills are imported into a new
  session budget.
- Slot capacity remains an independent restart-scoped value. Size 13 produces
  52 exits at both 20 and 22 slots.

## Verification

The focused configuration, runtime-count, partial-fill, exclusion, fee and
startup-banner set passed 21 tests. The complete dirty Windows source tree was
then exercised through the pinned WSL Python 3.12 environment: all 886 tests
passed in 76.927 seconds. The documentation audit covered 56 Markdown files
and 983 mandatory lines with zero broken/rule issues, including a repeat after
this evidence was recorded.

Deployment and restart remain gated on a clean final diff review, an exact
deployed-checkout full-suite pass and fresh authenticated ownership checks.
