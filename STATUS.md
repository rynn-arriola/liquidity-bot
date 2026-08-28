# Current operator status

Only current operational facts belong here. Verify them live before acting.
Historical snapshots are under [docs/history/](docs/history/README.md).

## Verified state

- **Verified:** 2026-08-28 07:40 ET / 2026-08-28 11:40 UTC
- **Liquidity bot:** STOPPED; no commissioning supervisor or runner remains
- **Live exposure:** the 11:40Z authenticated stopped-state dashboard proved zero positions, zero resting orders, zero unresolved and zero `UNKNOWN_ABSENT`
- **Maker-lab:** stopped
- **GitHub:** `main` includes webhook-routing repair `1d48584`
- **Server marker:** `8e348eb`; source/tests match the marker
- **Latest deployed trading source:** `1ff0e42`, on disk but not running
- **Parity:** desktop/GitHub source match `1d48584`; server matches marker `8e348eb`.
  Undeployed source and unrelated local measurement work leave expected drift
- **Deployed verification:** all 706 local and reduced-priority server tests passed
- **Official API remediation:** seven prioritized reliability fixes and the
  rolling-report compatibility repair are pushed, deployed and active; the
  additive `execution_trade_states`/fill-reversal migration completed
- **Changelog monitor:** public RSS baseline succeeded and the isolated hourly
  schedule is active at minute 23; it has no trading API or ledger access
- **Spread-scalp measurement:** isolated supervisor PID 8376 / collector PID
  8382 is active against `data/scalp.db`; it has no order path
- **Pending strategy repair:** pushed commit `dc71ae8` safely replaces
  continuously economically idle slots; it is not deployed or active
- **Pending notification repair:** pushed `1d48584` routes operations to error/status with no summary fallback; it is not deployed

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

## Last run outcome

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

At the operator's 11:39Z stop, shutdown discovered a 2.47-contract position
created after the earlier flat check and crossed it once at 0.43. The runner
then exited, and the independent 11:40Z authenticated dashboard proved flat,
order-free and ambiguity-free. Commit `dc71ae8` was not deployed, the production
marker remains `8e348eb`, and the separate scalp measurement stayed running.

## Current operating instruction

Keep the liquidity bot stopped. Do not deploy or restart until the operator
explicitly authorizes both. The reviewed economic-idle repair remains pushed
and undeployed; webhook repair `1d48584` is pushed and undeployed. Any restart
must re-run the authenticated exposure gate.

## Current repositories and records

- Open work: [NEXT.md](NEXT.md)
- Active measurements: [WATCHLIST.md](WATCHLIST.md)
- Findings index: [FINDINGS.md](FINDINGS.md)
- Production defects index: [BUGFIXES.md](BUGFIXES.md)
