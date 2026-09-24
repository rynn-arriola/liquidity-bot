# REST usage audit and cash-report separation — September 24, 2026

## Scope / status

The operator authorized the cash-history fix after first reviewing every REST
path for a better overall solution. This is implementation and offline review,
not permission to stop, deploy or restart. Production remains on `dc8f62556`,
size15/32 hybrid, four fixed$30 settled-loss ET windows. Supervisor481321 and
runner481337 were alive at22:56:40Z. No venue request or order mutation was made
for this task. Current exposure is unknown;22:22:58Z acceptance is historical.

## Network inventory and decisions

Audited SDK call sites and wrappers across `src/` and `scripts/`, their serial
runner callers, consumers, pagination, caching, scheduling and shared admission.
Endpoint families are below; a wrapper's existence does not imply constant use.

| REST family / implementation | Why it is used / existing saving | Decision |
|---|---|---|
| Open orders: `account_snapshots.fetch_open_orders`, REST/hybrid collectors | Complete account ownership; stable-flat optimization requires two matching lists, not one. Missing/changed/positioned/ambiguous orders retain exact proof. | Keep. One-list shortcut previously failed race tests. |
| Individual order: `live_orders.fetch_order`, `_read_owned_order_states` | Create/cancel confirmation, unresolved/absent recovery, positioned-owner exact remaining quantity; bounded not-found retries. Scoped/same-sweep receipts and bulk-flat proof already remove eligible duplicates. | Largest measured category, but not all redundant. Keep exact safety checks; no broad stale-order cache. |
| Trade activity: `account_snapshots.fetch_executions` | Durable fills, bust/conflict detection and position/risk reconciliation. Stable-flat first-known-page optimization already exists; active/ambiguous/startup/recovery and30s audits retain deeper overlap. | Keep cadence and coverage. Private hints are not complete missing-history proof. |
| Positions: `account_snapshots.fetch_positions` | Full decimal quantities, ownership/exposure, exact reducers, and the separate financial bracket. Independent collector reads overlap, but projection is serial. | Keep. A nearby result with different scope/clock/generation is not interchangeable proof. |
| Balances: `account_snapshots.fetch_balance/fetch_equity_snapshot` | Buying power; BALANCE→POSITIONS→BALANCE consistency bracket. Old path adds another balance after cash-history classification. | Keep fast bracket and freshness. Move only cash-report final verification to slower reporting cadence. |
| Cash activity: `cash_activity.fetch_nontrading_cash_changes` | Exclude deposits, withdrawals and classified transfers from account reports. Full EOF walk, bounded4pages, completion-time filtering. | Implement30s report target, not every financial tick. Do not stop at old/known rows: old withdrawals can complete later. |
| Market catalogs / metadata: `adapters.list_markets`, upcoming-event pagination, slug batches, individual metadata, recovery metadata | Candidate discovery, parent membership, contract validity and settlement/recovery. Exact slugs are batched; complete discovery bounded; known inactive owners already back off. | Keep breadth and filters. Do not activate paused300-coverage work or replace metadata with book messages. |
| Parent events: `event_guard.EventAwareGateway` | Batches up to100 parents with30s freshness; positive live/ended evidence persists. | Keep actual-live protection; slowing this can admit live games. |
| Incentives: `adapters.fetch_incentives` and discovery | Exact-symbol batch authorization; separate day_of/daily_event periods; periodic refresh/selection and required revalidation. | Keep period/identity checks. Book WebSocket does not prove reward eligibility. |
| Books: `adapters.fetch_book`, selection/runtime book helpers | Fresh scoped public full-book WebSocket already preferred. REST only for missing/stale/unscoped fallback and certain recovery/settlement safety paths. | Keep fresh-WS preference, explicit scope/age checks and bounded REST fallback. No switch to blanket REST scans. |
| Settlement: `adapters.fetch_settlement` | Authoritative settlement evidence in exceptional recovery, not ordinary quote pricing. | Keep; book silence is not settlement proof. |
| Earnings: `adapters.fetch_earnings`, `_refresh_reward_earnings` | Account-wide reward snapshot on900s cadence, yields urgent work; local cached attribution serves summary/tracker. | Already slow/shared. Do not replace paid/pending truth with an estimate or add per-market polling. |
| POST create/cancel: `live_orders` | Durable intent and one mutation executor, final ownership/admission checks. No automatic ambiguous-create retry. | Keep. These are necessary actions, not report reads. |

