# Current operator status

Only current operational facts belong here; verify live before acting. Historical
snapshots are under [docs/history/](docs/history/README.md).

## Verified state

- **Verified:** 2026-08-28 16:33 ET / 2026-08-28 20:33 UTC
- **Liquidity bot:** LIVE; supervisor PID 47601 / runner PID 47608, size 10
- **Live exposure:** 1 position, 32 resting, 0 absent, 1 current submitting intent
- **20-slot health:** 18/20 quoting; two bound owners are temporarily not quoting
- **Maker-lab:** stopped
- **Deployed source:** runner has `b7fe3fd` trading code; dashboard/reporting fix
  was deployed afterward without restarting or changing the runner PID
- **Stable rollback anchor:** `v1.0.0` points to deployed commit `744b7c1`;
  tagging it did not stop, restart or mutate the live process
- **Risk scaling:** active size-10 configuration is $300 portfolio, $40 market,
  $80 session and $80 hard
- **Verification:** 757 local tests, 756 server tests, focused server dashboard
  tests, compilation, docs audit and four multiplier reversals pass
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
| Portfolio / market loss | $300 / $40 (`30× / 4×` size) |
| Account threshold | $80 realized sensor and $80 marked-equity sensor |
| Risk day | `America/New_York`, DST-aware |

[DECISIONS.md](DECISIONS.md) is authoritative for policy details.

## Latest stop/restart outcome

At 20:22Z authenticated proof showed zero positions/orders and no unresolved or
absent state. The old process stopped gracefully, `b7fe3fd` deployed, and all
756 server tests passed before the authorized size-10 restart. The new runner
loaded `$300/$40/$80/$80`; after seven minutes it had 18/20 quoting, 32 resting,
one managed position, no absent order and no risk stop. Startup exposed a
dashboard-only defect: property-based limits fell into stale `$40/$40/$45`
fallbacks. The dashboard now reads the configured instance and displays the
same scaled limits as the runner; it was deployed without another restart.

## Current operating instruction

Keep the current runner live unless the operator directs otherwise or a safety
condition requires a stop. The one 20:33Z submitting intent was current order
flow, not persistent ambiguity; investigate only if it ages instead of
reconciling. Do not restart for the dashboard-only repair already on disk.

## Current records

[NEXT](NEXT.md) · [WATCHLIST](WATCHLIST.md) · [FINDINGS](FINDINGS.md) · [BUGFIXES](BUGFIXES.md)
