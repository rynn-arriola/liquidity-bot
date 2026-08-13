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
- Continue pagination with `nextPageToken`; do not assume the requested page
  size was honored.
- Only rows whose `programType` is `liquidityProgram` enter book-liquidity
  scoring. A volume program is not equivalent.
- A program contains `timePeriods`, and several periods can have active status;
  select only the lifecycle phase applying at the verified market clock.
- A filtered incentive row can contain an empty `eventStartTime`. Do not use it
  as the sole event clock.
- Each second, an eligible order's documented score is
  `discountFactor ^ ticks * size`. Full-side cumulative depth must reach
  `targetSize`, and all orders inside the resulting cutoff can score.
- Published pools are not guaranteed payout. Only settled `PAID` earnings are
  accepted for reward calibration; the documented minimum payout and settlement
  delay must not be modeled as immediate cash.

## Markets and books

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
- Heartbeat silence beyond the configured freshness window disables new risk.
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

## Earnings

- `GET /v1/incentives/earnings` returns `{ "rewards": [...] }`.
- Observed statuses include `PAID`, `PENDING`, and `SKIPPED`.
- Only attributable `PAID` rows can satisfy a profitability gate.

## Account balance

- `GET /v1/account/balances` must contain exactly one USD row.
- `currentBalance`, `assetNotional`, and `buyingPower` are all required finite
  decimals. A missing field fails closed.
- Commissioning marked equity is `currentBalance + assetNotional`, matching the
  API's cash plus marked securities values. `buyingPower` is retained for
  diagnostics but is never substituted for equity or P&L.

## Request budget

- The documented account-level ceiling is 20 requests/second.
- This project defaults to 4 REST requests/second through one shared async gate,
  including trading and reconciliation calls.
- Every attempt passes through that gate exactly once; callers do not pace
  manually.
- Only a confirmed HTTP 429 receives bounded read retry/backoff. State-changing
  requests never auto-retry.
- Market books and trades are streamed after startup rather than polled.
- Because the market WebSocket is change-driven, a quiet one-market book is
  refreshed by REST before its five-second decision freshness limit. The
  three-second fallback uses the same 4 requests/second gate and is skipped
  when a newer WebSocket book already arrived.
- REST books are timestamped at response receipt, not request start, and their
  refresh runs after slower account/incentive work. The decision clock is read
  again after all awaited calls; an unresolved-intent reconciliation always
  begins a fresh loop before any quote decision.
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
