# Daily execution-fee breadth scaling — 2026-08-31

## Trigger and operator decision

The first size-10/24 start reconstructed `$9.5300` of gross owned taker fees for
the current ET day. The loaded quantity-only policy had fallen from `$9.75` at
size 13 to `$7.50` at size 10, so it correctly emitted
`[execution-fee-pause]` and blocked new risk while keeping the process and exits
alive. This was not an API, 24-slot or accounting error.

The operator set the size-10/24 reference budget to `$10` and required it to
scale. The reviewed formula is:

`$10 × (quote size / 10) × (configured slots / 24)`

Equivalent implementation: `quote_size × configured_slots / 24`, rounded to
cents. Examples are `$8.33` at 10/20, `$9.17` at 10/22, `$10.00` at 10/24,
`$11.92` at 13/22 and `$30.00` at 30/24.

## Safety boundary

Only the budget value changes. Fees remain gross positive fees from durable
owned taker fills, scoped to the ET risk day; maker rebates never offset them.
Reaching the cap still blocks/cancels building quotes without terminating the
process or disabling passive reducers, IOC exits, cancellation, reconciliation
or pre-kickoff flattening. Restart never clears the accumulated daily total.

At the observed `$9.5300`, a size-10/24 restart has `$0.47` remaining and may
place new orders. A later taker exit may consume that remainder and pause new
risk again for the rest of the ET day; this is expected rather than a failure.

## Acceptance contract

Before restart, require the normal graceful stop and authenticated zero-order,
zero-position, ambiguity-free proof. After restart, require the loaded
size-10/24 banner to report a `$10.00` ET-day fee pause, at least one new order
today, zero API failures/429s and correct 24-owner/private-shard scope. The same
restart must activate the terminal-history capital-guidance repair and show a
dollar `Free to withdraw` value from fresh account evidence rather than the old
false `UNAVAILABLE`. If current account evidence is genuinely stale or
ambiguous, fail closed and report that fact instead of fabricating a number.

## Pre-deployment verification

The focused configuration and live-pause tests pass, including aggregation of
partial fills, exclusion of a negative rebate and the size/width matrix above.
A deliberate reversal to the old quantity-only formula made every new matrix
case fail, including `$7.50 != $10.00` at 10/24. The documentation audit passes.
The native-Windows complete discovery reached all 970 tests: every trading and
reporting assertion passed, while four measurement-runner constructors refused
their production host-wide limiter exactly because native Windows has no POSIX
`flock`. The authoritative full run remains the exact deployed laptop WSL
environment, where that required limiter and the installed SDK are available.
