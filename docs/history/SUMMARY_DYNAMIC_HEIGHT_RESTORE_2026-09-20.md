# Summary dynamic-height restoration — September 20, 2026

## Problem

The fixed 1200 by 1382 summary introduced for mobile consistency could hold at
most 12 rows in the lower diagnostic cards. A normal busy slate can exceed that
limit. The renderer deliberately raised an exception rather than omit values,
and the notifier safely sent the complete text summary instead. That is why a
normal summary sometimes appeared as text even though image delivery itself was
working.

## Correction

The summary returns to the accepted taller layout and grows vertically with the
number of market and health rows. It retains the operator-requested additions:

- five full-width lifetime rows;
- the original large green/red overall-net bar;
- pending reward separately below the net;
- the settled six-hour `HARD` meter; and
- independently aligned label/value columns.

Lower diagnostic rows keep readable type and one row per value. They are no
longer clipped, omitted, compressed into tiny type, or converted to text merely
because the slate is busy. Unexpected expansion of the fixed top safety schema
still fails safely to the complete text report. Render failures now log only the
safe exception class so an image fallback can be diagnosed without exposing
paths or third-party response text.

## Verification

This is presentation-only. It adds no API request and changes no report
arithmetic, trading rule, order, position, reward selection, risk calculation,
scheduler, or lifecycle behavior.

- All 33 focused summary tests pass in the production WSL environment.
- The complete production-environment suite passes all 1,986 tests.
- Reversals for fixed height, reduced value size, value alignment, metric
  parsing, pending/net separation, closed-fill P&L, unknown handling and canvas
  width all failed their targeted regressions as expected.
- The normal model renders at 1200 by 1793; a synthetic 24-row busy model grows
  to 1200 by 2313 and retains every diagnostic row.
- The final 1200 by 1793 preview was visually reviewed for hierarchy,
  readability, meter placement and alignment.

## Operational boundary

Commit `137121f22` was pushed and fast-forwarded into the laptop WSL checkout
under the report lock. All 33 focused tests and the complete 1,986-test suite
passed again in that exact deployed checkout. PID 1063 and `DEPLOYED_COMMIT`
remain on loaded release `2f607b679`; the bot was not stopped or restarted.
The new presentation therefore loads at the next operator-authorized restart.
The first natural summary after that restart is the remaining delivery-level
acceptance check.

## Command 1 activation

The operator authorized Command 1. The existing supervisor and runner stopped
gracefully at 21:04:34Z with the runner recording `flat=true`. Two independent
authenticated gates, at 21:05:08Z and 21:06:57Z, proved zero venue orders and
positions plus zero unresolved intents, unknown orders, working ledger orders
and ledger positions. No force kill, venue mutation from diagnostics, risk reset
or configuration change was used. The deployed commit marker was advanced only
after the reviewed source and full installed suite were verified.

Supervisor 22198 and runner 22212 started at 21:07:02Z with the persisted
size 10, capacity 32 and manual REST account source. At 21:08:46Z an
authenticated read found 14 flat-market orders, zero positions, zero
unresolved intents and zero unknown orders. The one-order ledger/venue count
gap was an in-flight publication observation, not yet a confirmed mismatch.
The confirmed 12:00–18:00 ET settled-loss window showed $18.8445 of $25 used,
so the unchanged policy selected the 15 pending-entry tier without a hold.
The first completed API window averaged 2.770 requests/second, had zero 429s
and three ordinary individual-order 404 reconciliation reads. Startup book
selection used the public stream with zero REST fallback. The first natural
post-restart summary image and sustained position/recovery behavior remain to
be accepted from live evidence.

At 21:12:45Z, after more than five minutes with the same runner, authenticated
state showed 14 flat-market entry orders and one exact BUY 3.27 reducing order
covering the sole -3.27 position. Unresolved intents and unknown orders were
zero. The ledger's 16 working rows versus 15 currently visible venue orders
is a publication-time observation, not proof of an unmanaged order; continued
reconciliation remains necessary. The first four completed API windows averaged
2.770, 2.407, 2.358 and 3.204 requests/second, each with zero 429 responses.
Ordinary individual-order 404 reads were reconciled; no summary-image fallback
error appeared in the observed log. Because successful image uploads are not
logged, this does not prove the actual Discord attachment was received. That
natural presentation receipt and a later position flatten remain open checks.