Standalone consumers: `accounting.py`, `dashboard.py`, `reward_table.py`,
`daily_pnl.py`, `rewards_report.py`, `market_economics.py` and
`program_reward_audit.py` have bounded authenticated/report paths using shared
coordination. `period_mix.py`, `resting_by_kickoff.py`, `usable_markets.py` and
explicit commissioning/probe/census commands can read discovery/metadata.
`backfill_executions.py` and `resolve_orphan_places.py` are explicit maintenance
tools, not extra always-on polling. Do not stack these during startup/recovery.
The TV dashboard itself and local tracker/diagnostic calculations add no venue
requests. This task adds no second collector or polling process.

Other HTTP is separate from Retail REST: maintenance Statuspage requests,
public-site health probe, changelog feed and Discord JSON/image uploads. None
can establish order/position/balance readiness; their traffic must not be counted
as Retail account requests. WebSocket Ping/Pong likewise is not a REST call or
account snapshot.

Official reference checked: [portfolio overview](https://docs.polymarket.us/api-reference/portfolio/overview),
[rate limits](https://docs.polymarket.us/api-reference/rate-limits),
[changelog](https://docs.polymarket.us/changelog), after the local canonical API
reference. Public-IP versus authenticated-key limits do not identify the actual
rule behind a particular429. This change retains the shared4/s governor,
endpoint backoff, recovery-probe ownership and API timeout.

## Why this improvement, not a broader rewrite

[Existing measured sample and alternatives](CASH_HISTORY_REFRESH_REVIEW_2026-09-24.md):
3278 attempts over1011.5s =3.241/s; cash210 (6.4%), balances317,
positions402, exact orders877. Three429s:cash2/positions1. These are dated
aggregate counts, not the current instantaneous rate or a provider-cause proof.
The review does not establish a safe way to remove every positioned exact read
or share arbitrary account snapshots. Those would risk missing fills or exits.

The selected change removes unnecessary cash/report work without reducing
trading proof frequency. Slowing all account polling or dropping fallback would
trade away execution safety. Caching only the cash adjustment would combine
different accounting moments and can create false P&L. An additional background
account writer would add concurrency/race complexity without needing it here.

## Implemented boundary

- `CommissioningConfig.cash_history_refresh_seconds=30`; allowed0(disabled)
  or integer5–60. Zero restores the complete synchronous refresh. No new CLI,
  persisted source change, database migration or risk policy change.
- Only ordinary periodic/decision refresh callers may take the fast path in
  settled-window mode. Startup/direct refresh, recovery, missing report,
  ET midnight, six-hour rollover and held/provisional release retain full proof.
  Legacy non-window risk keeps the original accounting-dependent path.
- Fast proof retains BALANCE→complete POSITIONS→BALANCE, original15s freshness,
  durable matched-fill loss through the original observation time, unchanged
  caps/HARD and current buying power. Execution/history/ownership proof remains
  independently required. No new execution clock or source authority.
- An immutable account-report snapshot freezes balance/positions, complete cash
  adjustment, ET baseline and original timestamp together. A due report reuses
  that cycle's successful bracket, walks full cash coverage and confirms the
  final balance. Fresh fast balances never get combined with old cash adjustments.
- Report failure invalidates account-marked report availability. A bounded
  transport/429/5xx failure or consistency race does not itself revoke valid
  fast settled-window proof; malformed/unclassified cash still blocks and forces
  complete recovery. A failed required financial/position read still invalidates
  entry freshness. The shared activities circuit still applies to execution
  history: this is not a bypass of a real endpoint outage.
- No optional cash walk starts for known urgent/uncovered positions, pending
  hybrid observations, HARD or imminent kickoff. Deferral preserves due work;
  attempted failures wait for the reporting cadence, never a catch-up burst.
  No background writer or cancellation/exit deadline change. A read already in
  flight remains bounded by the existing API timeout; zero exit latency is not
  promised. Urgent service and final admission remain independently required.
- Summary account P&L uses the complete reporting snapshot with its true as-of
  time; invalid/wrong-day/expired values are unavailable. Settled trading totals,
  window HARD meter, rewards and buying power retain their original sources.
  The TV cache retains its last complete timestamped mark, not a newly stamped
  stale adjustment; local readers expose age and reject the wrong ET day.

## Verification

Isolated stage `/tmp/cash-report-review.qeLHq6`, no credentials/live database:
production Python3.12.3, Ubuntu24.04/WSL, installed production dependencies.
Full suite: `nice -n 10 /home/polybot/liquidity-bot/.venv/bin/python -B scripts/run_tests.py -j 4`:
**2231 tests /96 files PASS in109.51s**. Focused dependency run81 passed
(included24 imported duplicate cases, subsequently removed from discovery).
Final dedicated test file: **22 tests PASS in3.038s**. After the full suite,
only this test file was expanded by two configuration/source-mode tests and its
fixture routing; production source was unchanged. The final file was rerun in
full; there are2233 unique test cases in the final tree, not a claim of a second
full-suite run. Source/config/scripts/other tests and environment remain the
same. Artifact manifest records the final combined tested inputs.
The [244-file manifest](REST_USAGE_AUDIT_2026-09-24.artifacts.sha256) matches
Windows after line-ending normalization; [environment/results](REST_USAGE_AUDIT_2026-09-24.environment.txt)
record dependency versions and test-log hashes. Later docs-only changes are not
new runtime inputs.

Sensitivity: the initial fast/report regression failed on unchanged baseline
`aa9ae15dd` (missing separated report state), then passed with this change.
Assertions also count actual fake-client calls: six5s routine refreshes use20
requests versus30 complete refreshes for identical single-page cash data.
That is ten fewer calls in this financial subpath, not33% of the whole bot.

Coverage: REST/hybrid actual runner entry points; legacy/disabled/startup missing
proof;5–60s configuration; current balance with old report; deposit classification;
cash consistency race; transient cash failure and no per-tick retry; required
balance failure; unknown transfer recovery; pending observations/urgent exit
deferral; midnight/six-hour boundaries; slow/negative clocks and baseline mismatch;
all loss tiers/HARD while reporting deferred; shared activities cooldown;
cancellation cleanup. Existing complete suite retains partial closes, late cash
completion/EOF errors, busts, holds/restarts, ownership, reducer/exit and summary
tests. No visual design change, venue fault injection or induced trade.

Expected whole-bot saving stays modeled at roughly6–7% on the prior workload,
including cash-only final balances. Recovery/active-state workload and report
deferral change it. Runtime saving, changed429 frequency and exit/resting-time
benefit remain **unproved until separately authorized live activation**.

## Next activation / rollback

No deployment or restart in this task. Keep current runner untouched. A later
authorized release needs normal stop/flat/known-state gates and final artifact/
environment parity. Reuse matching tests instead of repeating an unchanged suite.
Then compare existing api-budget cash/balance/total counts, account-report age,
429 categories, reducer coverage and settled-window results. Do not launch new
availability probes or infer performance from one quiet minute.
Rollback is code-only (or configuration0) through normal reviewed deployment;
no durable risk row, position timer, ownership or source reset is required.
