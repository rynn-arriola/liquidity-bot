# Current operator status

Only current operational facts belong here. Verify them live before acting.
Historical snapshots are under [docs/history/](docs/history/README.md).

## Verified state

- **Verified:** 2026-08-28 08:23 ET / 2026-08-28 12:23 UTC
- **Liquidity bot:** LIVE; supervisor PID 21783 / runner PID 21791, size 10
- **Live exposure:** authenticated dashboard showed zero positions, 35 resting
  orders, zero `UNKNOWN_ABSENT` and one seconds-old acknowledged PLACE
- **20-slot health:** 20/20 owners quoting, zero bound-idle and zero empty
- **Maker-lab:** stopped
- **GitHub/server:** `main` and marker `de1478b`; all deployed hashes matched
- **Verification:** all 726 local and reduced-priority server tests passed;
  documentation audit and deliberate reversal check passed
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

The operator-authorized restart first proved the account flat, order-free and
ambiguity-free. Commit `53fa838` passed 724 server tests and the docs audit,
then started at 11:58:57Z. Startup released three expired owners, committed
back to 20 active markets and resumed post-only placement. One market-local
unknown-state recovery at 12:02:55Z reconciled and resumed naturally; by 12:06Z
the dashboard was working with 19 quoted owners, one economic blocker, zero
positions and zero unresolved rows. No current-run REST 429 was observed.

The old scalp collector needed isolated SIGTERM after its graceful flush did
not finish; it has no trading path and `PRAGMA quick_check` returned `ok` before
the new collector started. The first GTC report exposed an offline-reporting
defect: one cumulative historical run gap censored every later clean pair.
Commit `de1478b` scopes reports to overlapping intervals; its report is active.
The collector started before that commit, so forward decode/drop interval
closure loads on its next natural supervisor restart.

## Current operating instruction

Keep the bot and collector running. Do not interrupt the 20 clean GTC trials
only to reload measurement code. Do not change strategy, size, slots or stops
from measurement output; collect three complete days plus one held-out day.

## Current repositories and records

- Open work: [NEXT.md](NEXT.md)
- Active measurements: [WATCHLIST.md](WATCHLIST.md)
- Findings index: [FINDINGS.md](FINDINGS.md)
- Production defects index: [BUGFIXES.md](BUGFIXES.md)
