# Current operator status

Only current operational facts belong here; [historical snapshots](docs/history/README.md) are archived.
## Verified state
- **Verified:** 2026-08-30 00:48 ET / 2026-08-30 04:48 UTC
- **Liquidity bot:** LIVE at 22 slots on the laptop; supervisor PID 74539, runner PID 74548 and isolated dashboard PID 74573 are active
- **Former server:** destroyed; it cannot run the bot or execute old schedules
- **Stop proof:** the runner received `operator_sigterm` and exited gracefully.
  The 02:34:46Z post-stop authenticated snapshot proved zero open orders and
  zero nonzero positions; the ledger had zero unresolved/unknown rows
- **Deployed checkout:** WSL HEAD and `DEPLOYED_COMMIT` are `6ae1335`. Runner PID 74548 was started from `fdc426e` and deliberately not restarted; staged business-logic repairs take effect only after a later authorized restart
- **Business-logic repair staged:** canonical LONG/YES-frame accounting exactly replays the 2026-08-29 settled-cash change at `-$79.4465`; owned taker-exit budgets and entry/exit summaries now use durable order roles. Three reversals passed. The 900-second timeout remains unchanged because the available replay favored 900 seconds and had only 29.2% stream coverage. [Evidence](docs/history/BUSINESS_LOGIC_AUDIT_2026-08-30.md)
- **Lifecycle refill:** eight synchronized 02:00Z kickoffs reduced the working slate to 16/17 orders across 11 markets while two positions exited. There were zero API failures/429s and 81 viable candidates. The ordinary 02:12:52Z selection restored 20 markets and 33 orders with zero positions/unresolved/unknown rows, proving safe recovery but an avoidable scheduler gap
- **API capacity measurement:** the 22-slot canary passed its 30-minute startup/selection-burst gate: 20 windows, 3,392 attempts, zero failures/429s, `1.263-2.643/s`, wait p99 at most `0.226s`, response p99 at most `0.919s` and queue depth at most one. It remains bounded; 20 is still the default and permanent baseline
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
- **Active quantity:** size 13 = $390 portfolio exposure, $117 market exposure, $52 market loss, $104 session/hard, $10.40 early-cross and $3.90 fee caps. At 04:48Z the ledger had 27 orders across 20/22 markets, seven positions, zero unresolved/unknown rows, 27 scoped trades and zero drops/decode failures
- **Natural defect acceptance:** a backward sample at 04:19:49Z emitted `[score-clock] ... action=ignored_stale_sample`; no retirement followed and the runner reached 20/20 quoting. [Evidence](docs/history/SCORE_CLOCK_SLOT_RETIREMENT_2026-08-28.md).
- **Stable rollback:** `v1.0.0` points to `744b7c1`

## Active operating envelope
| Setting | Current decision |
|---|---|
| Production runtime | Laptop is sole live owner; desktop is prepared but inactive manual cold backup; former server destroyed |
| Quote size | 13 contracts live |
| Concurrent capacity | 22-slot bounded canary live and accepted; 20 remains the launcher default and permanent baseline |
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
`Command 1` means the tracked immediate stop-review-deploy-restart workflow; every fix now has a permanent pre-push/pre-deploy review and edge-case-audit rule. These definitions did not control the current runner.

## Current records

[NEXT](NEXT.md) · [WATCHLIST](WATCHLIST.md) · [FINDINGS](FINDINGS.md) · [BUGFIXES](BUGFIXES.md)
