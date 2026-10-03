# Business-logic change log

**Operator rule (2026-09-27):** every change to how the bot trades must be written down here before it goes live, so we can judge later whether it helped. A change to trading logic, risk, size, order count or market selection counts; pure bug fixes and reporting do not.

Each entry records:
- the **baseline** (numbers before);
- the **expected effect**;
- **how to evaluate** it (metric, source, date);
- **how to undo** it;
- the **verdict** once measured.

## How to evaluate

**Sources:**
- **Reward Pulse:** `data/reward-pulse/<day>.md` and the Discord *Reward Pulse* section.
- **Daily calibration report:** `data/calibration-reports/`.
- **Logs:** `logs/commissioning.log` (`[thin-book-skip]`, `[selection-scan]`, `[window-risk]`, `[api-budget]`).

**Rules:**
- Judge whole ET days. Wait for payouts, which post the next day.
- Compare against the baseline window written in the entry.
- Pools changed around Sep 24–26 (about −50%), so reward comparisons must use days on the same pool level.

**Verdicts:** `KEEP`, `CHANGE` or `REVERT`, dated, with the numbers.

---

## BC-2026-10-02-B — Scorer mode: rest only entries the Trade Scorer rates ≥ 60% (normal reward farming switched off, not removed)

- **Status:** code BUILT and tested. Needs the operator's confirmation of the two config files, then a restart at a window boundary (the risk limits change).
- **Operator:** "Ok lets do what u recommend, but do not delete how we normally farm reward this is a different way to make profit on this bot, we will just disable for now the normal way we farm reward"; "We are just switching off the normal way to farm reward and enabling this one for now" (2026-10-02). Recommendation accepted: 60% minimum, at most 10 orders, $10 stop per 6-hour window.
- **Switch:** `config/trade_score_policy.json` `mode`: `"scorer"` = this mode; `"reward"` (or no file) = normal reward farming exactly as before. The reward-pool pause (BC-2026-10-02-A) applies only in reward mode.
- **Rule (scorer mode):**
  - Every existing check still runs (selection, entry quality, thin-book, reward/exit economics, risk caps). On top, a NEW entry quote is placed only if the Trade Scorer's chance of a profitable exit is ≥ 0.60 (`trade_score_below_minimum` otherwise; idle slots rotate as for other economic refusals).
  - A side already resting is never cancelled by the score; exits and reducers never consult it.
  - The model is the trade-scorer project's daily model (retrained 05:30 ET, exported to `data/trade-scorer/latest-model.json`), reloaded within 10 minutes of a change. A retrain with test AUC < 0.70 is rejected and the last good model kept. No model, or a model older than 72 h: no new entries (fail closed, `trade_score_model_unavailable`).
  - The scoring code is a copy of the trade-scorer math; a parity test checks 300 random entries against the project to 12 decimals.
  - Evidence: `[trade-score]` / `[trade-score-model]` / `[trading-mode]` log lines; each placed entry's quote observation records `trade_score_win_probability` and `trade_score_model`.
- **Risk limits for this mode** (`config/risk_window_policy.json`): 10 pending entries, $10 window stop, step-downs 8/6/4/2 at $2/$4/$6/$8 loss.
- **Why:** reward pools are low (our score 3.0/10) and normal farming lost on trading. In the Trade Scorer's shadow check (692 trades Sep 29–Oct 2, model saved before each trade) the ≥60% trades made +$16.71 (260 trades, about 4 in 5 profitable) while all trades lost −$65.76; the held-out test showed the same pattern. Rewards still accrue on what rests.
- **Baseline:** normal farming Sep 28–30: trading −$12 to −$40/day, rewards $28–68/day; Oct 2 afternoon: paused by the pool gate ($0/$0).
- **Expected:** trading about break-even to +$5–15/day plus small rewards; far fewer orders resting.
- **Known limits:** about 3 days of evidence; per-trade gains are small (about 6¢); the model only saw trades taken while farming rewards; once live it mostly sees trades it approved, which can bias later retrains (check the shadow AUC on scorer-mode trades).
- **Evaluate after 3 days:** trading $/day (settled fills), rewards/day (Reward Pulse), share of `[trade-score]` allows, profitable-exit rate of placed entries vs predicted, window stops hit.
- **Undo:** `mode: "reward"` in `config/trade_score_policy.json`, restore 30 orders / $40 / 24-18-12-6 in `config/risk_window_policy.json`, restart at a window boundary.
- **Verdict:** pending.

## BC-2026-10-02-A — Pause new entries while our reward pool score is 5 or under

