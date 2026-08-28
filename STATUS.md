# Current operator status

Only current operational facts belong here; [historical snapshots](docs/history/README.md) are archived.

## Verified state

- **Verified:** 2026-08-28 19:01 ET / 23:01 UTC
- **Liquidity bot:** STOPPED; supervisor and runner are absent
- **Stop proof:** 23:00Z authenticated dashboard found zero positions/orders;
  the 23:01Z graceful-stop record ended `flat=true`
- **Stop method:** normal SIGTERM completed in about 30 seconds; no force used
- **Last loaded source:** `31f815c`; it ran from 22:56Z to 23:01Z
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
| Production runtime | Trading disabled on current server; no local runner |
| Quote size | 10 contracts |
| Concurrent capacity | 20 market slots |
| Entry horizon | Six hours |
| League filter | None |
| New-entry spread | 2 cents; 1.5-cent retention hysteresis |
| Portfolio / market loss | $300 / $40 (`30× / 4×` size) |
| Account threshold | $80 realized sensor and $80 marked-equity sensor |
| Risk day | `America/New_York`, DST-aware |

[DECISIONS.md](DECISIONS.md) is authoritative for policy details.

## Active incident and accepted containment

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

The authorized 22:56Z start repaired exactly 20 legacy intents, bound all 20
owners and made three probes about 72 seconds apart. Every probe received the
same VPN/proxy 403. Later slots backed off locally: zero owners retired, zero
intents remained unresolved and the account stayed flat/order-free. This
accepts containment, not access recovery; no quote could rest.

The forced stop exposed a second defect: all-no-ID repair can complete without
an `await`, making ready stream tasks spin the loop and starve Unix signal
callbacks. Every outer pass now yields, and a fully no-ID-blocked active slate
adds a 250ms idle backoff without adding API calls or changing strategy.

## Current operating instruction

Keep trading disabled on this server; no manual restart. Polymarket still
rejects its mutations as VPN/proxy traffic. Do not use a residential proxy to
evade that control. Contact Polymarket US support for permitted-host guidance;
any replacement runtime needs explicit operator approval and a safe migration.

## Current records

[NEXT](NEXT.md) · [WATCHLIST](WATCHLIST.md) · [FINDINGS](FINDINGS.md) · [BUGFIXES](BUGFIXES.md)
