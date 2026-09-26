# Shadow "learning" ranking — design (measurement only)

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
