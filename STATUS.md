# Current operator status

Only current operational facts belong here; [historical snapshots](docs/history/README.md) are archived.

## Verified state

- **Verified:** 2026-08-28 20:42 ET / 2026-08-29 00:42 UTC
- **Liquidity bot:** LIVE on the laptop; supervisor PID 778 and runner PID 785
- **Former server:** destroyed; it cannot run the bot or execute old schedules
- **Live proof:** the 00:42Z authenticated dashboard found 20/20 quoting
  markets, 39 resting orders, zero positions, zero absent/unresolved state and
  no risk stop; laptop mutations succeeded without a 403/access error
- **Loaded source:** `04fddb1`; its trading core remains the reviewed `31f815c`
  repair and it adds only the isolated read-only TV dashboard/startup hook
- **Verification:** all 785 WSL tests passed before startup; the WSL checkout,
  GitHub `main` and `DEPLOYED_COMMIT` all identify `04fddb1`
- **Maker-lab:** stopped
- **Laptop runtime:** Ubuntu 24.04 WSL2 outside OneDrive; Python 3.12, pinned SDK,
  size 10, restricted credentials and exact stopped execution/scalp snapshots
  are installed; ledger integrity/hashes passed and all 785 tests passed
- **Laptop account proof:** the 00:39Z preflight was flat/order-free; the
  00:42Z authenticated live read found 39 working orders across all 20 slots
  and zero positions
- **Laptop access/power:** Windows and WSL use the same US residential Comcast
  egress with no active VPN adapter observed; Retail health returned 200/OK;
  AC sleep is disabled, AC lid-close does nothing and AC Wi-Fi uses maximum
  performance
- **Laptop schedules:** four read-only backup/report systemd timers are active;
  a stopped-safe ledger backup passed; no trading service is installed/enabled
- **Recovery copy on desktop:** the 23:21Z execution ledger passed integrity
  (`31,038` orders, `2,642` fills, 139.1 MB); the 23:38Z scalp database passed
  integrity with all nine tables (1.35 GB); restricted credentials, the 352-file
  `31f815c` recovery archive and a full-log/crontab archive also validated
- **Risk scaling:** size 10 = $300 portfolio, $40 market, $80 session/$80 hard
- **Measurement/display:** studies remain observational; the reviewed local TV
  view auto-opens after runner proof, uses zero venue calls and cannot fail trading
- **Stable rollback:** `v1.0.0` points to `744b7c1`

## Active operating envelope

| Setting | Current decision |
|---|---|
| Production runtime | Laptop is the sole live runtime; desktop is off/manual cold backup; former server destroyed |
| Quote size | 10 contracts |
| Concurrent capacity | 20 market slots |
| Entry horizon | Six hours |
| League filter | None |
| New-entry spread | 2 cents; 1.5-cent retention hysteresis |
| Portfolio / market loss | $300 / $40 (`30× / 4×` size) |
| Account threshold | $80 realized sensor and $80 marked-equity sensor |
| Risk day | `America/New_York`, DST-aware |

[DECISIONS.md](DECISIONS.md) is authoritative for policy details.

## Former server incident and current resolution

From 22:07 UTC, Retail mutations returned HTTP 403/code 7 location/VPN
denials. The old gateway mislabeled them as ambiguous no-ID PLACE intents and
retirement consumed all 20 owners. The deployed repair fails only the rejected
intent, limits later entry probes, preserves cancels/reducers and repairs only
legacy rows with exact persisted rejection proof.

The 22:56Z server acceptance repaired 20 rows and kept zero unresolved/retired
through three denied probes. The account stayed flat and no
quote could rest. The laptop's 00:40Z startup then accepted 39 quotes across
20 markets with no matching denial, so execution access works from the
selected residential runtime. The containment remains installed.
[Evidence](docs/history/MUTATION_ACCESS_DENIAL_2026-08-28.md).

## Current operating instruction

The laptop is the sole live ledger/API owner and the desktop is confirmed off
as a manual cold backup. Keep the laptop on AC power and do not start the
desktop, recreate the former server, or use automatic failover/proxy egress.
Any handoff begins with a graceful stop plus authenticated venue proof and a
verified stopped-runtime ledger snapshot.

## Current records

[NEXT](NEXT.md) · [WATCHLIST](WATCHLIST.md) · [FINDINGS](FINDINGS.md) · [BUGFIXES](BUGFIXES.md)
