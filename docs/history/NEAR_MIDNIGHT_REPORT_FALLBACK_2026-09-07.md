# Nearby midnight snapshot reporting fallback — September 7, 2026

## Operator request and scope

After the review deployment, the operator requested the closest available
snapshot when the bot was restarting across midnight. This is reporting only:
no risk baseline, ledger row, order rule, loss limit or trading process changes.
The announced implementation uses the durable first saved opening up to
15 minutes after ET midnight. There is one opening per ET date, so this is the
nearest saved post-midnight opening, not a search through arbitrary later marks.
Anything before midnight, beyond 15 minutes, missing, malformed or unproven
remains unavailable. The bounded fallback is not an exact-day reconstruction.

## Implementation

- One shared report-boundary validator serves historical rows and today's
  independent account report. Both use the actual saved timestamp. Existing
  field-pinned, audited midnight exceptions remain strict: changed fields
  cannot fall through into the wider estimate window.
- Equity includes carried positions. Completed non-trading cash is removed
  over the exact same `(opening, closing]` interval; today's scan starts after
  its same saved opening. A closing beyond the cash scan's cutoff is refused.
- Delayed calendar rows retain `~` and do not finalize calendar-day net.
  The text and Discord reports now list actual ET snapshot intervals, including
  timezone and seconds. The dashboard retains the existing estimate flag and
  interval metadata; no dashboard API or trading dependency was introduced.
- The risk-day creation/reset code is untouched. The $90 HARD, 30/40 clock caps,
  loss tiers, order limits, snapshot cadence and all trading source remain
  byte-identical. No trading restart is required for the isolated report script.

## September 6 evidence and correction semantics

The stored September 6 opening was $1,090.8613 at 00:01:46.645731 EDT;
September 7's opening was $1,086.6591 at 00:07:54.512370 EDT. The old report
rejected the latter because it exceeded two minutes. The reported historical
interval can now include it as an estimate, with completed non-trading cash
verified independently before displaying a number. The previously verified
$100 goodwill credit implies -$104.2022 for that interval, not a newly proven
exact midnight-to-midnight total. Live report acceptance is recorded below.

## Verification and dedicated edge-case audit

Focused report tests pass 30/30. New regressions cover the actual September 6/7
timestamps, consecutive-day shared boundaries, midnight-gap credit/withdrawal,
no double subtraction in today's report, the estimate marker, actual cutoff
text, withheld finalized net, and a 25-hour DST fall-back day. Exactly 15 minutes
is accepted; one microsecond later and pre-midnight values are refused. Existing
missing/source/finite-value/cash-movement/page-ceiling/audited-field tests remain.

Reversing the bound to two minutes made both the historical restart and today's
gap-credit regressions fail by assertion; the repair was restored before the
complete suite. Review confirms:

- No trading/risk write, schema change, mutation, new endpoint or extra API call.
- Empty/missing/late data never becomes zero; malformed or altered audited
  evidence cannot gain authority through the fallback.
- Positive and negative cash flows share actual snapshot boundaries; adjacent
  dates use the same endpoint, while fixed-window coverage remains mandatory.
- Timestamp arithmetic is timezone-aware and does not assume a 24-hour ET day.
- Discord keeps its seven-day, per-field and total-character limits; interval
  notes are separate from the narrow numeric table. Estimates remain explicit.
- The existing hourly report lock and opportunistic governor remain unchanged;
  deployment does not replace the live trading process or change its owner.
- Rollback affects reporting availability only; no database migration, secret,
  credential or recovery-data change is involved.

## Release acceptance

Full-suite, pushed deployment and live report evidence will be recorded here.

- Review-tree complete suite: **1,150 passed in 110.266 seconds** under pinned
  WSL Python 3.12. Documentation and final diff/whitespace checks passed.
- The operator explicitly reiterated **do not restart the bot**. Existing
  trading runner `475856` remained up. This release changes only the isolated
  report script, its tests and documentation; `src/` has no changes.
