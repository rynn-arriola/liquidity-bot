# Current operator status

Only current operational facts belong here; verify live before acting. Historical
snapshots are under [docs/history/](docs/history/README.md).

## Verified state

- **Verified:** 2026-08-28 11:04 ET / 2026-08-28 15:04 UTC
- **Liquidity bot:** LIVE; supervisor PID 31621 / runner PID 31627, size 10
- **Live exposure:** authenticated dashboard showed zero positions, 27 resting
  orders, zero unresolved and zero `UNKNOWN_ABSENT`; no risk stop is active
- **20-slot health:** 18/20 owners quoting and two bound-not-quoting owners;
  zero slots empty and the same runner continued placing
- **Maker-lab:** stopped
- **Running source:** `2831026`; server/GitHub stage `a8d91d0` needs a restart
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

## Latest restart outcome

The operator-authorized restart first re-proved the account flat, order-free
and ambiguity-free. Commit `2831026` started at about 14:55Z after passing the
740-test server suite. The same runner PID survived beyond the former 82-second
startup failure, bound all 20 markets and reached 18 quoting owners with 32
resting orders. A normal in-flight mutation briefly displayed as one unresolved
row and cleared on the next proof; final acceptance was zero positions, absent
orders and unresolved intents.

Live wire evidence from the failed prior start was
`order_eof=0/3 account_balance=3/3`: Polymarket sends a balance snapshot on each
private connection but can omit the order snapshot entirely when that scoped
subscription is empty. Startup and overlap repair now use each connection's
balance delivery as liveness proof, retain any order pages with exact-scope
validation, and require frozen whole-account REST balance/order/position proof
before decisions resume.

The first natural silence occurred on shard 3 at 15:02:46Z. Its overlap and
34-order reconciliation succeeded, but the final balance read hit Cloudflare
`1015`/HTTP 429. The generic failed-proof fallback quarantined the slate at
15:02:57Z without restarting PID 31627; it then rebuilt automatically. This
proves the base replacement works but exposes a second defect in rate-limited
proof handling. The staged correction retries only confirmed 429 reads while
decisions remain frozen and orders/private visibility are preserved.

## Current operating instruction

Keep the bot and collector running. Do not induce a 180-second private-stream
failure to test recovery. Avoid repeated authenticated dashboard probes during
live recovery because their separate process does not share the runner limiter.
Staged commit `a8d91d0` is deployed but inactive until an authorized restart. Do not
change strategy, size, slots or stops from measurement output.

## Current records

[NEXT](NEXT.md) · [WATCHLIST](WATCHLIST.md) · [FINDINGS](FINDINGS.md) · [BUGFIXES](BUGFIXES.md)
