# Archived experiments (2026-09-26)

These modules are not loaded by the live trader, and nothing in `src/`, `scripts/` or `tests/` imports them. They were moved here from the trading package on the operator's instruction ("Do all remaining", architecture review finding 5), so they no longer add test time or review surface. Git history is preserved (`git log --follow`).

| Experiment | Files |
|---|---|
| Shadow evaluation | `shadow_engine`, `shadow_run`, `shadow_store`, `shadow_report`, `calibration` |
| Strict spread scalp (stopped since August) | `scalp_engine`, `scalp_gtc`, `scalp_report`, `scalp_run`, `scalp_store`, plus the `*_scalp_measurement.sh` and scalp report scripts |
| Queue measurement collector | `queue_measurement`; `scripts/queue_measurement_report.py` still reads the ledger tables directly and stays live |
| Dry-run acceptance harness | `dry_run_acceptance` |

Kept in the package because the trader uses them:
- `shadow_math` (`cross_exit`, reward-day intervals); its tests are now `tests/test_shadow_math.py`;
- `capacity` (the API capacity reports).

**To restore one:** `git mv` its module back to `src/liquidity_bot/`, its tests back to `tests/` and its scripts back to `scripts/`, then run the full suite. The data files (`data/scalp.db`, `data/shadow.db`) were left untouched.
