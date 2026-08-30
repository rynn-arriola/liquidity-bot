# Observed and documented API contracts

These rules combine official documentation, the installed official SDK, and
recorded read-only responses. Unknown or conflicting schema fails closed.

Official references:

- https://docs.polymarket.us/api-reference/introduction
- https://docs.polymarket.us/incentives/liquidity
- https://docs.polymarket.us/api-reference/websocket/overview
- https://docs.polymarket.us/api-reference/websocket/private
- https://docs.polymarket.us/api-reference/websocket/markets

## Hosts and authentication

- Public REST data uses `https://gateway.polymarket.us`.
- Authenticated trading, portfolio, incentive, and earnings access uses
  `https://api.polymarket.us`.
- Authenticated WebSockets use `wss://api.polymarket.us/v1/ws/private` and
  `wss://api.polymarket.us/v1/ws/markets`.
- API keys use Ed25519 signing and are loaded only from environment variables.
- Authenticated request timestamps must remain within the documented 30-second
  window, so clock drift is a startup blocker.

## Incentives

- Use snake-case query parameters: `page_size`, `page_token`, and
  `program_type=liquidityProgram`. Camel-case parameters were observed being
  silently ignored.
- Omitting `symbols` is a documented universe-discovery request, but the active
  universe is too large for the commissioning hot path. Automatic selection
  bulk-loads the active sports catalog, applies metadata gates in memory, then
  requests incentives in batches of at most 40 candidate symbols. These reads
  use the same 4 requests/second limiter as books, reconciliation, and mutations.
- Steady bound-owner refresh uses the same symbol filter in groups of at most
  eight and joins it to one slug-filtered market-list read. Every returned row
  still passes the original exact per-market lifecycle and reward gate; a
  missing/malformed row fails only that owner, and a transport failure affects
  at most that private-shard-sized group.
- Continue pagination with `nextPageToken`; do not assume the requested page
  size was honored.
- Only rows whose `programType` is `liquidityProgram` enter book-liquidity
  scoring. A volume program is not equivalent.
- A program contains `timePeriods`, and several periods can have active status;
  select only the lifecycle phase applying at the verified market clock.
- `gameStartTime` can move after a market was selected. A successful refresh
  may therefore return the same active program ID while its `day_of` period no
  longer applies because the event moved back outside six hours. That exact-row
  observation is positive loss of current reward authorization; an omitted row
  or different ID is not.
- A filtered incentive row can contain an empty `eventStartTime`. Do not use it
  as the sole event clock.
- A live unfiltered page on 2026-08-13 returned 100 program groups plus a next
  token. Its first groups used two simultaneous active
  `tournament_with_cutoff` rows. That lifecycle is not described by the
  official Early/Day-of/Live/Daily definitions and therefore remains
  discovery-only rather than being guessed from `gameStartTime`.
- Each second, an eligible order's documented score is
  `discountFactor ^ ticks * size`. Full-side cumulative depth must reach
  `targetSize`, and all orders inside the resulting cutoff can score.
- Published pools are not guaranteed payout. Only settled `PAID` earnings are
  accepted for reward calibration; the documented minimum payout and settlement
  delay must not be modeled as immediate cash. On 2026-08-10 the authenticated
  endpoint returned 86 `PAID` market rows totaling $32.06, including 37 rows
  below $0.10. That observation disproves a $1-per-market interpretation, so
  commissioning treats $1 as an aggregate reporting/settlement threshold.
- First-fill risk is calibrated only from the 301 maker-lab order lifetimes in
  those paid markets: 114 first fills over 372,259.527 exposure-seconds. The
  global 30-second Wilson upper bound is 1.1024%. Commissioning takes the
  maximum upper bound among applicable global, league, price, queue, side,
  spread, and time-to-event cells; it does not multiply conditional effects.
  Unknown leagues retain the global and other applicable bounds.
- Monday reward yield is a ranking prior only. League yields are shrunk toward
  the $0.0304/contract-hour global value with ten prior markets and cannot
  authorize an otherwise negative current-book opportunity.

## Markets and books

