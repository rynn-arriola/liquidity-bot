# Market-quality grade tracker — design and implementation handoff

September 23, 2026. Design only; not implemented or collecting new fields.
Existing rank/quote/fill measurements continue. No selection, risk, account
source, scan breadth, deployment or restart change is authorized by this design.

## Objective and acceptance question

Identify changes to market selection that improve realized net earnings, not
merely order count or headline reward pools. Test whether priority at decision
time predicts later paid reward and net contribution per resting side-hour.
Track total net and occupied slot-hours too: a fast fill can have little resting
time but tie up a position slot for a long time. No single ratio proves quality.

The operator requires full selection-flow coverage and minimal REST pressure.
Chosen design: append bounded local evidence from existing producers, then
analyze offline. Target **zero incremental REST requests**, unchanged retries,
polling intervals, subscriptions and discovery depth. Do not revive collectors.

## Confirmed foundation and gaps

Read-only ledger sample ending 2026-09-23 14:06:34 UTC: 10,000 quote observations,
289 distinct markets, 330 selection bindings, all size15 and all with original
rank. Proposed grade distribution by binding: S187/A80/B40/C22/D1. These are
not independent completed profit samples. Thirteen of 297 market-days contained
multiple grades; a whole daily payment cannot be credited to each grade.

`selection_provenance.py` preserves original and current rank per process
binding, snapshot identity and binding time. Restart/rebinding starts a new
scope; it is not a durable first-ever market rank. `selection_measurement.py`
records evaluated-book candidates, not the entire discovered universe.
`market_economics.py` currently aggregates current-rank sets and approximate
request-to-terminal order intervals, not grade-attributed confirmed exposure.

The runner's `_refresh_earnings` fetches account-wide earnings but persists only
active-market rows to its existing session cache. The inspected cache contains
only 13 old August rows; this is not evidence of no September earnings.
Saved separate account-wide September snapshots exist. Preserve session-cache
semantics; add a separate complete-response measurement archive, including
retired markets, rather than relabeling account-wide rewards as bot-owned.

## End-to-end evidence contract

| Stage / current producer | Record from information already available | Question it supports |
|---|---|---|
| Discovery / `market_selector.py` | Scan ID/time/type (full or revalidation), policy/source version, discovery bounds/pages/completeness, counts and market identity/event/league/type/start/status | Was a market outside our scope, genuinely ineligible, or missing? |
| Metadata and reward filters | Exact pass/refusal/unknown reason, observed value and threshold, receipt age; program ID/type/window/pool/target/discount; missing versus unsupported versus inactive/ambiguous | Which eligibility rule removes candidates? |
| Book selection | Pre-book rank, detailed/deferred/failed state, evaluated count, budget/cursor and chosen universe | Did the first100 bound prevent evaluation? Deferred is not failed. |
| Detailed per-side evaluation | Existing spread, prices, depth, queue/share, size, estimate horizon/haircut, projected and remaining reward, fill probability, stressed exit cost/completeness, retained value, time to event and blockers | Which entry features predict reward and losses? |
| Rank | Global and within-program-tier rank, viable candidate counts, exact ranking components/model version, immutable snapshot ID, proposed grade | Did priority predict results, and what caused priority? |
| Allocation / runner | Selected/retained/replaced/excluded/not selected; binding ID; capacity and loss-tier cap; existing positions/reservations; both side admission decisions and refusal reasons | Was non-entry caused by economics, capacity, ownership or a safety hold? |
| Submit and execution | Join scan/binding to client order and venue ID; intent, acknowledgement, first/last confirmed working state, expiry/cancel request and terminal proof, partial fills, replacements, final send-time gate outcome | Did a selected market actually receive useful resting exposure? |
| Position and exit | Owned fill lineage, matched closing fills/fees, holding time, reducer coverage/delay, exit reason and deadline lateness, manual/unowned legs, reversals and remaining inventory | Is loss attributable to market selection or execution/recovery? |
| Reward outcome | Already-fetched response ID, acquisition start/end, source/schema/completeness and earning date/market/program/status/amount; revisions and attribution status | Were predicted rewards actually paid, and can they be assigned? |
| Operating conditions | Existing book/account source, age, maintenance/recovery/429 state and uptime intervals, policy/release/size changes | Are comparisons confounded by outages or different bot versions? |

Capture both market-level and side-level evidence: rank is market-level, while
one or two entry orders may rest. Reducers are not new entry participation.
Record every applicable filter result when its input is already available;
otherwise record the first blocking stage and downstream `not_evaluated`.
Do not fetch a book to complete a rejected/deferred candidate's record.
Do not store full raw books on every tick or every iteration.

### Shared pools and competition (operator additions)

Preserve the exact program/pool identity, period boundaries and reward date.
Do not merge programs merely because their names or dollar amounts match, or
sum the same pool once per market. When the feed cannot prove pool identity or
allocation scope, mark it ambiguous. Existing projection is a model, not proof
that each listed market receives the entire displayed pool.