- **Status:** LIVE since 16:31:13Z Oct 2 in `453726061` (Command 1). First check 16:32:10Z: score 3.0 → paused new entries, 0 orders/0 positions. Switch: `config/reward_pool_gate_policy.json` (`enabled`).
- **Operator:** "I think lets add the reward pool filter now"; chose "Pause new entries when score is 5 or under" and an hourly pool refresh (2026-10-02). Earlier rule: "Make sure score from 5 under means its not profitable".
- **Rule:**
  - Read the Reward Pool Tracker's our-markets score (`data/reward-pool-tracker/latest-score.json`, `ours.score`) once a minute.
  - Score ≤ 5.0: pause NEW entries through the existing entry pause (reason `reward_pool_low`); resting entry quotes are cancelled, exactly as for maintenance. Cancels, reducers and position exits keep running.
  - Score > 5.0: resume.
  - Missing, unreadable, future-dated or older than 3 hours: no pool evidence, so trade normally (log `[reward-pool-gate] ... detail=score_missing|score_unreadable|score_stale_*`).
  - Tracker cron changed from every 3 hours to hourly at :20, so a pool change shows within about an hour.
  - Discord gets the normal "paused NEW ENTRIES ... Reason: reward_pool_low" / "resumed" messages.
- **Why:** the pools offered on our six program families fell to 30% of their 30-day best (score 3.0 on Oct 2, 4.7 Sep 24–Oct 1). At those pools rewards barely covered trading: Sep 28–30 rewards $28–68/day against trading −$12 to −$40/day.
- **Baseline (Sep 28–30, 50 orders, pool score 4.7):** rewards $28–68/day, trading −$12 to −$40/day, about 1,000 order-hours/day (BC-2026-10-01-A).
- **Expected:**
  - At today's score (3.0) the bot rests nothing: rewards and trading losses both go to about $0.
  - When pools recover above 5 it resumes automatically.
- **Risk:** the score compares to the best pool of the last 30 days, so if Polymarket keeps the lower pools for good, the score stays low and the bot stays paused. Review the threshold on the Sunday Oct 4 review.
- **Evaluate:** daily `[reward-pool-gate]` / `[entry-pause]` lines, hours paused per day, and for resumed days rewards vs trading (Reward Pulse).
- **Undo:** set `enabled` to `false` in `config/reward_pool_gate_policy.json` and restart (Command 1). Changing the threshold works the same way.
- **Verdict:** pending.

## BC-2026-10-01-A — Lower pending entries 50 → 30 while pools are low

- **Status:** LIVE since 16:02:23Z Oct 1 (12:00 ET boundary) in `ff9627ab8`. The window row confirms cap 30 with tiers 24/18/12/6 and the $40 stop. Two operator manual closes that morning (10:40 and about 11:55 ET) made the runner self-stop, so it was force-stopped with the venue proven flat and the records reconciled read-only each time.
- **Operator:** "Can you make max orders to 30 for now", "We will increase it once reward go back".
- **Rule:**
  - Pending-entry cap 30, REST ceiling 30.
  - Scaled step-downs 24/18/12/6 at $8/$16/$24/$32 settled loss.
  - $40 stop and size 10 unchanged.
- **Why:**
  - Pools offered are about half of before (Reward Pool Tracker, our-markets score about 4.7/10, under 5 = not profitable).
  - More resting at low pools did not raise rewards: Sep 27–28 had 943–1,026 order-hours for $43–67.
- **Baseline (50 orders, Sep 28–30):**
  - 89–99% of cap used, about 1,000 order-hours/day.
  - Rewards $28–68/day, trading −$12 to −$40/day.
- **Expected:**
  - Fewer forced exits and lower trading loss.
  - Rewards down somewhat less than proportionally, because the ranker fills the best markets first.
- **Evaluate after 3 days:** trading $/day, rewards/day (Reward Pulse), order-hours/day and pool score.
- **Revisit when the Reward Pool Tracker pool score rises above 5:** raise the cap again then.
- **Undo:** restore 50 and 40/30/20/10 in `config/risk_window_policy.json` and the 50 ceiling in `config/account_source_policy.json`, then restart at a window boundary.
- **Verdict:** pending.

## BC-2026-09-27-A — Skip new entries on thin one-tick books in high-loss leagues

- **Status:** LIVE since 17:44:04Z Sep 27 in `4adbafb38` (Command 1; the runner had self-stopped after a manual close, was force-stopped with the venue proven flat, and the ledger was reconciled). Evaluation window: Sep 28–30 ET. Switch: `config/thin_book_policy.json` (`enabled`).
- **Operator:** "Ok do it" (option b, the narrow filter), 2026-09-27.
- **Rule:** do not place a new entry quote when the spread is 1¢ **and** fewer than 50 contracts sit ahead at our price, **and** the league is one of: ITF M/W, WTT, CS2, Dota 2, darts, Valorant, Modus, WTA, ATP doubles.
  - A side already resting is never cancelled by this rule.
  - Exits never consult it.
  - A market blocked on both sides counts as idle, so the existing 10-minute replacement moves the slot elsewhere.
