# Calibration measurements — September 26, 2026

The operator asked what data to save "to better calibrate our gate entries or how we do things", then said "Lets do all that will help our bot". This builds on the market-quality tracker (collector live since September 23; `scripts/market_quality_report.py` already covers reward by priority grade and league). It adds only what was missing: checking predictions against outcomes. **Measurement only: no gate, cap, threshold or timing changed.**

## What was built

| Piece | Where | Live |
|---|---|---|
| Calibration report | `scripts/calibration_report.py` (read-only ledger, archives and logs; 0 API requests; about 55 s) | Yes. Restart-free deploy `e4a8665d5`, plus a daily 04:40 local cron (`--install-cron`) writing `data/calibration-reports/<UTC>/report.{json,md}` |
| Post-fill markouts | `src/liquidity_bot/fill_markouts.py`, `_sample_fill_markouts` in `runner_diagnostics.py`, called from the `run()` loop | Next Command 1 |
| Cap-blocked entries | `_note_entry_suppressed` at the pending-entry cap branch in `runner_decisions.py` | Next Command 1 |

**Report sections:**
1. **Fill model:** first-fill rate per 30 s of resting exposure against `MondayFillHazardModel`, in its own cells (league, price, queue, side, spread, time to event), plus a reliability table.
2. **Position episodes:** flat to flat, replayed with `MatchedInventory`, so prices use the ledger's LONG/YES convention. Each episode's P&L, hold time and exit path (passive reducer, taker exit, open/settled) is attributed to its entry quote's retained value, the 70% refusal flag, kickoff distance, predicted fill and league.
3. **Markouts** (archive kind `fill_markout`).
4. **Cap-blocked entries** (archive kind `entry_suppressed`).
5. **Pending-entry cap coverage** from `[window-risk]` cap lines. Bot downtime counts as shortfall.

**Bot-side records.** Both write only to the existing bounded, non-blocking market-quality archive, and neither reads the API or gates an order.
- `fill_markout`: mid price at 30 s, 2 min and 10 min after each owned fill, from the runner's own book. There is no history backfill, and a stale (>5 s) or late (>60 s) sample is recorded with `mid=null`. At most 600 samples are pending, and ledger polls use `rowid` every 2 s.
- `entry_suppressed`: at most one record per market, side and minute when the cap blocks a planned entry.
- On any exception, a measurement prints one `[measurement-disabled] component=…` line and switches itself off for that process, rather than failing silently.

## First readout (MEASURED, 7 days to 17:15Z September 26; trading P&L only, rewards excluded)

**Fill model: the live model over-predicts fills about 3.7×.**
- Across 57,175 entry orders and 3,621 resting hours there were 3,348 first fills: 0.77% per 30 s observed versus 2.83% predicted.
- Only the last 15 minutes before kickoff exceed the prediction (3.95% observed versus 2.91% predicted).
- Low-fill leagues are itfme 0.10%, utr 0.07% and wtadb 0.08%. setkamemd (1.57%) and setkameua (1.23%) fill most.
- The model's own comment says not to recalibrate it silently while its replacement data is collected. These cells are that data, and a refresh is an operator decision.

**Positions: 3,025 closed episodes, -$278.60 trading net.**
- Passive reducers: 2,078 episodes, **+$172.00**, 80% wins, 2.9 min average hold.
- Taker exits: 947 episodes, **-$450.61**, 2% wins, 11.7 min average hold.
- The loss is concentrated in positions whose passive exit did not fill in time.

**Retained value at entry** (per closed episode):
- 70–80%: -$0.34;
- 80–90%: -$0.21;
- 90%+: -$0.02 (2,073 episodes).

**Kickoff distance at entry:**
- more than 4 h: -$0.24, with a 31% win rate;
- 15–30 min: +$0.01, with a 70% win rate.

**Leagues** (per episode):
- utr -$0.82, cs2 -$0.60, itfme -$0.55, itfwo -$0.55 and pdcdarts -$0.56 lose most;
- setkamemd +$0.02 and setkawoua +$0.28 are positive.

**Cap coverage.** The cap was at its limit 37–64% of the time per day.
- The mean shortfall was 1.6–8.3 orders.
- The worst days, September 23 and 24, had many restarts.

**Example the same afternoon.** Short 15 at 0.53 on `aec-setkameua-slomyk-fasiva` (entered 17:04:43Z, about 20 min before kickoff). The passive reducer chased from 0.53 to 0.55 without filling. At 17:19:45Z the 15-minute inventory limit sent a taker IOC, filled at 0.58 (14) and 0.59 (1), about -$0.76. The event API reported the game pregame (`live=false`, 0-0) throughout.

## Interpretation limits

