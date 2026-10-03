# Late-favourite tracker (2026-10-03)

**Operator:** "I want something that secures winning trade we stay at the trade until its almost over and we buy that trade like when its 90 percent winning and there is not much time left", "So we only make a trade when match almost done and we bet on it", "This dont need to be reward market", "I think we should observe our data first then check how that gonna work on polymarket side", "Ok build the tracker now make sure its all the live sports market so we get alot of data".

## First look at our own data (August 28 scalp study, 75 settled moneylines)
- Buying the favourite the first time it cost >= 85/90/95c after the start: won 84/91/93%, **-3.5 / -0.9 / -3.1 c per contract** (upsets even at 95-98c).
- Buying only in the last 5/10/20 minutes of the recorded data at >= 90c: won 98/95/93%, **+4.7 / +2.0 / +0.6 c**. This uses hindsight (the data end), so the real question is whether the live game state can tell "almost over"; one day, mostly table tennis.

## Tracker
- `scripts/late_favorite_tracker.py` + `src/liquidity_bot/late_favorite.py`; read-only, no order endpoint.
- Every 30 s the sports event listing (started in the last 14 h, all leagues, all market types, rewarded or not) gives each event's live state (period, clock, score, per-period scores, live/ended, sport) and every market's best bid/ask; only changes are stored. After an event ends (or a market leaves the listing) its settlement is read (retry every 10 min, give up after 48 h).
- Report: favourite (>= 85/90/95c) bought at the ask with the market's fee coefficient, held to settlement; by sport, by period (what the bot can see live) and by minutes before the end (hindsight reference).
- Runtime: worktree `/home/polybot/scorer-shadow`, live bot directory as working directory (shared API gate, report lane). Cron `# liquidity-bot late favorite tracker`: `*/10` keep-alive with flock, report at :55. Store `data/late-favorite/tracker.db`; report `data/late-favorite/latest.md`; log `logs/late-favorite.log`.
- First cycle 15:10Z Oct 3: 175 started events, 85 live (soccer 32, hockey 16, tennis 12, esports 9, table tennis 8, basketball 6, football, cricket), 1,003 markets (76 moneylines), 0 errors.
