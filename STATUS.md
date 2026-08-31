# Current operator status

Only current operational facts belong here; [historical snapshots](docs/history/README.md) are archived.
## Verified state
- **Hard-exit sequencing repair staged for the next authorized restart:** source commit `31c2574` cancels one order at a time, building side first, and starts grace only after exact submission. All 952 laptop-checkout tests passed; live PIDs 837439/837452 and loaded marker `3120a631` are unchanged, with no stop/restart. [Evidence](docs/history/HARD_EXIT_SEQUENTIAL_CANCEL_2026-08-31.md)
- **0.15 entry-floor recommendation rejected for now:** raw price/league tables reproduce, but the analysis treated liquidity as role and included expensive exits in “extreme.” Role-correct recent entries were -$0.0730/pair extreme versus -$0.0707 otherwise, not 2× worse. Floor remains 0.10. [Verification](docs/history/TRADING_LOSS_PRICE_MIX_VERIFICATION_2026-08-31.md)
- **Capital guidance staged at `5851370` for next restart:** the zero-API Discord card keeps $286 slot footprint + $104 cushion + $728 seven-day runway = $1,118 comfortable at size 13 / 22; pending is excluded, free cash is capped by settled cash/buying power, uncertainty is unavailable and the live runner remains unchanged. [Evidence](docs/history/CAPITAL_WITHDRAWAL_GUIDANCE_2026-08-31.md)
- **Verified:** 2026-08-31 13:00 ET / 2026-08-31 17:00 UTC
- **Liquidity bot:** LIVE on the laptop as exactly one size-13/22-slot supervisor PID 837439 and runner PID 837452, both unchanged throughout the capital-guidance build/deploy; no stop or restart occurred
- **Former server:** destroyed; it cannot run the bot or execute old schedules
- **Stop/restart proof:** the first updated-code restart attempt stopped gracefully at 19:25:08Z but correctly remained stopped because two authenticated snapshots proved four positions/four reducers. `Command 1` reused the completed review/deploy evidence, and a fresh 19:30Z authenticated snapshot proved zero positions, zero resting orders, zero unresolved/`UNKNOWN_ABSENT` rows and unblocked rotation. Exactly one new supervisor/runner pair was started; no force-kill, flatten or extra start occurred
- **Source state:** GitHub and the reviewed laptop WSL checkout include hard-exit source commit `31c2574` plus the documented price-mix verification; the unchanged live process still reports loaded marker `3120a631`. The staged checkout passed all 952 tests. This intentional loaded/check-out difference remains until the next authorized restart
- **Account P&L repair active:** Discord, dashboard and the HARD guard now use cash plus every open position's `cashValue`, less proven non-trading cash. The accepted live cache reported marked trading `-$24.2568` = closed `-$24.2568` + open mark `$0`, with no reward/deposit included. [Evidence](docs/history/ACCOUNT_MARKED_PNL_2026-08-30.md)
- **Business-logic repair active:** canonical LONG/YES-frame accounting exactly replays the 2026-08-29 settled-cash change at `-$79.4465`; owned taker-exit budgets and entry/exit summaries use durable order roles. Three reversals passed. The 900-second timeout remains unchanged because the available replay favored 900 seconds and had only 29.2% stream coverage. [Evidence](docs/history/BUSINESS_LOGIC_AUDIT_2026-08-30.md)
- **Daily fee pause / ET rollover:** the count remains `4 × quote size` (52 at size 13), and the scalable `$9.75` ET-day pause correctly reconstructed the prior day's `$9.9600` and blocked entry without killing the process. `3120a631` repaired the baseline/cash ordering cycle: live acceptance created the 2026-08-31 baseline at 04:31:45Z, then reconciled 40 PLACE intents and reported 41 live orders, zero positions and an API window with 589 reads, 41 creates, zero failures and zero 429s. [Evidence](docs/history/TAKER_EXIT_SCALING_2026-08-30.md) · [fee-pause change](docs/history/DAILY_EXECUTION_FEE_PAUSE_2026-08-30.md)
- **Lifecycle refill:** eight synchronized 02:00Z kickoffs reduced the working slate to 16/17 orders across 11 markets while two positions exited. There were zero API failures/429s and 81 viable candidates. The ordinary 02:12:52Z selection restored 20 markets and 33 orders with zero positions/unresolved/unknown rows, proving safe recovery but an avoidable scheduler gap
- **API capacity measurement:** with the accurate balance/positions/balance mark active, the accepted 22-slot startup burst was `2.806/s` and the next two normal windows were `1.440-1.482/s`; all had zero failures/429s, wait p99 at most `0.223s` and queue depth at most one. The operator selected 22; 20 remains the bare-launcher default
- **Maker-lab:** stopped
- **Laptop runtime:** Ubuntu 24.04 WSL2, Python 3.12, pinned SDK/encryption dependency, size 13, restricted credentials and exact execution/scalp data are active; the ledger stayed authoritative through the stopped deployment
- **Laptop access/power:** Windows and WSL use the same US residential Comcast egress with no active VPN adapter observed; Retail health returned 200/OK; AC sleep is disabled, AC lid-close does nothing and AC Wi-Fi uses maximum performance
- **Laptop schedules:** three read-only backup/report systemd timers remain active; the obsolete daily selection probe is disabled/inactive after two path failures; a stopped-safe ledger backup passed; no trading service is installed/enabled
- **Measurement change active:** runner PID 632263 carries the 60/70/80 retained-value refusal shadow with recent-selector/reward/no-candidate/unknown evidence and does not run the retired queue public-trade collector; the shadow remains evidence-only
- **Dashboard scope repair accepted:** the active runner published `RUNNER_ACTIVE_MARKETS` at 04:31:47Z with exact PID 645718, capacity 22 and all 22 bound markets, replacing the fallback scope without a dashboard or bot restart. [Evidence](docs/history/TV_DASHBOARD_ACTIVE_SCOPE_2026-08-30.md)
- **Host API correction active:** `0ff6e3c6` retains one aggregate 4/second WSL gate with independent background/entry/safety lanes, stops selection/revalidation on the first local cooldown, avoids error/fallback-scan amplification and exposes peer lane cooldowns without another API call. The first live window at 19:33:57Z reported 429s `0`, failures `0`, `shared=1` and all lanes at `0.0`; a later shared background deadline appeared at `2.7s` beside local 429s `0`, left entry/safety at zero and cleared next window. No backpressure, JSON-state, quarantine or runtime error appeared. [Evidence](docs/history/API_BACKPRESSURE_OBSERVABILITY_2026-08-30.md)
- **Desktop cold backup:** inactive; its latest parity execution-ledger recovery copy matches the laptop SHA-256 and passes SQLite integrity `ok`. It is not activated and has no credential path, trading process, service, timer or API check
- **Selected quantity:** size 13 = 52 terminal taker exits, $390 portfolio exposure, $117 market exposure, $52 market loss, $104 session/hard and $10.40 early-cross. The selected gross owned taker-fee ET-day pause is `$9.75` (`0.75 × size`); the bot is live with this reviewed configuration
- **Natural defect acceptance:** a backward sample at 04:19:49Z emitted `[score-clock] ... action=ignored_stale_sample`; no retirement followed and the runner reached 20/20 quoting. [Evidence](docs/history/SCORE_CLOCK_SLOT_RETIREMENT_2026-08-28.md).
- **Stable rollback:** `v1.0.0` points to `744b7c1`

