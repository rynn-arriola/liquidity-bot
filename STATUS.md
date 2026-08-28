# Current operator status

Only current operational facts belong here; verify live before acting. Historical
snapshots are under [docs/history/](docs/history/README.md).

## Verified state

- **Verified:** 2026-08-28 11:28 ET / 2026-08-28 15:28 UTC
- **Liquidity bot:** STOPPED by operator request; no supervisor or runner, persisted
  size 10
- **Live exposure:** post-stop authenticated dashboard proved zero positions,
  zero resting orders, zero unresolved and zero `UNKNOWN_ABSENT`
- **20-slot health:** inactive while stopped; no claim about live slot capacity
- **Maker-lab:** stopped
- **Running source:** none; server/GitHub stage `a8d91d0` loads on the next
  authorized start
- **Verification:** all 744 local and server tests, documentation audit,
  compilation and deliberate reversal checks passed
- **Webhook routing:** operations use error/status only; routine live, shadow
  and dashboard summaries explicitly opt into summary delivery
- **Spread-scalp measurement:** supervisor PID 22830 / collector PID 22836
  started 12:04:58Z; separate database/socket, one-read-per-second cap and no
  order path
- **Fixed GTC:** 20 trials appeared after warm-up; 20/20 feature rows and 20
  clean paths, with zero false historical-gap exclusions
- **Other collection:** adverse-fill and queue studies run inside the bot;
  daily probe, hourly rewards/changelog and nightly backup schedules are active

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

## Latest stop outcome

At 15:27Z the operator redirected the pending restart into a graceful stop.
The pre-stop authenticated snapshot proved zero positions but 17 resting orders.
The stop disabled supervisor PID 31621, signaled runner PID 31627, cancelled the
orders and exited in 27 seconds with the runner's final record `flat=true` and
`stop_reason=operator_sigterm`. The independent post-stop authenticated snapshot
at 15:28Z proved zero positions, zero resting orders, zero unresolved mutations
and zero `UNKNOWN_ABSENT` rows.

## Current operating instruction

Keep the liquidity bot stopped until the operator explicitly authorizes a new
start. The isolated spread-scalp collector remains independent. On the next
authorized start, staged commit `a8d91d0` becomes active; do not induce a
180-second private-stream failure to test it. Avoid repeated authenticated
dashboard probes during live recovery because their separate process does not
share the runner limiter. Do not change strategy, size, slots or stops from
measurement output.

## Current records

[NEXT](NEXT.md) · [WATCHLIST](WATCHLIST.md) · [FINDINGS](FINDINGS.md) · [BUGFIXES](BUGFIXES.md)