From existing discovery/incentive responses, record distinct **events** and
distinct **markets** belonging to each exact pool, separately. One event may
contain several markets. Also record observed active, strategy-eligible,
book-evaluated and actually traded counts, with snapshot timestamps and scope.
Do not confuse a union over many scans with simultaneous pool participation.
The bot's bounded sports/timing/moneyline discovery cannot establish venue-wide
pool membership: call such counts `observed_events`/`observed_markets` and set
`pool_population_complete=false` unless the original response positively proves
the whole pool. Missing membership is unknown, never a small denominator.
No new catalog/pool enumeration request to fill this gap. Pool/event ratio can
be an explicitly modeled feature only, not an equal-share entitlement.

Competition features from already-received books: each side's qualifying depth,
depth ahead/at price, discounted total score, own modeled score/share, ticks from
best, concentration by price level, and their observed changes during residence.
Weight summaries by covered exposure, preserve timestamps, stale/gap fractions
and own-order inclusion assumptions. `project_order_reward` deliberately adds
own size conservatively to the denominator when own presence is uncertain;
`total_score - order_score` is therefore a proxy, not measured competitor size.
Public aggregate depth does not identify unique market makers. Never label
number of levels/orders as number of competitors or infer trader identities.
Do not increase public subscriptions, book refresh or REST rates for these
features; missing observations remain missing. Test whether pool crowding and
local book competition improve paid/net prediction on later completed periods.

## Identity, grades and accounting rules

- Link scan -> candidate -> binding -> order -> fill/closed inventory episode.
  Include run identity and original snapshot; preserve unknown legacy evidence.
- Proposed priority grades v1: S1-5, A6-10, B11-20, C21-30, D31+. Store exact
  ranks, cohort size and tier alongside the label. Grade is predicted priority,
  not measured quality. Definitions and policy versions must be reproducible.
- Never regrade a historical entry using later prices, scans or reward results.
  Report original-binding priority separately from priority at each new entry.
- Outcome unit is the finest **provable reward attribution unit** supported by
  recorded responses, presently market/earning-date/program where unique. Use
  the earning date, not cash-posting date. Do not invent per-order payouts.
- Multiple grades, programs, sizes or versions inside one reward unit remain
  Mixed/unallocated in the primary grade analysis. Preserve their costs and
  exposure in coverage totals; do not silently remove likely losers. A weighted
  allocation may be shown separately as a model, never actual earned dollars.
- Keep paid, pending, explicitly skipped and missing distinct. Deduplicate
  snapshots/status revisions; never sum repeated fetches or repeated shared
  pools as new rewards. Failed/incomplete response is not an empty account.
- Use matched closed-fill P&L and actual fees/rebates once. Existing
  `MatchedInventory.apply` already includes fees. Deposits, withdrawals,
  open marks and rewards are not trading P&L. Incomplete inventory, unresolved
  settlement/manual attribution and cross-day episodes stay flagged/pending;
  do not promote their partial net as final profitability.
- Rest-time evidence must distinguish confirmed/bounded local resting time
  from venue-qualified scoring time. Preserve send/ACK and terminal uncertainty
  bounds, gaps and partial-fill quantity changes; never label an inferred span
  exact. Union same-side overlap; retain contract-hours; separate position
  occupancy and exit orders. Clock resets, ET midnight/DST and restarts need
  explicit treatment. Uncertain exposure cannot produce a certified hourly rate.

## Architecture and resource isolation

Extend existing bounded measurements, not the strategy interface. Proposed
`market_quality_measurement.py` is a pure versioned evidence builder;
`market_quality_store.py` is an optional, private, separate diagnostic store;
`scripts/market_quality_report.py` is an offline reader with no API client.
Reuse quote/fill/P&L primitives instead of introducing a second trading ledger.

Archive successful complete existing reward fetches before active-market
filtering via the optional measurement sink. Account-wide data stays private;
the report must separately prove bot ownership. No new reward polling loop,
pagination request, recovery retry or per-market endpoint call for this feature.
If an existing fetch is incomplete, retain its incompleteness and wait.

Use bounded nonblocking enqueue at existing event boundaries and a single
background writer to an ignored local store. No synchronous disk flush or full
ledger replay on the order/exit path. No unbounded tasks. Writer failure, queue
overflow and disk-full produce bounded local health/drop counters and an
explicit coverage gap; they cannot block/cancel entries, reducers or shutdown.
No raw auth payloads, credentials or webhook URLs in evidence. Version the
schema, bound record sizes/retention/storage and make enable/disable explicit.
Offline reports use short consistent read-only transactions. Legacy/backfill
is limited to facts already recorded; absent event history remains unknown.

## Reports and decision discipline

