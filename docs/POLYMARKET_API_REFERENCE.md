# Polymarket rules and information — canonical reference

This is the project's go-to, agent-neutral reference for Polymarket rules,
official documentation, API behavior and venue-specific operating guidance.
Start here for every Polymarket question before searching scattered project
notes or applying advice from an external guide. It maps the official
documentation to this project and records which guidance applies to the Retail
API used by this bot.

The documentation site also contains an Institutional API with different
hosts, authentication, field names, transports and limits. Similar concepts do
not make those interfaces interchangeable.

This file owns stable Polymarket knowledge and the source map; it does not own
live bot state, operator decisions, unfinished implementation or detailed wire
schemas. Read
[STATUS.md](../STATUS.md) for live state, [DECISIONS.md](../DECISIONS.md) for
operator policy, [NEXT.md](../NEXT.md) for unfinished work and
[API_CONTRACTS.md](API_CONTRACTS.md) for the detailed wire contracts already
implemented.

## Interface actually used by this project

- Product: Polymarket US Retail API.
- REST hosts: `https://api.polymarket.us` for authenticated account/trading
  resources and `https://gateway.polymarket.us` for public resources.
- WebSockets: `wss://api.polymarket.us/v1/ws/private` and
  `wss://api.polymarket.us/v1/ws/markets`.
- Authentication: API key ID plus Ed25519 request signatures through the
  official Python SDK.
- Installed SDK contract: `polymarket-us==0.1.2`.
- Deployed transport: REST and WebSocket. The bot does not use Institutional
  JWT/Auth0, participant/account headers, FIX, gRPC, Drop Copy or Report Search.

The Retail endpoint-specific API reference is binding for Retail wire shapes.
The trader guides are useful for lifecycle and reliability principles, but
many examples use Institutional hosts such as
`api.prod.polymarketexchange.com`, `accountId`/`instrument`, bearer tokens or
gRPC. Never copy those paths, limits, headers or request bodies into the Retail
bot without first proving that the Retail API documents the same capability.

When official pages disagree with the installed SDK or observed Retail wire
behavior, fail closed, capture the exact discrepancy and update
[API_CONTRACTS.md](API_CONTRACTS.md). Observed behavior can explain a defensive
decoder or workaround; it does not authorize undocumented trading behavior.

## Source hierarchy for an AI review

1. The exact Retail endpoint page under `/api-reference/`.
2. The matching Retail Python SDK page and the pinned SDK implementation.
3. The applicable trader guide for conceptual and operational guidance.
4. Institutional documentation only when the project is explicitly adopting
   an Institutional interface.
5. Current authenticated observations as evidence of actual venue behavior.

Official documentation can change. Re-open the relevant official page during
each new API decision rather than relying only on this dated summary.

## When Polymarket suddenly behaves differently

