# Current operator status

Only current operational facts belong here. Verify them live before acting.
Historical snapshots are under [docs/history/](docs/history/README.md).

## Verified state

- **Verified:** 2026-08-27 23:11 ET / 2026-08-28 03:11 UTC
- **Liquidity bot:** RUNNING under its supervisor, automatic selection at size
  10; the reviewed restart launched supervisor PID 5439 and runner PID 5445
- **Startup gate:** after the operator manually flattened and canceled all
  venue exposure, an authenticated stopped-state read proved zero positions,
  zero resting orders and zero unresolved rows before start
- **Live exposure:** authenticated acceptance showed zero positions, 37
  resting orders, zero unresolved rows and no risk stop
- **Maker-lab:** stopped
- **GitHub:** `main` includes reviewed source commit `1ff0e42`; the current
  documentation handoff is tracked separately from the server source marker
- **Server marker:** `1ff0e42`; source/tests match the marker
- **Latest trading-source change:** `1ff0e42`, deployed and active in the
  current process
- **Parity:** desktop/GitHub/server passed for source/tests; a laptop result is
  authoritative only from the laptop's own output
- **Verification:** all 690 local and reduced-priority server tests passed;
  documentation, diff, secret, shell-syntax and rolling-schema checks passed
- **Official API remediation:** seven prioritized reliability fixes and the
  rolling-report compatibility repair are pushed, deployed and active; the
  additive `execution_trade_states`/fill-reversal migration completed
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

The operator-authorized 03:06Z restart loaded source marker `1ff0e42` from a
positively flat/order-free account. By 03:10Z it held exactly 20/20 quoting
owners and 37 resting orders, with zero positions and zero unresolved rows.
The restart-window log recorded a 20-market queue, 44 placements and no
traceback, unresolved-mutation timeout, runtime recovery, stream-silence event,
buying-power block or rejection backoff. Natural failure/reversal paths still
need observation; do not induce a closure, disconnect, rejection or bust.

## Current operating instruction

Let the current run continue. Judge slot health from queue scope, distinct
owners, ownership state and resting coverage, not raw order count. Any future
stop/restart remains a separate operator decision and must use the normal
authenticated exposure gate.

## Current repositories and records

- Open work: [NEXT.md](NEXT.md)
- Active measurements: [WATCHLIST.md](WATCHLIST.md)
- Findings index: [FINDINGS.md](FINDINGS.md)
- Production defects index: [BUGFIXES.md](BUGFIXES.md)