- The P&L is trading-only; the rewards these positions earned are in the market-quality report.
- Cohorts are confounded (league, price and kickoff distance correlate), and 7 days is short.
- **No setting should change from this readout alone.** Candidate decisions for the operator after 1–2 more weeks, each with this report as evidence:
  - refresh the fill model;
  - raise the retained-value threshold;
  - shorten the entry horizon or exclude leagues;
  - revisit the cap tiers once `entry_suppressed` data exists;
  - reduce taker exits, once markouts show whether they are adverse selection.

## Verification

- `tests/test_calibration_report.py` (8 tests):
  - exposure is censored at the first fill;
  - flat-to-flat episodes with exit classification, and owned-entry attribution;
  - markout sign, for both sides;
  - suppressed counts and coverage arithmetic;
  - archive checksum verification;
  - cron coexistence with log rotation, and `--install-cron` without `--db`;
  - an end-to-end run on a real SQLite file.
- `tests/test_fill_markouts.py` (3 tests) and 2 runner tests in `tests/test_live_commissioning.py`:
  - the cap-blocked entry is recorded once per minute and never sent;
  - the markout is sampled from the runner's book;
  - failure is visible and disables the measurement once;
  - `run()` calls the sampler.
- Deliberate reversals that each fail a test:
  - no first-fill censoring;
  - flipped markout sign;
  - no suppression hook;
  - no rate limit;
  - no loop hook;
  - silent failure.
- Full suite: 2,324 PASS.
- The restart-free deploy first refused because a timing-sensitive `test_adapters` test measured 0.0141 s against a 0.015 s floor under production load. It passed on retry, and fixing that test is filed as a separate task.

## Rewarded-market ranker (added the same evening)

`scripts/market_ranker.py` (`fdec829b9`, schedule `c3c6dd5cd`) ranks every rewarded moneyline/drawable market starting within 6 h. For each one it estimates net $/h: reward (live model, 2-cent rule relaxed for measurement) plus trading P&L from our league history.
- Leagues we rested in at least 3 days ago but were never paid for count as zero reward and are read last. Never-quoted leagues are marked `no_history`.
- It is read-only on the report lane, and the bot's API waits were unchanged while it ran.

**First run (19:47Z).** 817 open markets, 683 rewarded, 638 paying now, 148 scored in about 14 min.
- Never paid: bbl, r6, rl, **ufc**, utr.
- Our 32 traded markets averaged about -$0.09/h (model).
- 31 untraded markets were positive, most of them setkamemd books with spreads under 2 cents (the 2-cent rule).
- CFB pays about $0.002/h at our size, despite about $200 pools.
- These are preliminary, single-snapshot results.

**Schedule.** Runs at :05/:35 and nightly at 03:50 (with spreads/totals), with `flock` so runs never overlap. Output goes to `data/market-rankings/`.

**TEMPORARY:** the cron lines run the script from the Windows checkout (`/mnt/c/...`), because the restart-free deploy correctly refuses while undeployed trader commits are pending. **After the next Command 1**, switch the schedule to the production script:

```bash
cd /home/polybot/liquidity-bot && .venv/bin/python -B scripts/market_ranker.py --install-cron
```

## Command 1 activation of `9e3b4f6e8` (20:24–20:32Z)

**Review before restart.** The three pending changes were reviewed: calibration records, catalog/funnel fix and UTR/R6 exclusion. Warm-restart readmission also applies the exclusion (`_catalog_market_is_executable`).

**Self-stop just before the stop.**
- At 20:24:15, :19 and :23Z three `venue:` taker orders (not bot-created) closed positions on `aec-itfme-nazmel-yukike`, `aec-itfme-chekog-aledub` and `aec-setkameua-paryur-novrus`.
- The runner raised `RuntimeInvariantViolation: working order has no owned place intent`, first in private-stream handling, then in the decision sweep, and began its own shutdown. The operator's stop arrived at 20:24:34Z.
- This is the designed ownership fail-closed rule, not a code defect.

**Stop and deploy.**
- Gate 1 at 20:24:48Z: all zero.
- Deployed `9e3b4f6e8`: 16 changed-file hashes match; production suite 2,334 PASS.
- Gate 2 passed at 20:28:44Z; supervisor 724417, runner 724433.

**Acceptance at 20:32Z.**
- `[selection-exclusions] excluded_leagues=r6,utr`.
- Warm restart 31/32 (1 inside the flatten window).
- 26 placements; 0 Traceback, critical, pregame-unavailable, 404 and `[measurement-disabled]`.
- 1 benign `account_cash_report` retry (cash changed during the report).
- API 2.39/s, 0 rate-limited.
- 14 orders on 13 markets (cap 15 from the window loss).
- First funnel line: `rewarded=628 unsupported=0 no_period=478 spread_filtered=97 eligible=53 books_ranked=52 viable=46`.
- New archive records are flowing: discovery 1, entry_suppressed 70, fill_markout 3.

**Ranker schedule.** Switched to the production script with `--install-cron`.
