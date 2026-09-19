# Measurement and diagnostic checklist

Requested September18 ET for the September19 review. This is the collection
and review catalog, not the current runtime handoff: check [STATUS](../STATUS.md)
and live evidence first. No new collector, timer, reminder or trading rule is
created by this checklist.

## Tomorrow: review in this order

1. **Positions and exits:** verify current venue quantities and exact exit
   coverage, then measure observed fill to builder cancel, replacement gaps,
   deadline to IOC and actual flat time. Separate new fills from old positions.
2. **Cancellation churn:** if the new diagnostics have been deployed/restarted,
   inspect the exact cancel-time book and target price. If not, mark this
   question pending; identical replacement prices alone do not prove a bug.
   Use the [matched pre/post-rewards baseline](history/REWARDS_CHANGE_CANCELLATION_COMPARISON_2026-09-18.md):
   exit reprices113→352 while entry-touch cancels456→219;70.2% of current exit
   reprices concentrated in three day_of markets. Preserve interval/market mix.
3. **REST load and recovery:** inspect all comparable run windows for429s,
   endpoint/error categories, latency, cooldown and recovery completion. Compare
   periods with similar numbers of positions, not only total average requests/s.
4. **Rewards and trading results:** use closed-fill P&L and dated earnings,
   separate paid/pending/missing, and identify manual closes. Do not treat
   deposits, withdrawals or unrealized positions as trading loss.
5. **Reward productivity:** compare per-market rewards, entry side-hours and
   losses separately for daily/day_of and exact pool/quantity settings. Record
   coverage and whether enough completed periods exist before drawing conclusions.
6. **Capacity and selection:** explain unused entry capacity using eligibility,
   pending proof, GTD expiries, lifecycle turnover and current loss-tier cap.
   Confirm day_of priority; do not infer a problem from raw order count alone.

For each result record: ET/UTC interval, source commit, account mode, quote
size, configured slots, applicable entry cap, data capture time, position load,
sample count, missing evidence, measured result and next action. A before/after
comparison requires the actual restart boundary; prepared code is not live data.

## A. Trading/reward measurements

These observations do not themselves authorize a new filter or ranking.

| Item | What we measure | Evidence and review | Collection state |
|---|---|---|---|
| Per-market profitability | Paid/pending rewards, closed-fill trading P&L, fees, entry side-hours and quantity-hours | `scripts/market_economics.py`; private `data/market-economics/<run>/report.md` and JSON | Offline/on-demand analysis; existing ledger inputs live |
| Exact program cohorts | Results separated by daily/day_of, program, league, pool, target, discount and size | Same economics report; population coverage and withheld reasons | Measurement only; September18 report withheld all18 cohorts, not evidence of profitable rankings |
| Prospective profitability ranking | Frozen historical model compared with a later complete candidate scan | `--model-json` plus `--scan-log`; rank only supported eligible candidates within existing program tiers | Shadow comparison, not selection enforcement; requires dated pre-scan model |
| Complete selection slate | Up to100 evaluated candidates: program, timing, prices, pool/share, rank, reward projection, fill risk and exit depth | `[market-measurement]` in `logs/commissioning.log`, `enforcement=none` | Live scan logging verified; not a record of every possible market or executed entry |
| $1/$1.50 payout potential | Remaining reward estimate per selected market/period and qualifying counts | `[payout-estimate]`, `[selection-scan]`, summary counts | Live; no minimum-payout gate. Excludes already accrued rewards; never sum repeated scans |
| Entry quote/fill calibration | Actual attempted entry price/size, spread, depth, queue-at-placement estimate, touch changes, fill probability and later fill labels | `execution_quote_observations` joined with orders/fills; `scripts/quote_observation_report.py` | Live placement observations; on-demand read-only report. Quote observation is not proof of successful placement |
| Stressed exit / retained value | Estimated executable value, fees, completeness and60/70/80% refusal labels | Same quote observations: `stressed_exit_*`, `retained_value_ratio`, `would_refuse_retained_*` | Live shadow fields. The separately configured entry-quality rule is active behavior, not merely this measurement |
| Replacement and dark-slot cost | Available replacement candidate, selector age, projected reward delta and whether refusal leaves a slot idle | Quote-observation `replacement_*`, `slot_would_go_dark`; later outcome/reward attribution | Live placement snapshots, unknown remains unknown; no guaranteed counterfactual reward |
| Selection rank / shared reward pools | Whether placed markets were top-ranked and performance by shared program | Quote `selection_rank`, program identity; `scripts/program_reward_audit.py` | Live placement evidence; report on demand. Do not add the same shared pool once per member market |
| Resting participation | Daily side-hours, average resting sides, durations, GTD expiry/rebuild gaps | Orders/intents and `resting_time.py`; seven-day reward table; economics report for building-only entry rest | Existing ledger/report evidence. Distinguish report side-hours from building-only economics exposure and reward-eligible time |
| Reward accuracy and payment status | Venue market-day PAID/PENDING/SKIPPED versus estimates, missing dates and later revisions | Dated authenticated earnings snapshots; `reward_table.py`; saved economics `earnings.json` | Scheduled reward reporting plus explicit bounded report reads; missing is not zero and paid status is not a bank deposit |
| Settled trading outcome | Matched closed inventory and actual fees, including manual/unattributed flags | Canonical closed-fill replay, reward table and economics report | Live fills, reports on demand/schedule; open inventory and transfers excluded. Unmatched settlement is not fabricated as paired-fill P&L |
| Local reward score accrual | Integrated modeled reward score over time | `execution_score_days`, `[score-clock]` diagnostics | Existing runtime counter; not venue-earned dollars or payout proof |