Check the [official changelog](https://docs.polymarket.us/changelog) before
assuming the project introduced a regression. Filter for **Retail API** and
review the effective date, version and tags—especially **Breaking Change**,
**Maintenance**, **Upcoming**, **Incentives** and **Bug Fix**. Then check the
[live exchange status](https://status.polymarketexchange.com/) because the two
sources answer different questions: the changelog records announced changes
and maintenance; the status page reports current service health.

Compare the first observed symptom with the announced ET effective window,
the deployed commit and SDK version, and the exact Retail endpoint involved.
An announcement is evidence, not permission to weaken reconciliation or change
trading behavior. Record the matched version and date in `FINDINGS.md`; put an
active outage in `STATUS.md`, unfinished adaptation in `NEXT.md`, and a proven
code repair in `BUGFIXES.md`.

The changelog provides an RSS feed at
`https://docs.polymarket.us/changelog/rss.xml`. This project does not currently
poll or subscribe to it automatically.

## Audited official pages

Last reviewed: **2026-08-27**.

| Topic | Official sources | Project coverage | Known difference or open review |
|---|---|---|---|
| Changelog and venue health | [Changelog](https://docs.polymarket.us/changelog), [live status](https://status.polymarketexchange.com/) | Mandatory diagnostic sources for unexplained venue behavior. AI operators compare Retail-tagged announcements and effective windows with the symptom before attributing it to local code. | This is a manual review, not an automated monitor. Changelog `v0.0.79` announced 2026-08-27 extended maintenance and a stored execution-history reset; an empty pre-maintenance history query therefore cannot prove that no execution occurred. |
| Rate limits | [Trader guide](https://docs.polymarket.us/trader-guide/rate-limits), [Retail reference](https://docs.polymarket.us/api-reference/rate-limits) | One shared process-wide async gate defaults to 4 REST requests/second; confirmed 429 read retries are bounded; mutations never auto-retry. | The trader guide's institutional firm/endpoint limits do not replace the Retail 20 requests/second per-key ceiling. The limiter is process-local, so concurrent external reports on the same key still aggregate at the venue. `Retry-After` is not consumed. |
| Fees and rebates | [Fee schedule](https://docs.polymarket.us/fees), [Retail order schema](https://docs.polymarket.us/api-reference/orders/get-order) | Each Retail fill's signed `commissionNotionalCollected.value` is stored directly in USD. Positive taker fees reduce realized P&L; negative maker rebates increase it. The session fee stop counts only positive fees, while entry forecasts intentionally assume zero maker rebate. | Effective exchange-wide from 00:00 ET on 2026-07-01: `fee = theta × contracts × price × (1 - price)`, with taker theta `0.06` and maker theta `-0.0125`. Venue fees/rebates use cent-level banker's rounding. Partner execution reports use scaled integer notional, but the Retail `MoneyAmount.value` must not be divided by partner price/quantity scales. |
| Error handling | [Trader guide](https://docs.polymarket.us/trader-guide/error-handling) | 4xx failures are not blindly retried; 429 reads use bounded backoff; 5xx/transport failures enter scoped recovery or maintenance pause; ambiguous mutations remain durable and quarantined until positive reconciliation. | Some SDK exceptions lose structured status, leaving narrow text classification. REST trace IDs are not persisted. Generic advice to retry POST must never override mutation ambiguity safety. |
| Reporting | [Trader guide](https://docs.polymarket.us/trader-guide/reporting), [Retail portfolio](https://docs.polymarket.us/api-reference/portfolio/overview) | Private events plus Retail `/v1/portfolio/activities` recover recent executions; exactly one account execution leg is selected with `isAggressor`; execution IDs deduplicate; rewards and non-trading cash are separated from trading P&L. | The ledger does not separately persist `tradeId` or trade state and cannot reverse `TRADE_STATE_BUSTED`. Institutional Report Search, Drop Copy and ledger CSV exports are not integrated or assumed available. |
| Order management | [Trader guide](https://docs.polymarket.us/trader-guide/order-management), [Retail orders](https://docs.polymarket.us/api-reference/orders/overview) | Reward quotes are asynchronous post-only GTC limits; forced reductions are bounded IOC limits; local validation covers identity, open state, price bounds, tick and minimum quantity; HTTP 200/order ID is acknowledgement only; stream/REST state proves acceptance, fills and cancellation; cancel-confirm-replace prevents overlapping ownership. | `buyingPower` is fetched but not used as an entry gate. Rejection reasons are stored but do not yet drive reason-specific backoff or alerting. Preview is deliberately not called for every quote because it would consume the shared hot-path budget. Batch mutation is not used and its echoed IDs would not prove per-order success. |
| Connection issues | [Trader guide](https://docs.polymarket.us/trader-guide/connection-issues), [Retail WebSocket reference](https://docs.polymarket.us/api-reference/websocket/overview) | Close/error signals trigger bounded exponential reconnect; new risk stops during recovery; reconnect requires REST reconciliation and fresh private snapshots; sustained venue failure enters a maintenance pause and requires three successful authenticated account reads before normal recovery. The SDK reuses an `httpx.AsyncClient`. | REST reconciliation can currently keep the state gate fresh while an application heartbeat is silent. The three-connection private pool does not expose per-shard heartbeat age, and the market adapter does not expose heartbeat age. The SDK default REST timeout is 30 seconds while the guide recommends 15. |

## Safety interpretations that must not be lost

### REST retry is not mutation retry

The guide's generic retry examples include POST, but this bot commits an intent
before every order mutation. A timeout or connection reset after submission is
an unknown outcome: repeating it can create a duplicate order or cancel against
the wrong state. Reads may retry when their failure is positively classified;
creates, cancels and exits require venue reconciliation before another mutation.

### HTTP success is not order success

A returned exchange order ID acknowledges receipt. It does not prove that the
order is resting. A place intent reconciles only from a venue order state; a
cancel or modify reconciles only from a terminal state. Absence from a snapshot
is never treated as cancellation.

### Venue-reported commission is the accounting authority

The schedule computes taker fees and maker rebates symmetrically around a
price of 0.50. Fees exist only on executed fills; a canceled, expired or
rejected order has no fee. Taker charges are deducted and maker rebates are
credited when the fill occurs. For partial aggressive fills, the venue rounds
each fill using banker's rounding and limits the total charge to the rounded
cumulative exact fee; maker rebates are independently rounded per fill.

Use the signed commission reported on the Retail execution for the ledger and
P&L rather than recomputing it. The formula is appropriate for forecasts and
risk ceilings. Retail `commissionNotionalCollected` is a USD `MoneyAmount`,
whereas the partner/institutional execution schema can expose a scaled integer
that requires both price and fractional-quantity scales. Mixing those encodings
can misstate fees by 100 times.

### Streaming and REST prove different things

The private WebSocket is the real-time execution channel. Complete REST orders,
positions, activities and balance reads are the restart/reconnect backstop.
REST freshness may prove current account state, but it should not silently
certify that every WebSocket connection is alive. The 20-slot private stream is
sharded across three connections, so liveness must ultimately be per shard.

### Public health is diagnostic, not an account recovery proof

The public `/v1/health` endpoint can distinguish broad service/network failure
from an account-specific problem. It cannot prove authentication, ownership,
open orders, exact positions or balance. The existing three complete
authenticated account reads remain the stronger maintenance recovery gate.

### Empty venue history is not proof of no execution

Changelog `v0.0.79`, published 2026-08-26, announced that stored execution
history would be cleared during the extended 2026-08-27 maintenance window.
Queries for earlier executions can therefore return empty after maintenance.
The bot's durable local ledger remains the history it already observed, while
current complete orders, positions and balance reads remain the restart and
reconciliation evidence. Never reinterpret a post-reset empty history response
as cancellation, flatness or proof that an ambiguous mutation did not fill.

### Busted trades are reversals

Retail activity history exposes trade states. A `TRADE_STATE_BUSTED` trade was
executed and later reversed by the exchange; it remains in history and must be
recorded as a reversal for positions, P&L and reconciliation. Silently dropping
it or retaining the original fill unchanged is incorrect.

## Unfinished work and authorization boundary

[NEXT.md](../NEXT.md) is authoritative for unresolved implementation work. At
this review the documentation identified three grouped follow-ups:

1. Persist trade ID/state and implement auditable busted-trade reversal.
2. Add buying-power and rejection-reason-aware protection without turning
   expected post-only collisions into a global stop.
3. Separate per-connection WebSocket heartbeat liveness from REST state
   freshness and review a 15-second REST timeout.

These are engineering findings, not authorized strategy or deployment changes.
Do not implement, deploy or restart solely because they appear here. The human
operator retains the decision on behavior and deployment timing, and all normal
tests, mutation checks, review and live safety gates still apply.

## Required update procedure

When a future task reviews another official Polymarket page:

1. Check the changelog for a matching Retail announcement, then identify
   whether the affected page is Retail, Institutional or shared guidance.
2. Read the exact affected code path end to end, including failure, recovery,
   shutdown and 20-slot behavior.
3. Record what is implemented, what is intentionally different and what is a
   proven defect versus an unmeasured gap.
4. Add unfinished work to [NEXT.md](../NEXT.md), not to `STATUS.md`.
5. If code is fixed, update [BUGFIXES.md](../BUGFIXES.md), the affected contract
   documentation and tests in the same task.
6. Update the reviewed date and source table here without copying secrets,
   server addresses, credentials or private account data.