- The bulk market catalog exposes slug, `gameStartTime`,
  `orderPriceMinTickSize`, `minimumTradeQty`, active/closed state, status,
  `sportsMarketTypeV2`, `bestBidQuote`, and `bestAskQuote`.
  Automatic selection joins these normalized rows to incentives in memory;
  per-reward-slug metadata retrieval is forbidden.
- `marketSides=2` is not a moneyline discriminator: the measured rewarded
  catalog also reports two sides for futures and props. Commissioning requires
  the exact `SPORTS_MARKET_TYPE_MONEYLINE` V2 enum and fails closed when it is
  missing or different.
- Catalog BBO is advisory and may only remove a known-tight candidate from
  book fan-out. A fresh `markets.book` snapshot rechecks the 2-cent entry
  threshold and is the only spread observation allowed to authorize risk.
- `markets.retrieve_by_slug(slug)` returns `{ "market": {...} }`.
- The verified start clock is `market.gameStartTime`.
- The market metadata endpoint has emitted `MARKET_STATUS_OPEN`, while the
  full-book endpoint emits `MARKET_STATE_OPEN`. Live validation accepts only
  those two observed open values (plus an absent optional metadata status) and
  fails closed on every other value.
- Live validation requires both `market.orderPriceMinTickSize` and
  `market.minimumTradeQty`; prices must also remain inside 0.01–0.99.
- `markets.book(slug)` returns `{ "marketData": {...} }`. Bids and offers are
  nested under `marketData`.
- Market WebSocket books are full depth and sorted best-to-worst. The initial
  controlled trial does not debounce a one-market feed because current depth is
  required for the bounded exit decision.
- The documented subscription ceiling is 100 markets per subscription; the
  trial configuration is capped at three and begins with one.

## Private WebSocket

- Subscribe to order, position, and account-balance state. Order snapshots can
  be paged; only an explicit `eof=true` completes reconciliation.
- `positionSubscription` updates contain before/after state. The exact
  `netPositionDecimal` value is authoritative; legacy integer `netPosition` is
  rounded/backward-compatible and is rejected when exact state is unavailable.
- Balance updates can arrive as `accountBalancesUpdate`.
- The overview's snake-case/numeric subscription examples conflict with the
  specific Private/Markets pages and the installed SDK's camel-case/string-enum
  wire shapes. Outbound messages use the official SDK. Inbound decoders accept
  only the two explicitly documented schema families and reject unknown shapes.
- There is no documented durable replay cursor. Every disconnect disables new
  risk; reconnect requires whole-account REST reconciliation plus complete,
  fresh private snapshots before risk can resume.
- A replacement private listener must be active before recovery sends cancels.
  Terminal order events produced before the listener exists are not replayable
  by contract, and GET-by-ID can temporarily answer NotFound. Recovery and
  shutdown drain already-buffered private events before relying on REST.
- A shard-filtered order or position snapshot is complete only for the exact
  subscribed market scope; it may never mark another shard's order absent or
  position zero. An empty scoped order subscription may emit no snapshot/EOF.
  On heartbeat silence, replacements overlap only stale shards and each must
  deliver its account-balance event before swap; received order pages remain
  scope-checked and buffered. The frozen runner then refreshes REST balance plus
  whole-account order/position state before resuming without cancels. A
  confirmed HTTP 429 during those reads receives a slower bounded handoff retry
  while private events are drained and every replacement remains live. Other
  failures, stale replacements, operator stop and exhausted 429 proof fall back
  to the global listener-first quarantine path.
- Each private connection has its own last-message clock. Heartbeats and
  account events refresh only the shard that delivered them; 180 seconds of
  silence on any shard fails the whole private pool into bounded recovery.
- The market connection has a separate last-message clock and the same
  180-second deadline. A fresh REST reconcile cannot refresh either WebSocket
  clock. If both stream families are stale they are rebuilt together; if one
  is stale its existing scoped recovery path applies.
- The authenticated account stream can also be change-driven after its initial
  balance snapshot. While the socket remains connected, a complete whole-
  account REST order/position reconciliation every ten seconds can refresh
  execution-state proof. The actual private-message timestamp remains separate;
  if both sources age past the freshness window, new risk is disabled.

## Orders