### Data-quality rules for the above

- Default local-cache analysis can be stale. At September19 02:10:05Z the
  newest `execution_reward_earnings.observed_at` was **August21**, while entry
  quote observations were current through02:10:01Z. Do not confuse the old
  ledger earnings table with fresh report output or saved API snapshots.
- Use a known dated `earnings.json`, or explicitly fetch one account-wide
  earnings report through the active owner's shared report lane. Do not add
  per-market polling. A fresh earnings response can still contain pending or
  unreported periods; tomorrow may be too early for final payout comparisons.
- Keep manual closures, mixed programs, changed settings, cross-day/unmatched
  inventory and incomplete periods visible. Do not train on only the winners
  whose rewards happened to be posted.
- Cohort research defaults:20 distinct markets,3 completed ETdays and10
  side-hours plus complete attribution. These are evidence gates, not proof of
  statistical significance. Old day_of outcomes cannot validate new daily
  programs solely because the sport matches.
- Quote/fill calibration, fixed-rate reward estimates and hypothetical skipped
  entries have selection bias. No promise of higher profit follows from one day.

## B. Execution and operational diagnostics

| Item | What to check | Evidence | Collection state |
|---|---|---|---|
| Position response latency | Fill observation, builder cancel ACK, positive terminal proof, exact replacement ACK, deadline offset, actual flat observation | `[execution-timing]`, `[action]`, durable order/fill history | Live. Original opposite-side entries may already cover inventory; first new reducer age is not automatically uncovered time |
| Exact reducer decisions | Cancel-time bid/offer, candidate price, order direction/remaining size, preferred order, book age, reason and outcome | `[reducer-decision]` | **Prepared/pushed, NOT loaded as of this audit**; requires approved deployment/restart. ACK is not terminal/working proof |
| Position priority/fairness | Due exits before uncovered positions before optional covered repricing; simultaneous fills; expired/mismatched exit coverage | Existing timing/actions plus new priority repair `f54b540fe` and regression tests | Fix prepared/pushed, NOT loaded; live benefit still requires comparable post-restart observations |
| REST quota and latency | Weighted attempts/elapsed time, categories, reads/mutations, endpoint429s,404s, wait/response percentiles and cooldown | `[api-budget]`, `[api-backpressure]`; `scripts/api_capacity_report.py` | Live periodic counters; capacity projections are hypothetical, not permission to raise limits |
| Scheduler performance | Decision/action percentiles, event-loop delay, CPU, evaluated/skipped owners | `[decision-scheduler]`, `[priority-replan]` | Live. Whole-slate decision latency is not position-exit latency |
| Account proof and ownership | Reconciliation age, unresolved intents, unknown/absent orders, terminal repair and exact size | `[account-watch]`, `[rest-reconcile]`, `[unresolved-repair]`, ledger plus bounded authenticated snapshot | Live safety machinery. Sequential venue reads are not an atomic snapshot; no safety bypass |
| Public feed / recovery | Stale-book causes, rebind scope, quarantine, recovery attempts, eventual resume and exit continuity | `[book-stale-why]`, `[runtime-recovery]`, recovery/quarantine tags, `[selection-books]` | Live event diagnostics. Missing events in a short window do not prove a feature is disabled |
| Private WebSocket delivery | Auth/handshake, Ping/Pong, application frames and subscription-specific snapshots/updates | Explicit bounded private-socket diagnostic only | **On demand, not continuous in manual REST mode**; no automatic source switching or probe loop added |
| Selection and market turnover | Scan duration/supply, viable rank, missing rewards, pregame status, top-up replacements and idle retention | `[selection]`, `[selection-scan]`, `[eligibility]`, `[top-up*]`, `[event-started]` | Live; diagnosis must account for five-minute entry expiries and fills |
| Risk/capital reporting | Six-hour settled loss, current cap, hold/reset, buying power and proof freshness | `[window-risk]`, cap/hard-stop/provisional tags, summary, risk tables | These reflect **active controls**, not experiments. Four$25 windows and existing caps remain unchanged |
| Fee observation | ET-day taker/execution fees for attribution | `[execution-fee-observation]`, fill records | Telemetry only, not an additional fee-triggered stop |
| Maintenance/entry pauses | Schedule,15-minute buffer, entry latch, delayed recovery and exit continuity | Maintenance and `[entry-pause]` tags; existing status/changelog checks | Active safety controls with diagnostics; no induced maintenance or force-stop test |
| Reporting/host/storage health | Reward webhook freshness/failure, changelog monitor, process/watchdog, backup age/integrity and ledger growth slope | Existing reporting/monitor/backup/watchdog artifacts; SQLite online backup/file sizes | Existing tools; independently verify latest run when reviewing, not assumed healthy from code presence |

