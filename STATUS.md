# Current operator status

Only current operational facts belong here; [historical snapshots](docs/history/README.md) are archived.

## Verified state

- **Verified:** 2026-08-28 20:11 ET / 2026-08-29 00:11 UTC
- **Liquidity bot:** STOPPED; supervisor and runner are absent
- **Stop proof:** 23:00Z authenticated dashboard found zero positions/orders;
  the 23:01Z graceful-stop record ended `flat=true`
- **Stop method:** normal SIGTERM completed in about 30 seconds; no force used
- **Last loaded source:** `31f815c`; it ran from 22:56Z to 23:01Z
- **Deployed repair:** `31f815c`; classifier, entry circuit, risk-action bypass, legacy
  startup repair and cooperative-loop backoff are implemented; focused tests and three deliberate
  reversals plus all 776 local/server tests pass; all 13 deployed hashes matched
- **Maker-lab:** stopped
- **Laptop runtime:** Ubuntu 24.04 WSL2 outside OneDrive; Python 3.12, pinned SDK,
  size 10, restricted credentials and exact stopped execution/scalp snapshots
  are installed; ledger integrity/hashes passed and all 776 tests passed
- **Laptop account proof:** the 00:07Z authenticated read found zero positions
  and zero working orders; the local runner/supervisor are absent
- **Laptop access/power:** Windows and WSL use the same US residential Comcast
  egress with no active VPN adapter observed; Retail health returned 200/OK;
  AC sleep is disabled, AC lid-close does nothing and AC Wi-Fi uses maximum
  performance
- **Recovery copy on desktop:** the 23:21Z execution ledger passed integrity
  (`31,038` orders, `2,642` fills, 139.1 MB); the 23:38Z scalp database passed
  integrity with all nine tables (1.35 GB); restricted credentials, the 352-file
  `31f815c` recovery archive and a full-log/crontab archive also validated
- **Risk scaling:** size 10 = $300 portfolio, $40 market, $80 session/$80 hard
- **Measurement:** spread-scalp collector is isolated; adverse-fill and queue
  studies remain observational; scheduled reports/backups remain independent
- **Stable rollback:** `v1.0.0` points to `744b7c1`

## Active operating envelope

| Setting | Current decision |
|---|---|
| Production runtime | Current server stopped/disabled; laptop selected as the next primary but not started; desktop is cold standby |
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

From 22:07 UTC, Retail mutations returned HTTP 403/code 7 location/VPN
denials. The old gateway mislabeled them as ambiguous no-ID PLACE intents and
retirement consumed all 20 owners. The deployed repair fails only the rejected
intent, limits later entry probes, preserves cancels/reducers and repairs only
legacy rows with exact persisted rejection proof.

The 22:56Z acceptance repaired 20 rows, retained 20 owners and kept zero
unresolved/retired through three denied probes. The account stayed flat and no
quote could rest: containment passed, access recovery did not. A separate
cooperative yield/backoff repair prevents the all-no-ID loop from starving
signals. [Evidence](docs/history/MUTATION_ACCESS_DENIAL_2026-08-28.md).

## Current operating instruction

Keep trading disabled on this server; no manual restart. The laptop is the
selected next primary and the desktop its cold standby, but no local runner is
authorized yet. Laptop parity, direct verified execution/scalp/recovery pulls,
Linux runtime, tests, account read and home-network/power preflight pass. Four
replacement laptop report/backup timers are installed but disabled: SSH became
unreachable before the preserved server crontab could be disabled, so enabling
both copies would duplicate work. Before an explicit start, complete that
schedule handoff, prove the desktop runner absent and make the laptop the sole
writable ledger/API owner. Do not use a residential proxy or run both machines
at once.

## Current records

[NEXT](NEXT.md) · [WATCHLIST](WATCHLIST.md) · [FINDINGS](FINDINGS.md) · [BUGFIXES](BUGFIXES.md)
