# Current operator status

Only current operational facts belong here. Verify them live before acting.
Historical snapshots are under [docs/history/](docs/history/README.md).

## Verified state

- **Verified:** 2026-08-27 21:01 ET / 2026-08-28 01:01 UTC
- **Liquidity bot:** RUNNING under its supervisor, automatic selection at size
  10
- **Startup gate:** complete authenticated reads proved zero venue orders and
  positions; the reviewed orphan-create recovery then resolved the one
  remaining no-exchange-ID PLACE and a second complete gate proved zero
  unknown-absent and unresolved rows
- **Latest live checkpoint:** 20 active market owners, 28 venue orders across
  17 markets, zero nonzero positions and zero `UNKNOWN_ABSENT`; two fresh
  acknowledged mutations were ordinary in-progress cancel/place work
- **Maker-lab:** stopped
- **GitHub:** `main` includes the compact AI handoff through `f2a5e02`; run
  parity for the exact current documentation-only HEAD
- **Server marker:** `14e438a`
- **Latest trading-source change:** `dc26c96`
- **Parity:** desktop/GitHub/server passed; a laptop result is authoritative
  only from the laptop's own output
- **Documentation acceptance:** local and stopped-server audits plus all 667
  tests passed; server AI files are installed and no bot restart occurred
- **Official API remediation:** seven prioritized reliability fixes and the
  safe rolling-report compatibility repair passed final local review; rollout
  is authorized but not yet deployed or active in production

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

The local official-API remediation must not be treated as live until it is
committed, deployed, migrated and accepted after an explicitly authorized safe
restart. Until then the server continues to run the earlier deployed source.

## Current repositories and records

- Open work: [NEXT.md](NEXT.md)
- Active measurements: [WATCHLIST.md](WATCHLIST.md)
- Findings index: [FINDINGS.md](FINDINGS.md)
- Production defects index: [BUGFIXES.md](BUGFIXES.md)
