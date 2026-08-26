# liquidity-bot — read this first

A live Polymarket US liquidity-reward market maker. **It trades real money on a
real account.** Every change can lose it.

## Where things stand — 2026-08-25

Update this block when it stops being true. It is here so a new session starts
from fact rather than from the last thing someone remembers.

- **OPEN — the reward webhook's `Trading P&L` is a balanced-pair estimate, not
  complete daily realized loss.** At 2026-08-26T00:53Z it showed -$24.40 for
  the current ET day, while venue cash plus exact basis of four open positions
  proved -$30.98 realized. Cash was down -$51.55; $20.56 remained position
  basis. No reward/funding transfer occurred after 04:00Z. Two one-sided
  positions resolved for -$7.54 and are omitted by the balanced-leg report,
  explaining most of the $6.58 understatement. The current BUY_SHORT wire
  frame is positively proven: ten at recorded 0.29 cost $7.07 after rebate, so
  economic cost is `qty * (1 - price)`. No code or bot state changed. See the
  newest FINDINGS/NEXT entry before changing the webhook.

- **DEPLOYED AND ACTIVE — transactional top-up repair.** At
  21:04Z one incremental replacement committed its new active tuple and closed
  both healthy streams before strict REST proof. Three owned order reads then
  returned NotFound; global recovery canceled 32 orders and eighteen owners
  retired for `unresolved_mutation_timeout`. The local repair moves strict
  whole-account proof before the tuple/stream commit and scopes only the
  complete post-bind handoff, so a preflight failure preserves all old streams,
  scope and orders. A delivered operator stop now interrupts the remaining
  owners in an in-progress decision sweep. Three mutants are killed and all
  621 local tests pass. Commit `466ef9c` is pushed and deployed; all eight
  hashes matched and all 621 server tests passed in 54.037 seconds. A fresh
  venue/ledger gate proved completely clean immediately before the size-10
  restart. Supervisor `187359` / runner `187549` reached queue scope 20, actions
  in 18 distinct owners and 18 working orders across 18 markets with zero
  positions or unknown-absent rows. Six acknowledged mutations were fresh and
  progressing; no new bound-stream/quarantine/timeout/runner-failure marker
  appeared. Observe the next natural top-up; do not induce one.

- **PUSHED AND DEPLOYED; ACTIVE ON NEXT RESTART — LIVE must show resting side-hours, not
  reward-score hours.** The requested number was the `Resting side-hours`
  column from `scripts/reward_table.py`. At 22:06Z that exact calculation was
  416.65 hours / 23.01 average sides for the current ET day, while Discord
  showed 17.82 because it divided contract-weighted reward score by 3,600 and
  had only begun persisting at the 21:51Z restart. The fix moves the table's
  merged `(market, side)` interval calculation into shared code and runs it
  read-only on a worker thread at the existing five-minute summary boundary.
  It makes no venue request and cannot place, cancel, select or reprice. LIVE
  renders `Resting side-hours`; SHADOW retains modeled score-hours. Two focused
  integration tests prove the exact value and failure isolation; all 623 local
  tests pass. Source commit `39e8ddb` is pushed and deployed on disk, all 12
  file hashes match, and all 623 reduced-priority server tests passed in 74.516
  seconds. A deployed-code read rendered 422.21 current-day side-hours in 0.632
  seconds. Supervisor `187359` / runner `187549` remained unchanged, so the
  running process still has the old display until a future reviewed restart.

- **Historical 20-slot acceptance on reviewed commit `26b11b2`.** Before
  restart, all 15 changed
  files matched the server and all 612 tests passed there in 59.027 seconds.
  Whole-account preflight showed zero positions and open orders, and the ledger
  showed zero unresolved and `UNKNOWN_ABSENT`. The unchanged `auto 10` runner
  started at about 18:36Z under its supervisor. At 18:40Z it held 36 open
  quantity-10 orders across all 20 markets, with zero positions, unresolved
  intents or `UNKNOWN_ABSENT`, and no new critical fault marker. Raw order count
  is not slot count; use queue `markets=20` plus distinct active owners when
  auditing the scheduler. A subsequent snapshot briefly saw three positively
  owned cancels in `UNKNOWN_ABSENT`; the automatic terminal-proof path cleared
  all three without intervention and the next checkpoint held 38 open orders
  across 20 markets. Treat age/progress as the signal: a new acknowledged
  cancel missing from one complete snapshot is not the historical deadlock.

