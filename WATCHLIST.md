# Active measurement watchlist

These are measurements, not authorized trading behavior. Historical and
closed items are preserved in
[docs/history/WATCHLIST_THROUGH_2026-08-27.md](docs/history/WATCHLIST_THROUGH_2026-08-27.md)
and [the retained-value audit](docs/history/RETAINED_VALUE_SHADOW_2026-08-30.md).

## Active collection map

**DECIDED September26 (evening) — UTR and R6 excluded; loads at the next Command 1.** The operator chose to exclude leagues we rested in but were never paid for, and to keep barely-tried leagues (RL, BBL, UFC, LGS Cup) in. A later full-history check found zero rows of any program type for UTR, and zero for UTR by exact slug. Original notes follow.
- **Paid rewards:** none. Zero paid rows for any `utr` or `r6` market in 6,585 bot-era liquidity payouts (read-only, 17:4xZ September26; Sept24 is fully posted, Sept25 still posting).
- **Trading:** 12 UTR positions in 7 days, 0 wins, -$9.81, all taker exits (thin books). About 2,900 UTR quotes occupied slots. [Evidence](docs/history/CALIBRATION_MEASUREMENTS_2026-09-26.md), [readout](docs/history/MARKET_QUALITY_READOUT_2026-09-26.md).
- **On Monday:** recheck payouts (Sept25–27 fully posted) and UTR/R6 trading. If still unpaid, choose between two options. (a) Exclude via `config/selection_exclusions.json` → `["utr", "r6"]`. (b) Operator preference 2026-09-26: keep measuring them but give them only spare slots, never priority; this needs a small selector change. Either option is selection-only: positions and exits are untouched. Also review the first `scripts/market_ranker.py` rankings (every rewarded market within 6 h, net $/h, blocking rule) for the 2-cent rule question.
- **Activation:** needs operator approval plus Command 1, ideally the same restart that loads the markout and blocked-entry records.

**September23 OPEN — unexplained risk-snapshot failure:** the16:29:19–27Z
account-risk pause cancelled entries across13 markets. The exact triggering
exception was not logged; earlier REST runs also contain similar waves.
Reproducing the cancellation mechanism or adding diagnostics does **not**
establish the live root cause. Capture the failing refresh stage, safe error
category/status, snapshot age and active pause blockers on a natural recurrence
on loaded038408240 after Command1.17:36:56Z cash/history movement was logged
without a wave; it does not prove the old trigger. No extra polling or relaxed gate. Close only with evidence identifying the trigger
and verification of the corresponding remedy. This remains separate from the
known hybrid order-admission scheduling fix. [Evidence and open work](docs/history/ENTRY_CHURN_DIAGNOSIS_2026-09-23.md).

**September23 broad scan coverage — PAUSED:** operator approved an isolated, bounded
comparison of the existing modeled100-candidate shortlist with up to300 public
book candidates. Prepared diagnostic only; no live selection wiring, recurring
collector, REST-book fallback or trading effect. See
[design, command and limits](docs/SCAN_COVERAGE_MEASUREMENT.md).

The complete [measurement and diagnostic checklist](docs/MEASUREMENT_CHECKLIST.md)
includes live reward/entry/selection measurements, execution diagnostics,
prepared-not-loaded hooks, retired collectors and the prioritized next-day review.
The table below retains the original watchlist experiments, not the whole catalog.

| Measurement | Collection path | Venue/API cost | Trading effect |
|---|---|---|---|
| Retained executable value | Normal building PLACE observation plus the most recent already-fetched selector snapshot | No extra call or socket | None; 60/70/80 are `WOULD_REFUSE` labels only |
| Ledger growth slope | Existing SQLite file sizes and read-only diagnostics | None | None |
| Private WebSocket availability | STOPPED at operator request after hybrid preparation; collected JSONL retained | No further diagnostic socket or REST calls | Historical evidence only; activation needs a reviewed release, not this measurement |

Hourly seven-day reward reporting, public changelog monitoring and nightly
SQLite backup are operational controls rather than strategy experiments. They
remain active. The queue-depletion socket, daily selection probe and separate
spread-scalp collector are no longer active measurements.

## 1. Retained-value refusal shadow

Determine whether refusing entries below 60%, 70% or 80% stressed executable
value would improve paid net after accounting for everything the refusal costs.

For every prospective sample:

- preserve the exact PLACE/fill linkage and current stressed exit;
- distinguish a recent selector candidate, stale/unknown evidence and no candidate;
- measure projected reward on the refused side, replacement projected reward
  and capacity that would remain dark;
- attribute actual later trading outcome using canonical owned entry/reducer
  roles;
- compare projected reward with paid reward over complete ET reward days;
- separate historical sizes 10/13; the current runtime is size 10 / 32.

Do not enforce a threshold from the historical association. Freeze a training
interval and a later held-out interval. A candidate must reduce realized loss
by more than reward surrendered and must not create material dark-slot cost.
Any live proposal requires a separate review and explicit operator decision.
The September 7 audit finds a repeatable signal in 1,069 coherent episodes;
the operator approved a configurable gate and exact-action estimate repair,
now separately authorized for a September 7 trial. Top-20 reward sacrifice remains
unproven; keep 900 seconds. [Handoff](docs/history/ENTRY_QUALITY_FILTER_2026-09-07.md).

## 2. Ledger growth slope

Disk size alone is not a concern. Watch for a sudden increase in daily growth,
which can reveal retry storms or duplicate event writes. Use SQLite online
backup and read-only diagnostics; never copy the live WAL database directly.

## Open design: 0.15 entry-floor counterfactual

Do not enforce 0.15 from the retrospective “extreme market” association: it
uses any extreme fill and therefore includes the costly exit. A valid future
measurement must label the actual building decision, record whether 0.15 alone
would refuse it, and compare canonical realized outcome, paid reward and
replacement/dark-slot cost over complete ET days. This is not collecting yet;
building it requires a separate operator request. [Verification](docs/history/TRADING_LOSS_PRICE_MIX_VERIFICATION_2026-08-31.md).

## Retired measurements

- **Queue depletion / almost-front:** 12,227 orders over 5.90 days produced
  2,061 clean reset signals but only 197 signals preceding a later fill. The
  extra public-trade socket and interval writes stop with the next runner
  restart. Historical tables/report remain read-only evidence.
- **Daily selection probe:** disabled on the laptop. Its August 29 and 30 runs
  failed before collection because the migrated wrapper still targeted
  `/root/liquidity-bot`.
- **Strict spread scalp / fixed GTC:** collector was already stopped. The
  isolated database and reports are retained, but no socket or REST loop runs.
- **Kickoff/hour and size-20 studies:** analysis is closed. Actual inventory
  entry time did not reproduce the claimed 30–60 minute sweet spot; current
  size is 13, so the stale size-20 counterfactual is not an active decision.
