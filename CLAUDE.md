# liquidity-bot — read this first

A live Polymarket US liquidity-reward market maker. **It trades real money on a
real account.** Every change can lose it.

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
- **P&L is currently untrustworthy** by about -$22.41. Positions that settle,
  or that an operator closes by hand, have their entry recorded and their exit
  invisible — there is no settlements table. See `NEXT.md` TOP PRIORITY. Do not
  weigh any optimisation against reported P&L until that is fixed.
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