- **DEPLOYED AND ACTIVE: ET risk day and one $80 account threshold.** The
  operator authorized both session and hard thresholds at
  $80 and moved their durable reset from UTC midnight to midnight
  `America/New_York`. Both sensors remain: realized loss pauses new risk;
  marked-equity loss is terminal emergency protection. The guarded first-run
  migration copies the baseline the UTC runner was already enforcing, so a
  restart cannot grant a second budget; if stopped across UTC midnight it uses
  the latest prior row, and later baselines are ET-only. The local
  implementation passes all 612 tests and kills the relevant deliberate safety
  mutations. Live acceptance found the first ET row at `2026-08-25`, source
  `legacy_utc_cutover`, exactly preserving legacy opening equity/cash
  `478.6998` and realized `-207.39180000`. The dashboard now reports `ET DAY`
  and both account limits as `$80`.

- **DEPLOYED AND ACTIVE — visually accepted hourly reward table.** The first
  real post at `2026-08-25T18:06:15Z` proved delivery but its wide pipe table
  did not render as a usable Discord table. The replacement is an embed with
  two narrow fixed-width monospaced tables whose headers, separators and rows
  have exact equal widths. It does not rely on color or Discord Markdown table
  support and refuses more than seven days. The underlying report remains one
  account-wide earnings request plus a read-only SQLite scan; the separate
  wrapper retains its nonblocking lock, 90-second timeout and `nice -n 10`.
  The revised post at
  `2026-08-25T18:18:42Z` passed server tests, delivered successfully and was
  visually accepted by the operator; the minute-7 hourly crontab entry is
  active.
  During the first acceptance sequence `/root/maker-lab` was unexpectedly
  absent. No cause was established. It was restored from the local sibling
  credential copy, the dedicated reward assignment was re-added without
  displaying its value, and directory/file modes were restored to `0700/0600`.
  Names and modes were validated immediately before and after the accepted
  post and remained intact at `0700/0600`.
  The pre-restart review found that unconditional validation of this report-
  only URL could block trading startup if the value were malformed. The
  deployed isolation fix requires and validates it only in the hourly process.
  Trading subsequently passed its independent startup path and reached all 20
  markets.

- **The second slot-lifecycle gap is fixed and deployed.** At 13:00Z ten
  bound markets positively transitioned to `no_incentive`; at 13:07Z those ten
  held no orders while nine other markets held 18 size-10 orders. Capacity was
  still 20, viable alternatives were 96, and positions/unresolved/absent state
  were zero. Top-up replaces free or past-kickoff slots, not flat pregame
  markets whose reward period ended. The reviewed repair confirms only the
  exact previously-authorizing program's explicit elapsed end, fails closed on
  missing/different reward data, releases only flat/order-free/unresolved-free
  owners and immediately tops up the exact free count. Projection now uses the
  explicit reward interval. The implementation and initial acceptance are on
  `2e3c677`; the first natural reward-ended replacement remains to be observed.
  See the newest FINDINGS, BUGFIXES and NEXT entries for the exact proof.

