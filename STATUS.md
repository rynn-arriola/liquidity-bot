# Current operator status

Only current operational facts belong here. Verify them live before acting.
Historical snapshots are under [docs/history/](docs/history/README.md).

## Verified state

- **Verified:** 2026-08-28 00:19 ET / 2026-08-28 04:19 UTC
- **Liquidity bot:** RUNNING under its supervisor, automatic selection at size
  10; the reviewed restart launched supervisor PID 5439 and runner PID 5445
- **Startup gate:** after the operator manually flattened and canceled all
  venue exposure, an authenticated stopped-state read proved zero positions,
  zero resting orders and zero unresolved rows before start
- **Live exposure:** the 04:18Z authenticated dashboard showed one position,
  29 resting orders and no risk stop; a follow-up ledger read proved zero
  unresolved rows
- **Maker-lab:** stopped
- **GitHub:** `main` includes reviewed source commit `8e348eb`; the current
  documentation handoff is tracked separately from the server source marker
- **Server marker:** `8e348eb`; source/tests match the marker
- **Latest trading-source change:** `1ff0e42`, deployed and active in the
  unchanged PID 5445 process; deploying the measurement did not restart it
- **Parity:** desktop/GitHub/server passed for source/tests; a laptop result is
  authoritative only from the laptop's own output
- **Verification:** all 706 local and reduced-priority server tests passed;
  documentation, diff, secret, shell-syntax and rolling-schema checks passed
- **Official API remediation:** seven prioritized reliability fixes and the
  rolling-report compatibility repair are pushed, deployed and active; the
  additive `execution_trade_states`/fill-reversal migration completed
- **Changelog monitor:** public RSS baseline succeeded and the isolated hourly
  schedule is active at minute 23; it has no trading API or ledger access
- **Spread-scalp measurement:** isolated supervisor PID 8376 / collector PID
  8382 is active against `data/scalp.db`; it has no order path

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

The separate strict spread-scalp collector started at 04:07:22Z without a
trading-bot restart. Its 10-minute acceptance covered 100 markets, 24,050 book
updates and 792 public trades across both `PURE_VOLUME` and `REWARD_SLOTS`,
with 99.3% stream coverage and zero gaps, reconnects, stream errors or REST
429s. The bot remained active. One private-socket silence recovered normally;
the same condition repeatedly predated this collector, including at 04:05Z.

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
