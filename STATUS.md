# Current operator status

Only current operational facts belong here; [historical snapshots](docs/history/README.md) are archived.

## Verified state

- **Verified:** 2026-08-28 18:14 ET / 2026-08-28 22:14 UTC
- **Liquidity bot:** LIVE; supervisor PID 51980 / runner PID 51989, size 10
- **Live exposure:** last authenticated 22:05Z snapshot found 1 position/32 orders;
  later logs retired a market with 3.13 contracts, so re-read before mutation
- **20-slot health:** scheduled 22:11Z top-up restored `active=20`, then outcome-
  uncertain placements retired five markets and left more recovery in flight
- **Maker-lab:** stopped
- **Deployed source:** runner and server marker are commit `2ece3c3`
- **Stable rollback anchor:** `v1.0.0` points to deployed commit `744b7c1`;
  tagging it did not stop, restart or mutate the live process
- **Risk scaling:** active size-10 configuration is $300 portfolio, $40 market,
  $80 session and $80 hard
- **Verification:** 67 focused and all 766 local/server tests passed, plus the
  deliberate post-flat authorization reversal
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

## Deployed post-flat refill optimization

The 21:08:53Z flat proof waited until 21:11:17Z for refill despite 29-34 viable
markets. Commit `2ece3c3` now gives mandatory post-flat holes one safe, freshly
revalidated fast path while preserving exit priority and optional-idle cadence.
Natural post-flat acceptance remains pending; do not induce a position to test
it. No `[top-up-request]` occurred through 22:14Z; the 22:11Z refill was a normal
scheduled scan, not acceptance. [Full evidence](docs/history/POST_FLAT_REFILL_2026-08-28.md).

## Latest stop/restart outcome

At 21:51Z authenticated proof showed zero positions/orders. The old process
stopped gracefully and cleared 29 stale `UNKNOWN_ABSENT` rows. Commit `2ece3c3`
passed all 766 server tests before the authorized size-10 restart. At 22:00Z the
new runner had 20 bound, 18 quoting, 34 resting orders, zero positions,
absent/unresolved rows or risk stops. At 22:01Z one silent private shard
recovered under the same PID with `cancels=0` and all 33 working orders retained.
The next normal fill opened one managed position; its transient absent cancel
cleared by 22:05Z and the dashboard returned to `WORKING` with no risk stop.
From 22:07-22:14Z eight PLACE outcomes became uncertain during/after all-shard
private silence. Scoped recovery preserved 28 orders with `cancels=0`, but five
markets retired unresolved; one carried 3.13 contracts. This is separate from
the post-flat refill repair and remains live recovery work.

## Current operating instruction

Do not restart or force-stop while position/mutation outcomes remain unresolved.
Let recovery work, then require venue proof; observe post-flat only naturally.

## Current records

[NEXT](NEXT.md) · [WATCHLIST](WATCHLIST.md) · [FINDINGS](FINDINGS.md) · [BUGFIXES](BUGFIXES.md)