- **Historical maintenance run: `feba761` started at 12:18:56Z.**
  The previous runner stopped at 2026-08-25T11:14:26Z with
  `runner_failure:bound_stream_recovery_exhausted`, `flat:false`, after
  `GET /v1/orders/open` returned `InternalServerError` during Polymarket US
  maintenance. The operator subsequently cancelled and flattened at the
  venue. The deployed runner now has a maintenance-aware dormant health gate
  for startup and all business streams. All eight hashes matched and all 581
  tests passed locally and on the droplet; three deliberate mutants were
  killed. Startup safely dropped two candidates that had become non-open,
  so it initially bound 18 with unchanged capacity 20. Acceptance at 12:26:15Z
  had 37 placements across 17 markets, 23 working orders all at size 10, and
  zero position, unresolved or unknown-absent state. The maintenance branch
  awaits a natural outage; do not induce one. See the newest NEXT, FINDINGS,
  BUGFIXES and invariant 69 entries.

- **The free-capacity top-up repair is deployed as `73d5c73`.**
  The current startup safely dropped two candidates that became non-open and
  bound 18, but a live-policy diagnostic still found 97 viable markets. The
  top-up scheduler only recognized expired-flat markets, not
  `len(active_slugs) < max_markets`, so free startup slots could persist. The
  The repair makes under-capacity immediate top-up work, retains every existing
  order/market, caps additions at exactly 20, and preserves the inventory
  deadline/ownership/stream safety gates. All 585 local and production tests
  and three deliberate mutation checks pass. See the newest FINDINGS,
  BUGFIXES and NEXT entries.
  Do not run another heavy authenticated supply probe beside the live runner;
  its final extra read already caused one isolated Cloudflare 1015.

- **The dual-stream recovery repair is deployed as `bec8a83`.**
  At 21:31Z a top-up bind closed both streams, then owned GET-by-ID NotFound
  lag left four acknowledged cancels `UNKNOWN_ABSENT`; strict
  `_recover_bound_streams` held all twenty markets dark while the process
  stayed alive. The local fix scopes those known owners after complete REST
  snapshots and fresh private state, keeps each ambiguous owner barred, and
  bounds genuine rebind failure at 12 attempts. Normal active-set binds remain
  strict. All 573 local and production tests and three mutation checks pass.
  The old runner ultimately proved flat; the new size-10 runner began at
  22:01:08Z, bound exactly 20 markets and produced placements across 18
  distinct markets with zero positions or unknown-absent orders at acceptance.
  See the newest BUGFIXES, FINDINGS and NEXT entries for the exact detection
  signature and the marker to verify on the next natural failed bind.

- **The almost-front queue study is deployed as `6085ca5` and collecting.**
  It adds only an isolated bounded public-trade socket, durable
  lower/upper queue bounds and a `WOULD_QUEUE_RESET` shadow signal after 60s
  when the conservative upper bound is at most one order quantity. It has no
  cancel/repost path and does not alter selection, 20 slots, REST, order
  payloads or exits. All 571 local and production tests and two deliberate
  mutation checks pass. The 18:49:40Z restart bound exactly 20 measurement
  markets; the acceptance checkpoint held 38 measured orders with 38 post-ACK
  baselines and zero coverage gaps, drops, decode failures or errored stream
  sessions. See NEXT
  "queue-depletion measurement" and FINDINGS.

- **Historical acceptance: the bot ran on `6085ca5` from
  2026-08-24T18:49:40Z at quote size 10.**
  The first process safely recovered one residual size-5 position, proved flat
  and exited `startup_recovery_only`; the supervisor then started the normal
  runner. Live acceptance found all open orders at quantity 10, 20 bound
  slots, 18 instantaneously quoting, zero positions, zero unresolved intents
  and zero unknown-absent orders. Active stops are $40/$70/$80 for
  market/session/hard. The six-hour horizon is unchanged.
  Live verification reached 20/20 quoted markets; venue orders reported only
  original/remaining quantity 5, with zero positions, unknown-absent orders or
  unresolved intents. Quote size is one persistent restart-scoped setting,
  bounded to whole values 5-10; its early-exit, per-market exposure and fee
  caps derive automatically. The six-hour horizon, 20 slots, 8 + 8 + 4 private
  layout, 4 req/s and $40/$50/$60 account stops are unchanged. All 551 tests
  passed both in an isolated server copy and again on the deployed tree.
  Before the size change, rotation was unblocked with no risk stops and resting
  fraction **~54%** span-weighted over a full UTC day (2026-08-22).
  The **85.6%** this block used to carry is a *different statistic* --
  `resting_fraction.py --coverage 0.8` over a 3h41m window, which keeps only
  sides whose market stayed bound for 80% of it and so silently excludes every
  market that bound, failed to quote, and rotated out. Measured span-weighted
  over full days the bot has run at **46.9-71.8% every day since 08-15**, and
  the churn fix moved it 63.1% -> 64.2% -> 67.4%, not 57% -> 86%.
  **Reward does not track this number** -- the best reward day (08-17, $34.60)
  had the lowest resting fraction of any mature day. See FINDINGS, "Resting
  fraction: the 85.6% is a filtered statistic".
