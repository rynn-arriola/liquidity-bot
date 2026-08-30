# TV dashboard active-scope repair — 2026-08-30

## Symptom and evidence

At 20:39Z the live TV API rendered 16-17 of 22 cells as `OBSERVED`, while two
authenticated account snapshots proved 20-21 quoting markets, 35-36 resting
orders, one position, zero unresolved/unknown ownership and no risk stop. The
TV scope identified its own cause: it was the retired queue collector's final
`STOPPED` row from 19:25:07Z, already about 75 minutes old. Only five markets
overlapped the runner's current rotated slate.

`OBSERVED` itself was not a trading state. It meant that a market existed in
the chosen local scope but lacked a working ledger order. The defect was using
historical collector scope as current, which also let its 22 old entries fill
the display before current position or attention fallback markets could be
appended.

## Repair

- The runner atomically publishes a mode-0600, no-secret active-market cache
  after successful preflight and every binding change, including its publisher
  PID. It marks the cache `STOPPED` after orderly shutdown. Cache writes are
  observation-only, transition-logged on failure and cannot fail the runner.
- The dashboard accepts that cache only as schema 1 `RUNNER_ACTIVE_MARKETS`,
  status `RUNNING`, exact currently detected publisher PID, matching runtime
  capacity, unique bounded market strings and a non-future timestamp. No
  periodic cache fsync is added to the event loop.
- Missing or unusable runner scope falls back to the working, position,
  unresolved and unknown markets read from the ledger in that request. Empty
  capacity stays `EMPTY`, never a stale `OBSERVED` market.
- The retired queue table remains backward-compatible only for a fresh,
  unended `COLLECTING` session. `STOPPED`, ended or stale rows are history.

The current trading process cannot load the new publisher without a later
normal restart. A dashboard-only reload can activate the safe ledger fallback
immediately and does not signal or replace the bot.

## Edge-case audit

| Case | Result |
|---|---|
| Fresh matching runner cache | Exact bound order drives the cells; a truly bound idle market may say `OBSERVED`. |
| Stopped cache or publisher PID no longer live | Ignored immediately in favor of current ledger evidence. |
| Wrong slot capacity | Ignored so an earlier 20-slot session cannot describe a 22-slot run. |
| Corrupt, oversized, duplicate, blank or future-dated cache | Fails closed to ledger evidence with a local display error only. |
| Cache write/fsync/rename failure | Runner logs one transition and continues unchanged. |
| Current working/position/ambiguity outside stale scope | No stale scope can consume the cells before current evidence. |
| No cache before the next bot restart | Ledger fallback removes false `OBSERVED` cells; bound-but-idle cells remain conservatively `EMPTY`. |
| Venue truth | Unchanged: authenticated account reads still outrank every local dashboard source. |

## Verification

The focused TV and commissioning suites cover stopped legacy scope, dead and
stopped runner cache, exact live-PID scope precedence, capacity mismatch, future
time, duplicate/over-capacity payloads, atomic runner publication, read-only
ledger behavior and the existing HTTP security boundary. A deliberate reversal
that again accepted a `STOPPED` legacy scope failed the regression exactly. All
931 tests then passed in the production dependency environment in 88.447
seconds.

The implementation was pushed as `10d1e4c1`. The exact 22-slot WSL checkout
then exposed and rejected one test fixture's accidental dependency on the live
capacity file; `fda2bb37` isolated that fixture. All 931 exact-checkout tests
passed in 67.168 seconds. Before the dashboard reload its API showed the
historical `STOPPED` scope at age 6,144.6 seconds, five quoting and 17 observed
cells. Dashboard PID 116618 stopped cleanly and PID 614753 loaded the repair;
the bot supervisor/runner remained 563476/563490. The first repaired snapshot
reported `CURRENT_LEDGER_EVIDENCE`, 21 quoting, zero observed, one empty, 38
working orders and zero position/unresolved/unknown rows. The exact runner cache
will replace fallback only after the next ordinary bot restart; none occurred.
