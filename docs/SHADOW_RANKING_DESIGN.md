# Shadow "learning" ranking — design (measurement only)

> **Backtest verdict (September 26): do not build as designed.** A walk-forward
> backtest on existing data (below) found no ranking improvement over the
> current model. Kept for the record; see "Backtest on existing data".

September 26, 2026. Operator-approved design task (see DECISIONS). Nothing here
changes selection, entry, exits or API usage. Implementation, deployment and
any later live use each need separate operator approval.

## Why

The [September 26 readout](history/MARKET_QUALITY_READOUT_2026-09-26.md) found
that the current reward model ranks markets well (market-level Spearman 0.78
against paid $/side-hour) and is conservative overall (paid 1.59× model), but it
has two blind spots:

1. **No payout feedback.** It trusts advertised pools: UTR and R6 were ranked
   yet have never paid; modus and darts paid ~0.5× model.
2. **Reward, not net.** The gate charges a stressed unwind per probable fill
   ($0.2-3.7) that does not match realized loss per fill, which is small and
   league-specific (setkameua -$0.03, ITF -$0.17 to -$0.23, CS2 -$0.18). ITF
   used ~24% of resting time at ~$0.01-0.05 net/side-hour.

In-market competition (our score share, total score, queue ahead) is already in
the model; program crowding showed no effect, so no crowding term is added.

## What the shadow computes

For every candidate side the live selector already evaluated (no new reads):

```
shadow_net_per_hour = payout_ratio(league) × model_reward_per_hour
                    − loss_per_fill(league) × fill_rate_per_hour
```

- `model_reward_per_hour`: the existing projection for the side, unchanged.
- `fill_rate_per_hour`: from the existing fill-hazard estimate, calibrated by
  the observed fills-per-side-hour ratio for the league (the gate's fill
  probability ranks fill frequency well; readout table).
- `payout_ratio(league)`: trailing PAID ÷ model reward over markets the bot
  rested on whose game dates have posted, matched per market across dates
  (payouts are dated by game day, not rest day).
- `loss_per_fill(league)`: trailing closed-fill P&L ÷ fills from the ledger.

Small samples are shrunk toward the all-league value with a pseudo-count, e.g.
`ratio = (paid_L + k·ratio_all·model_L) / (model_L + k·model_unit)`, so a league
with a few markets cannot swing far on luck. A league with a large model total
and zero paid (UTR, R6) converges toward zero; a new league starts at the
all-league value. Parameters (window 7 days, `k`, minimum hours) are recorded
in every log line so results are reproducible.

## Data sources — zero added API

- **Earnings:** `_refresh_earnings` already fetches account-wide liquidity
  earnings (`rows_all`) on its existing cadence; keep the latest rows in memory
  instead of discarding them. No new request, no cadence change.
- **Resting time, fills, P&L:** existing ledger tables (quote observations,
  orders, fills) read locally, as `scripts/market_quality_report.py` already
  does. Reads are local SQLite; recomputed at most once per earnings refresh.
- **Candidate features:** the `RewardMarketSelection` the runner just produced.

## Where it runs and what it logs

After each completed selection scan, in the same best-effort pattern as
`selection_measurement`: wrapped so any exception is swallowed and logged,
never raised into selection, binding or trading. It emits one bounded line:

`[shadow-rank] at=… scan=… window=7d k=… live_top=[…] shadow_top=[…]
agree=N only_live=[…] only_shadow=[…] league_factors={…}`

and appends the same record to the market-quality archive (existing sink).

## How we will judge it (acceptance)

Shadow-only picks are never traded, so their payouts cannot be observed. The
valid test is **prediction accuracy on markets the bot actually traded**:

1. For every traded market with posted payouts, compare Spearman correlation
   of realized net $/side-hour against (a) the current model score and (b) the
   shadow score, using scores logged *before* the outcome (no look-ahead).
2. Report which leagues and ranks the two disagree on, and how much resting
   time the disagreement covers.
3. Minimum evidence: at least 7 posted ET days and 500 traded markets after
   deployment, then a written readout.

Success = the shadow predicts realized net clearly better (for example higher
Spearman across several independent days), not merely a different ranking.

## Rollout, each step needing operator approval

1. Implement behind no switch (logging only), tests including "selection result
   and API calls are identical with the shadow on" (same style as the tracker's
   `test_real_tracker_does_not_change_selection_calls_or_result`).
2. Review, deploy with a normal Command 1, collect ≥7 posted days.
3. Readout. If better, the operator may choose a limited live use (for example
   ordering only among already-viable markets), which is a separate business
   change with its own review and tests.

## Risks and limits

- Account-wide earnings could include manual activity; flagged, not filtered.
- League is a coarse unit; program- or market-level factors may be better but
  need more data.
- Trailing windows lag regime changes (a league starting or stopping payouts).
- Correlation is not causation; live use must still pass every safety gate.

## Backtest on existing data (September 26, offline, 0 API)

Inputs already on the laptop: ledger quote observations since 2026-08-24
(137,991 rows with projected reward, fill probability, program, pool, share,
total score, expected net), orders/fills since 2026-08-14 (170,103 / 19,012),
PAID earnings since 2026-08-14 (6,208 rows), tracker archives since 09-23.
`market_quality_report.py --days 33` produced 7,378 market-days
(2026-08-23..09-24); markets with manual/unowned fills (220 market-days) and
unposted game dates were excluded, leaving 5,935 markets.

Walk-forward: for each game date D (31 test days), factors were learned only
from markets with game dates in the prior 7 (or 14) days, with shrinkage
(model $3, 20 side-hours). Test markets on D with ≥0.5 side-h were ranked by
each score and compared with realized net (PAID + closed-fill P&L) per
side-hour.

| Score | Mean daily Spearman vs realized net | Top-half net $/h | Bottom-half net $/h |
|---|---:|---:|---:|
| Current model (expected reward/h) | 0.533 | 0.334 | 0.054 |
| League payout ratio × model | 0.536 | 0.333 | 0.053 |
| Same, 14-day window | 0.537 | 0.335 | 0.052 |
| Program payout ratio × model + program trade/h | 0.521 | 0.331 | 0.057 |
| League payout ratio × model + league trade/h | 0.487 | 0.336 | 0.052 |
| League ratio × model − league loss/fill × fill rate | 0.477 | 0.335 | 0.053 |

The current model already ranks low-value leagues (for example ITF) low;
learned payout ratios add ≤0.004 Spearman and the trading terms hurt. ITF's
large resting share comes from filling otherwise-empty slots with still
positive (bottom-half ~$0.05/h) markets, which is a supply/threshold question,
not a ranking error. The one clear cross-league signal — leagues that never pay
— is handled directly by the approved UTR/R6 exclusion. Recommendation: do not
implement the live shadow; revisit only with new features (e.g. time-to-kickoff
bands or post-selection spread tightening) that the current model lacks.
