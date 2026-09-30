# Pool Watch — September 30, 2026

**Operator request:** detect from the rewards data whether the pools are good. "Build it but dont connect it to the bot yet", and "dont look at the payment we got but the pool that was offered".

## Built

`scripts/pool_watch.py` runs every 3 hours from cron.
- **Sources:** the pools the bot recorded as **offered**, from every entry quote (program id + pool, since Sep 14) and every program its scans saw (since Sep 23).
- **Does not:** call the API, trade, or post to Discord.
- **Output:** pool per program family and day, before/now changes, and alerts for NEW programs or CHANGED pools in the last 2 days.
- **Files:** `data/pool-watch/latest.md|json`.
- **Tests:** `tests/test_pool_watch.py`.

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
