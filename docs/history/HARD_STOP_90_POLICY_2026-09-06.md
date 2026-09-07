# $90 HARD policy — 2026-09-06

## Operator decision

The operator lowered the sole marked-equity HARD loss stop from $128 to $90 at
the current size 10 / 32-slot configuration. No pricing, ranking, inventory,
exposure, exit, ownership or API rule changed; the existing percentage-based
entry caps follow the lower HARD amount as designed.

## Prepared behavior

HARD is `$90 × quote size / 10 × configured slots / 32`, preserving automatic
quantity/breadth scaling. At size 10 / 32, the existing entry-order tiers now
begin at $45, $63, $72, $81 and $85.50; HARD stops at $90. The seven-day
capital-guidance reserve correspondingly becomes $630.

The running process remains loaded with the old $128 value. This policy takes
effect only after reviewed deployment and an explicitly authorized restart.

## Verification

Focused policy/dashboard tests pass. Replacing the prepared $90 reference with
$128 makes the exact size-10/32 regression fail. The complete 1,130-test WSL
suite passes, and the documentation audit reports no issues.