- **Private-reconnect availability repair is deployed as `a4836b5`.** The
  13:01Z restart reached 20/20 quoting, zero positions, zero unknown-absent
  orders and zero unresolved intents after startup convergence. On the next
  natural private disconnect, validate five-owner `[unresolved-repair]`
  progress and immediate flat-owner release using the newest FINDINGS entry;
  do not induce a disconnect merely to test a funded live account.
- **Venue reward dates and active risk days are ET, not UTC.** Daily reward/trading reports use
  `America/New_York` and DST-aware boundaries. Reward totals never changed.
  The operator's 2026-08-25 ET risk-day change is now live with its guarded
  legacy-baseline cutover verified from the production ledger.
- **Standalone dashboard risk bars are directional, not authoritative.** At
  16:13Z they displayed $60.97 for both SESSION and HARD, while the venue plus
  durable UTC baseline proved the actual cash/equity drawdown was $47.032.
  The runner uses the latter and had $2.968 session / $12.968 hard headroom at
  restart. Do not stop the bot from the standalone bars alone; see FINDINGS.
- **Do not run stacked authenticated diagnostics during live startup.** The
  size-10 acceptance ran `dashboard.py` and a second venue snapshot back to
  back; the runner's next backfill received one Cloudflare 1015 rate limit,
  then recovered at attempt 1. Use one venue read, then SQLite `mode=ro`.
- **Economics, bot era only** (first order 2026-08-14): reward earned +90.16
  against matched-pair trading -44.66, so roughly **+45 net**. `TOTAL` on the
  dashboard is account-wide since inception and is dominated by pre-bot
  maker-lab trading — it is **not** this bot's result.
- **$67.38 of cost across 29 markets is unpriced** (one leg bought, exit
  invisible). That is the honest uncertainty on the number above, and it is
  what the settlements table would close.
- **Two failure modes from 2026-08-19 can still recur and need a human:**
  a `POST /v1/orders` 500 that returns no exchange id leaves an intent nothing
  can resolve (`scripts/resolve_orphan_places.py` is the stopgap), and the
  unresolved-mutation stop is still account-wide, so one stuck intent stops all
  twenty markets. Both are `NEXT.md` item 00.
- **The restart re-baseline defect is fixed but awaits live acceptance.** The
  first ET-aware restart must create one `legacy_utc_cutover` row and retain
  the prior opening marks; later restarts on that ET day must reuse it.

## Before you touch anything

1. **`grep FINDINGS.md` before theorising.** It holds measured facts precisely
   so nothing is derived twice. This has failed at least once: the
   "20 markets exceed the 4 req/s REST budget" theory was already tested and
   disproved in that file, and got re-derived from first principles anyway.
   Search first, always.
2. **Mutation-check every fix.** Revert it, confirm a specific test fails, put
   it back. Several tests in this repo once passed against the unfixed code.
3. **Write it down when you find it, not at the end.** Production bug →
   `BUGFIXES.md`. Measured fact → `FINDINGS.md`. Both are standing rules.
