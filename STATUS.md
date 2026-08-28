# Current operator status

Only current operational facts belong here; verify live before acting. Historical
snapshots are under [docs/history/](docs/history/README.md).

## Verified state

- **Verified:** 2026-08-28 15:56 ET / 2026-08-28 19:56 UTC
- **Liquidity bot:** LIVE; supervisor PID 46008 / runner PID 46019, size 10
- **Live exposure:** dashboard: 0 positions, 19 resting, 0 absent/unresolved
- **20-slot health:** 19/20 quoting; one empty slot reports no qualifying
  supply. No slot, size, horizon or strategy setting changed in this repair
- **Maker-lab:** stopped
- **Deployed source:** server marker is `63061cf`; runner has the `c28aba2`
  no-ID startup repair loaded
- **Stable rollback anchor:** `v1.0.0` points to deployed commit `744b7c1`;
  tagging it did not stop, restart or mutate the live process
- **Dynamic size capability:** `5165fc4` is deployed; whole values 5-30 share
  one validator, while persisted/running size remains 10 without a restart
- **Verification:** all 755 local and server tests, Linux shell syntax,
  compilation, documentation audit, three reversals and four-way parity pass
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

## Latest stop/restart outcome

The operator-authorized restart on 2026-08-28 stopped supervisor 34772, but
runner 34778 could not complete its 300-second shutdown because one ambiguous
PLACE had no exchange order ID. After authenticated flat/order-free proof, it
was force-stopped. Three replacement runners then died in 11-12 seconds on
`book is empty on both sides`: startup had bound the no-ID intent's finished
market as live recovery risk. The supervisor gave up after its three-fast-death
bound. The underlying mutation was a Polymarket/Cloudflare 504 at 18:35:11Z.

The reviewed stopped-only recovery was run dry and then applied. Its fresh
snapshot returned zero live orders and zero position markets, so exactly that
intent moved `UNCERTAIN -> FAILED`; zero unresolved remain. Deployed `c28aba2`
now makes future startups refuse once with the correct recovery instruction
instead of loading the stale market and entering a misleading transport-failure
loop. The authorized 19:54Z start passed live acceptance: the runner remained
up, placed 19 resting orders across 19/20 slots, stayed flat and reported no
unresolved/absent mutation or risk stop. Old `book is empty on both sides`
lines at 19:23Z belong to the failed pre-fix run, not the current process.

## Current operating instruction

Keep the current runner live unless the operator directs otherwise or a safety
condition requires a stop. Do not restart merely because the historical 19:23Z
empty-book failures remain in the append-only log. Avoid repeated authenticated
probes; the 19:56Z dashboard is the accepted post-start snapshot. Do not change
strategy, size, slots or stops from this availability repair.

## Current records

[NEXT](NEXT.md) · [WATCHLIST](WATCHLIST.md) · [FINDINGS](FINDINGS.md) · [BUGFIXES](BUGFIXES.md)
