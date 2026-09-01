# Current operator status

Only current operational facts belong here; [historical snapshots](docs/history/README.md) are archived.
## Verified state
- **Discord capital-summary timing repair is loaded in `0245d31`:** routine five-minute posts wait at most 60 seconds only when fresh balance evidence is temporarily blocked by current order ambiguity, using two-second ledger-only rechecks; stale/persistent evidence remains `UNAVAILABLE`, and shutdown/critical summaries never wait. The first natural post used the loaded path at 23:52:12Z; its outbound Discord body is not stored locally, so operator-visible rendering remains the final acceptance evidence. [Evidence](docs/history/CAPITAL_GUIDANCE_TERMINAL_HISTORY_2026-08-31.md)
- **Size-10/24 canary passed its 30-minute live gate:** deployed `9001a4b` uses three `8/8/8` private shards, $340 portfolio exposure, $96 session/hard stops, $10 ET-day gross-fee pause and $1,012 comfortable capital. Across 26 windows and 2,774 attempts through 23:06Z, load was `1.209-2.154/s` with zero failures/429s and queue depth one; exact scope, lifecycle top-up and ordinary cancel resolution stayed healthy. [Evidence](docs/history/SLOT_CAPACITY_CANARY_24_2026-08-31.md)
- **Capital-guidance terminal-history repair is loaded and accepted:** terminal `venue:` history no longer fabricates ambiguity; active unowned and `UNKNOWN_ABSENT` still fail closed. At 22:39:53Z one real cancel intent correctly produced `UNAVAILABLE`; after proof, 22:40:26Z showed zero unresolved/unknown, fresh cash/power `$1,406.26`, comfortable `$1,012.00` and `Free to withdraw $394.25`. [Evidence](docs/history/CAPITAL_GUIDANCE_TERMINAL_HISTORY_2026-08-31.md)
- **Tiny-residual cashout is loaded; natural path acceptance pending:** deployed source includes `6c5ea49`. Exact venue-tradable `0.01`–`0.09` positions get five passive minutes, then sequential cancel-confirm-exact-IOC; `0.10+`, the 900-second clock and API cadence are unchanged. Do not induce a fill. [Evidence](docs/history/TINY_RESIDUAL_CASHOUT_2026-08-31.md)
- **Hard-exit sequencing repair active; natural path acceptance pending:** source/deployment marker `31c2574` cancels one order at a time, building side first, and starts grace only after exact submission. All 952 laptop-checkout tests passed. The flat-gated 17:46Z restart loaded it; do not induce a two-order hard exit. [Evidence](docs/history/HARD_EXIT_SEQUENTIAL_CANCEL_2026-08-31.md)
- **0.15 entry-floor recommendation rejected for now:** raw price/league tables reproduce, but the analysis treated liquidity as role and included expensive exits in “extreme.” Role-correct recent entries were -$0.0730/pair extreme versus -$0.0707 otherwise, not 2× worse. Floor remains 0.10. [Verification](docs/history/TRADING_LOSS_PRICE_MIX_VERIFICATION_2026-08-31.md)
- **Current loss mechanism:** canonical August 29–31 realized trading was `-$234.4553` through the 17:02Z fill boundary: 92% adverse paired price movement and 8% fees. Reducing taker fills realized 97.4% of adverse net loss; sub-0.15 prior entry basis was only 3.1%. Keep 0.10 and 900 seconds; use the retained-value shadow before proposing an entry refusal. [Attribution](docs/history/CURRENT_TRADING_LOSS_ATTRIBUTION_2026-08-31.md)
- **Capital guidance live display accepted:** the zero-extra-API Discord path now uses $240 slot footprint + $100 cushion + $672 seven-day runway = $1,012 comfortable at size 10 / 24; pending is excluded, and free cash is capped by settled cash/buying power. [Evidence](docs/history/CAPITAL_WITHDRAWAL_GUIDANCE_2026-08-31.md)
- **Verified:** 2026-08-31 20:00 ET / 2026-09-01 00:00 UTC
- **Liquidity bot:** LIVE but intentionally new-risk paused on the laptop as exactly one size-10/24-slot supervisor PID 1029753 and runner PID 1029761; exact active scope remains 24, while the current ledger is flat/order-free with zero unresolved/unknown orders after the ET-day gross-fee cap canceled entry quotes
- **Former server:** destroyed; it cannot run the bot or execute old schedules
- **Stop/restart proof:** Command 1 stopped supervisor/runner 1027997/1028005 gracefully in about nine seconds; the final record said `flat: true`, and an independent authenticated 23:44:39Z read proved zero venue orders/positions and zero unresolved/absent/unowned state before exactly one new process pair started
- **Source state:** the loaded runner and laptop `DEPLOYED_COMMIT` identify source commit `0245d31993f6bbba7d15a77e4c3368d11d11e9ae`; the exact staged laptop WSL suite passed 973/973 before deployment and restart. PC/GitHub/laptop tracked HEAD may be a later documentation-only acceptance commit with identical executable source.
- **Account P&L repair active:** Discord, dashboard and the HARD guard now use cash plus every open position's `cashValue`, less proven non-trading cash. The accepted live cache reported marked trading `-$24.2568` = closed `-$24.2568` + open mark `$0`, with no reward/deposit included. [Evidence](docs/history/ACCOUNT_MARKED_PNL_2026-08-30.md)
- **Business-logic repair active:** canonical LONG/YES-frame accounting exactly replays the 2026-08-29 settled-cash change at `-$79.4465`; owned taker-exit budgets and entry/exit summaries use durable order roles. Three reversals passed. The 900-second timeout remains unchanged because the available replay favored 900 seconds and had only 29.2% stream coverage. [Evidence](docs/history/BUSINESS_LOGIC_AUDIT_2026-08-30.md)
- **Daily fee pause / ET rollover:** the count is `4 × quote size` (40 now), while gross owned taker fees pause new risk at `$10 × (size / 10) × (slots / 24)` ($10 now). At 23:55:38Z a natural hard-timeout exit moved the durable ET-day total to `$10.0800`; the bot correctly canceled entry quotes and became flat/order-free. This is the configured pause, not an API/slot fault; restart cannot bypass it and the next ET day resets it automatically. [Evidence](docs/history/DAILY_EXECUTION_FEE_BREADTH_SCALING_2026-08-31.md)
- **Lifecycle refill:** eight synchronized 02:00Z kickoffs reduced the working slate to 16/17 orders across 11 markets while two positions exited. There were zero API failures/429s and 81 viable candidates. The ordinary 02:12:52Z selection restored 20 markets and 33 orders with zero positions/unresolved/unknown rows, proving safe recovery but an avoidable scheduler gap
- **API capacity measurement:** the accepted 24-slot gate covered 26 windows and 2,774 attempts at `1.209-2.154/s`, zero failures/429s, wait p99 at most `0.464s`, response p99 at most `1.294s` and queue depth one; 20 remains the bare-launcher default
- **Post-restart acceptance:** first API window was 2.101/s and later windows 1.270-1.484/s, all with zero failures/429s, queue depth one and no runtime error/retirement; the first selection found 38 viable markets and reported 56 creates with all 24 owners retained
- **Maker-lab:** stopped
- **Laptop runtime:** Ubuntu 24.04 WSL2, Python 3.12, pinned SDK/encryption dependency, size 10 / 24 slots, restricted credentials and exact execution/scalp data are active; the ledger stayed authoritative through the stopped deployment
- **Laptop access/power:** Windows and WSL use the same US residential Comcast egress with no active VPN adapter observed; Retail health returned 200/OK; AC sleep is disabled, AC lid-close does nothing and AC Wi-Fi uses maximum performance
- **Laptop schedules:** three read-only backup/report systemd timers remain active; the obsolete daily selection probe is disabled/inactive after two path failures; a stopped-safe ledger backup passed; no trading service is installed/enabled
- **Measurement change active:** runner PID 1029761 carries the 60/70/80 retained-value refusal shadow with recent-selector/reward/no-candidate/unknown evidence and does not run the retired queue public-trade collector; the shadow remains evidence-only and 70% is only a provisional candidate
- **Dashboard scope repair accepted:** the active runner publishes exact PID 1029761, capacity 24 and all 24 bound markets. [Evidence](docs/history/TV_DASHBOARD_ACTIVE_SCOPE_2026-08-30.md)
- **Host API correction active:** `0ff6e3c6` retains one aggregate 4/second WSL gate with independent background/entry/safety lanes, stops selection/revalidation on the first local cooldown, avoids error/fallback-scan amplification and exposes peer lane cooldowns without another API call. The first live window at 19:33:57Z reported 429s `0`, failures `0`, `shared=1` and all lanes at `0.0`; a later shared background deadline appeared at `2.7s` beside local 429s `0`, left entry/safety at zero and cleared next window. No backpressure, JSON-state, quarantine or runtime error appeared. [Evidence](docs/history/API_BACKPRESSURE_OBSERVABILITY_2026-08-30.md)
- **Desktop cold backup:** inactive; its latest parity execution-ledger recovery copy matches the laptop SHA-256 and passes SQLite integrity `ok`. It is not activated and has no credential path, trading process, service, timer or API check
- **Selected quantity/runtime:** live size 10 / 24 has $340 portfolio cap, $90 market exposure, $40 market loss, $96 session/hard, $8 early-cross, 40 terminal taker exits and $10 ET-day gross-fee pause
- **Natural defect acceptance:** a backward sample at 04:19:49Z emitted `[score-clock] ... action=ignored_stale_sample`; no retirement followed and the runner reached 20/20 quoting. [Evidence](docs/history/SCORE_CLOCK_SLOT_RETIREMENT_2026-08-28.md).
- **Stable rollback:** `v1.0.0` points to `744b7c1`

