# Current operator status

Only current operational facts belong here; verify live before acting. Historical
snapshots are under [docs/history/](docs/history/README.md).

## Verified state

- **Verified:** 2026-08-28 14:11 ET / 2026-08-28 18:11 UTC
- **Liquidity bot:** LIVE; supervisor PID 34772 / runner PID 34778, size 10
- **Live exposure:** authenticated dashboard proved zero positions and 30
  resting orders, zero `UNKNOWN_ABSENT` and three just-submitted unresolved
  intents; no risk stop is active
- **20-slot health:** rebuilding after whole-stream recovery: 16/20 quoting,
  two bound-idle and two empty; same runner remains active
- **Maker-lab:** stopped
- **Running source:** the unchanged bot has `a8d91d0` behavior loaded; report
  fix `1c82778` is deployed for hourly/new report processes
- **Verification:** all 745 local and server tests, documentation audit,
  compilation and deliberate reversal checks passed
- **Webhook routing:** operations use error/status only; routine live, shadow
  and dashboard summaries explicitly opt into summary delivery
- **Spread-scalp measurement:** supervisor PID 22830 / collector PID 38363
  started 12:04:58Z; separate database/socket, one-read-per-second cap and no
  order path
- **Fixed GTC:** 20 trials appeared after warm-up; 20/20 feature rows and 20
  clean paths, with zero false historical-gap exclusions
- **Other collection:** adverse-fill and queue studies run inside the bot;
  daily probe, hourly rewards/changelog and nightly backup schedules are active
- **Reporting fix:** deployed report accepted August 26 at audited `-$64.49`
  trading and `+$145.12` finalized net; no bot restart occurred

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

## Latest start outcome

The operator-authorized start followed a 15:34Z authenticated flat/order-free
preflight. The new runner completed discovery without restart, loaded the staged
429 handoff correction and reached 20/20 quoting with 37 resting orders by
15:38Z. One just-acknowledged PLACE was visible during that snapshot and cleared
normally; the 15:39Z ledger check showed zero unresolved and zero absent orders.
No startup-window traceback, critical, 429, quarantine or recovery event was logged.

Private-only silence then recovered repeatedly with `cancels=0`, proving the
overlap handoff preserves orders. At 16:22Z both the market stream and one
private shard were silent together; the designed whole-stream fallback globally
quarantined the slate and rebuilt under unchanged PID 34778. By 16:25Z it had
restored 30 orders. This was not a 429 or 20-slot capacity change.

## Current operating instruction

Keep the liquidity bot and isolated spread-scalp collector running. Commit
`a8d91d0` is active, but do not induce a 180-second private-stream failure to
test it. Avoid repeated authenticated
dashboard probes during live recovery because their separate process does not
share the runner limiter. Do not change strategy, size, slots or stops from
measurement output.

## Current records

[NEXT](NEXT.md) · [WATCHLIST](WATCHLIST.md) · [FINDINGS](FINDINGS.md) · [BUGFIXES](BUGFIXES.md)
