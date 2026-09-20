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

The source is intended for a no-restart deployment. The live process continues
to use its already loaded release until the next operator-authorized restart.
The next natural summary after that restart is the remaining delivery-level
acceptance check.