- Bot orders set `manualOrderIndicator` to automatic.
- Passive reward quotes are GTC with `participateDontInitiate=true` (post-only).
- Forced reductions are exact-position, price-capped IOC orders and are never
  treated as passive reward quotes.
- The project exposes create, retrieve, and single-market-order cancel only.
  Modify, cancel-all, and account-wide close are deliberately absent.
- A durable intent is committed before each mutation. A mutation exception is
  ambiguous and is never automatically retried; venue snapshots must reconcile
  it first.
- A create response ID acknowledges submission but private/REST order state is
  required to reconcile it. A cancel HTTP response is not terminal proof because
  a fill can win the race.
- Terminal retrieve responses can omit `updateTime`; the observed terminal
  clock is `lastTransactTime`. Order normalization prefers `updateTime`, then
  `lastTransactTime`, and only then creation/insertion clocks.
- A new-risk post-only quote requires cached `buyingPower` at least equal to
  `price * quantity`. A successful submission reserves that notional locally
  for later markets in the same decision sweep until the normal authenticated
  balance refresh replaces the cache.
- The latest structured rejection delays only a flat market's new entry:
  post-only races wait one second, balance/risk failures wait 15 seconds and an
  unknown validation rejection waits 60 seconds. A nonzero-position reducing
  quote, cancel or IOC exit bypasses both buying-power and rejection delays.

## Execution and trade lifecycle

- Execution ID identifies one fill leg. Trade ID identifies the separate venue
  trade lifecycle; either can arrive before the other and both are durable.
- Retail order and execution prices used by this strategy are in the
  LONG/YES frame for both live intents. `BUY_LONG` at `p` costs `p` per
  contract; `BUY_SHORT` at `p` acquires the opposite outcome and costs
  `1 - p`. The wire `side` identifies the outcome being taken and is not a
  cash-flow sign. A matched pair therefore realizes
  `quantity * (short_price - long_price) - signed_fees`.
- Authenticated Retail evidence on 2026-08-29 confirmed a ten-contract
  `BUY_SHORT` at stored price `0.29` with venue trade cost `7.07` after a
  three-cent maker rebate: `10 * (1 - 0.29) - 0.03`. A replay of 364 active
  production fills through 2026-08-30T03:39:08Z produced `-79.4465`, exactly
  matching the settled-cash change from the ET opening baseline. Reports and
  safety attribution must use the same conversion; a symmetric `0.50` test
  cannot distinguish the frames.
- Position `cost` is an absolute economic basis. For a negative position the
  strategy-frame entry price used to plan its reducing order is
  `1 - abs(cost / position)`; for a positive position it is
  `abs(cost / position)`.
- Retained portfolio activity can report `TRADE_STATE_BUSTED`. Each linked
  unreversed fill is marked reversed exactly once and is thereafter excluded
  from inventory basis, known-fill totals, realized P&L, fee/fill telemetry and
  reports. A repeated BUSTED observation may repair a fill linked later but may
  not reverse it twice.
- Trade and fill market identity must agree. A conflict fails closed. A BUSTED
  trade may not silently transition back to an active state.
- A complete current venue position whose authoritative boundary is at or
  after the bust already includes the reversal and outranks arithmetic. The
  ledger marks the fill reversed without changing that newer position again.
- Portfolio execution history is retained venue data, not durable negative
  evidence. It may be pruned or reset; an empty page never proves no fill,
  cancellation or flatness. The local ledger preserves only history already
  observed by this project.
- Rolling deployment may expose read-only reports to the pre-migration fill
  schema while the old runner is still active. Reports inspect SQLite metadata
  without writing: before `reversed` exists all legacy fills remain visible;
  after the additive migration they exclude rows where `reversed=1`.

## Execution commission

- Retail executions expose `commissionNotionalCollected` as a USD
  `MoneyAmount`. Persist its decimal `value` unchanged: positive means a taker
  charge and negative means a maker rebate. A missing field is zero.
- The venue-reported signed amount is authoritative for ledger accounting and
  realized P&L. The published formula is used only for projections and safety
  ceilings; do not replace an observed fill commission with a recomputation.
- Partner/institutional execution examples expose scaled integer notional and
  require price and fractional-quantity scales. That decoding does not apply
  to the Retail `MoneyAmount` used by this bot.
