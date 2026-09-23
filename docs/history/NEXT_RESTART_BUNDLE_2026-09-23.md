# Next restart bundle — September 23, 2026

## Authority and live separation

The operator requested the Discord fix, saved hybrid upgrade and market-quality
tracker together on the next restart. This is preparation only, not authority
to interrupt the current runner. Branch `work/next-restart-bundle` starts from
main `a62cf76db`. The stable REST tag and the two saved development branches
remain intact; the separate broad-scan experiment is NOT included.

Last read-only process check: supervisor23308/runner23326 retain their original
September23 01:11:38UTC start; `DEPLOYED_COMMIT` remains
`6deb707c6a5d9e6a56cdf983840b60f202e64dc2`. No authenticated exposure query was
made for offline development: current venue exposure is unknown. No deployment,
stop/restart, order mutation, source switch or risk change occurred.

## Three components and their boundaries

1. **Discord:** a separate reporting timestamp now travels with already-fetched
   bot-era reward totals. A market rotation still invalidates the original
   active-session economics, but does not erase the account-wide report clock.
   Failed earnings refreshes retain the last successful report values and mark
   them stale. The notification worker atomically saves a small key-scoped cache
   next to its ledger, so a restart followed by an earnings timeout can display
   prior values with their original timestamp. Missing/invalid/future/different-
   key evidence stays unknown, never invented zero. A valid new zero replaces
   old values; same-time conflicting snapshots are withheld. Cache IO failures
   cannot break fresh reports or trading. This cache is never trading authority.
2. **Hybrid:** the reviewed `f63036ac2` core is included unchanged. Private order
   messages assist the same serial ledger projector; complete REST proof remains
   authoritative. Exact unchanged ACKs and disconnects retain baseline cadence;
   uncertain fills/positions still require proof. Scoped reducer certificates
   remain single-use and exact, with event-during-proof invalidation. Source
   choice is explicit, not an automatic migration to websocket-only mode.
3. **Tracker:** the `04c680e37` observational collector/report is included, with
   supervised activation and large-catalog overhead improvements. It consumes
   existing results only. S/A/B/C/D describe original priority, not demonstrated
   profitability. It does not feed selection, sizing, risk or exits.

The summary retains its accepted full-width lifetime rows, overall-net bar,
pending line and hard-stop meter. Net still uses matched/closed-fill P&L plus
paid bot-era rewards; pending is separate. Stale paid values produce a labeled
last-known calculation, not a claim of fresh account profit. Historical ledger
settlement gaps and date-based reward attribution remain explicitly partial.

## Tracker performance, storage and failure isolation

Discovery copies collections of frozen domain objects and enqueues at most one
pending/building catalog. A daemon worker builds the large JSON and compresses
archive bodies; the execution loop does not perform catalog JSON serialization
or disk writes. Original record hashes cover uncompressed JSON. Legacy TEXT and
multipart archives remain readable. Offline CLI reports compact validated
discovery records into stage counts/pool evidence instead of retaining every
large candidate array; full raw records remain available through the reader.

Queue32, normal record2MB, catalog20,000 rows/16MB, directory512MB and free-space
reserve512MB bound capture. Quota/reserve checks include projected SQLite pages
and journal allowance. Queue overflow, invalid builders or storage failure
produce explicit gaps/errors; they do not block an order or retry an API call.
Evidence is never automatically deleted. Shutdown waits at most50ms and records
an incomplete run if background work cannot finish.

The supervisor supplies `--market-quality-directory data/market-quality`.
For a later authorized launch, `LIQUIDITY_MARKET_QUALITY_DIRECTORY=off` disables
capture; another directory can be explicitly selected. Direct Python invocation
remains opt-in. No separate private-WS measurement process is restarted.

## Verification

- Initial combined focused run:211 tests passed. Added final reserve-boundary
  coverage; reporting/storage focused run:16 tests passed. A joint fake-venue
  replay proves the collector/report adds no account reads and REST/hybrid
  read counts match for that workload. A real background collector leaves
  selection results and gateway call lists unchanged.
- Six isolated deliberate reversals are detected: discarding last-known
  rewards, reusing another key's cache, removing the reporting clock, building
  catalogs synchronously, storing uncompressed catalogs, and allowing a write
  to spend reserved disk space. Candidate source
  was not mutated by those child-process function replacements. Existing
  hybrid/grade reversals remain documented in their original reviewed work.
- Synthetic12,311-row fixture: synchronous builder75.25ms versus enqueue0.391ms;
  6,107,818 raw JSON bytes versus57,344 archive bytes, all rows retained,
  zero drops/errors. This low-entropy fixture is NOT a compression forecast for
  actual reward-rich catalogs or proof of unchanged live event-loop latency.
  Python worker scheduling still shares CPU/GIL; live acceptance is required.
- Local image preview visually verified the net bar, pending value, meter and
  timestamp/stale note, without layout redesign. No preview webhook was sent.
- The initial full combined suite passed2,126 tests in371.054s. After the final
  reserve safeguard/test, frozen executable commit `1fb120701` passed all2,127
  tests in366.540s using `/home/polybot/liquidity-bot/.venv/bin/python` against
  the isolated candidate worktree. No source changed during that final run.
  Documentation/whitespace audits and shell syntax checks pass. The integration
  branch is the reviewed artifact; main receives only this documentation handoff.
  Deployment/hash/runtime gates and live acceptance remain deliberately pending.

## Dedicated final review and edge cases

Producer/consumer flow was traced from CLI/supervisor through account source,
selector, committed ledger witnesses, earnings refresh, notification worker,
archive writer and offline report. Core hybrid files match the saved reviewed
branch byte-for-byte. The execution-ledger change only emits optional witnesses
after commit; callback exceptions are isolated. No schema, query ownership,
mutation ordering or durable intent semantics change.

Reviewed: empty/current-zero/stale/future/conflicting reward data; same-key and
rotated-key restart; cache write failure; rotation versus reporting scope;
queue full/oversize/catalog failure; disk quota/reserve; frozen inputs and hash
checks; mixed/pending/unattributed outcomes; lost shutdown tail; private gaps,
duplicate ACKs, unknown ownership and events during reducer proof. Existing
REST safety coverage also runs against hybrid. Current stop-loss policy and
percentage tiers,15 contracts/32 slots, pricing, live-game guards and exit timers
are unchanged; open positions/reducers still do not consume pending-entry caps.

## Required next activation (not executed)

On fresh operator restart authorization, merge/review this bundle through main,
gracefully stop the current owner, prove authenticated flat/known state, run
deployment/hash/runtime tests and fresh release gates. Launch through the normal
supervisor as `./scripts/start_commissioning.sh auto 15 32 hybrid`. Confirm the
saved four$30 ET6h settled-loss policy and cap tiers remain intact.

Observe5–7 minutes without induced fills/faults: exact reducer coverage and exit
timing, unknown/unresolved ownership, API endpoint counts/429s, stream gaps,
discovery completion and decision latency, tracker health/space and a natural
summary containing overall net and pending. Do not claim guaranteed savings,
no429s, fresh reward data or profitable grade ordering before that evidence.
If a safety gate fails, leave stopped. Rollback retains the stable tag and all
archives; it also requires normal stop/flat/source gates, never ledger repair.