4. **Decide from THIS bot's data, not maker-lab's.** Operator rule, set
   2026-08-22. maker-lab is a different system: quote size 10-25 against our
   5, thirty markets, the **live** period included, and
   `FLATTEN_NEVER_AT_A_LOSS` on. Its outcome splits measure the configuration
   it ran, not the better policy -- its 60-to-1 passive-exit win rate is that
   basis floor, which it documented itself as *"79% losers BY CONSTRUCTION"*.
   Numbers carried across silently import that configuration as if it were
   physics.

   When only maker-lab data exists, it may be used as a **provisional prior**
   -- say so explicitly, and treat replacing it with our own measurement as
   work, not as optional. Never let it settle a question our own ledger could
   answer.

   Currently resting on maker-lab evidence, so a reader knows what is
   provisional:

   | what | maker-lab basis | our own data? |
   |---|---|---|
   | `max_inventory_age_seconds` 900 | 112 exit episodes, 26% by 30s / 46% by 900s | pair-completion buckets exist; the A/B has never run |
   | `minimum_entry_spread` 0.02 | markout by spread bucket, sub-2c the only loser | none -- we refuse sub-2c, so we cannot sample it |
   | `fill_hazard.py` entirely | 301 order lifetimes, 114 first fills | never recalibrated on our fills |
   | league reward priors | 168 maker-lab market-days | our own reward history now exists |
   | exposure buffer | `CAPITAL_BUFFER_PCT` | account-specific, fine |

   Our own trading is measurably worse than maker-lab's buckets predict --
   -5.07c/contract against their worst bucket at -0.87c, roughly 6x -- which
   is itself the argument: their numbers do not describe this bot.

5. **The ledger is not the venue.** `data/commissioning.db` stops updating the
   instant the runner dies, so after a crash it describes the account *as it
   was*. Query the venue directly before concluding anything about exposure —
   this has produced a wrong call before.
6. **`git pull` first, push before you stop.** This repo is worked from more
   than one machine, and `FINDINGS.md`, `BUGFIXES.md` and `NEXT.md` are all
   append-heavy, so two machines editing a stale copy conflict in the file that
   exists to stop work being repeated. Pull at the start of every session, and
   commit and push anything learned before ending one — a finding that only
   exists on one laptop is a finding the other machine will re-derive.
   Nothing else carries context between machines: Claude's memory files are
   per-machine and do not sync, which is why everything durable lives in these
   files instead.

   **Pushing is not deploying.** Three separate steps, and only the first is
   git: `commit + push` so the other machine sees it; `scp` to the droplet so
   it actually runs (runner code only); write `DEPLOYED_COMMIT` so anyone can
   tell what is running. Pushed-but-not-deployed is not running.
   Deployed-but-not-pushed is invisible to the other machine and will be
   clobbered by the next deploy from it. Both fail silently.

   To answer "is the droplet in sync?", run `./scripts/check_sync.sh <host>`.
   It compares the droplet's `src/` against this working copy and checks that
   `DEPLOYED_COMMIT` is telling the truth. Docs and scripts running ahead is
   expected and is reported as harmless — forcing them level would mean
   restarting a healthy bot to ship a README fix.

## What is authoritative for what

Git is the source of truth for **code and decisions**, and nothing else. Four
questions have a different authority, and collapsing them has already produced
two wrong calls:

| question | authority |
|---|---|
| why something is the way it is | this repo |
| what code is actually running | `DEPLOYED_COMMIT` + the runner's command line on the droplet |
| what limits are in effect | `commissioning.py` defaults, **and** the runner's command line, which would override them |
| do we hold a position or an order | **the venue**, queried live — never the ledger |
| trading history and P&L | the ledger on the droplet — settled exits are invisible, so it understates losses; see the P&L note below |

The repo records intent. The droplet is the fact of what executes. The venue is
the fact of what is owned. They drift apart silently — `DEPLOYED_COMMIT` is
written by hand and goes stale the moment someone forgets — and on 2026-08-16
the markdown confidently stated safety limits the code had disagreed with for
weeks, in the direction that made the bot look ~20x safer than it was.

**A number in prose is a copy, not a source.** Quote it with the date it was
verified, or cite `commissioning.py`.

