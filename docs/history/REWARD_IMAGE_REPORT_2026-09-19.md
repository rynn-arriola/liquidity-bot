# Mobile-width reward image — September 19, 2026 ET

## Operator request

The operator liked the new image report but observed that its narrow portrait
shape used only part of the available Discord mobile width. The requested
change is presentation-only: keep the same values and visual style, but let the
attachment occupy the device width more effectively.

## Reviewed design

The report renderer uses the compact text payload as its only data source; it
does not perform a second money, reward or liquidity calculation. The PNG is
now 1200 by 1382 pixels for a seven-day report instead of 800 by 1382. Discord
height-constrains tall mobile attachments, so the aspect ratio changes from
about 0.58 to 0.87 while preserving the existing vertical density and apparent
font size. Cards, table backgrounds and numeric columns expand to the new
content width.

The same report work also keeps local side-hours separated only when the
original order observation durably records `day_of` or `daily_event`. Modified,
missing, mismatched or conflicting evidence is retained as unallocated and
marked partial; it is never inferred from current market settings. Rendering
failure falls back to the compact text payload only before transport begins.
An ambiguous image upload is not retried as text, preventing duplicate posts.

This path opens the ledger read-only, adds no venue request and has no trading,
order, position, selection, risk or process-control route. Pillow is an optional
report-only dependency and is not imported by the trader.

## Verification

- The rendered seven-day preview is 1200 by 1382, 121,608 bytes and visually
  keeps every value inside the cards and table bounds.
- Forty-six focused image, period-attribution, reward-table and Discord-layout
  tests pass.
- Reversing the canvas to 800 pixels fails the dedicated mobile-aspect
  regression at 0.58 versus the required 0.85; restoring 1200 passes.
- A native full suite ran 1,928 tests in 199.587 seconds. Its five failures are
  the pre-existing POSIX-only `fcntl`/host-wide-lock tests on Windows; all
  changed report tests passed. The canonical WSL full-suite result is recorded
  in the final release update below.

## Release state

Prepared locally only at this checkpoint. No Discord webhook, venue API,
trading process, timer, bot restart or deployment was invoked by the preview or
tests.

## Final review and integration

The operator subsequently requested completion, retaining the earlier requests:
short names throughout the report, M/D dates, day-of hours, daily-event hours,
average sides, and the wider image from the other session. Report-only rollout
does not authorize loading the separate pending trading changes or restarting.

The first 800px sample was explicitly sent to the rewards webhook on request
and marked PREVIEW / Sample data. Later offline previews use the saved private
ledger, never invented account values. The renderer uses the compact payload as
its sole numerical input and includes the same table text as attachment alt text.
Official multipart contract: <https://docs.discord.com/developers/resources/webhook#execute-webhook>.
One PNG upload with `wait=true` replaces one JSON post. No Polymarket reads are
added, and the existing report API lane/timer and trader are unchanged.

### Data checks and corrections

- Real-ledger replay caught a draft-only timing mistake: quote observations are
  written atomically with their intent but their timestamp follows intent
  creation by milliseconds (observed 4.693ms). Same-order market/side identity
  establishes provenance; the timestamp must be no later than the inspected
  interval end, not no later than intent creation. The former check made every
  category unknown. The dedicated regression and exact reversal cover this.
- Rejected orders are excluded: they supplied no liquidity. Compared with the
  old local coverage report, this removes at most0.003716 side-hours from any
  inspected September13–19 day. CLI/dashboard calculations are untouched.
- Same-market/same-side overlaps are unioned, opposite sides add. A conflicting
  type, modification, absent/malformed/mismatched observation stays unallocated.
  The period columns show known hours followed by `+` when the split is partial;
  Avg includes all recorded non-rejected coverage, including exits/untyped hours.
  These are approximate local order intervals, not exchange-qualified scoring
  seconds or guarantees of reward payment.
- Midnight/DST, current/future days, empty data, invalid chronology and readonly
  operation are covered. Real saved September18 coverage was371.3696 day-of,
  44.2795 daily-event and37.0169 unallocated side-hours. These per-order interval
  types are not the same as the earlier market-day economics buckets, which
  deliberately classify mixed market-days wholly unallocated.
- A missing closed-trade day can no longer silently become zero in the P&L
  headline total. No daily fill replay or risk calculation changes. Thousands
  separators are removed before compact numeric formatting; large values no
  longer cause a Decimal parsing failure.
- Financial headlines and table use `P&L`, `Paid`, `Pend`; dates use9/13.
  Image liquidity headers are `Date`, `Day-of h`, `Daily h`, `Avg`. The text
  fallback is31 characters for results and28 for liquidity. Rendering failures
  fall back before sending; delivery errors never trigger a duplicate fallback.

### Dedicated edge-case review

Reviewed success, empty/one/seven-day reports, partial/missing/zero values, large
numbers, December/year rollover, DST, overlapping replacements, modified orders,
malformed/future records, PNG and Discord limits, missing fonts/Pillow, timeout,
unconfigured webhook and readonly DB failures. Original known values are not
reconstructed from today's market settings. No credentials appear in images,
exceptions, tests or tracked evidence; network failures log only fixed reasons.
There is no order mutation, risk/capacity, source-switch, recovery or shutdown
path in this report change. Reports stay isolated and lower priority. Rollback
is report-file-only and does not require a bot restart.

Five deliberate in-memory reversals fail assertions (zero errors): narrow
canvas, post-intent observation refusal, modified-order misclassification,
conflicting-period attribution and missing P&L day treated as zero. Tracked
source is never altered by these reversal probes. The63 focused tests pass;
the preliminary native suite passes1,946 tests. Final shipping and installed
compatibility evidence follows after completion.

Final shipping suite: **1,947 tests pass in227.202s** in an isolated native WSL
tree with the production Python environment and pinned report-only Pillow.
Loaded-version compatibility: **1,898 tests pass in214.656s** against WSL
`70467880` source plus ONLY the three report scripts and relevant report tests.
Both suites avoid venue requests and mutations. The Windows63-test report slice
also passes. Artifact roots are `/tmp/liquidity-reward-final.T0dAJS` and
`/tmp/liquidity-reward-compat.L1bGuT` (private local diagnostics).

The first real-ledger-only preview correctly showed reward dashes because that
ledger had no cached reward rows. This was not a broken renderer or missing
live API result. Final numerical preview uses the existing07:07 ET authenticated
hourly dashboard cache: $666.30 paid/$130.36 pending, with today's earnings
unposted. It reuses that report's settled P&L and timestamp, plus ledger coverage
clipped to the same time. No new API reads were made for this preview. Unknown
today values remain `--`, never fabricated zeroes. The normal main path retains
its existing fresh rewards API read before image generation.
