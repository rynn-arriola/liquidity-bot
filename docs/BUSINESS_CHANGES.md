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