- The exchange-wide schedule effective 2026-07-01 uses
  `theta * contracts * price * (1 - price)`, with theta `0.06` for takers and
  `-0.0125` for makers, rounded to cents with banker's rounding. Canceled,
  expired and rejected orders have no fee.

## Earnings

- `GET /v1/incentives/earnings` returns `{ "rewards": [...] }`.
- Observed statuses include `PAID`, `PENDING`, and `SKIPPED`.
- Live verification on 2026-08-13 found that `marketSlug`/`programType` query
  keys were silently ignored while `market_slug`/`program_type` returned the
  exact filtered result. The adapter uses the observed snake_case keys and the
  commissioning boundary filters the returned rows again by exact market and
  program identity.
- Only attributable `PAID` rows can satisfy a profitability gate.

## Account balance

- `GET /v1/account/balances` must contain exactly one USD row.
- `currentBalance`, `assetNotional`, and `buyingPower` are all required finite
  decimals. A missing field fails closed.
- Commissioning marked equity is `currentBalance + assetNotional`, matching the
  API's cash plus marked securities values. `buyingPower` is an affordability
  gate only for a flat market's new post-only entry; it is never substituted
  for equity or P&L and never gates risk reduction.

## Request budget

- Retail documents 20 authenticated requests/second per API key and 20 public
  requests/second per IP. Institutional endpoint/firm limits are a different
  interface and do not replace either Retail ceiling.
- This project defaults to 4 REST requests/second through one shared async gate,
  including trading and reconciliation calls.
- The gate is process-local. Server dashboards and other authenticated tools do
  not share the live runner's limiter; operators must avoid repeated probes
  during recovery and interpret them as additional account/IP request load.
- Every attempt passes through that gate exactly once; callers do not pace
  manually.
- The real gate records a bounded, secret-free local window: fixed operation
  categories, read/mutation attempt counts, response and gate-wait percentiles,
  live/max waiters, failures and confirmed 429 responses. Reading/resetting the
  window makes no venue request and categories never contain a slug, order ID,
  payload or credential.
- Only a confirmed HTTP 429 receives bounded read retry/backoff. State-changing
  requests never auto-retry. A parseable numeric or HTTP-date `Retry-After` is
  honored up to 60 seconds; otherwise the existing bounded exponential delay
  applies.
- Every owned official-SDK client uses a 15-second REST request timeout.
  Adapter errors preserve safe operation, HTTP status, structured error code,
  trace/request ID and parsed retry delay when available; secrets and arbitrary
  response bodies are not copied into logs.
- Market books and trades are streamed after startup rather than polled.
- Because the market WebSocket is change-driven, a quiet one-market book is
  refreshed by REST before its five-second decision freshness limit. The
  three-second fallback uses the same 4 requests/second gate and is skipped
  when a newer WebSocket book already arrived.
- That fallback is not a scalable replacement for the market stream: a silent
  N-market slate demands approximately `N/3` book reads per second. Capacity
  planning must preserve stream recovery and stale-book fail-closed behavior
  and cannot certify a wide slate by assuming whole-slate REST polling works.
- REST books are timestamped at response receipt, not request start, and their
  refresh runs after slower account/incentive work. The decision clock is read
  again after all awaited calls; an unresolved-intent reconciliation always
  begins a fresh loop before any quote decision.
- A skipped REST refresh caused by a recent WebSocket book schedules its next
  check from that book's receipt time, not from the check time, so the
  five-second decision limit cannot arrive before the next refresh opportunity.
- The one account/key cannot be shared concurrently with maker-lab during a
  controlled session.

## Read-only acceptance sample

The pure Phase 0 core was fed a current Setka market using the market endpoint's
start time and tick, the incentive endpoint's day-of program, and both sides of
the full book. At the snapshot, a two-contract order at either touch qualified
with score `2.0`; the day-of program had pool `35`, target `5000`, and discount
factor `0.5`. This proves data availability and rule composition, not future
payout or profitability.

Phase 1's bounded acceptance also received one full market-book snapshot and
one authenticated account-balance snapshot without invoking an order method.
See `PHASE1_ACCEPTANCE.md`.