## Where things live

| file | what it is |
|---|---|
| `FINDINGS.md` | measured facts; the anti-re-derivation file. Large — search it |
| `BUGFIXES.md` | one entry per production bug: symptom, mechanism, fix, cost |
| `NEXT.md` | the work queue, ordered by what blocks earning |
| `WATCHLIST.md` | things being watched but not yet acted on |
| `docs/SAFETY_INVARIANTS.md` | the invariants; numbered and referenced from code |
| `docs/PHASE*_ACCEPTANCE.md` | historical phase gates, already passed |

Code lives in `src/liquidity_bot/`. The big ones: `live_commissioning.py` (the
runner and its loop), `reconciler.py` (pure desired-state planning, no I/O),
`execution_ledger.py` (durable order/fill/position state), `live_session.py`
(readiness gate, action driver), `adapters.py` (REST parsing, rate limiter),
`streams.py` (market and private WebSockets).

## Running it

```bash
$env:PYTHONPATH = "src"; python -B -m unittest discover -s tests   # PowerShell
PYTHONPATH=src python -B -m unittest discover -s tests             # bash
```

The suite is ~25s. **Run the affected module while iterating** — an infinite
retry in a shutdown path once made a broken change look like a slow suite.

Production runs on a DigitalOcean droplet under `/root/liquidity-bot`. It is
**not a git checkout** — deploys are `scp` of individual changed files, then
`DEPLOYED_COMMIT` is written by hand. Nothing writes it automatically, so it
goes stale silently. Verify a deploy with `md5sum` on both sides, and run the
suite **on the droplet** with its own `.venv` before restarting. `sqlite3` is
not installed there; query the ledger with `python3` and open it read-only
(`file:...?mode=ro`) so a live runner is never disturbed.

Connection details (host, SSH key) are deliberately **not** in this repo. They
are in the operator's local notes. To set up a new laptop, desktop or phone,
see `docs/MACHINE_SETUP.md` — it covers what travels via git, why no
workstation needs the API credentials, and how to authorise and revoke a
machine's SSH key.

Lifecycle: `scripts/status_commissioning.sh`, `start_commissioning.sh`,
`stop_commissioning.sh` (graceful; waits up to 300s for a flat proof).
`start_commissioning.sh auto SIZE` changes the persistent restart-scoped quote
size; allowed whole values are 5-10, the persisted operator setting is 10, and
5 is only the fallback when the setting file does not exist. It
automatically derives early-exit, per-market exposure and execution-fee caps.
Never change size by editing those derived limits or while a runner is live.
`scripts/resting_fraction.py` measures what share of time each side actually
holds a live order — the quantity that maps to reward.

`scripts/accounting.py` is the P&L you can trust. The ledger's `realized_pnl`
read -\$143.59 on 2026-08-17 when the account had moved -\$48.69 -- wrong by 3x,
in the number the loss stops use. This prices only what is unambiguous (a
matched YES+NO pair settles at exactly 1.0000) and reconciles the rest against
marked equity, so `settlements = equity_change + paid_for_fills - reward_paid`
falls out exactly. It records a dated snapshot each run; consecutive snapshots
bound each settlement window. **Do not add venue history paging to it** -- an
earlier version did and triggered rate limiting, because analysis shares one
4 req/s governor with the live runner.

`scripts/usable_markets.py` answers **"why is it quoting 3 markets and not 20 —
is that a problem or normal?"** Use it whenever that question comes up; do not
answer it from the `[selection]` log line, which prints five counters that
cannot tell "nothing is eligible" apart from "markets are being lost to book
errors". The counters that separate them (`catalog_spread_filtered`,
`unsupported_programs`, `failures`, `deferred_markets`) exist on the selection
object and are never logged. The script runs the real selector read-only,
prints the whole funnel, lists what is resting at the venue, and states a
verdict. `--rejects` lists the tight-book markets with their spreads. It costs
~50 venue calls, so never loop it.

