# Program reward audit measurement

Date: 2026-09-03

## Question

Did reward fall because the bot selected different reward programs, because
competition diluted its share, because quote size changed, or because slots
25-32 add low-value resting time?

## Measurement boundary

The public rewards directory describes a pool shared across every market in a
program/time period. A market-level projection may repeat that pool, so the
audit must never sum it once per market. The new report groups by the immutable
`program_id` recorded with every building PLACE observation.

For each ET reward day and program it reports:

- exact merged resting side-hours and distinct markets;
- the shared pool as a value, not a market-summed amount;
- duration-weighted quote size, target, discount, external score and observed
  share;
- authenticated PAID and PENDING reward;
- balanced BUY-LONG/BUY-SHORT trading P&L including recorded fees; and
- net only when no reward remains pending.

Unmapped and multiply mapped earnings remain visible as coverage failures. The
trading estimate retains the existing lower-bound limitation: unbalanced legs
and positions that settle without a recorded closing fill are excluded.

## Width comparison

New quote observations record the market's one-based rank in the latest fresh,
complete viable selector slate. Rank is evidence only, lives in
`observation_json`, adds no database migration and is never read by selection,
risk or execution code.

This creates two factual cohorts while the live width remains 32:

- `top24`: rank 1 through 24;
- `slots25plus`: rank 25 and above.

Older observations and quotes whose selector snapshot was absent, stale or
future-dated remain `rank_unknown`; the report does not guess. Replacement
overlaps count as one resting side and are assigned to the newest active quote,
including when its rank crosses the cohort boundary.

Actual market reward cannot honestly be divided between two simultaneous slot
cohorts from the venue earning row alone, so PAID/PENDING and net remain at the
program-wide level. Cohort output is an exposure comparison until enough
program-day observations support a controlled follow-up model.

## Operation

The default report makes exactly one account-wide earnings read through the
opportunistic `report` lane. It performs no venue mutation and writes no ledger
state. `--local-only` makes no network call and uses the most recent locally
persisted snapshot per market reward day.

Example:

```text
python -B scripts/program_reward_audit.py --days 14
python -B scripts/program_reward_audit.py --days 14 --local-only
```

Do not change size, ranking, program gates or slot count from this report until
several complete reward days exist and pending rows have settled. The first
decision table should compare program-level dollars per side-hour and net per
side-hour, then use the rank cohorts to show where the added 25-32 exposure was
spent.

## Review and verification

Final review covered empty and malformed observations, absent/stale/future rank
evidence, replacement overlap, ET-midnight interval splitting, repeated shared
pools, unmapped and multiply mapped earnings, pending reward, reversed fills,
unbalanced market-days, read-only database access and the single opportunistic
report request. The instrumentation is synchronous, consumes only the existing
in-memory selector slate and cannot alter selection, orders, positions, risk,
shutdown or recovery.

Review found and corrected one pre-commit reporting defect: fills from multiple
ET days in the same market were initially combined under the first fill's day.
The corrected audit groups before balancing by `(ET day, market)`. Its dedicated
cross-day regression failed exactly when that grouping was deliberately
reversed, then passed after restoration. All 12 audit tests passed, the focused
native suite passed 470/470, and the exact isolated production-matching WSL
candidate passed 1,075/1,075 tests in 79.639 seconds.