## Active operating envelope
| Setting | Current decision |
|---|---|
| Production runtime | Laptop is sole live owner; desktop is prepared but inactive manual cold backup; former server destroyed |
| Quote size | 13 contracts selected; bot live |
| Concurrent capacity | 22-slot bounded canary live and accepted; 20 remains the launcher default and permanent baseline |
| Entry horizon | Six hours |
| League filter | None |
| New-entry spread | 2 cents; 1.5-cent retention hysteresis |
| Portfolio / market loss | $390 exposure / $52 loss (`30× / 4×` size); market exposure is $117 (`9×`) |
| Account threshold | $104 realized sensor and $104 marked-equity sensor |
| Taker-exit count / fee | 52 distinct owned taker-exit orders terminates the session / $9.75 gross owned taker fees pauses new trading for the ET day |
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
`Command 1` means the tracked immediate stop-review-deploy-restart workflow; every fix now has a permanent pre-push/pre-deploy review and edge-case-audit rule. The 04:27Z restart loaded `3120a631` only after fresh positive flat/order-free proof. A transient startup WebSocket 503 recovered through the existing fail-closed retry; the durable new-day baseline, live placements and clean API window then completed acceptance without a duplicate process.

## Current records

[NEXT](NEXT.md) · [WATCHLIST](WATCHLIST.md) · [FINDINGS](FINDINGS.md) · [BUGFIXES](BUGFIXES.md)
