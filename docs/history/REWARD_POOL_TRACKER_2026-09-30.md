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

