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
`https://docs.polymarket.us/changelog/rss.xml`. The project now has an isolated
public-RSS monitor and bounded shell wrapper. It is not part of the trading
runner. Production polls it hourly at minute 23 through an isolated schedule.

## Audited official pages

Last reviewed: **2026-08-29**.

| Topic | Official sources | Project coverage | Known difference or open review |
|---|---|---|---|
| Changelog and venue health | [Changelog](https://docs.polymarket.us/changelog), [live status](https://status.polymarketexchange.com/) | Mandatory diagnostic sources for unexplained venue behavior. The isolated hourly RSS monitor establishes a baseline without history spam, alerts only unseen items and retains unseen state after notification failure. | Changelog `v0.0.79` announced 2026-08-27 extended maintenance and a stored execution-history reset; an empty pre-maintenance history query therefore cannot prove that no execution occurred. The monitor remains diagnostic and cannot change trading behavior. |
| Rate limits | [Trader guide](https://docs.polymarket.us/trader-guide/rate-limits), [Retail reference](https://docs.polymarket.us/api-reference/rate-limits) | One shared process-wide async gate defaults to 4 REST requests/second; confirmed 429 reads use bounded numeric or HTTP-date `Retry-After` when present and exponential fallback otherwise; mutations never auto-retry. Local request telemetry is categorized and redacted; it adds no diagnostic request. | Retail documents a global authenticated ceiling of 20 requests/second per API key and a public ceiling of 20 requests/second per IP. Institutional firm/endpoint limits do not replace them. The project's 4/second governor is a conservative internal ceiling, not venue capacity. It is process-local, so another process on the same key or public IP still contributes venue load. |
| Fees and rebates | [Fee schedule](https://docs.polymarket.us/fees), [Retail order schema](https://docs.polymarket.us/api-reference/orders/get-order) | Each Retail fill's signed `commissionNotionalCollected.value` is stored directly in USD. Positive taker fees reduce realized P&L; negative maker rebates increase it. The session fee stop counts only positive fees, while entry forecasts intentionally assume zero maker rebate. | Effective exchange-wide from 00:00 ET on 2026-07-01: `fee = theta × contracts × price × (1 - price)`, with taker theta `0.06` and maker theta `-0.0125`. Venue fees/rebates use cent-level banker's rounding. Partner execution reports use scaled integer notional, but the Retail `MoneyAmount.value` must not be divided by partner price/quantity scales. |
| Error handling | [Trader guide](https://docs.polymarket.us/trader-guide/error-handling) | 4xx failures are not blindly retried; 429 reads use bounded backoff; 5xx/transport failures enter scoped recovery or maintenance pause; ambiguous mutations remain durable and quarantined until positive reconciliation. A response-backed 403 fails only its exact intent and opens a one-minute new-entry circuit. Cancels/reducers bypass entry denial; their own rejection gets a 10-second safety circuit. Successful mutations prove recovery only for their scope. Safe status, structured error code and trace/request ID survive adapter boundaries. | Generic advice to retry POST must never override mutation ambiguity safety. Retail code 7 location/VPN denial was observed on 2026-08-28 while public status was operational; it is account-specific evidence, not cancellation, flatness, a permanent ban or permission to restart. Trace IDs are retained on raised errors and logs, not stored as a new durable ledger relation. |
| Reporting | [Trader guide](https://docs.polymarket.us/trader-guide/reporting), [Retail portfolio](https://docs.polymarket.us/api-reference/portfolio/overview) | Private events plus retained Retail `/v1/portfolio/activities` backfill recent executions; exactly one account leg is selected with `isAggressor`; execution and trade IDs deduplicate independently; trade state is durable; a linked `TRADE_STATE_BUSTED` fill is reversed exactly once and excluded from P&L/basis/counts. | Venue history can be reset and is never negative evidence. Institutional Report Search, Drop Copy and ledger CSV exports are not integrated or assumed available. |
| Order management | [Trader guide](https://docs.polymarket.us/trader-guide/order-management), [Retail orders](https://docs.polymarket.us/api-reference/orders/overview) | Reward quotes are asynchronous post-only GTC limits; forced reductions are bounded IOC limits; local validation covers identity, open state, price bounds, tick and minimum quantity; HTTP 200/order ID is acknowledgement only; stream/REST state proves acceptance, fills and cancellation; cancel-confirm-replace prevents overlapping ownership. New-risk post-only quotes are gated by cached `buyingPower`, successful submissions reserve it for the rest of the sweep, and structured rejection delays are market-local. | Buying power never substitutes for equity/P&L and never blocks reducing quotes, cancels or IOC exits. Expected post-only races wait only one second; balance/risk and unknown validation rejects wait longer locally. Preview is deliberately not called per quote, and batch mutation remains unused. |
| Connection issues | [Trader guide](https://docs.polymarket.us/trader-guide/connection-issues), [Retail WebSocket reference](https://docs.polymarket.us/api-reference/websocket/overview), [private WebSocket](https://docs.polymarket.us/api-reference/websocket/private) | Close/error signals trigger bounded exponential reconnect; new risk stops during recovery; reconnect requires REST reconciliation and fresh private snapshots; sustained venue failure enters a maintenance pause and requires three successful authenticated account reads before normal recovery. Every private shard accepts only a decoded application message or positively awaited WebSocket protocol Pong as liveness; the market socket retains its independent last-message deadline. The deadline is 180 seconds and owned SDK clients use a 15-second REST timeout. | Retail documents at most 100 market slugs in one subscription. Separately, live measurement on 2026-08-14 found that an 11th private subscription on one connection is rejected asynchronously. Because position updates omit market identity, this project uses one position subscription per market plus one all-market order subscription and one balance subscription: eight markets per private connection. That measured implementation constraint must not be confused with the documented 100-market subscription width. |
| Market data and settlement | [Retail markets](https://docs.polymarket.us/api-reference/market/overview), [market WebSocket](https://docs.polymarket.us/api-reference/websocket/markets) | The isolated scalp study stores full books, public trades, documented book statistics and settlement, with a combined scope capped at 100 markets. Real-time books use the market WebSocket; REST books are a bounded freshness fallback, not the intended continuous feed. | Retail documents at most 100 markets per market-data subscription and explicitly recommends WebSocket rather than continuous REST polling. On 2026-08-28 observed Retail list responses omitted the documented list-level volume fields even when volume ordering was requested. A 2026-08-29 read-only stream probe stayed healthy at 29 subscribed markets; two nonpublishing books at widths 24 and 29 were market-specific silence, not evidence of a socket-width cap. Settlement uses observed Retail `marketSlug` plus `settlementPrice`, not an inferred list state. |

## API capacity and scaling evidence

This section separates official limits, observed implementation behavior and
scenario modeling. A modeled slot ceiling is never promoted to a venue fact or
production authorization.

### Business-logic consumers of the shared REST governor

The live process does more than place and cancel orders. Its steady and burst
work must all fit through the same conservative 4-request/second process gate.
The following inventory was audited end to end on 2026-08-29:

| Responsibility | Current cadence and request demand | Scaling behavior |
|---|---|---|
| Balance freshness | One balance read every 5 seconds: `0.20/s`. | Account-wide, fixed. |
| Non-trading cash proof | Every 15 seconds, current one-page cash activity plus a second bracketing balance: `2/15 = 0.133/s`. More activity pages add directly. | Account-wide, page-sensitive. |
| Whole-account reconciliation | Every 10 seconds, at least one complete open-order page, one position page and one execution-activity page: `3/10 = 0.30/s`. Extra pages and individually unresolved/absent order reads add directly. | Account-wide plus ambiguity-sensitive. |
| Bound-market metadata and rewards | The deployed batching uses one slug-filtered market read plus one symbol-filtered incentive read per bounded group of eight: `2 × ceil(N/8) / 60`. Startup validation still reads every bound book and has its own finite burst. | Stepwise by private-shard-sized group. |
| Quiet-book REST fallback | A market with no recent streamed full book becomes due after 3 seconds, so demand is approximately `N × quiet_fraction / 3` requests/second. Its decision book is stale at 5 seconds. | The dominant degraded-feed term; a silent full slate cannot be made fresh by polling at scale. |
| Mutations | Creates, cancels and IOC reductions share the governor. They are event-driven and never automatically retried. | Depends on fill/cancel/replace business activity, not slot count alone. |
| Market selection | On the measured planning assumptions, 52 catalog pages, 4 incentive pages and up to 60 candidate books every 900 seconds: `116/900 = 0.129/s` on average, but a 116-request burst with a 29-second floor at 4 requests/second. | Catalog/page/book-sensitive burst. |
| Earnings | One earnings read every 900 seconds: about `0.0011/s`. | Fixed. |
| Recovery and retry | Confirmed 429 read retries, private-stream handoff proof, missing-order repair, pagination and maintenance probes consume additional reads. Unknown mutations do not retry. | Failure- and page-sensitive; must be measured in tail windows. |

Selection and service are largely serial. Response latency below the 0.25-second
governor interval is hidden by the gate; slower responses reduce effective
throughput and lengthen head-of-line delay even when average request rate is
below four. Capacity replay therefore accepts response latency as an explicit
input and checks deadlines/backlog as well as average requests per second.

### Measured live baseline and modeled scenarios

Read-only log analysis of the laptop session from its 2026-08-29 12:25 UTC
restart through a 4,649.7-second observation window found 221 planned action
lines (`126` post-only places, `91` cancels and `4` IOC placements), or
`0.0475/s`; 204 reconciliation log events, or `0.0439/s`; 47 priority replans;
and selection scans with recorded duration p50 `33.8s` and p95/max `44.9s`.
Those action lines are planning evidence, not exact transport-attempt counts.
There was one rate-limit event in the window. The new local limiter telemetry
is required before treating exact category rates, response latency, wait tails
or 429 count as measured production inputs.

The flat-gated `2aa9fec` deployment supplied the first exact 20-slot transport
measurement. Its startup window through 14:27:13Z covered 146.9 seconds and
373 attempts (`338` reads, `35` mutations, `2.539/s`), with no failures,
governor wait p99 `0.217s`, response p99 `0.964s` and maximum queue depth one.
The following 64.0-second window covered 116 attempts (`1.813/s`) but recorded
10 confirmed Cloudflare `1015`/HTTP 429 responses. Runtime evidence localized
the repeated rejection to `GET /v1/portfolio/activities` execution-history
backfill while order placement, account reconciliation and private-stream
handling continued. This proves that low average aggregate RPS does not by
itself make a restart window comfortable: an endpoint-specific or rolling
Cloudflare limit can bind first. Twenty slots must remain the ceiling while
ordinary windows and a no-429 restart are collected; no width canary is
authorized from this sample.

The immediately following 66.2-second window recovered without an operator or
process change: 105 reads (`1.585/s`), including six execution-history calls,
completed with zero failures and zero 429s. This bounds the observed incident
to a transient restart/rolling window; it does not erase the failed window or
prove that another restart will be clean.

The flat-gated 2026-08-30 `859b891` restart supplied that second clean restart
sample. Its first 152.0 seconds made 416 attempts (`370` reads, `46` mutations,
`2.736/s`) with zero failures and zero 429s; wait p99 was `0.223s`, response p99
`0.706s` and maximum queue depth one. The burst included 44 discovery pages,
232 book reads, 41 creates, five cancels and eight execution-history reads.
This satisfies the requested no-429 restart observation at 20 slots, but one
clean replay does not explain the earlier endpoint-specific `1015` window or
authorize a width increase.

The 2026-08-30 02:00Z synchronized lifecycle window separated slot churn from
API capacity. Eight bound markets reached kickoff while two positions were
being exited; working orders fell to 16/17 across 11 markets, but the relevant
transport windows contained zero failures and zero 429s and the selector still
had 81 viable candidates. The ordinary 02:12:52Z selection replaced eight
expired owners plus one economic-idle owner and restored 20 markets/33 orders
with zero positions, unresolved intents or unknown orders. This is evidence of
avoidable lifecycle scheduling delay, not a venue limit. The source repair
uses the existing bounded selection and validation calls; it adds no endpoint,
polling cadence or diagnostic request.

A natural router outage on 2026-08-29 supplied the first deployed network-loss
window. DNS failures and timeouts disconnected public/private streams from
about 17:48Z; the runner quarantined new risk and reconciled/canceled until an
18:01Z authenticated snapshot proved zero positions and zero resting orders.
The 735.6-second telemetry window recorded 380 attempts, 40 transport failures,
zero 429s, response p95/p99/max `10.013/10.016/15.004s` and maximum limiter
queue depth one. After connectivity returned, the same PID committed a fresh
20-market top-up at 18:01:55Z and resumed placements at 18:02:18Z without a
restart. This accepts safe flat recovery from one outage; it is not evidence
that every network-loss duration or in-flight mutation outcome is harmless.
The subsequent 20-slot rebuild produced a clean 73.0-second burst of 208
attempts (`2.849/s`: 180 reads and 28 creates), with zero failures/429s and
maximum limiter queue depth one. The following 80.1-second ordinary window
returned to 150 attempts (`1.873/s`) with zero failures/429s. Thus recovery can
approach the project's 3/s comfort budget even when steady state remains well
below it; canary decisions must include recovery bursts, not only quiet means.

The offline capacity model deliberately uses more conservative mutation input
than that action-line rate and preserves 25% of the internal 4/s governor:

| Scenario | Explicit assumptions | Result after bounded contract batching |
|---|---|---|
| Normal | 10% of owners need quiet-book REST fallback; `0.10` mutations/s; one execution page; no retry multiplier. | At 22 slots: `1.70/s`; at 24: `1.76/s` (44% of the gate), with zero five-minute replay misses/backlog. API/public-only modeled ceiling: 56 slots. |
| Busy | 25% quiet owners; `0.25` mutations/s; two execution pages; reads multiplied by `1.05` for retry/page pressure. | Modeled REST ceiling with 25% headroom: 19 slots. The estimate is `3.01/s` at 20 and `3.19/s` at 22, so a 22 canary must measure bursts rather than infer safety from ordinary windows. |
| Entire public feed silent under current fallback | Every owner requests a REST book every 3 seconds; two execution pages. | API-only modeled ceiling: 6 slots. Demand is `7.50/s` at 20 and `8.17/s` at 22, proving this is a degraded-feed design limit, not a viable scaling mode. Each owner receives a fresh service-time sample and new risk still fails closed on stale books; the correct scale design is bounded fallback plus stream recovery, not whole-slate REST polling. |

These results do **not** select 19, 24 or 56 as the final production width.
They prove that 24 is not a documented venue slot limit and that average normal
REST load is not currently the first modeled constraint. A 2026-08-29 point
sample measured the runner at `0.564` of one CPU core at 20 slots; a deliberately
conservative linear projection reaches the `0.75`-core comfort budget at 26
slots. Current portfolio policy provides 30 quote-size units; preserving its
25% cushion caps fully utilized width at 24 slots. Both are project constraints,
not API limits, and both require new measurement or an explicit risk decision
before increasing production width.

### Evidence required before choosing the durable maximum

The deployed code logs a secret-free 60-second API budget window with
attempt counts by fixed category, read/mutation split, response and governor
wait p95/p99/max, live/max queue depth, failures and confirmed 429s. It does not
make a diagnostic request and never records slugs, order IDs, credentials or
payloads. It was deployed at `2aa9fec` on 2026-08-29.

The 20-slot restart loaded at `859b891` later supplied 46 clean telemetry
windows through 03:40Z: 6,064 attempts, zero failures/429s, request rates from
`1.459/s` to `2.736/s`, maximum wait p99 `0.223s`, response p99 `1.007s` and
maximum limiter queue depth one. This satisfies the no-429 baseline observation
but does not explain the earlier endpoint-local execution-history `1015`s.

On 2026-08-29 the operator authorized one flat-gated 22-slot measurement
canary. Twenty remains the launcher default and durable baseline. The canary
must run at least 30 continuous minutes covering startup and selection bursts,
with zero failures/confirmed 429s, no queue above one in two consecutive
60-second windows, no safety
deadline or stream-coverage loss, stable ownership and zero unresolved/unknown
state. A breach requires graceful stop, positive flat proof and restart at 20.
The durable maximum is the highest width
that preserves the chosen request headroom with zero safety-deadline misses,
zero sustained backlog/429s, acceptable response/wait tails, proven stream
coverage, CPU/thermal headroom and the separately authorized portfolio cushion.
No single clean minute and no raw official 20/s ceiling can establish it.

## Safety interpretations that must not be lost

### REST retry is not mutation retry

The guide's generic retry examples include POST, but this bot commits an intent
before every order mutation. A timeout or connection reset after submission is
an unknown outcome: repeating it can create a duplicate order or cancel against
the wrong state. Reads may retry when their failure is positively classified;
creates, cancels and exits require venue reconciliation before another mutation.

A structured HTTP 403 is different: the venue positively rejected that exact
request, so its intent may fail without inventing an order. It proves nothing
about existing orders or positions. A permission/location denial pauses new
entries account-wide and permits one later desired entry as a recovery probe.
Cancels and reducers remain available unless their own request is rejected;
the same intent is never blindly retried.

### Hosting and location access

The Retail API documents API-key authentication and signed HTTP requests; it
does not document an "authenticated network identity", a residential-IP
add-on, or Polygon RPC signing for Retail order entry. A Retail rate limit is
HTTP 429, while the observed HTTP 403/code 7 is a permission/location denial.
Do not treat a US datacenter label or third-party "residential VPS" marketing
as venue authorization, and never disguise egress to evade an access control.
Kamatera explicitly states that its servers use datacenter IPs and that it does
not sell residential ISP addresses. Before choosing another runtime, send the
exact timestamps, safe trace/request IDs and public egress IP to
`support@polymarket.us`; never send the API secret. Ask which self-hosted or
cloud topology Polymarket US permits for this verified Retail account.

The Retail documentation does not expose a cancel-on-disconnect/dead-man
control. Retail GTC orders remain until filled or canceled; GTD can bound an
individual order's life but would change quote renewal, queue position and
reward economics. The documented Cancel on Disconnect feature belongs to
institutional FIX and cancels only DAY orders; GTC and GTD continue resting.
Therefore UPS power protection and reconnect logic do not eliminate the risk
of an ISP outage while Retail quotes rest. Any redundant ISP/failover egress
must also be confirmed with Polymarket before use.

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
sharded across three connections, so liveness is per shard. A decoded official
application heartbeat/message or a positively awaited WebSocket protocol Pong
advances that shard's liveness clock. Business-event silence alone is not a
transport failure; a failed, timed-out or unavailable transport probe is. A
shard with no positive proof retains the 180-second silence alarm and may
be overlap-replaced without cancelling resting orders after each replacement
delivers private balance and a frozen REST balance/order/position reconciliation
succeeds. An empty scoped order subscription may emit no EOF. Filtered snapshots
are complete only
within their subscribed markets; any failed handoff proof uses global
quarantine.

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

## Rollout and authorization boundary

[NEXT.md](../NEXT.md) is authoritative for unresolved work. The prioritized
2026-08-27 review is implemented locally: history-reset semantics, independent
stream liveness, busted-trade reversal, entry-only buying-power/rejection
protection, structured HTTP diagnostics and `Retry-After`, the 15-second REST
timeout, and an isolated changelog RSS monitor.

Local implementation is not deployment. The operator still decides commit,
deployment, RSS scheduling and restart timing. The additive ledger migration
runs when the new runner first opens the ledger; read-only reports tolerate the
old schema during the deployment window and begin filtering `reversed` after
migration. Live acceptance must use normal authenticated flat/order-free
startup gates. No document entry alone authorizes a restart or a trading-policy
change.

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
