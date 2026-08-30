# Current operator status

Only current operational facts belong here; [historical snapshots](docs/history/README.md) are archived.
## Verified state
- **Verified:** 2026-08-29 22:34 ET / 2026-08-30 02:34 UTC
- **Liquidity bot:** STOPPED on the laptop by explicit operator request;
  supervisor and runner are absent; isolated dashboard PID 1681 remains active
- **Former server:** destroyed; it cannot run the bot or execute old schedules
- **Stop proof:** the runner received `operator_sigterm` and exited gracefully.
  The 02:34:46Z post-stop authenticated snapshot proved zero open orders and
  zero nonzero positions; the ledger had zero unresolved/unknown rows
- **Loaded source:** marker `2aa9fec`; source commit `599fb5f` is staged in WSL and activates only on the next restart. The lifecycle-refill source repair is tracked for a later reviewed deployment and is not loaded by the live runner
- **Verification:** all 830 loaded-source tests, all 842 staged-WSL tests and all 844 current Windows-source tests pass. No deployment or restart followed the stop
- **Lifecycle refill:** eight synchronized 02:00Z kickoffs reduced the working slate to 16/17 orders across 11 markets while two positions exited. There were zero API failures/429s and 81 viable candidates. The ordinary 02:12:52Z selection restored 20 markets and 33 orders with zero positions/unresolved/unknown rows, proving safe recovery but an avoidable scheduler gap
- **API capacity measurement:** startup made 373 attempts/146.9s clean; the next
  64s received 10 execution-history `1015`/429s at 1.813/s, then 66.2s recovered
  with 105 clean reads. The bot stayed live/flat; no slot increase is authorized
- **Maker-lab:** stopped
- **Laptop runtime:** Ubuntu 24.04 WSL2, Python 3.12, pinned SDK/encryption
  dependency, size 13, restricted credentials and exact execution/scalp data
  are active; the ledger stayed authoritative through the stopped deployment
- **Laptop access/power:** Windows and WSL use the same US residential Comcast
  egress with no active VPN adapter observed; Retail health returned 200/OK;
  AC sleep is disabled, AC lid-close does nothing and AC Wi-Fi uses maximum
  performance
- **Laptop schedules:** four read-only backup/report systemd timers are active;
  a stopped-safe ledger backup passed; no trading service is installed/enabled
- **Desktop cold backup:** inactive; its latest parity execution-ledger recovery
  copy matches the laptop SHA-256 and passes SQLite
  integrity `ok`. It is not activated; no credential path, trading process,
  service, timer or API check exists there
- **Active quantity:** size 13 = $390 portfolio exposure, $117 market exposure, $52 market loss, $104 session/hard, $10.40 early-cross and $3.90 fee caps; slots, horizon and REST rate stay fixed
- **Natural defect acceptance:** a backward sample at 04:19:49Z emitted `[score-clock] ... action=ignored_stale_sample`; no retirement followed and the runner reached 20/20 quoting. [Evidence](docs/history/SCORE_CLOCK_SLOT_RETIREMENT_2026-08-28.md).
- **Stable rollback:** `v1.0.0` points to `744b7c1`

## Active operating envelope
| Setting | Current decision |
|---|---|
| Production runtime | Laptop is sole live owner; desktop is prepared but inactive manual cold backup; former server destroyed |
| Quote size | 13 contracts live |
| Concurrent capacity | 20 market slots |
| Entry horizon | Six hours |
| League filter | None |
| New-entry spread | 2 cents; 1.5-cent retention hysteresis |
| Portfolio / market loss | $390 exposure / $52 loss (`30× / 4×` size); market exposure is $117 (`9×`) |
| Account threshold | $104 realized sensor and $104 marked-equity sensor |
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

The laptop remains the sole live owner; the desktop is an inactive manual
backup. Any handoff requires stop, venue proof and a verified ledger snapshot.

Unqualified `run the bot` targets the laptop; explicit `run on the PC` selects
the desktop with full handoff. Parity covers PC/laptop/GitHub, laptop WSL and a
verified private execution-ledger recovery copy; no secret/database enters git
and no copy is activated. `dba96ba` passed end-to-end; Drive remains weekly-only.

## Current records

[NEXT](NEXT.md) · [WATCHLIST](WATCHLIST.md) · [FINDINGS](FINDINGS.md) · [BUGFIXES](BUGFIXES.md)
