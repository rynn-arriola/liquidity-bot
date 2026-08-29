# Current operator status

Only current operational facts belong here; [historical snapshots](docs/history/README.md) are archived.

## Verified state
- **Verified:** 2026-08-28 22:00 ET / 2026-08-29 02:00 UTC
- **Liquidity bot:** STOPPED normally on the laptop at the operator's request;
  supervisor PID 778 and runner PID 785 are absent
- **Former server:** destroyed; it cannot run the bot or execute old schedules
- **Stop proof:** the final runner record says `operator_sigterm` and
  `flat=true`; the post-stop authenticated dashboard found zero venue orders,
  zero positions, zero absent/unresolved state and no risk stop
- **Loaded source:** `04fddb1`; its trading core remains the reviewed `31f815c`
  repair and it adds only the isolated read-only TV dashboard/startup hook
- **Verification:** laptop passed 785 pre-start tests; desktop passed 787 offline tests; trading source is unchanged
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
- **Desktop cold backup:** inactive WSL copies of the Windows stopped-server
  databases, credentials and archives match byte-for-byte;
  both databases pass integrity and WSL files are owner-only. No active ledger,
  credential path, trading process, service, timer or API check exists there
- **Risk scaling:** size 10 = $300 portfolio, $40 market, $80 session/$80 hard
- **Display/defect:** TV remains read-only. At 01:58Z authenticated state had
  only 10/20 quoting markets and 17 orders after 34 score-clock reversals;
  diagnose this unresolved availability/reward defect before any restart
- **Stable rollback:** `v1.0.0` points to `744b7c1`

## Active operating envelope
| Setting | Current decision |
|---|---|
| Production runtime | Laptop is sole live owner; desktop is prepared but inactive manual cold backup; former server destroyed |
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

The laptop remains the sole stopped owner; the desktop is an inactive manual
backup. Any handoff requires stop, venue proof and a verified ledger snapshot.

Unqualified `run the bot` targets the laptop; explicit `run on the PC` selects
the desktop with full handoff. `Run parity` checks reachable clean workstations
and adds encrypted Drive recovery; packaging/readback remains open.

## Current records

[NEXT](NEXT.md) · [WATCHLIST](WATCHLIST.md) · [FINDINGS](FINDINGS.md) · [BUGFIXES](BUGFIXES.md)
