# Current operator status

Only current operational facts belong here. Verify them live before acting.
Historical snapshots are under [docs/history/](docs/history/README.md).

## Verified state

- **Verified:** 2026-08-27 23:00 ET / 2026-08-28 03:00 UTC
- **Liquidity bot:** RUNNING under its supervisor, automatic selection at size
  10; supervisor PID 2245 and runner PID 2251 were unchanged through deployment
- **Startup gate:** complete authenticated reads proved zero venue orders and
  positions; the reviewed orphan-create recovery then resolved the one
  remaining no-exchange-ID PLACE and a second complete gate proved zero
  unknown-absent and unresolved rows
- **Deployment exposure boundary:** no authenticated venue exposure query was
  made for this no-restart deployment; current orders and positions are not
  inferred from the ledger or process state
- **Maker-lab:** stopped
- **GitHub:** `main` includes reviewed source commit `1ff0e42`
- **Server marker:** `1ff0e42`; source/tests match the marker
- **Latest trading-source change:** `1ff0e42`, deployed on disk but not loaded
  by the unchanged running process
- **Parity:** desktop/GitHub/server passed for source/tests; a laptop result is
  authoritative only from the laptop's own output
- **Verification:** all 690 local and reduced-priority server tests passed;
  documentation, diff, secret, shell-syntax and rolling-schema checks passed
- **Official API remediation:** seven prioritized reliability fixes and the
  rolling-report compatibility repair are pushed and deployed on disk; runner
  behavior and ledger migration remain inactive until an authorized restart
- **Changelog monitor:** public RSS baseline succeeded and the isolated hourly
  schedule is active at minute 23; it has no trading API or ledger access

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

Commit `dc26c96` preserves unknown dashboard state during venue failure,
reduces duplicate maintenance tracebacks, groups equivalent permanent book
faults, and replaces only positively closed, flat/order-free slot owners after
fresh exact proof. The 2026-08-28 start safely bound 19 owners and transactionally
topped up the free slot to exactly 20. Natural closed-owner replacement still
needs observation; do not induce a closure or disconnect to test it.

## Current operating instruction

Let the current run continue. Judge slot health from queue scope, distinct
owners, ownership state and resting coverage, not raw order count. Any future
stop/restart remains a separate operator decision and must use the normal
authenticated exposure gate.

The official-API remediation must not be treated as live merely because its
files and marker are deployed. The unchanged process still has its earlier
modules loaded. Migration and live behavior begin only after an explicitly
authorized safe restart and then require natural acceptance.

## Current repositories and records

- Open work: [NEXT.md](NEXT.md)
- Active measurements: [WATCHLIST.md](WATCHLIST.md)
- Findings index: [FINDINGS.md](FINDINGS.md)
- Production defects index: [BUGFIXES.md](BUGFIXES.md)