`scripts/rewards_report.py` answers "what have we actually earned" — daily
table, per-market and per-league breakdown, straight from the venue. Query it
account-wide; passing a market slug to `/v1/incentives/earnings` returns \$0 even
for markets that plainly earned. A missing day means either "not posted yet" or
"earned nothing", and one reading cannot tell them apart — re-run a day later.

**“Run 7 day reward table”** means execute:

```bash
.venv/bin/python -B scripts/reward_table.py --days 7
```

It combines ET-dated balanced-leg trading P&L, paid/pending venue reward and
merged resting side-hours. It opens the ledger read-only and makes exactly one
account-wide earnings request—no discovery, books, account snapshots or order
calls, and no mutation path—so it is safe beside the live runner. Do not loop
it tightly or schedule it more frequently than the reviewed hourly wrapper.
`Not posted` is not zero, pending is excluded from finalized net, the
current ET day is partial, and trading still omits unbalanced/settled markets.

`scripts/backup_ledger.py` runs nightly from cron at 04:17 UTC and keeps 30
dated snapshots in `backups/daily/`. It uses SQLite's online backup API, so it
is safe against the live writer — never `cp` a WAL database. **Those snapshots
are on the same droplet, so they do not survive losing it.** Pull them off
periodically; that is still a manual step.

## Things that will mislead you

- **`book_stale` event counts mean nothing.** They measure how often the bot
  *looks*, not how often it is broken. Use escalation depth (`attempt=N`) and
  whether anything retired. Hundreds of `attempt=1` is a healthy bot.
- **A bot that is up and placing nothing may be correct.** The economic gate
  declines markets that cannot earn. `target_not_met` is the venue's reward
  threshold, not a fault.
- **P&L is untrustworthy, and by a moving amount.** Positions that settle, or
  that an operator closes by hand, have their entry recorded and their exit
  invisible — there is no settlements table. Measured 2026-08-19: **$67.38
  across 29 markets** sits unpriced, against a bot-era matched-pair result of
  -44.66. So the sign of the trading line is not certain, only its rough size.
  Do not weigh any optimisation against reported P&L until that is fixed, and
  do not quote a single figure without its date. See `NEXT.md`.
- **Scope every money figure to the bot, and say which one it is.** The
  account is older than the bot: maker-lab earned **$86.90** on it before the
  bot's first order on **2026-08-14**. `scripts/_bot_era.py` holds that
  boundary; `rewards_report.py` and the dashboard now default to bot-era and
  print which scope they used, with `--account-wide` to opt back in.
  **Equity and funding cannot be scoped** -- they are account-level facts with
  no per-system attribution -- so any `equity - funding - reward` figure is
  account-wide however the reward term is filtered, and must be labelled that
  way. Conflating the two overstated this bot by about 2x, twice.
- **An absent count, a slot count, or any instant sample can lie.** A market
  between a cancel and its re-place holds no order and is not stuck; a
  dashboard "bound, not quoting" figure is inferred from log mentions and has
  exceeded slot capacity. Judge quoting health by `scripts/resting_fraction.py`
  (share of time a side actually holds an order — the quantity reward is paid
  for), not by a count taken at one moment.
- **Closing a position by hand while the bot runs** is safe for the account but
  debits the loss stops with a phantom loss. Stop the bot first. Manually
  *placing* an order is worse: it appears as a `venue:` order and trips a
  terminal safety stop across every market.

## Safety properties that must not be weakened

- `RuntimeInvariantViolation` is terminal — ownership violations, loss stops.
  Per-market error handling deliberately does **not** absorb it.
- An unowned (`venue:`) working order stops every market at once. That is the
  one genuinely account-wide test; everything else about execution state is
  per-market.
- The pre-kickoff flatten deadline overrides every other exit rule. Nothing may
  hold a position into settlement.
- Absence is never read as cancellation. Only a terminal state from the venue
  resolves an order we lost sight of.
- Exits never use `entry_quote_price`; entries never use `exit_quote_price`.
  Changing one must not move the other.
