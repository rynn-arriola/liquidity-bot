# Reward Pool Tracker — September 30, 2026

**Operator request:** detect from the rewards data whether the pools are good. "Build it but dont connect it to the bot yet", and "dont look at the payment we got but the pool that was offered".

## Built

`scripts/reward_pool_tracker.py` (named by the operator: "we calling this reward pool tracker"; tracking only, "lets track this for now") runs every 3 hours from cron.
- **Sources:** the pools the bot recorded as **offered**, from every entry quote (program id + pool, since Sep 14) and every program its scans saw (since Sep 23).
- **Does not:** call the API, trade, or post to Discord.
- **Output:** pool per program family and day, before/now changes, and alerts for NEW programs or CHANGED pools in the last 2 days.
- **Files:** `data/reward-pool-tracker/latest.md|json`.
- **Tests:** `tests/test_reward_pool_tracker.py`.

## Offered pools before and after the Sep 24 program change

Pre-game (day-of), per market:

| Program | Before | Now | Change |
|---|---:|---:|---:|
| Table tennis moneyline | $35 | $15 | −57% |
| ITF moneyline | $30 | $15 | −50% |
| ATP/WTA moneyline | $200 | $100 | −50% |
| Esports moneyline | $100 | $50 | −50% |
| Tennis doubles | $50 | $25 | −50% |
| Darts | $35 | $5 | −86% |
| MLB moneyline | $500 | $200 | −60% |
| WNBA | $200 | $100 | −50% |
| Cricket (Sep 28) | $75 | $15 | −80% |

- **In-game pools** were cut 30–60% on the same day.
- **New programs:**
  - Sep 29: NHL regular season ($50 pre-game / $525 in-game) and MLB Wild Card ($175 / $2,150).
  - Sep 28: cricket early and live.

The report compares offered pools only; payouts are tracked separately in Reward Pulse.

## In the Discord reward summary (operator, 2026-10-01: "Add it in the summary just to track it")

- **What it shows:** a **Reward Pools** section in the hourly reward post (`scripts/reward_table.py`, image via `scripts/reward_image.py`) for the pre-game programs we trade (TableTennis, ITF, ATP/WTA, Esports, Doubles, Darts): pool now, the previous pool and the change.
- **Alerts:** the footer adds `pools: N new/changed` when the tracker has alerts.
- **Source:** `data/reward-pool-tracker/latest.json`. The section is skipped if that file is missing or more than 12 hours old.
- **Tracking only:** no API call and no trading effect.

## Pool score 1-10 and the single summary line (operator, 2026-10-01)

**Requests:** "it should be a 1 single section", "it should say good or bad", "the measure should be for all rewarded market", "measure it 1 out of 10", "in the future we might based it on the number if we are trading or no", "score from 5 under means its not profitable".

**Rewards page, all programs:**
- Every run the tracker also reads the public page https://polymarket.us/rewards: one request, every program with its period, pool, discount, target and market count.
- It saves a snapshot to `data/reward-pool-tracker/site/` (kept 35 days).
- If the page cannot be read, it falls back to the bot's own records.

**Score definition (fixed, so it can later be backtested and used for decisions):**
- For every program on the page now, the reference is the highest daily pool its family and period showed in the last 30 days (page snapshots plus bot records).
- Score = 10 × sum(now) / sum(reference), one decimal, clamped to 1–10.
- A program seen for the first time counts at its own level.

**Two scores:**
- **Our markets:** the six pre-game programs we trade.
- **All rewarded markets:** every program on the page.

**Profitable verdict, from the our-markets score:**

| Score | Verdict |
|---|---|
| 5 or under | **NO** (operator rule) |
| 5–8 | MAYBE (unproven) |
| 8 or higher | YES |

Anchors:
- 10/10 = the pools before Sep 24 (net about +$112/day).
- About 4.5/10 = the pools since (net about break-even).

**Record:** each run writes `latest-score.json` and appends `score-history.jsonl`, so the cutoff can be checked against each day's actual net.

**Discord:** the Reward Pools section is now one line (our score, all score, total pool offered now, Profitable?), plus a note naming new pre-game programs.

**First run (Oct 1):**
- Our markets 4.5/10 → profitable NO.
- All markets about 10/10. Most non-sports programs have no history before today's first page snapshot, so this score becomes meaningful as snapshots build up.


**Update the same day:** operator: "we can add it on reward puls actually like a field on the same section".
- The separate Reward Pools section is removed. The our-markets pool score is now the last column of the **Reward Pulse** section: `Date | Level | vs normal | Pool`.
- Past days get their score from the bot's recorded pools: `ours_by_day` in `latest-score.json`, about 10.0 before Sep 24 and about 4.5 since.
- The note under the section gives the all-markets score, the our-markets verdict (5 or under = not profitable) and any new pre-game programs.

**Fix (Oct 2):** the 07:20Z and 10:20Z cron runs were killed by the kernel (log line `Killed`). Loading every archived discovery record in full peaked at about **15 GB**. The tracker now streams those records and keeps only their pool lists: about **0.12 GB**.

**Pool change seen the same day:**

| Program | Before | Now |
|---|---:|---:|
| Table tennis pre-game | $15 | $10 |
| Esports pre-game | $50 | $20 |
| ATP/WTA pre-game | $100 | $60 |

- Live and futures pools for esports, table tennis, ITF and ATP/WTA were also cut 38–62%.
- Our-markets pool score: 4.7 → 3.0.
