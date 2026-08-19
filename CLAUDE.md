# liquidity-bot — read this first

A live Polymarket US liquidity-reward market maker. **It trades real money on a
real account.** Every change can lose it.

## Where things stand — 2026-08-19

Update this block when it stops being true. It is here so a new session starts
from fact rather than from the last thing someone remembers.

- **Bot is RUNNING** and healthy: rotation unblocked, no risk stops, resting
  fraction **85.6%** (was 57.3% before the 2026-08-19 churn fix).
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
4. **The ledger is not the venue.** `data/commissioning.db` stops updating the
   instant the runner dies, so after a crash it describes the account *as it
   was*. Query the venue directly before concluding anything about exposure —
   this has produced a wrong call before.
5. **`git pull` first, push before you stop.** This repo is worked from more
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
- **`scripts/accounting.py` and `--pnl` are account-wide, not bot-era.** The
  bot's first order was 2026-08-14; reward before that date was earned by
  maker-lab. Attributing it to this bot overstates its performance by about
  $87, which has already happened once in analysis.
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
