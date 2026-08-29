# Findings index
Measured facts are indexed here; [history through 2026-08-27](docs/history/FINDINGS_THROUGH_2026-08-27.md) is archived.
Before theorizing, search both files:
```powershell
rg -n -i "<market, error, metric, or theory>" FINDINGS.md docs/history/FINDINGS_*.md
```
## Current high-value findings
- **Application silence was mistaken for private-transport failure.** In about eight hours the live runner detected roughly 123 silences and recorded 120 recoveries covering 133 shards, all `cancels=0`, without associated 403/429, risk stop, unknown absence or quarantine. The SDK's transport answered protocol Ping/Pong while exposing only JSON to the old clock. The stopped-source fix records awaited Pongs, fails closed on probe loss and passed 810 tests plus reversal; it is not loaded. [Evidence](docs/history/PRIVATE_SHARD_FALSE_SILENCE_2026-08-29.md).
- **Score telemetry was allowed to retire trading slots.** At 01:58Z, 34 backward-clock failures left authenticated state at 10/20 quoting. The deployed high-water repair naturally contained another backward sample at 04:19:49Z, then reached 20/20 with no retirement. Search `[score-clock] ... action=ignored_stale_sample`. [Evidence](docs/history/SCORE_CLOCK_SLOT_RETIREMENT_2026-08-28.md).
- **Laptop commissioning is live at size 13.** After 807 WSL tests and a
  flat/order-free preflight, `08b5b70` reached 20/20 markets and 38 quantity-13
  orders with zero positions/absent rows or risk stop.
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
- **Risk now has one quantity authority.** The live size 13 maps to portfolio/market exposure/market loss/account limits `$390/$117/$52/$104`; the launcher, selector and reconciler share it while 20 slots and six hours stay fixed.
- **Closed owners parked capacity.** The repair requires fresh closed/not-open
  metadata plus flat/order-free proof; natural replacement remains uninduced.
- **Bound capacity can remain economically idle.** Lifecycle repairs do not
  cover a pregame owner continuously blocked on current-book economics. The
  local repair adds a 10-minute dwell and one-for-one supply; deploy is pending.
- **Maintenance recovery works when the venue recovers.** It requires three
  consecutive complete authenticated checks, normal reconciliation and fresh
  streams. Current exposure was positively re-established before the 03:06Z
  restart; do not carry the prior forced-stop uncertainty into this run.
- **Open marked loss is provisional.** New entries block immediately, but an
  open-position mark does not become a terminal hard stop until the documented
  close/newer-balance evidence arrives.
- **Resting side-hours are the gain-facing time metric.** The TV table reuses the hourly ET snapshot; its dashboard refresh adds no venue call. [Evidence](docs/history/TV_GAIN_DASHBOARD_2026-08-28.md).
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
  successful entry must reserve cached buying power within the same 20-slot
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
