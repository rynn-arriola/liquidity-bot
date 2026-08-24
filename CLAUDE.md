# liquidity-bot — read this first

A live Polymarket US liquidity-reward market maker. **It trades real money on a
real account.** Every change can lose it.

## Where things stand — 2026-08-24

Update this block when it stops being true. It is here so a new session starts
from fact rather than from the last thing someone remembers.

- **Bot is STOPPED for the reviewed size/risk configuration deployment.** The
  next authorized start is quote size 10 with $40/$70/$80 market/session/hard
  stops. The 20-slot scheduler and six-hour horizon are unchanged.
  The previous runner was `b3ad73e`, started 2026-08-24T16:15:26Z at size 5.
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
- **Venue reward dates are ET, not UTC.** Daily reward/trading reports use
  `America/New_York` and DST-aware boundaries. Reward totals never changed.
  The session and hard-stop budget deliberately remains a durable UTC-day
  policy; do not move its reset time as part of an accounting change.
- **Standalone dashboard risk bars are directional, not authoritative.** At
  16:13Z they displayed $60.97 for both SESSION and HARD, while the venue plus
  durable UTC baseline proved the actual cash/equity drawdown was $47.032.
  The runner uses the latter and had $2.968 session / $12.968 hard headroom at
  restart. Do not stop the bot from the standalone bars alone; see FINDINGS.
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
- **Both loss stops re-baseline on every restart.** 2026-08-19 saw ten
  sessions, so the $40 budget was handed back ten times. Top of `NEXT.md`.

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
size; allowed whole values are 5-10 and the current/default value is 5. It
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
