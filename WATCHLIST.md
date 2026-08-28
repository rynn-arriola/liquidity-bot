# Active measurement watchlist

These are measurements, not authorized trading behavior. Historical and closed
items are preserved in
[docs/history/WATCHLIST_THROUGH_2026-08-27.md](docs/history/WATCHLIST_THROUGH_2026-08-27.md).

## Server collection map

This is the operator's quick inventory for **Polymarket liquidity-bot**
measurements. Verify processes/cron live before relying on the status words.
The separate Kalshi `record_books.py` process is not part of this project and
does not use the Polymarket API budget.

| Measurement | How it collects on the server | Venue/API cost | Trading effect |
|---|---|---|---|
| Adverse-fill / liquidity cliff | Normal live runner writes decision-time quote observations and links later fills | No extra call or socket | None |
| Queue depletion / almost-front | Normal live runner uses one bounded public-trade socket and durable queue intervals | WebSocket only; no REST | None; `WOULD_QUEUE_RESET` is a label |
| Loss by market, side, ET hour and kickoff | Read-only analysis of the execution ledger and quote observations | None when analyzed | None |
| Size-20 counterfactual | Read-only reuse of size-10 score, reward, fill and exit observations | None when analyzed | None; live size remains 10 |
| Daily selection/period probe | Cron at 14:00 UTC runs one bounded snapshot | About 50 read-only calls once/day | None |
| Strict spread-scalp study | Separate `scalp_run` process, database and combined book/trade socket | Normally 1-2 reads per 15 minutes, locally capped at 1 req/s; at most 100 market subscriptions | None; code has no order path |

The hourly seven-day reward post is operational reporting, not a strategy
experiment. The hourly changelog poll is venue diagnostics. Nightly SQLite
backup is data protection. Ledger growth below is a health watch, not a
separate collector.

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

## 6. Strict two-sided spread-scalp study

Measure two separately labeled cohorts without changing the 20-slot strategy:

- `PURE_VOLUME`: upcoming full-game moneylines ranked by contemporaneous
  full-book `sharesTraded`; discovery rotates without a league filter.
- `REWARD_SLOTS`: the exact current commissioning queue read from the live
  ledger in SQLite read-only mode.

One episode posts hypothetical fixed touch orders on both sides at the back of
the displayed queue. Success requires the entire requested quantity to fill on
**both original sides**. Partial fills, one-side fills and no-fills remain
separate outcomes. Store every full book and public trade, queue-ahead changes,
selection/rank history, fees/rebates, stream coverage and settlement so any
early-exit horizon can be replayed later without recollection.

Primary review metrics are strict both-side fill rate, time to second leg,
one-side exposure rate, pair net, forced-exit completion/loss by horizon,
settlement result, and outcomes by initial spread/volume/depth/stability. A
coverage gap cannot count as success. Collection does not authorize a live
scalping order, change reward selection, consume a slot, or change any exit.
