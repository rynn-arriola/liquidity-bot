# Findings index
Search current and [archived findings](docs/history/FINDINGS_THROUGH_2026-08-27.md) before theorizing: `rg -n -i "<term>" FINDINGS.md docs/history/FINDINGS_*.md`.
## Current high-value findings
- **Global Polymarket health does not make every market executable.** After 31 bounded shutdown retries, the runner stopped truthfully `flat=false`; independent authenticated proof found zero orders and one exact 10-contract position in `aec-itfme-pathom-julbie-2026-09-01`. At 12:22-12:23Z public health/status and an authenticated account read were operational, yet the exact market still had a valid empty book and no settlement record. Treating that owner as an account-wide veto leaves every unrelated slot unavailable indefinitely. The bounded remedy is exact-owner settlement wait: no quote/mutation on that slug, one slot/full-risk reservation, private/account monitoring and 60-second read-only book polling, while independently proven markets may trade. Terminal record plus venue snapshot remain stop authority.
- **Generic book refresh was incorrectly coupled to two-sided active-entry validation.** `_refresh_book` dereferenced both sides and called entry validation for inactive recovery owners. Reviewed `5f1ef3369` moves entry-only checks to startup, validates only present sides and preserves empty-book/missing-metadata/order-payload refusal. The current ITF owner still has 10 contracts in a closed market with no depth, so the code repair cannot force settlement and its repeated positioned-owner reads remain safety work.
- **The 11:22-11:29Z fast passes are degraded recovery, not a healthy CPU baseline.** Maintenance began at 06:56Z; the health gate passed only at 11:10Z, after which many Retail markets remained inactive or empty. Fast 3-4 second passes and lower CPU during that interval reflect early failures and missing normal decision work. Also, `[decision-scheduler] cpu_cores` is process CPU divided by the full recent wall-clock window, while `TIME/ELAPSED` is lifetime average; it is not an active-work-only ratio.
- **One proven CPU cost is ledger history-squared; the REST attribution needs narrower wording.** At 3,697 fills/1,048 markets, `realized_by_market()` took 311.562 ms because it rescanned all fills once per historical market; one chronological replay measured about 19 ms and preserves exact market-local matching. Separately, the 04:43:11Z window really had 51 book reads out of 87 attempts, but `max_waiting=1` is not in-flight concurrency and p95 wait/response values cannot be summed as averages. Prepared `773677b2f` fixes only the independently proven ledger path; profiling/REST redesign remains open. [Evidence](docs/history/DIRTY_MARKET_SCHEDULER_2026-08-31.md).
- **The loaded dirty scheduler is safe but initially skips nothing.** Six windows after the 04:21Z restart reported 0 skipped across 168 pass owners; steady windows used 0.679-0.726 CPU core and took 70-80 seconds. Each owner's 3-second quiet-book deadline is therefore due before the next pass and independently makes the whole slate schedulable. API health remained clean and exact venue state was 44 orders/24 markets/zero positions, so this is an optimization finding rather than a trading-state failure. Do not widen beyond 24 or claim the CPU goal until the timer/decision boundary is reviewed and comparable-window evidence passes. [Evidence](docs/history/DIRTY_MARKET_SCHEDULER_2026-08-31.md).
- **The gross-fee gate duplicated stronger loss protection and destroyed reward uptime.** At 23:55:38Z one natural exit moved the durable ET-day total from `$9.5300` to `$10.0800`; with zero API errors or ownership fault, the gate canceled the entire 24-slot slate while the account remained below its $96 loss sensors. The operator removed fee enforcement: keep the scaled amount for telemetry, but rely on the $96 account sensors, $40 market stop and 40-exit ceiling for authority. [Evidence](docs/history/EXECUTION_FEE_GATE_REMOVAL_2026-08-31.md).
- **Breadth now scales capital and account stops without changing per-market policy.** Loaded source uses `(slots + 10) × size` portfolio exposure and `8 × size × slots / 20` session/hard limits. Live size 10 / 24 has $340 portfolio, $96 account stops and $1,012 comfortable capital; per-market loss/exposure, count exits, REST and strategy clocks retain quantity or policy authority, while the fee telemetry reference separately scales with size and breadth. [Evidence](docs/history/SLOT_CAPACITY_CANARY_24_2026-08-31.md).
- **Capital availability had two reporting defects, not a reserve-calculation defect.** Loaded source no longer calls 59 terminal `venue:` rows unknown, and a clean live sample proved `Free to withdraw $394.25`. Five-minute posts can also land during a normal cancel's brief unresolved interval; loaded `0245d31` defers only that fresh-balance/current-order sample for at most 60 seconds without an API call. Persistent or stale evidence still correctly shows `UNAVAILABLE`. [Evidence](docs/history/CAPITAL_GUIDANCE_TERMINAL_HISTORY_2026-08-31.md).
- **Tiny fractional positions are tradable; their delay was policy, not a venue minimum.** The ledger contains 569 accepted sub-one-contract PLACE orders and 384 sub-one fills, both down to `0.01`, with no quantity-minimum rejection. An observed `0.03` residue later reduced exactly and flattened. In 32 paired maker-small episodes, 23 remained beyond 15 minutes, so the ordinary clock could let a few cents occupy a whole slot. The loaded five-minute exact `0.01`–`0.09` repair awaits natural acceptance. [Evidence](docs/history/TINY_RESIDUAL_CASHOUT_2026-08-31.md).
- **Current loss is adverse reduction, not the 0.10–0.15 entry band.** Canonical August 29–31 replay was `-$234.4553`: 92% adverse paired movement and 8% fees; reducing taker fills realized 97.4% of adverse net loss while sub-0.15 prior entry basis was 3.1%. The earlier 0.15 report also confused liquidity with role and included costly exits in “extreme.” Keep 0.10, no league filter and the evidence-backed 900-second clock while the retained-value shadow measures a causal entry lever. [Attribution](docs/history/CURRENT_TRADING_LOSS_ATTRIBUTION_2026-08-31.md) · [price verification](docs/history/TRADING_LOSS_PRICE_MIX_VERIFICATION_2026-08-31.md).
- **A multi-action hard-exit plan violated the one-mutation driver contract.** With two working orders, only the first cancel ran while one shared timer treated both as outstanding; two 2026-08-31 episodes cancelled the reducer and left the builder able to grow inventory. Hard exit must sequence exact order IDs, building side first. [Evidence](docs/history/HARD_EXIT_SEQUENTIAL_CANCEL_2026-08-31.md).
- **Safe withdrawal guidance needs reserve and availability.** Loaded size 10 / 24 keeps $340 breadth-derived exposure plus $672 for seven breadth-scaled hard-stop days, then caps free cash by buying power; the comfortable target is $1,012. Pending is excluded and ambiguity publishes no amount. [Evidence](docs/history/CAPITAL_WITHDRAWAL_GUIDANCE_2026-08-31.md) · [breadth scaling](docs/history/SLOT_CAPACITY_CANARY_24_2026-08-31.md).
- **Designed host backpressure was being displayed as broken markets and hidden from peer telemetry.** One locally deferred book read could be repeated across the selection candidate set and inflate Discord `Lost to errors`; cached revalidation could immediately fall back to a larger scan. Separately, a peer process's shared cooldown governed admission while the runner's lane fields still showed zero. The next-runner correction aborts the pass once, preserves prior evidence, retries after the bounded delay and merges bounded shared cooldowns without another API request. [Evidence](docs/history/API_BACKPRESSURE_OBSERVABILITY_2026-08-30.md).
- **The first host-wide governor protected aggregate rate but not safety latency.** Background 429 cooldown, shared entry/background clocks, TIF-only reducer classification, inline retry, event-loop file IO and uninterruptible lock-held waits could serialize the live runner before a cancel or exit; native Windows also skipped coordination. The reviewed class-aware correction preserves one 4/second aggregate gate while separating traffic lanes and pre-network deferral. [Evidence](docs/history/HOST_API_ADMISSION_PRIORITY_2026-08-30.md).
- **Retained executable value is promising enough for shadow, not enforcement.** In 661 closed opening-fill episodes, a 70% minimum flagged 19.8%, caught 20.8% of taker-exit episodes and 34.0% of covered loss while also flagging 15.9% of maker-only episodes. A prospective 60/70/80 shadow now records recent-selector/reward/no-candidate/unknown cost with zero extra venue calls. [Evidence](docs/history/RETAINED_VALUE_SHADOW_2026-08-30.md).
- **The claimed 30–60 minute kickoff sweet spot was a timestamp mismatch.** The original table used the market's first captured observation, not its inventory-opening fill. Actual entry replay did not reproduce the sweet spot and does not justify a proximity or no-trade-hour gate. [Evidence](docs/history/RETAINED_VALUE_SHADOW_2026-08-30.md).
- **Queue-reset measurement is decision-complete and negative.** Across 12,227 orders, 2,061 clean almost-front signals preceded only 197 later fills. Its live public-trade socket is retired for the next runner; historical data and reports remain preserved. [Evidence](docs/history/RETAINED_VALUE_SHADOW_2026-08-30.md).
- **The fixed 20-taker-exit count was still the size-5 commissioning value.** The size-13/22-slot session reached it in 5h28m with 20 distinct owned exits, 26 fill rows, $1.35 fees and approximately `-$11.1404` attributable realized P&L. The operator selected `4 × quote size`, giving 40 at loaded size 10; fee telemetry remains independent and non-enforcing. [Evidence](docs/history/TAKER_EXIT_SCALING_2026-08-30.md).
- **`BUY_SHORT` accounting used the wrong price frame.** A 364-fill replay with the repaired LONG/YES-frame arithmetic exactly matches settled cash at `-$79.4465`. [Evidence](docs/history/BUSINESS_LOGIC_AUDIT_2026-08-30.md).
- **The 02:00Z low-order state was lifecycle scheduler delay, not API pressure.** Eight bound markets reached kickoff together; working orders fell to 16/17 across 11 markets while two positions exited. The relevant windows had zero API failures/429s and selection still found 81 viable candidates. The ordinary 02:12:52Z top-up replaced eight expired owners plus one economic-idle owner, restored 20 markets and 33 orders, and left zero position, unresolved or unknown rows. The immediate-refill repair is deployed in `859b891`; natural kickoff acceptance remains. [Evidence](docs/history/BOUND_LIFECYCLE_REFILL_2026-08-29.md).
- **Twenty-four slots are not a Polymarket API limit.** The corrected model includes the full balance/positions/balance risk mark: 24 projects at `2.16/s` normally and `3.78/s` busy, uses three `8/8/8` private shards and projects 0.677 CPU core. Busy headroom and deterministic quiet-book deadline misses keep it canary-only; exact live telemetry is still required. [API capacity evidence](docs/POLYMARKET_API_REFERENCE.md#api-capacity-and-scaling-evidence).
- **Twenty-two passed its bounded live measurement, not permanent selection.** Twenty clean startup/selection windows covered 3,392 attempts with zero failures/429s, `1.263-2.643/s`, wait p99 at most `0.226s`, response p99 at most `0.919s` and queue depth at most one. Stream scope had no drops/decode failures or persistent account ambiguity. That process loaded the historical fixed $390 cap; prepared source replaces it with the breadth-derived reserve. Twenty remains the bare-launcher default. [Canary evidence](docs/history/SLOT_CAPACITY_CANARY_2026-08-29.md).
- **Application silence was mistaken for private-transport failure.** In about eight hours the old runner detected roughly 123 silences and recorded 120 recoveries covering 133 shards, all `cancels=0`, without associated 403/429, risk stop, unknown absence or quarantine. The SDK transport answered Ping/Pong while exposing only JSON to the old clock. Deployed `a1ba212` records awaited Pongs and fails closed on probe loss; 810 WSL tests and reversal passed, while natural timeout acceptance is pending. [Evidence](docs/history/PRIVATE_SHARD_FALSE_SILENCE_2026-08-29.md).
- **Score telemetry was allowed to retire trading slots.** At 01:58Z, 34 backward-clock failures left authenticated state at 10/20 quoting. The deployed high-water repair naturally contained another backward sample at 04:19:49Z, then reached 20/20 with no retirement. Search `[score-clock] ... action=ignored_stale_sample`. [Evidence](docs/history/SCORE_CLOCK_SLOT_RETIREMENT_2026-08-28.md).
- **Desktop cold standby is prepared and inactive.** Its 787 offline tests passed; handoff still requires a current laptop snapshot. [Evidence](docs/history/DESKTOP_COLD_BACKUP_SETUP_2026-08-28.md).
- **A no-ID PLACE was misclassified as recovery risk.** An 18:35Z 504 left one `UNCERTAIN` create; its finished market caused three fast empty-book deaths, not a slot defect. Stopped repair blocks same-market orders because Retail never receives the local client ID.
- **Private-shard silence erased the reward slate.** At 13:50Z two shards crossed 180 seconds; recovery canceled all before the same PID rebuilt to 34, while filtered snapshots also acted account-complete.
- **Empty subscriptions emit no order EOF:** `order_eof=0/3 account_balance=3/3`;
  balance proves each socket and complete REST proves empty orders.
- **A 429 during shard proof can erase the slate.** At 15:02:46Z shard 3 overlap
  reconciled 34 orders, then Cloudflare `1015`/429 on balance drove quarantine;
  the same PID rebuilt. Dashboards do not share the runner limiter; avoid repeated
  recovery probes. Search handoff-rate-limit/quarantine.
- **Private-only and whole-stream silence have different order cost.** Eight natural
  private-shard repairs (two on the laptop) preserved 32-39 orders with `cancels=0`; simultaneous
  251-second market/private silence at 16:22Z globally quarantined and rebuilt to 30. Neither was HTTP 429 or a 20-slot cap.
- **The 2026-08-28 one-order state was a 403 cascade, not low supply.** Retail
  returned HTTP 403/code 7 location denials, but the old mutation path wrote
  rejected creates as no-ID `UNCERTAIN`. All 20 owners retired while selection
  still had 27 viable markets. Private silence recovered with `cancels=0`; the
  post-flat trigger never ran. The no-ID loop reached 67% CPU and starved SIGTERM.
  The repaired restart closed all 20 legacy rows, preserved 20 owners and zero unresolved through three denied probes, then stopped flat in 30 seconds; access remains denied.
  [Evidence](docs/history/MUTATION_ACCESS_DENIAL_2026-08-28.md).
- **A signed rejection and a lost response require opposite intent states.**
  Response-backed 403 means that exact request failed; timeout, connection loss,
  5xx, malformed success and absence remain ambiguous. Only later mutation
  success proves account mutation access recovered.
- **Risk has quantity and breadth authorities.** Per-market exposure/loss remain `9 × / 4 × size`; portfolio and account-wide stops additionally follow configured breadth. At size 10 / 24 these become `$90/$40`, $340 portfolio and $96 account stops. The six-hour horizon does not move. [Evidence](docs/history/HARD_METER_NONTRADING_CASH_2026-08-29.md) · [breadth scaling](docs/history/SLOT_CAPACITY_CANARY_24_2026-08-31.md).
- **Maintenance recovery works when the venue recovers.** It requires three
  consecutive complete authenticated checks, normal reconciliation and fresh
  streams. Current exposure was positively re-established before the 03:06Z
  restart; do not carry the prior forced-stop uncertainty into this run.
- **Open marked loss is provisional.** New entries block immediately, but an
  open-position mark does not become a terminal hard stop until the documented
  close/newer-balance evidence arrives.
- **The TV dashboard is a viewport-bound local instrument panel.** Its gain table reuses the hourly ET snapshot with zero venue calls, and the deployed responsive layout fits every panel without page/panel overflow at five measured landscape sizes from 800×600 through 1920×1080. [Gain evidence](docs/history/TV_GAIN_DASHBOARD_2026-08-28.md) · [Layout evidence](docs/history/TV_DASHBOARD_VIEWPORT_FIT_2026-08-29.md).
- **Later cash activity erased proven historical account truth.** The historical
  scan now ignores only post-window completions while still reaching EOF; live
  snapshots remain strict. Deployed acceptance resolved August 26 to
  `-$64.4870` trading and `+$145.12` finalized net.
- **GitHub `main` is the tracked-file source of truth.** Production source/tests
  are verified against `DEPLOYED_COMMIT`; venue state remains a separate live
  authority.
- **Trader guides are not all Retail wire contracts.** Institutional examples
  are conceptual unless the Retail API/SDK documents them; use the audited map
  in [docs/POLYMARKET_API_REFERENCE.md](docs/POLYMARKET_API_REFERENCE.md).
- **The 2026-07-01 fee schedule matches the bot's signed-fee accounting.**
  Retail execution commission is already a USD `MoneyAmount`: positive taker
  fees are costs and negative maker rebates are credits. Partner fixed-point
  decoding is a different interface and would overstate some Retail fees if
  applied here. No fee-handling defect was found in this documentation review.
- **Changelog `v0.0.79` explains the 2026-08-27 venue interruption and a new
  history boundary.** Polymarket announced extended 02:00-08:00 ET maintenance
  for Retail and Institutional APIs and deletion of stored execution history
  during that window. A post-maintenance empty pre-window history result is not
  positive proof of no fill, cancellation or flatness; retain normal durable-
  ledger and current authenticated-state requirements.
- **Trade state is independent of fill delivery.** Retail activity can retain a
  trade as `TRADE_STATE_BUSTED` after its execution leg was booked. Trade ID,
  lifecycle state and an exactly-once reversal are therefore separate durable
  facts; a current authoritative position snapshot outranks fill arithmetic.
- **REST freshness cannot certify WebSocket liveness.** The 20-market private
  feed spans three connections, so each shard and the market socket needs its
  own last-message clock. A complete REST snapshot can bridge a brief gap but
  cannot hide 180 seconds of stream silence.
- **Buying power is an entry constraint, not an equity value or exit gate.** A
  successful entry must reserve cached buying power within the same configured-capacity
  sweep. Insufficient-power and validation rejections should pause only that
  market's new entries; reducers, cancels and IOC exits remain available.
- **Official HTTP guidance is compatible with mutation safety.** Confirmed 429
  reads can honor bounded `Retry-After`; order mutations still never retry
  automatically. Status, structured error code and trace/request ID are safe
  diagnostics, and owned SDK clients use the recommended 15-second timeout.
- **Changelog monitoring belongs outside trading.** The public RSS feed can be
  polled by a lock/timeout-bounded process with independent state and Discord notification. It needs no API key, ledger access, REST budget or runner hook.
- **Retail list volume fields were absent on the observed 2026-08-28 wire.** The first implementation silently selected no pure markets. Bounded discovery now ranks homogeneous full-book `sharesTraded`; its 10-minute acceptance covered 100 markets, 24,050 books and 792 trades at 99.3% stream coverage with no gaps/reconnects/errors/REST 429s and no effect on the live runner.
- **Rolling scalp episodes are not leave-it orders.** Untouched pairs renew at five minutes and can rotate out of scope. Exact GTC evidence needs the bounded retained cohort; it excludes coverage loss and uses no future row.
