# Current operator status

Only current operational facts belong here; [historical snapshots](docs/history/README.md) are archived.

## Verified state

- **Verified:** 2026-08-28 18:54 ET / 22:54 UTC
- **Liquidity bot:** STOPPED; supervisor and runner are absent
- **Stop proof:** latest authenticated recovery found zero working orders;
  authoritative ledger snapshot found zero nonzero positions. Twenty legacy
  no-ID PLACE intents remain `UNCERTAIN` and match exact 403/code-7 proof.
- **Stop method:** graceful SIGTERM exhausted 300 seconds because the old
  no-ID repair loop starved its callback; after flat/order-free proof, the
  reviewed force path terminated PID 51989. Nothing was restarted.
- **Last loaded source:** `2ece3c3`; it did not contain today's repair
- **Deployed repair:** `31f815c`; classifier, entry circuit, risk-action bypass, legacy
  startup repair and cooperative-loop backoff are implemented; focused tests and three deliberate
  reversals plus all 776 local/server tests pass; all 13 deployed hashes matched
- **Maker-lab:** stopped
- **Risk scaling:** size 10 = $300 portfolio, $40 market, $80 session/$80 hard
- **Measurement:** spread-scalp collector is isolated; adverse-fill and queue
  studies remain observational; scheduled reports/backups remain independent
- **Stable rollback:** `v1.0.0` points to `744b7c1`

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

## Active incident and deployed dormant repair

From 22:07 UTC, Retail order mutations returned structured HTTP 403/code 7
location/VPN denials. The old gateway mislabeled these positive rejections as
ambiguous, creating no-ID `UNCERTAIN` PLACE intents. Retirement then consumed
all 20 owners. Private-shard silence recovered safely and the post-flat fast
path never ran, so neither caused this collapse.

The deployed repair fails a response-backed 403 intent, opens a 60-second
new-entry circuit, alerts once, and probes recovery with one later desired
entry. Cancels and reducers bypass an entry denial; only a rejection of their
own request opens a short 10-second risk-mutation circuit. Each circuit clears
only on a successful mutation in its scope. Startup also repairs
only legacy rows containing the exact persisted 403/code-7 rejection proof;
timeouts, 5xx, absence and near-matches remain unresolved. [Full evidence and
verification](docs/history/MUTATION_ACCESS_DENIAL_2026-08-28.md).

The forced stop exposed a second defect: all-no-ID repair can complete without
an `await`, making ready stream tasks spin the loop and starve Unix signal
callbacks. Every outer pass now yields, and a fully no-ID-blocked active slate
adds a 250ms idle backoff without adding API calls or changing strategy.

## Current operating instruction

Keep the bot stopped; do not restart without separate operator authorization.
On the next authorized
start, verify the exact 20 legacy rows repair before the orphan gate, then prove
20-slot recovery and mutation-access circuit behavior from natural evidence.

## Current records

[NEXT](NEXT.md) · [WATCHLIST](WATCHLIST.md) · [FINDINGS](FINDINGS.md) · [BUGFIXES](BUGFIXES.md)