## C. Inactive or historical measurements

- Queue-depletion/almost-front public-trade collector: retired; historical
  tables/reports remain. This differs from the still-recorded queue estimate
  at entry placement.
- Separate spread-scalp/fixed-GTC collector: stopped; historical artifacts only.
- Daily selection probe: disabled; not the same as the live in-run selection
  measurement logger.
- Kickoff/hour and size20 studies: closed historical analysis, not active policy.
- Proposed0.15 entry-floor counterfactual: not collecting and not enforced.

## Read-only review recipes

Run on the active laptop checkout with `PYTHONPATH=src`. These are manual
commands, not scheduled jobs; the agent can run them when asked tomorrow.

```bash
# Local entry/fill calibration; no API.
.venv/bin/python -B scripts/quote_observation_report.py --recent-hours 24

# Local observed API windows; same capacity, not a proposed increase.
.venv/bin/python -B scripts/api_capacity_report.py --source-slots 32 --target-slots 32 --last 60

# Reward model with explicitly fresh dated earnings on the shared report lane.
.venv/bin/python -B scripts/market_economics.py --days 14 --fetch-earnings --limiter-state data/api-rate-limiter.state

# Pool-grouped legacy audit without a network call; check its cache age first.
.venv/bin/python -B scripts/program_reward_audit.py --days 14 --local-only
```

Do not run several authenticated reports at once. Reuse the saved earnings
snapshot for further economics analysis. Frozen-model comparisons must use a
later scan and the documented no-lookahead rules; a fresh retrospective report
must not be presented as a prediction known before its own capture time.

Inventory audit September19 02:10:05Z used only local read-only evidence:
last5000 log lines contained8 complete-scan records,252 payout-estimate records,
116 API-budget windows,114 scheduler records and1,344 execution-timing rows.
These are bounded-log counts, not daily totals or separate independent samples.
No reducer-decision rows were expected because the new hook is not loaded.

References: [exit review](history/EXIT_PRIORITY_REVIEW_2026-09-18.md),
[economics methodology](history/MARKET_ECONOMICS_MEASUREMENT_2026-09-18.md),
[payout measurement](history/REWARD_PAYOUT_MEASUREMENT_2026-09-18.md),
[watchlist](../WATCHLIST.md). Tomorrow's outcome belongs in a new dated handoff,
not an unqualified claim that all measurements are already validated.
