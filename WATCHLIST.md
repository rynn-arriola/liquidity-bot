# Active measurement watchlist

These are measurements, not authorized trading behavior. Historical and closed
items are preserved in
[docs/history/WATCHLIST_THROUGH_2026-08-27.md](docs/history/WATCHLIST_THROUGH_2026-08-27.md).

## 1. Adverse-fill and immediate liquidity-cliff study

Determine whether decision-time stressed exit value predicts the operator's
observed fill cliff, where a roughly $5 resting order becomes worth about $3
immediately after filling.

- Use complete captured cohorts and actual post-fill outcomes.
- Compare avoided realized loss with projected/paid reward surrendered.
- Keep league as diagnostic context only.
- Do not enforce a retained-value threshold until held-out evidence is
  positive and the operator authorizes it.

## 2. Queue-depletion measurement

The deployed tracker estimates lower/upper queue-ahead bounds and emits a
measurement-only `WOULD_QUEUE_RESET` signal. It does not know exact rank.

- Measure whether almost-front state predicts adverse fills.
- Include cancel-confirmation/replacement dark time and reward loss.
- Never automatically cancel/repost, never reset a reducer, and never act with
  unresolved ownership.
- Automatic same-price queue reset remains explicitly unauthorized.

## 3. Fill-loss concentration by market, side and hour

Use existing quote observations and execution history to identify whether
losses concentrate by ET hour, time to kickoff, market structure, side, depth
or touch velocity. Observe all hours first; do not preselect only suspected
windows. Any proposed no-trade window must compare avoided trading loss against
reward surrendered during the same hours.

## 4. Size-20 counterfactual

Live size 20 is not authorized. Continue the measurement-only comparison using
complete ET reward days, resting side-hours, projected share multiplier,
maker-fill rate and stressed-exit sensitivity.

Before any staged live proposal, review capital, the $340 portfolio exposure
cap, per-market exposure, market/account stops, exit depth and whether reward
gain remains materially below 2x. Adding capital alone changes none of those
controls.

## 5. Ledger growth slope

Disk size alone is not a concern. Watch for a sudden increase in daily growth,
which can reveal retry storms or duplicate event writes. Use SQLite online
backup and read-only diagnostics; never copy the live WAL database directly.
