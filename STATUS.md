# Current operator status

Only current operational facts belong here. Verify them live before acting.
Historical snapshots are under [docs/history/](docs/history/README.md).

## Verified state

- **Verified:** 2026-08-28 07:30 ET / 2026-08-28 11:30 UTC
- **Liquidity bot:** RUNNING under supervisor PID 5439 / size-10 runner PID 5445
- **Startup gate:** after the operator manually flattened and canceled all
  venue exposure, a stopped-state read proved zero positions/orders/unresolved
- **Live exposure:** the latest complete authenticated dashboard at 11:02Z
  showed zero positions, 13 resting and zero unresolved. At 11:07Z the ledger
  showed 23 open and zero position/unresolved/absent; it is not a venue snapshot
- **Maker-lab:** stopped
- **GitHub:** `main` includes reviewed source commit `8e348eb`
- **Server marker:** `8e348eb`; source/tests match the marker
- **Latest trading source:** `1ff0e42`, deployed and active in unchanged PID 5445
- **Parity:** desktop/GitHub/server passed; laptop requires its own result
- **Deployed verification:** all 706 local and reduced-priority server tests passed
- **Official API remediation:** seven prioritized reliability fixes and the
  rolling-report compatibility repair are pushed, deployed and active; the
  additive `execution_trade_states`/fill-reversal migration completed
- **Changelog monitor:** public RSS baseline succeeded and the isolated hourly
  schedule is active at minute 23; it has no trading API or ledger access
- **Spread-scalp measurement:** isolated supervisor PID 8376 / collector PID
  8382 is active against `data/scalp.db`; it has no order path
- **Pending strategy repair:** local work safely replaces continuously
  economically idle slots; it is not deployed and has not changed the runner

## Active operating envelope

| Setting | Current decision |
|---|---|
| Production runtime | Server only; no local runner |
| Quote size | 10 contracts |
| Concurrent capacity | 20 market slots |
| Entry horizon | Six hours |
| League filter | None |
| New-entry spread | 2 cents; 1.5-cent retention hysteresis |
| Market loss stop | $40 |
| Account threshold | $80 realized sensor and $80 marked-equity sensor |
| Risk day | `America/New_York`, DST-aware |

[DECISIONS.md](DECISIONS.md) is authoritative for policy details.

## Current live acceptance

The operator-authorized 03:06Z restart loaded source marker `1ff0e42` from a
positively flat/order-free account. By 03:10Z it held exactly 20/20 quoting
owners and 37 resting orders, with zero positions and zero unresolved rows.
The restart-window log recorded a 20-market queue, 44 placements and no
traceback, unresolved-mutation timeout, runtime recovery, stream-silence event,
buying-power block or rejection backoff. Natural failure/reversal paths still
need observation; do not induce a closure, disconnect, rejection or bust.

The strict spread-scalp collector started at 04:07:22Z without a trading-bot
restart. Ten-minute acceptance covered 100 markets, 24,050 book updates and
792 trades across both cohorts, with 99.3% stream coverage and zero gaps,
reconnects, stream errors or REST 429s. Bot PID 5445 stayed active. A private-
socket silence matched repeated pre-collector recoveries; later transient
UNKNOWN_ABSENT/cancel intents also cleared, with no mutation timeout or stop.

## Current operating instruction

Let the current run continue on its unchanged deployed build. At 11:30Z the
same supervisor/runner PIDs were active and action logs were still placing
quotes. Judge slot health from queue scope, distinct owners, ownership state
and resting coverage, not raw order count. The reviewed economic-idle repair
passed all 719 local tests and deliberate reversal, but deploy/restart remains
a separate operator decision and must use the normal authenticated exposure
gate.

## Current repositories and records

- Open work: [NEXT.md](NEXT.md)
- Active measurements: [WATCHLIST.md](WATCHLIST.md)
- Findings index: [FINDINGS.md](FINDINGS.md)
- Production defects index: [BUGFIXES.md](BUGFIXES.md)
