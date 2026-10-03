# Trade Scorer shadow — paper trading on any market (2026-10-03)

**Operator:** "While doing this can we also shadow other markets? Because right now we are limited correct?", "I think we can apply same criteria of how we pick trades on trade scorer", "Sure it does not need to be reward market as long its passing the 60 percent", "We should test as much as we can", "once we go live we will use it as based model and will calibrate it again as we see results on trade scorer".

## What it is
- `scripts/scorer_shadow.py` (process, store, report) + `src/liquidity_bot/scorer_shadow.py` (fill/exit logic). Read-only: no order endpoint is called.
- Every pregame moneyline market within 6 h of its start (any league, rewarded or not), up to 100 at a time (the documented WebSocket subscription ceiling), chosen round-robin across leagues, soonest game first.
- For each market side it computes the same traits the Trade Scorer uses at the exact price the bot would quote, applies the bot's non-reward checks (price floor, stressed exit complete, entry quality ≥ 0.70), scores it and rests ONE pretend entry, whatever the score, so both sides of the 60% line are measured.
- **Fills (strict):** a public trade through our price fills us; a trade at our price first consumes the size that was ahead of us; an opposite order at/through our price in the book fills us. No credit for cancellations ahead of us.
- **Exit (the bot's rules):** passive reducer one tick inside the touch while not losing (`exit_quote_price`), else at the touch; forced taker exit 15 min after the fill or 5 min before the start (`cross_exit`, 0.5 depth haircut, taker fee), deferred to 5 min before start when the early cross would lose more than `max_early_exit_loss` (size × 0.8).
- **Simplifications:** the pretend entry is not repriced while resting (10-minute life); maker fees/rebates are zero; partial fills are kept.

## Runtime
- Code runs from a separate detached git worktree `/home/polybot/scorer-shadow` (so the live checkout stays exactly at the loaded release); working directory, data, venv, credentials and the **host-wide API gate** are the live bot's (`state_path=data/api-rate-limiter.state`, report lane: only unused slots).
- Cron (`# liquidity-bot scorer shadow`): `*/10` with `flock -n data/scorer-shadow/cron.lock` (restarts it if it stops), report at :50 hourly. Log `logs/scorer-shadow.log`; store `data/scorer-shadow/shadow.db`; report `data/scorer-shadow/latest.md|json`.
- Update the code: `git -C /home/polybot/scorer-shadow checkout --detach <commit>` after fetching; the next cron restart picks it up after the running process is stopped.
- Stop: remove the two cron lines and kill `scorer_shadow.py run`.

## First trial (02:34Z Oct 3, 3 minutes)
- 100 markets, 23 leagues; 168 paper entries (147 scored ≥ 60%); first close +$0.15.
- Live bot during the trial: 1.8–2.1 rps, 0 rate-limited, 0 errors.
- Note: the allowed share (87%) is much higher than in the live bot (about 40%); watch whether those scores hold up (calibration table in the report) before trusting them on new leagues.

## How it will be used
Evidence for which leagues/markets the scorer can trade beyond rewarded markets; later the live results recalibrate the model (operator).
