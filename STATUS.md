# Current operator status

Only current operational facts belong here; [historical snapshots](docs/history/README.md) are archived.
## Verified state
- **Verified:** 2026-08-30 23:10 ET / 2026-08-31 03:10 UTC
- **Liquidity bot:** STOPPED on the laptop; no supervisor or runner process exists. The prior runner reached its old `$3.90` session fee ceiling at 22:28 ET (02:28 UTC) and exited. Dashboard PID 614753 remains active and read-only
- **Former server:** destroyed; it cannot run the bot or execute old schedules
- **Stop/restart proof:** the first updated-code restart attempt stopped gracefully at 19:25:08Z but correctly remained stopped because two authenticated snapshots proved four positions/four reducers. `Command 1` reused the completed review/deploy evidence, and a fresh 19:30Z authenticated snapshot proved zero positions, zero resting orders, zero unresolved/`UNKNOWN_ABSENT` rows and unblocked rotation. Exactly one new supervisor/runner pair was started; no force-kill, flatten or extra start occurred
- **Deployed source:** WSL checkout is `38136f52`; `DEPLOYED_COMMIT` remains `fda2bb37`. The reviewed `$6.00` ET-day fee-pause change is not deployed yet; no process is waiting to load it
- **Account P&L repair active:** Discord, dashboard and the HARD guard now use cash plus every open position's `cashValue`, less proven non-trading cash. The accepted live cache reported marked trading `-$24.2568` = closed `-$24.2568` + open mark `$0`, with no reward/deposit included. [Evidence](docs/history/ACCOUNT_MARKED_PNL_2026-08-30.md)
- **Business-logic repair active:** canonical LONG/YES-frame accounting exactly replays the 2026-08-29 settled-cash change at `-$79.4465`; owned taker-exit budgets and entry/exit summaries use durable order roles. Three reversals passed. The 900-second timeout remains unchanged because the available replay favored 900 seconds and had only 29.2% stream coverage. [Evidence](docs/history/BUSINESS_LOGIC_AUDIT_2026-08-30.md)
- **Taker-exit count scaling active:** the count is `4 × quote size`, so size 13 stops on the 52nd distinct owned taker-exit order rather than the 20th. The old independent `$3.90` terminal fee ceiling fired first at `$4.00` gross fees; its reviewed replacement is a `$6.00` ET-day new-trading pause. [Evidence](docs/history/TAKER_EXIT_SCALING_2026-08-30.md) · [fee-pause change](docs/history/DAILY_EXECUTION_FEE_PAUSE_2026-08-30.md)
- **Lifecycle refill:** eight synchronized 02:00Z kickoffs reduced the working slate to 16/17 orders across 11 markets while two positions exited. There were zero API failures/429s and 81 viable candidates. The ordinary 02:12:52Z selection restored 20 markets and 33 orders with zero positions/unresolved/unknown rows, proving safe recovery but an avoidable scheduler gap
- **API capacity measurement:** with the accurate balance/positions/balance mark active, the accepted 22-slot startup burst was `2.806/s` and the next two normal windows were `1.440-1.482/s`; all had zero failures/429s, wait p99 at most `0.223s` and queue depth at most one. The operator selected 22; 20 remains the bare-launcher default
- **Maker-lab:** stopped
- **Laptop runtime:** Ubuntu 24.04 WSL2, Python 3.12, pinned SDK/encryption dependency, size 13, restricted credentials and exact execution/scalp data are active; the ledger stayed authoritative through the stopped deployment
- **Laptop access/power:** Windows and WSL use the same US residential Comcast egress with no active VPN adapter observed; Retail health returned 200/OK; AC sleep is disabled, AC lid-close does nothing and AC Wi-Fi uses maximum performance
- **Laptop schedules:** three read-only backup/report systemd timers remain active; the obsolete daily selection probe is disabled/inactive after two path failures; a stopped-safe ledger backup passed; no trading service is installed/enabled
- **Measurement change:** the next normal runner records 60/70/80 retained-value refusal shadows with recent-selector/reward/no-candidate/unknown evidence and does not run the retired queue public-trade collector; the shadow remains evidence-only
- **Dashboard scope repair active:** the retired collector's 6,144-second-old `STOPPED` scope previously produced 17 false `OBSERVED` cells. The dashboard-only reload now uses current ledger evidence: 21 quoting, zero observed, one empty, 38 working orders and zero position/unresolved/unknown rows. The exact PID-bound runner scope publisher remains safely dormant until the next normal bot restart. [Evidence](docs/history/TV_DASHBOARD_ACTIVE_SCOPE_2026-08-30.md)
- **Host API correction active:** `0ff6e3c6` retains one aggregate 4/second WSL gate with independent background/entry/safety lanes, stops selection/revalidation on the first local cooldown, avoids error/fallback-scan amplification and exposes peer lane cooldowns without another API call. The first live window at 19:33:57Z reported 429s `0`, failures `0`, `shared=1` and all lanes at `0.0`; a later shared background deadline appeared at `2.7s` beside local 429s `0`, left entry/safety at zero and cleared next window. No backpressure, JSON-state, quarantine or runtime error appeared. [Evidence](docs/history/API_BACKPRESSURE_OBSERVABILITY_2026-08-30.md)
- **Desktop cold backup:** inactive; its latest parity execution-ledger recovery copy matches the laptop SHA-256 and passes SQLite integrity `ok`. It is not activated and has no credential path, trading process, service, timer or API check
- **Configured quantity:** size 13 = 52 terminal taker exits, $390 portfolio exposure, $117 market exposure, $52 market loss, $104 session/hard and $10.40 early-cross. The reviewed fee change is `$6.00` gross owned taker fees per ET day and pauses only new trading; deployment remains pending
- **Natural defect acceptance:** a backward sample at 04:19:49Z emitted `[score-clock] ... action=ignored_stale_sample`; no retirement followed and the runner reached 20/20 quoting. [Evidence](docs/history/SCORE_CLOCK_SLOT_RETIREMENT_2026-08-28.md).
- **Stable rollback:** `v1.0.0` points to `744b7c1`

## Active operating envelope
| Setting | Current decision |
|---|---|
| Production runtime | Laptop is sole live owner; desktop is prepared but inactive manual cold backup; former server destroyed |
| Quote size | 13 contracts persisted; bot currently stopped |
| Concurrent capacity | 22-slot bounded canary persisted and accepted; 20 remains the launcher default and permanent baseline |
| Entry horizon | Six hours |
| League filter | None |
| New-entry spread | 2 cents; 1.5-cent retention hysteresis |
| Portfolio / market loss | $390 exposure / $52 loss (`30× / 4×` size); market exposure is $117 (`9×`) |
| Account threshold | $104 realized sensor and $104 marked-equity sensor |
| Taker-exit count / fee | 52 distinct owned taker-exit orders terminates the session / $6.00 gross owned taker fees pauses new trading for the ET day |
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
`Command 1` means the tracked immediate stop-review-deploy-restart workflow; every fix now has a permanent pre-push/pre-deploy review and edge-case-audit rule. The 19:30Z invocation loaded `0ff6e3c6` only after fresh positive flat/order-free proof and completed live acceptance without a duplicate process.

## Current records

[NEXT](NEXT.md) · [WATCHLIST](WATCHLIST.md) · [FINDINGS](FINDINGS.md) · [BUGFIXES](BUGFIXES.md)