## Active operating envelope
| Setting | Current decision |
|---|---|
| Production runtime | Laptop is sole live owner; desktop is prepared but inactive manual cold backup; former server destroyed |
| Quote size | 10 loaded live |
| Concurrent capacity | 24 loaded canary; 20 remains the bare-launcher default |
| Entry horizon | Six hours |
| League filter | None |
| New-entry spread | 2 cents; 1.5-cent retention hysteresis |
| Portfolio / market loss | $340 exposure / $40 loss; market exposure is $90 |
| Account threshold | $96 realized sensor and $96 marked-equity sensor |
| Taker-exit count / fee | 40 distinct owned taker-exit orders terminates the session / $10 gross owned taker fees pauses new trading for the ET day |
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
`Command 1` means the tracked immediate stop-review-deploy-restart workflow; every fix has a permanent pre-push/pre-deploy review and edge-case-audit rule. The 22:34Z restart loaded `9001a4b` only after graceful `flat: true` shutdown and two independent authenticated zero-order/zero-position proofs. Startup reached the exact 24-owner scope with zero failures/429s and no duplicate process.

## Current records

[NEXT](NEXT.md) · [WATCHLIST](WATCHLIST.md) · [FINDINGS](FINDINGS.md) · [BUGFIXES](BUGFIXES.md)
