# Current operator status

Only current operational facts belong here. Verify them live before acting.
Historical snapshots are under [docs/history/](docs/history/README.md).

## Verified state

- **Verified:** 2026-08-27 19:08 ET / 23:08 UTC
- **Liquidity bot:** STOPPED; no supervisor or runner process
- **Stop quality:** FORCED during an authenticated venue outage
- **Venue exposure:** UNKNOWN; possible unmanaged orders must be ruled out by a
  new complete authenticated account snapshot before any restart
- **Maker-lab:** stopped
- **GitHub:** `main` at `d59339a` at the last parity check
- **Server marker:** `e97e231`
- **Latest trading-source change:** `dc26c96`
- **Parity:** desktop/GitHub/server passed; a laptop result is authoritative
  only from the laptop's own output

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

## Deployed but not live-accepted after the stop

Commit `dc26c96` preserves unknown dashboard state during venue failure,
reduces duplicate maintenance tracebacks, groups equivalent permanent book
faults, and replaces only positively closed, flat/order-free slot owners after
fresh exact proof. Local and stopped-server suites passed before deployment.
Because the bot has not restarted, the closed-slot behavior still needs natural
live acceptance on the next authorized run.

## Next safe action

Do not restart from this snapshot alone. First obtain one complete authenticated
venue view of open orders and positions and reconcile unresolved ownership.
Restart remains a separate operator decision. After a safe start, verify queue
scope `markets=20`, distinct active owners, zero unmanaged ownership and normal
top-up behavior; raw order count alone is not acceptance.

## Current repositories and records

- Open work: [NEXT.md](NEXT.md)
- Active measurements: [WATCHLIST.md](WATCHLIST.md)
- Findings index: [FINDINGS.md](FINDINGS.md)
- Production defects index: [BUGFIXES.md](BUGFIXES.md)