- **Why:** in those setups 55–80% of positions ended in the forced 15-minute taker exit (taker fee 1.5¢ against a 0.3¢ maker rebate). Exit-timing changes were tested and do not help. Evidence: [TRADING_LOSS_TESTS_2026-09-27](history/TRADING_LOSS_TESTS_2026-09-27.md).
- **Baseline, Sep 19–27 (8 days):**
  - Trading −$355 (−$44/day).
  - Affected setup: 162 positions, −$13.05/day.
  - About 8.5% of our resting order-hours (603 order-hours/day total).
  - Rewards at the new pools: $25–32/day.
- **Expected:**
  - Trading loss about $13/day smaller.
  - Reward down at most about $4/day (the slots move elsewhere, so probably less).
  - Rest hours down at most 8.5%.
- **Evaluate after 3 full days live:**
  - `[thin-book-skip]` counts.
  - Forced-exit rate and trading $/day in the listed leagues against baseline.
  - Total order-hours/day.
  - Reward Pulse $/order-hour against the same-pool days (Sep 25–26).
  - **KEEP** if trading loss/day falls by at least $8 and reward/day does not fall by more than $5.
  - **Widen** to all leagues (option a, about +$19/day estimated) only if KEEP.
- **Undo:** `"enabled": false`, then a restart.
- **Verdict:** pending.

## BC-2026-09-27-B — Size 10, 50 pending entries, $40 window stop, scaled step-downs

- **Status:** LIVE since 05:46:54Z Sep 27 (mid-window, via a one-time operator-authorized window-policy override; see STATUS).
- **Operator:** "10 contract, 50 orders, 40 stop loss", "And scale it".
- **Rule:**
  - Quote size 10.
  - Pending-entry cap 50.
  - Step-downs to 40/30/20/10 at $8/$16/$24/$32 settled loss.
  - $40 stops new entries for the window.
- **Baseline:**
  - Size 20 with 40 orders (22:00Z Sep 26 → 05:04Z Sep 27).
  - Size 15 with 30–40 orders before that.
  - Sep 25–26: about 22–23 orders resting on average.
  - API about 2.2/s, 1 limit hit in 7 h.
- **Expected:**
  - More resting orders and markets at about the same total contracts resting.
  - Smaller loss per forced exit.
- **Early reading (Sep 27, 06–11 ET):**
  - 44–49 orders resting (89–99% of cap), 34–38 markets.
  - API 2.0–2.4/s (peak 3.5), 0 limit hits.
  - 404 "order not found" spike, 1,555 in the 8 AM ET hour.
- **Evaluate after 3 full days:**
  - Order-hours/day.
  - Reward Pulse $/order-hour and reward/day (same-pool days).
  - Trading $/day.
  - Forced-exit $ per position.
  - API limit hits and risk-pause cancels.
- **Undo:** change `config/risk_window_policy.json` and the start size at a window boundary, then Command 1.
- **Verdict:** pending.

## BC-2026-09-26-C — Ranker slot priority

- **Status:** LIVE since 23:18:31Z Sep 26. Switch: remove `data/market-rankings/priority.json` or stop the ranker cron.
- **Rule:** the ranker's top 50 rewarded markets (corrected net $/h, never-paid leagues and never-quotable markets left out) get book reads and free slots first. Every live check still applies.
- **Baseline:** the bot's own ordering (day-of first, then projected reward).
- **Evaluate:**
  - `[selection-scan] ranker_priority_viable`.
  - Reward Pulse $/order-hour for ranked against unranked markets.
- **Verdict:** pending. The pool cut the same week makes before/after comparisons hard, so compare ranked against unranked on the same days.

## BC-2026-09-26-D — Size-20 / $40 trial

- **Status:** ENDED Sep 27 05:04Z. The operator switched to size 10 (BC-B).
- **Verdict:** not measured on its own (it ran for about 7 hours, overlapping the pool cut).

## BC-2026-09-26-E — Exclude UTR and R6

- **Status:** LIVE since 20:28Z Sep 26 (`config/selection_exclusions.json`).
- **Why:** 53 of 53 UTR markets we rested in were never paid.
- **Verdict:** KEEP. UTR stayed unpaid through Sep 26 (Reward Pulse: Dead, $0).