Primary grade table: unique markets/events, completed earning days, exposure
and coverage percentage, paid/pending, settled trading P&L including fees,
paid reward/hour, paid net/hour, total net and position/slot occupancy.
Add cancellation/replacement rates, fill rate, holding time, exit loss/delay
and estimate-versus-paid calibration. Counts are not repeated quote samples.
Show raw totals plus exclusion/missing/mixed totals so coverage is auditable.

Stratify by day_of/daily_event, size, reward parameters, league/market type,
entry timing, spread/depth/retained-value bands and operational/release regime.
Avoid an overly fragmented automatic model; show uncertainty and sample sizes.
Minimum existing research floors (20 markets/3 completed days/10 side-hours)
are not statistical confidence. Cluster comparisons by market/event/day rather
than treating hundreds of quotes from one game as independent evidence.

Evaluate candidate filter/rank changes offline using only information known at
the decision. Freeze proposals before a later completed-day holdout; prevent
reward-revision lookahead. Report excluded exposure, historically paid reward
and realized costs separately. Historical removed losses are not guaranteed
future savings: replacement trades/queue position/fills are unobserved.
Untraded/rejected candidates have no actual bot payout. Their recorded features
can support coverage and model comparisons, not counterfactual profit claims.
No deliberate lower-grade trades for data and no automatic rule promotion.

## Implementation phases and acceptance

1. Version the evidence and joins; capture selection-stage reasons and allocation
   context locally; build pure grade/coverage aggregation against saved data.
2. Add isolated whole-response reward archive and order-state exposure evidence
   using existing callbacks only. Preserve the execution ledger and old reports.
3. Add attribution, partial-fill/rest bounds, net/grade reports and later-period
   validation. Exercise existing records first; label missing new fields.
4. Review exact diff, regressions/reversals/full suite and edge cases. Deploy or
   restart only on later explicit authority; shadow acceptance precedes any
   operator-approved selection change. Keep hybrid/scanner branches separate.

Required tests: identical gateway call sequence/count and identical trading
decisions/mutations with tracker on/off; bounded queue and disk failure isolation;
startup/recovery/shutdown timing; duplicate/reordered events; reject/cancel/fill
races and partial fills; manual/reversed/cross-day trades; fee-once handling;
mixed grades/programs/sizes; restart/rebind; ET/DST boundaries; delayed/revised/
partial/missing/empty rewards; unknown scan coverage; rank ties and small slates;
model leakage; no invented payments for untraded candidates. Measure writer CPU,
memory/disk growth, queue drops and decision/exit latency against identical replay
before claiming low overhead. Natural live acceptance must not induce trades.

### Accuracy acceptance: no silent inconsistencies

Every published value needs source IDs/hashes, earning/event/receipt times,
units, scope, computation version and a measured/modelled/unknown classification.
Use decimal money; rounding is presentation-only. Same immutable inputs must
reproduce the same report. Store edits as revisions rather than silently
overwriting historical knowledge. No NaN, infinity, negative durations or
impossible share can become a valid number. A schema mismatch must be visible.

Reconcile primary grade totals plus Mixed/Unknown to the exact eligible source
population: every reward counted once, every owned closed-fill cost counted
once, overlap-unioned time counted once per side. Do not hide rejected,
unmatched, delayed or manual rows in reconciliation. Account-wide source totals
must separately reconcile to bot-attributed, unattributed and out-of-scope
amounts, not force them all into bot profit. Check pool/event deduplication,
partial universe, shared-pool repeats and own-depth ambiguity in fixtures.

Do not reuse `market_economics`'s existing per-row `reward_per_side_hour` as
paid-only: that field includes pending. New outputs must explicitly distinguish
paid reward/hour from provisional reward/hour. Closed-fill P&L already includes
fees; reconcile it to the canonical replay before any grade net is publishable.

Sampling checks: trace representative source orders through rank, rest, partial
fills, exits, fees and late reward revisions; test rejected, profitable, losing,
zero-reward, mixed-grade and unresolved cases. Compare new report totals with
existing authoritative source data on identical dates/scopes and account for
every discrepancy. Fixtures and intentional reversal tests must prove that
double reward, double fee, skipped losers, guessed pool counts, lookahead and
invented resting time fail validation.

Publish coverage/age and uncertainty beside every cohort. A pending/unknown or
unreconciled result cannot become a finalized profitability grade or automatic
filter. Missing reward receipts must not make the available winners appear
representative. Distinguish a recorded S-priority label from `performance:
unproven`. Zero new REST calls does not guarantee complete data immediately;
wait for existing scheduled responses rather than silently adding traffic.

## Current handoff

Only this design and routing are changed. Source, tests, filters, $30 windows,
size15/32 and REST account mode remain unchanged. September23 process check
confirmed supervisor23308/runner23326 and deployed marker6deb707c6; no fresh
venue exposure query was required for this design. Implementation and all new
collector acceptance remain pending, not delivered by creating this document.
