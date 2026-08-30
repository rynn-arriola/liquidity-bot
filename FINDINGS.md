# Findings index
Measured facts are indexed here; [history through 2026-08-27](docs/history/FINDINGS_THROUGH_2026-08-27.md) is archived. Before theorizing, search both files:
```powershell
rg -n -i "<market, error, metric, or theory>" FINDINGS.md docs/history/FINDINGS_*.md
```
## Current high-value findings
- **The first host-wide governor protected aggregate rate but not safety latency.** Background 429 cooldown, shared entry/background clocks, TIF-only reducer classification, inline retry, event-loop file IO and uninterruptible lock-held waits could serialize the live runner before a cancel or exit; native Windows also skipped coordination. The reviewed class-aware correction preserves one 4/second aggregate gate while separating traffic lanes and pre-network deferral. [Evidence](docs/history/HOST_API_ADMISSION_PRIORITY_2026-08-30.md).
- **Retained executable value is promising enough for shadow, not enforcement.** In 661 closed opening-fill episodes, a 70% minimum flagged 19.8%, caught 20.8% of taker-exit episodes and 34.0% of covered loss while also flagging 15.9% of maker-only episodes. A prospective 60/70/80 shadow now records recent-selector/reward/no-candidate/unknown cost with zero extra venue calls. [Evidence](docs/history/RETAINED_VALUE_SHADOW_2026-08-30.md).
- **The claimed 30–60 minute kickoff sweet spot was a timestamp mismatch.** The original table used the market's first captured observation, not its inventory-opening fill. Actual entry replay did not reproduce the sweet spot and does not justify a proximity or no-trade-hour gate. [Evidence](docs/history/RETAINED_VALUE_SHADOW_2026-08-30.md).
- **Queue-reset measurement is decision-complete and negative.** Across 12,227 orders, 2,061 clean almost-front signals preceded only 197 later fills. Its live public-trade socket is retired for the next runner; historical data and reports remain preserved. [Evidence](docs/history/RETAINED_VALUE_SHADOW_2026-08-30.md).
- **The fixed 20-taker-exit count was still the size-5 commissioning value.** The size-13/22-slot session reached it in 5h28m with 20 distinct owned exits, 26 fill rows, $1.35 fees and approximately `-$11.1404` attributable realized P&L. The operator selected `4 × quote size`, giving 52 at size 13; the independent $3.90 fee ceiling remains unchanged and may stop first. No other cap increased for 22 slots. [Evidence](docs/history/TAKER_EXIT_SCALING_2026-08-30.md).
- **`BUY_SHORT` accounting used the wrong price frame.** A 364-fill replay with the repaired LONG/YES-frame arithmetic exactly matches settled cash at `-$79.4465`. [Evidence](docs/history/BUSINESS_LOGIC_AUDIT_2026-08-30.md).
- **The 02:00Z low-order state was lifecycle scheduler delay, not API pressure.** Eight bound markets reached kickoff together; working orders fell to 16/17 across 11 markets while two positions exited. The relevant windows had zero API failures/429s and selection still found 81 viable candidates. The ordinary 02:12:52Z top-up replaced eight expired owners plus one economic-idle owner, restored 20 markets and 33 orders, and left zero position, unresolved or unknown rows. The immediate-refill repair is deployed in `859b891`; natural kickoff acceptance remains. [Evidence](docs/history/BOUND_LIFECYCLE_REFILL_2026-08-29.md).
- **Parity needs two truths, not one sync bucket.** GitHub owns tracked files; the bounded live-safe ledger backup is verified private recovery evidence only.
- **Twenty-four slots are not a Polymarket API limit.** The 2026-08-29 full business-load audit separates documented limits, measured stream behavior and modeled REST/CPU/exposure ceilings; exact telemetry and bounded canaries remain required. [API capacity evidence](docs/POLYMARKET_API_REFERENCE.md#api-capacity-and-scaling-evidence).
- **Twenty-two passed its bounded live measurement, not permanent selection.** Twenty clean startup/selection windows covered 3,392 attempts with zero failures/429s, `1.263-2.643/s`, wait p99 at most `0.226s`, response p99 at most `0.919s` and queue depth at most one. Stream scope had no drops/decode failures or persistent account ambiguity. Size 13 still keeps the $390 cap and 36% width cushion; 20 remains the default and permanent baseline. [Canary evidence](docs/history/SLOT_CAPACITY_CANARY_2026-08-29.md).
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
- **Risk now has one quantity authority.** Live size 13 maps to portfolio/market exposure/market loss/account limits `$390/$117/$52/$104`; launcher, selector and reconciler share it while 20 slots and six hours stay fixed. The stop intentionally removes proven non-trading cash, while the staged reporting repair separates raw account Today P&L from its exact adjusted HARD input. [Evidence](docs/history/HARD_METER_NONTRADING_CASH_2026-08-29.md).
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
  polled by a lock/timeout-bounded process with independent state and Discord
  notification. It needs no API key, ledger access, REST budget or runner hook.
- **Retail list volume fields were absent on the observed 2026-08-28 wire.**
  The first implementation silently selected no pure markets. Bounded discovery
  now ranks homogeneous full-book `sharesTraded`; its 10-minute acceptance
  covered 100 markets, 24,050 books and 792 trades at 99.3% stream coverage with
  no gaps/reconnects/errors/REST 429s and no effect on the live runner.
- **Rolling scalp episodes are not leave-it orders.** Untouched pairs renew at
  five minutes and can rotate out of scope. Exact GTC evidence therefore needs
  the bounded retained cohort; it excludes coverage loss and uses no future row.
