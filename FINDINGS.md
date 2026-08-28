# Findings index

Measured facts are indexed here; detailed history through 2026-08-27 is in
[docs/history/FINDINGS_THROUGH_2026-08-27.md](docs/history/FINDINGS_THROUGH_2026-08-27.md).

Before theorizing, search both files:

```powershell
rg -n -i "<market, error, metric, or theory>" FINDINGS.md docs/history/FINDINGS_*.md
```

## Current high-value findings

- **Twenty-slot health is not raw order count.** Use queue `markets=20`, active
  owners, eligibility, resting-time coverage, unresolved ownership and
  positions. A slot can be healthy while temporarily holding no order.
- **Closed owners parked capacity.** The deployed repair releases only after
  fresh exact closed/not-open metadata plus complete flat/order-free ownership
  proof. The 2026-08-28 reviewed restart reached 20/20 quoting; natural closed-
  owner replacement remains uninduced and pending observation.
- **Maintenance recovery works when the venue recovers.** It requires three
  consecutive complete authenticated checks, normal reconciliation and fresh
  streams. Current exposure was positively re-established before the 03:06Z
  restart; do not carry the prior forced-stop uncertainty into this run.
- **Open marked loss is provisional.** New entries block immediately, but an
  open-position mark does not become a terminal hard stop until the documented
  close/newer-balance evidence arrives.
- **Resting side-hours are the reward-facing time metric.** Reward-score seconds
  and process-session hours answer different questions.
- **Current-day account-boundary trading is more complete than matched pairs,**
  but per-market P&L remains incomplete when settlements/manual closes are not
  attributable.
- **Queue rank is only bounded.** The venue does not publish exact position;
  automatic cancel/repost remains unauthorized.
- **GitHub `main` is the tracked-file source of truth.** Production source/tests
  are verified against `DEPLOYED_COMMIT`; venue state remains a separate live
  authority.
- **The mandatory AI startup route is now bounded.** On 2026-08-27 the tracked
  entry/status/decision/engineering set fell from 1,030 lines / 8,990 words to
  442 lines / 3,170 words while the former long files were preserved in dated
  archives. `scripts/audit_docs.py` enforces budgets, routes and local links.
- **Polymarket's trader guides are not all Retail wire contracts.** The bot
  uses `api.polymarket.us` and the pinned Retail SDK; Institutional examples
  using participant accounts, JWT/Auth0, FIX, gRPC, Drop Copy or
  `api.prod.polymarketexchange.com` are conceptual unless the matching Retail
  endpoint documents them. The audited source map and implementation gaps are
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
- **Initial official-API remediation acceptance passed on 2026-08-28.** A
  positively flat/order-free restart completed the additive trade-state
  migration and reached exactly 20 quoting owners, 37 resting orders, zero
  positions and zero unresolved mutations. The new-run log had zero traceback,
  mutation timeout, runtime recovery or stream-silence event; destructive
  fault injection remains prohibited, so bust/rejection/silence branches await
  natural evidence.
- **Retail list volume fields were absent on the observed 2026-08-28 wire.**
  `markets.list` and `events.list` omitted documented `volume`/`volumeNum`, so
  the first server-ranked implementation silently selected zero pure markets.
  The correction discovers bounded upcoming events and ranks homogeneous full-
  book `sharesTraded`. A two-minute smoke captured both cohorts with no orders.
  The deployed 10-minute acceptance then covered 100 markets, 24,050 books and
  792 trades, with strict episodes in `PURE_VOLUME` and `REWARD_SLOTS`, 99.3%
  stream coverage and zero gaps/reconnects/errors/REST 429s. The live runner
  was not restarted and stayed active. One private-stream silence during the
  window matched repeated pre-collector recoveries and cleared normally.
## How to add a finding

Add a concise, dated fact here while it is current. Move detailed incident
evidence into a dated archive during periodic compaction. Record what was
measured, the scope, the source, what interpretation was disproved and what
decision remains.
