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

September 13 optional-book control is local load policy, not a changed venue
quota: production admission spaces optional discovery/startup/flat price reads
four seconds apart; routine flat work defers while held-position/account safety
retains its existing lane. Recent book-429 history survives one successful
probe. The 4/s aggregate ceiling, complete account proof and Retail interfaces
are unchanged. No claim of unlimited capacity or private-stream recovery follows.
[Implementation and limits](history/OPTIONAL_BOOK_LOAD_CONTROL_2026-09-13.md).

September 12 public-book follow-up: confirmed `market_book@429`/Cloudflare 1015
responses exposed a local inline-retry amplifier despite sub-4/s aggregate
traffic. The prepared repair isolates book reads in a shared endpoint backoff
with one expiring recovery probe, independent of account/mutation lanes. This
qualifies the older class-only descriptions below; book 429s never cool safety
or retry inline. The public status API reported operational, but the first
venue rejection's exact quota/root cause remains unknown. No endpoint, rate,
cash classification or source selection changed. [Audit](history/PUBLIC_BOOK_BACKOFF_2026-09-12.md).

The bot has two permanent, explicitly selected account modes:
[WebSocket and REST](ACCOUNT_SOURCE_ARCHITECTURE.md).
It is bot implementation policy, not a venue guarantee of REST freshness or
equivalence to the private stream. Public books still require market data.

September 12 release follow-up: the execution collector requests documented
`types=[ACTIVITY_TYPE_TRADE]` on every activity page. It already consumes only
trade executions and trade lifecycle, including BUSTED; cash accounting remains
separate. Unfiltered queries timed out while the same endpoint's trade-filtered
queries succeeded. Pagination/coverage and exact-account proofs are unchanged.
This is an observed query-path difference, not a claim that all REST or private
WebSocket service recovered. [Evidence](history/ACCOUNT_PERFORMANCE_RELEASE_2026-09-12.md).

The [September 11 bulk/continuity repair](history/REST_BULK_CONTINUITY_2026-09-11.md)
uses complete Retail open-order lists to avoid repeated individual reads only
for unchanged flat OPEN/zero-fill entries. The Retail
[open-orders contract](https://docs.polymarket.us/api-reference/orders/get-open-orders)
was rechecked for the complete-list interface. Missing/changed/ambiguous orders
still require exact proof; successful lists do not guarantee source freshness.

The [September 11 REST review](history/REST_FALLBACK_REVIEW_2026-09-11.md)
introduced the REST-only 30 pending-entry maximum and bounded concurrent exact
reads without changing the shared rate limit. The Retail
[activities contract](https://docs.polymarket.us/api-reference/portfolio/get-activities),
reopened September 11, documents descending timestamp sorting and cursor/EOF
pagination. The REST source starts from durable fill anchors (retained EOF
for an empty ledger), then keeps a minimum two-page known-fill overlap; it must not silently truncate new fills
at the normal source's two-page backfill bound. This is positive retained
evidence, not proof that older/pruned or arbitrarily late history is complete.

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

**September 8 live-event diagnosis:** the Retail
[Get Event By Slug](https://docs.polymarket.us/api-reference/events/get-event-by-slug)
contract exposes event `live`, `ended`, `score` and `period` independently of
`startTime`. Exact Mutti, Picard and Vivarelli event responses reported live
sets S4/S5/S3 while nested market clocks remained hours in the future. Parent
event state must not be discarded or confused with `marketGroups[].live`,
which was false in the same live responses. The implemented event guard retains
positive live evidence and overrides future clocks for entries and exits.
[Evidence](history/ACTUAL_LIVE_EVENT_GUARD_2026-09-08.md).

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

Last reviewed: **2026-09-12**.

| Topic | Official sources | Project coverage | Known difference or open review |
|---|---|---|---|
| Changelog and venue health | [Changelog](https://docs.polymarket.us/changelog), [live status](https://status.polymarketexchange.com/) | Mandatory diagnostic sources for unexplained venue behavior. The isolated hourly RSS monitor establishes a baseline without history spam, alerts only unseen items and retains unseen state after notification failure. | Changelog `v0.0.79` announced 2026-08-27 extended maintenance and a stored execution-history reset; an empty pre-maintenance history query therefore cannot prove that no execution occurred. The monitor remains diagnostic and cannot change trading behavior. |
| Scheduled production maintenance | Official Statuspage v2 scheduled-maintenance feeds and exact item details under `status.polymarketexchange.com` | The runner filters the exact production component, persists verified windows locally, begins its unchanged cancel-confirm/exact-IOC evacuation 30 minutes before the announced start, and holds new risk until one hour after the later of scheduled end or official completion plus three authenticated health cycles. | Statuspage `started_at` is notice creation, not actual start. Notices have arrived as little as six minutes before a window, so automation improves response but cannot manufacture thirty minutes of lead time. Upcoming/active disappearance is never treated as completion. |
| Rate limits | [Trader guide](https://docs.polymarket.us/trader-guide/rate-limits), [Retail reference](https://docs.polymarket.us/api-reference/rate-limits) | Cooperating WSL tools share one file-locked laptop-wide 4/second aggregate gate. Background discovery has a separate 2/second lane; maker entry/revalidation have an independent 4/second lane; safety reads, cancels, passive reducers and IOC exits use the 4/second safety lane. Read-only reports/measurements use an opportunistic fourth lane: only after an unused aggregate interval and at most `0.25/s`. A confirmed 429 cools only its originating lane: report/background/entry retain bounded adaptive `Retry-After`, while safety is capped at one second. Mutations never auto-retry. Categorized/redacted telemetry adds no request; its attempt/response counters are process-local while its four lane cooldown fields merge bounded host-shared deadlines. | Retail documents a global authenticated ceiling of 20 requests/second per API key and a public ceiling of 20 requests/second per IP. The project's lower class-aware rates are internal admission policy, not a discovered venue limit. Native Windows has no compatible `fcntl` coordinator, so the shared factory fails closed and authenticated executable clients must run inside WSL. An uncooperative external process or another machine on the same key/public IP can still contribute venue load, so software cannot promise that Cloudflare will never emit a first 429. |
| Fees and rebates | [Fee schedule](https://docs.polymarket.us/fees), [Retail order schema](https://docs.polymarket.us/api-reference/orders/get-order) | Each Retail fill's signed `commissionNotionalCollected.value` is stored directly in USD. Positive taker fees reduce realized P&L; negative maker rebates increase it. Gross positive owned taker fees are observe-only telemetry, not a stop; entry forecasts intentionally assume zero maker rebate. | Effective exchange-wide from 00:00 ET on 2026-07-01: `fee = theta × contracts × price × (1 - price)`, with taker theta `0.06` and maker theta `-0.0125`. Venue fees/rebates use cent-level banker's rounding. Partner execution reports use scaled integer notional, but the Retail `MoneyAmount.value` must not be divided by partner price/quantity scales. |
| Liquidity reward programs | [Public rewards directory](https://polymarket.us/rewards), authenticated Retail earnings | The selector evaluates individual executable markets, but reward accounting must group them by `program_id`: the displayed pool belongs to the program/time period and is shared across its member markets. `scripts/program_reward_audit.py` therefore never sums a repeated pool once per market; it joins exact resting side-hours, the latest authenticated earnings snapshot and balanced-pair trading P&L by program. A measurement-only `selection_rank` in quote observation JSON separates virtual top-24 from ranks 25+ without changing selection or placing another request. | On 2026-09-02 the directory displayed 166 program rows and 99,448 program-market memberships. That figure is not 99,448 unique simultaneously tradable markets: the same market families appear in different reward periods/program rows. Public membership counts do not prove this account's earned share. Only authenticated `PAID`/`PENDING` earnings establish actual reward, pending never enters net, and earnings that cannot be mapped to exactly one observed program remain explicitly unallocated. |
| Error handling | [Trader guide](https://docs.polymarket.us/trader-guide/error-handling) | 4xx failures are not blindly retried; 429 reads use bounded backoff; 5xx/transport failures enter scoped recovery or maintenance pause; ambiguous mutations remain durable and quarantined until positive reconciliation. A response-backed 403 fails only its exact intent and opens a one-minute new-entry circuit. Cancels/reducers bypass entry denial; their own rejection gets a 10-second safety circuit. Successful mutations prove recovery only for their scope. Safe status, structured error code and trace/request ID survive adapter boundaries. | Generic advice to retry POST must never override mutation ambiguity safety. Retail code 7 location/VPN denial was observed on 2026-08-28 while public status was operational; it is account-specific evidence, not cancellation, flatness, a permanent ban or permission to restart. Trace IDs are retained on raised errors and logs, not stored as a new durable ledger relation. |
| Market lifecycle status | [Retail markets](https://docs.polymarket.us/api-reference/market/overview) | A successful exact-market response with `active=false`, `closed=true` or an explicit non-open status is positive lifecycle evidence, not a transport failure. A flat bound owner may become a replacement candidate only after the normal fresh top-up reconciliation re-fetches that exact market and still proves it non-open. An inactive market with an exactly reconciled bot-owned position remains reserved and fully risk-counted, but does not issue locally impossible orders; one-minute metadata probes restore its ordinary exit path if it reopens. | Production evidence on 2026-09-01 showed many exact market responses in a non-open state during venue maintenance while authenticated account reads remained healthy. Missing, malformed, timed-out or generic failed responses never prove closure or authorize slot release. The local Retail payload validator refuses both new and reducing order construction against confirmed non-open metadata, so repeatedly planning IOC exits cannot recover such a position. |
| Reporting | [Trader guide](https://docs.polymarket.us/trader-guide/reporting), [Retail portfolio](https://docs.polymarket.us/api-reference/portfolio/overview) | Private events plus retained Retail `/v1/portfolio/activities` backfill recent executions; exactly one account leg is selected with `isAggressor`; execution and trade IDs deduplicate independently; trade state is durable; a linked `TRADE_STATE_BUSTED` fill is reversed exactly once and excluded from P&L/basis/counts. | Venue history can be reset and is never negative evidence. Institutional Report Search, Drop Copy and ledger CSV exports are not integrated or assumed available. |
| Account balances and positions | [Account SDK](https://docs.polymarket.us/api-reference/sdks/python/account), [account balances](https://docs.polymarket.us/api-reference/account/get-account-balances), [Retail portfolio](https://docs.polymarket.us/api-reference/portfolio/overview) | The current account mark is `currentBalance + sum(open position cashValue)`. A complete positions page is bracketed by equal settled-cash reads; moving cash or a missing/malformed `cashValue` fails closed. `cost` supplies the current open basis for a closed/open split, while the total mark remains available without basis. Positively classified rewards/deposits/withdrawals/transfers are subtracted from trading P&L. | Official schemas define `currentBalance` as current fiat cash and positions expose `cashValue`, `cost` and `realized`. Authenticated production evidence on 2026-08-30 returned `assetNotional=0` while two open positions had nonzero `cashValue`; therefore the raw balance-row `assetNotional` is not used as position value. The day total is account-wide because venue equity cannot attribute another manual/system trade. |
| Binary price frame | [Retail order schema](https://docs.polymarket.us/api-reference/orders/get-order), [Retail portfolio](https://docs.polymarket.us/api-reference/portfolio/overview) | Live order/fill prices are stored in the strategy's LONG/YES frame. `BUY_LONG` costs `price`; `BUY_SHORT` costs `1 - price`; wire `side` selects the outcome and is not the sign of cash flow. A matched pair realizes `quantity × (short price - long price) - signed fees`. Venue position `cost` is economic basis and is converted back with one-minus for a negative position before exit planning. | Authenticated 2026-08-29 evidence: ten `BUY_SHORT` contracts at stored `0.29` carried venue cost `7.07` after a `-0.03` rebate. A 364-fill replay exactly matched the audited `-79.4465` settled-cash change. Symmetric 50-cent examples conceal this distinction. |
| Order management | [Trader guide](https://docs.polymarket.us/trader-guide/order-management), [Retail orders](https://docs.polymarket.us/api-reference/orders/overview) | Reward quotes are asynchronous post-only GTC limits; forced reductions are bounded IOC limits; local validation covers identity, open state, price bounds, tick and minimum quantity; HTTP 200/order ID is acknowledgement only; stream/REST state proves acceptance, fills and cancellation; cancel-confirm-replace prevents overlapping ownership. New-risk post-only quotes are gated by cached `buyingPower`, successful submissions reserve it for the rest of the sweep, and structured rejection delays are market-local. | Buying power never substitutes for equity/P&L and never blocks reducing quotes, cancels or IOC exits. Expected post-only races wait only one second; balance/risk and unknown validation rejects wait longer locally. Preview is deliberately not called per quote, and batch mutation remains unused. |
| Connection issues | [Trader guide](https://docs.polymarket.us/trader-guide/connection-issues), [Retail WebSocket reference](https://docs.polymarket.us/api-reference/websocket/overview), [private WebSocket](https://docs.polymarket.us/api-reference/websocket/private), [Python SDK WebSocket](https://docs.polymarket.us/api-reference/sdks/python/websocket) | Close/error signals trigger bounded exponential reconnect; new risk stops during recovery; reconnect requires REST reconciliation and fresh private snapshots; sustained venue failure enters a maintenance pause and requires three successful authenticated account reads before normal recovery. Every private shard accepts only a decoded application message or positively awaited WebSocket protocol Pong as liveness; the market socket retains its independent last-message deadline. The deadline is 180 seconds and owned SDK clients use a 15-second REST timeout. | Retail documents at most 100 market slugs in one subscription. Separately, live measurement on 2026-08-14 found that an 11th private subscription on one connection is rejected asynchronously. Because position updates omit market identity, this project uses one position subscription per market plus one all-market order subscription and one balance subscription: eight markets per private connection. On 2026-09-12 the normal order subscription again delivered initial order snapshot/update frames; the raw reference also listed `SUBSCRIPTION_TYPE_ORDER_SNAPSHOT`, but adding it only duplicated those order frames. Balance and position initial snapshots remained absent in the same healthy Pong-connected probes, so partial order delivery is not sufficient for WebSocket account-source promotion. The pinned and latest published Python SDK were both `0.1.2`; no upgrade was available. Treat global/API health as permission to reconcile, not proof that every stream or market is healthy. A response-backed empty book is market state; transport/schema failures are not equivalent evidence. |
| Public US application participation | Official [Polymarket US application](https://polymarket.us/) | The public application is observed separately from credentialed Retail API/account health. Exact `Maintenance Mode` plus trading-paused/platform-maintenance text blocks new entries, makes flat quotes undesired and skips discovery while the live process continues cancels, reconciliation and position exits. Unknown evidence cannot clear the block; recovery needs three consecutive healthy application observations. A persistent machine-local operator latch may also pause entries, but clearing it cannot override UI maintenance. | On 2026-09-01 the US application blocked ordinary users while the status page and Retail API still worked; one ordinary bot order filled during the overlap. API success therefore does not prove symmetric customer participation, and the status page alone is insufficient for this product-scoped state. The public page is not account evidence and may never prove flatness, ownership, cancellation, settlement or safe mutation. The bounded gzip page read is outside the Retail API limiter and carries no credentials. [Evidence](history/UI_MAINTENANCE_ENTRY_PAUSE_2026-09-01.md). |
| Market data and settlement | [Retail markets](https://docs.polymarket.us/api-reference/market/overview), [market WebSocket](https://docs.polymarket.us/api-reference/websocket/markets) | The isolated scalp study stores full books, public trades, documented book statistics and settlement, with a combined scope capped at 100 markets. Real-time books use the market WebSocket; REST books are a bounded freshness fallback, not the intended continuous feed. | Retail documents at most 100 markets per market-data subscription and explicitly recommends WebSocket rather than continuous REST polling. On 2026-08-28 observed Retail list responses omitted the documented list-level volume fields even when volume ordering was requested. A 2026-08-29 read-only stream probe stayed healthy at 29 subscribed markets; two nonpublishing books at widths 24 and 29 were market-specific silence, not evidence of a socket-width cap. Settlement uses observed Retail `marketSlug` plus `settlementPrice`, not an inferred list state. |

## API capacity and scaling evidence

This section separates official limits, observed implementation behavior and
scenario modeling. A modeled slot ceiling is never promoted to a venue fact or
production authorization.

### Business-logic consumers of the shared REST governor

The live process does more than place and cancel orders. Its steady and burst
work must all fit through the same conservative 4-request/second process gate.
The following inventory was audited end to end again on 2026-09-01:

| Responsibility | Current cadence and request demand | Scaling behavior |
|---|---|---|
| Marked-equity freshness | A complete balance/positions/balance bracket becomes due at the five-second decision margin: up to `3/5 = 0.60/s` with one position page. Extra position pages add directly. | Account-wide, page-sensitive and fixed with respect to slots. |
| Non-trading cash proof | Each published equity mark requires current bounded cash-activity pages and one final equal-cash read after the marked-equity bracket. Completion latency and additional pages affect actual demand; do not assume a separate cached 15-second cash cadence. | Account-wide, page-sensitive; data age is not completion time. |
| Whole-account reconciliation | Every 10 seconds, at least one complete open-order page, one position page and one execution-activity page: `3/10 = 0.30/s`. Extra pages and individually unresolved/absent order reads add directly. | Account-wide plus ambiguity-sensitive. |
| Bound-market metadata and rewards | The deployed batching uses one slug-filtered market read plus one symbol-filtered incentive read per bounded group of eight: `2 × ceil(N/8) / 60`. Startup validation still reads every bound book and has its own finite burst. | Stepwise by private-shard-sized group. |
| Quiet-book REST fallback | A market with no recent streamed full book becomes due after 3 seconds, so nominal demand is approximately `N × quiet_fraction / 3` requests/second. Its decision book is stale at 5 seconds. Ordinary due reads are serviced at the guarded owner decision after account proof, not prefetched across the whole slate; economic-replacement dwell retains normal checks. | The dominant degraded-feed term; serialization/backoff changes realized rate. Just-in-time service removes wasted prefetch, not freshness limits or the degraded-feed capacity bound. [Local review](history/JUST_IN_TIME_BOOK_SERVICE_2026-09-12.md). |
| Mutations | Creates, cancels and IOC reductions share the governor. They are event-driven and never automatically retried. | Depends on fill/cancel/replace business activity, not slot count alone. |
| Market selection | Active/open sports events are offset-paged over the strategy horizon plus a one-day clock-skew buffer. Nested markets pass the unchanged market-level execution gates before symbol-filtered incentive reads and up to 100 ranked books. On the 2026-09-02 live contract this was two event pages plus six incentive pages, instead of 100 global catalog reads or more than 200 global incentive pages. | Buffered-event/executable-market/book-sensitive burst. It remains background traffic, fails closed after ten event pages, and every failed complete scan establishes a shared 60-second top-up retry floor. |
| Earnings | One earnings read every 900 seconds: about `0.0011/s`. | Fixed. |
| Recovery and retry | Confirmed 429 read retries, private-stream handoff proof, missing-order repair, pagination and maintenance probes consume additional reads. Unknown mutations do not retry. | Failure- and page-sensitive; must be measured in tail windows. |
| Hourly reward table | Worst bounded path is one earnings read, two cash-history walks of at most four pages each, two balance reads and one position read: at most 12 calls. It is outside the runner's process-local telemetry. | Fixed and independently scheduled. It now waits for spare shared allowance, is capped at `0.25/s`, has its own lane/cooldown and may use up to 45 minutes of the hourly interval. |
| Manual reports and measurements | Accounting, P&L, dashboard diagnostic, period mix, resting-by-kickoff, reward census, usable-market, shadow and scalp tools can fan out across catalog, book and account reads. | Not part of steady live demand unless actually running. Every audited non-trading tool uses the same opportunistic report lane; repair/safety tools retain their stronger class. |

Selection and service are largely serial. Response latency below the 0.25-second
governor interval is hidden by the gate; slower responses reduce effective
throughput and lengthen head-of-line delay even when average request rate is
below four. Capacity replay therefore accepts response latency as an explicit
input and checks deadlines/backlog as well as average requests per second.

The loaded 24-slot runner supplied a narrower post-scheduler sample at
04:43:11Z on 2026-09-01: 87 attempts in 62.2 seconds, comprising 84 reads and
three mutations.  `market_book` was the largest category at 51 attempts;
balance used 12, positions seven and execution history four.  This is evidence
that quiet-book REST dominates request count in that window, not evidence that
the venue rate limit was reached: the window recorded no failure or 429.
`max_waiting=1` counts limiter admission waiters and does not prove the number
of requests already in flight.  The logged p95 wait and response values are
tail percentiles, not per-request averages, so multiplying their sum by all 87
attempts is not a valid elapsed-time attribution.  Any later scheduler or
capacity change must measure per-request/category duration or use a permitted
sampling profiler before assigning the remaining CPU or wall time.

The 2026-09-01 direct-32 preparation parsed the latest twenty healthy 24-slot
windows rather than inferring from action logs. They covered 1,595.6 seconds,
with observed median/p95/max `1.852/2.295/2.485` requests/second, maximum queue
eight and zero failures/confirmed 429s. Every emitted category was classified:
book, discovery, contract/incentive batch and single-contract reads; balance,
positions, cash/execution history and open orders; order retrieval; earnings;
and create/cancel mutations. Fixed account work stayed fixed, eight-market
contract batches stepped from three to four, and every other event/market term
scaled conservatively by `32/24`. The resulting 32-slot median/p95/max was
`2.361/2.912/3.189/s`; a coincident bounded report raised the maximum projection
to `3.329/s`. This is normal-window canary evidence only. The deterministic
busy model is `4.73/s` at 32 and the fully silent-feed model is `11.94/s`, so
degraded/recovery rollback gates remain mandatory.

The same read-only report now ranks high-demand limiter windows by exact UTC
time and correlates their API categories with the secret-free event tags since
the preceding window. Across the latest 100 24-slot windows, the maximum was
`2.801/s` at `2026-09-01T15:38:12Z`: 165 book reads led the interval, with 15
creates plus runtime-recovery, market-retirement and stale-book signals. Other
high windows at `15:29:09Z`, `15:30:28Z`, `15:24:20Z`, `16:11:53Z` and
`16:24:38Z` separate selection/refill, exact-order ambiguity repair and
mutation churn from the book-heavy baseline. Their conservative 32-slot
maximum is `3.601/s`, or `3.739/s` with coincident report work. These are
sustained governed intervals, not raw instantaneous spikes: the shared 4/s
gate smooths calls, while queue and latency expose pressure before 429 alone.

Controls follow business responsibility. Stream freshness and bounded
empty/dead-market retries may reduce book fallback, but position markets remain
unconditional. Discovery may be cached, batched and coalesced, but mandatory
slot replacement remains timely. Entry replans may be coalesced, while cancels,
reducers, IOC exits, exact-order ambiguity proof, account proof and recovery
must not be suppressed. Non-trading reports use the opportunistic report lane.
If mandatory safety/recovery traffic contends, breadth is rolled back instead
of throttling that evidence.

The 2026-09-01 32-slot canary also exposed a source-level discovery amplifier
rather than a venue limit. One exact non-open owner retained a real position,
but each successful one-minute metadata probe requested urgent lifecycle
top-up before the runtime established that the slot was risk-reserved and could
not be released. Requests reached 19/23/24 per hour across 16:00–18:00 UTC and
many scans changed no owner. Admission control bounded the traffic, but the
correct fix is earlier suppression: an exact positioned inactive wait does not
request discovery; a flat non-open owner and a later authoritative position-
zero transition still do. This removes calls without delaying a legal exit,
weakening ownership or changing the normal under-capacity selection schedule.

On 2026-09-02 the account-wide active market catalog grew past the selector's
fixed 100-page by 500-row safety ceiling. The last successful scan observed
47,377 catalog rows; later scans exhausted all 100 pages and failed closed at
50,000. Twenty-two top-up failures accumulated from 13:16Z through 13:52Z.
Each failed attempt consumed 100 background reads without reaching incentive,
book or replacement selection, while owner signals restarted discovery after
only a few seconds. The resulting order drop was local discovery starvation,
not a confirmed 429 or exhaustion of the 4/s host budget.

The first correction tried to remove catalog size from live selection cost by
enumerating nominally active incentives. Live acceptance rejected that design:
even with `statuses=active`, the endpoint exceeded the separate 200-page by
100-row ceiling and the runner failed closed before placing an order. Raising
that ceiling would only move the same unbounded-growth failure.

The corrected path uses the documented event `startTimeMin`/`startTimeMax` and
offset pagination. It requests active/open sports events within one day on
either side of the six-hour strategy window, flattens nested markets, applies
the unchanged market-level lifecycle, clock, V2-moneyline, tick and minimum-
quantity gates, then requests incentives only for those exact symbols before
the unchanged spread, period, full-book and ranking gates. The buffer matters:
an exact-six-hour event window missed seven executable delayed/rescheduled
markets because event `startTime` and nested `gameStartTime` disagreed. A live
comparison found the one-day-buffered set and the complete all-active-sports-
event census produced the same 228 executable markets, while the buffered path
used 736 rather than 2,148 events. Ten event pages are a hard ceiling and a
full final page fails closed rather than silently accepting truncation. Every
successful selector scan reports `event_pages` beside `event_page_ceiling`, so
headroom and growth are visible without reconstructing pagination afterward.

Two other documented market filters were not used as substitutes because the
2026-09-02 wire did not honor them usefully: `categories=sports` still returned
60,000 market rows without completing, while combining the documented
moneyline type filter returned only one row despite hundreds of locally parsed
open moneylines. These are observed API contracts, not assumptions from the
documentation.

On 2026-09-08, primary market `aec-lol-vfb-cgn-2026-09-08` exposed a
16:00 UTC `gameStartTime` while its same-row generated question stated 15:00
UTC; sibling markets and the public event page also used 15:00 UTC. The bot
therefore uses an exact earlier `scheduled for ... UTC` timestamp in the same
market question as a conservative upper bound on pregame time. Structured
`gameStartTime` remains mandatory, and question text can neither extend it nor
fill a missing value. This adds no request and deliberately prefers an early
stop if venue fields conflict.

A separate runner-level retry-not-before deadline prevents lifecycle,
reward-end and post-flat signals from bypassing the ordinary backoff after any
complete top-up failure. This is admission/load control only; it does not
change prices, quantity, ranking, ownership, exit priority or mutation
authority.

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

The 22-slot 2026-08-30 17:00Z lifecycle event proved why a fixed per-process
interval was insufficient. Six owners became non-incentivized/live together.
Although a complete selection only about 40 seconds old had already found 40
viable markets, the lifecycle path did not authorize its existing bounded
revalidation cache and repeated a fresh discovery scan before the mandatory
stream-handoff account proof. The 166.4-second transport window then recorded
269 attempts (240 reads, 29 mutations), nine confirmed Cloudflare 1015/HTTP
429 responses, and the failing operation was a positions read. Recovery
quarantined and rebuilt owned orders; the next 112.1-second window completed
232 attempts with zero failures/429s, committed 22 active markets, and the
ledger reached 32 working orders across all 22 markets while remaining flat.

This is not a 22-slot-only failure: the earlier 20-slot sample also produced
1015 at a low aggregate average. The source response is therefore admission
control rather than a smaller hard slot ceiling. Event-driven lifecycle,
reward-end and post-flat refills may revalidate a complete selection no older
than 120 seconds; background discovery yields to independent entry and safety
lanes; and every audited executable API client cooperates through
`data/api-rate-limiter.state`. This includes the live runner, dashboard,
scheduled and manual reports, probes, census, shadow/scalp measurements,
accounting, history backfill and repair tools. A source-level architecture test
rejects any new executable `AsyncReadOnlyGateway` without an explicit limiter.
A confirmed 429 publishes a class-scoped adaptive cooldown into the same state.
A discovery/report 429 cannot suspend a cancel, passive reducer or IOC exit;
safety's own cooldown is capped at one second. Mutations remain non-retrying,
and all account/risk proof remains fail-closed.

The complete local admission architecture is intentionally layered:

1. WebSocket market/private streams carry normal live state; REST is proof,
   recovery and bounded refresh rather than the primary event transport.
2. One POSIX-file-locked host gate serializes every cooperating WSL process,
   so adding a dashboard or manual report cannot add an independent budget.
3. Request context, not endpoint name or TIF alone, assigns pace. Bot account
   proofs, cancels, passive GTC reducers and IOC exits retain the 4/second
   safety pace. Maker entry and bounded cached revalidation use an independent
   4/second entry lane. Whole-universe selection—including its per-candidate
   book reads—uses the 2/second background lane. Non-trading reports and
   measurements use a separate opportunistic lane capped at one request every
   four seconds and require at least one unused aggregate interval before a
   claim. Report claims update only `next_report_at`, never the background,
   entry or safety clock. All four still consume the single aggregate
   4/second clock.
4. Bulk catalog/incentive/account endpoints, bounded candidate fan-out and the
   120-second revalidation cache remove calls before they reach the queue.
5. Confirmed 429 evidence applies a shared, class-scoped bounded cooldown.
   Background/entry use the adaptive 2/4/8/.../30-second fallback and at most
   60 seconds of `Retry-After`; those reads fail promptly after publishing the
   cooldown instead of serializing the live runner through an inline retry.
   Later non-safety work also defers locally before network IO when more than
   one second of that cooldown remains. A deferred selection or cached
   revalidation aborts that pass once, preserves the prior selection, and is
   scheduled after the bounded shared delay; it is not repeated for every
   remaining candidate, counted as a broken market, reported in Discord's
   `Lost to errors`, or replaced immediately by another full scan. A deferred entry intent is recorded
   `FAILED`, never `UNCERTAIN`, because no request was sent; the action loop
   neither reports nor quarantines that market and holds later entry plans
   behind the remaining local cooldown, avoiding repeated failed intents.
   Safety is isolated, may retry inline, and is capped at one second. Telemetry
   records each of the four lane cooldowns plus queue wait, response latency, category,
   failure and 429 counts without another diagnostic request. Attempt,
   response and 429 counts remain local to the reporting process; the four
   cooldown fields merge the bounded shared file deadlines, so a peer-induced
   cooldown may correctly appear beside `rate_limited=0`. The shared snapshot
   uses non-blocking file locking in a worker and falls back to local evidence
   when contended or unreadable, so observability cannot stall the event loop
   or the trading coroutine.
6. Shared file claims perform only one short locked transaction in a worker;
   neither the worker nor the per-process asyncio lock sleeps. Shared 429 writes
   also run off the event loop. The first stop request interrupts an existing
   limiter or read-retry wait, while subsequent shutdown proof/cancel/exit calls
   capture a fresh generation and remain available. A mutation interrupted
   during pre-network admission closes its durable intent as exact `FAILED`;
   it cannot survive shutdown as a false unresolved/uncertain order.
7. Native Windows execution fails closed because `fcntl` coordination is absent.
   Unit tests may inject an isolated limiter, but an authenticated executable
   must run inside WSL rather than silently acquiring an independent budget.

This architecture makes market count create queue latency instead of an API
burst; it does not turn an arbitrary slot count into free capacity. In
particular, a complete REST-only refresh of 22 books cannot fit inside a
five-second freshness target at a strict 4/second ceiling. Stream health,
risk-bearing-market priority and queue-latency evidence therefore remain
separate capacity constraints. The shared file is host-local: a second machine
or an uncooperative external process using the same key/public IP is outside
this coordinator. No local implementation can guarantee that Cloudflare will
never emit the first 429, but cooperating laptop workloads now stop multiplying
the request budget and all observe the same response.

The first `0ff6e3c6` live acceptance supplied an important observability check.
At 19:35:37Z the runner reported `cooldown_background=2.7` and `shared=1`
beside process-local `rate_limited=0`; entry and safety cooldowns remained zero,
and the next window showed all lanes back at zero with no failures. The line
therefore proved that a governing shared background deadline is visible without
inventing a local 429 or blocking safety traffic. The aggregate line cannot by
itself attribute which cooperating process published that deadline, and no
selection deferral occurred, so natural acceptance of the one-event
`[api-backpressure]` path remains open.

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

The 2026-08-31 ET-midnight acceptance exposed an application retry amplifier,
not a higher slot demand or a newly discovered venue ceiling. The runner tried
to refresh non-trading cash while its durable baseline still named the prior
ET day. Its own day-equality check rejected every otherwise complete snapshot,
entry remained fail-closed and one 71.4-second window made 190 read attempts:
78 balance, 26 cash-history and 27 position reads, with 13 confirmed 429s and
zero mutations. The repair advances the durable day from one fresh raw account
mark before the dependent cash scan, then requires same-day cash proof plus a
fresh adjusted mark before entry. This removes the retry loop; it does not
raise any limiter lane, add an endpoint or reinterpret 429 as venue ambiguity.

The offline capacity model deliberately uses more conservative mutation input
than that action-line rate and preserves 25% of the internal 4/s governor:

| Scenario | Explicit assumptions | Result after bounded contract batching |
|---|---|---|
| Normal | 10% of owners need quiet-book REST fallback; `0.10` mutations/s; one execution page; no retry multiplier; bounded hourly report included at its long-run average. | `2.17/s` at 24 and `2.47/s` at 32. The deterministic replay still records ten quiet-book deadline misses at both widths, so average headroom alone is not acceptance. |
| Busy | 25% quiet owners; `0.25` mutations/s; two execution pages; reads multiplied by `1.05`; explicit order-retrieve, lifecycle and recovery traffic. | `4.00/s` at 24 and `4.73/s` at 32. Thirty-two exceeds the internal host gate in this modeled window and must roll back if live tail evidence approaches it. |
| Entire public feed silent under current fallback | Every owner requests a REST book every 3 seconds; two execution pages; bounded hourly report included. | `9.24/s` at 24 and `11.94/s` at 32, proving this is a degraded-feed design limit, not a viable scaling mode. New risk still fails closed on stale books; stream recovery and risk-owner priority, not whole-slate REST polling, are the safety design. |

These results do **not** select 24 or 32 as the final production width.
They prove that 24 is not a documented venue slot limit and that average normal
REST load is not currently the first modeled constraint. A 2026-08-29 point
sample measured the runner at `0.564` of one CPU core at 20 slots; a deliberately
conservative linear projection reaches the `0.75`-core comfort budget at 26
slots. Portfolio policy now derives exposure as `(configured slots + 10) ×
quote size`, so capacity no longer consumes a hidden fixed reserve; at the
selected size-10/32 canary the cap is $420 with $100 cushion. CPU, request
scheduling and the fourth private shard remain independent constraints, not
API limits. The latest steady CPU baseline used for the conservative linear
projection was `0.398` core at 24, giving `0.531` at 32—below the `0.75` comfort
budget but not a substitute for live latency/thermal evidence.

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
The canary started at 04:12:20Z on 2026-08-30 and passed its continuous
30-minute gate across startup and repeated selection bursts. Twenty telemetry
windows recorded 3,392 attempts, zero failures and zero confirmed 429s;
request rates ranged from `1.263/s` to `2.643/s`, maximum wait p99 was `0.226s`,
maximum response p99 was `0.919s` and maximum queue depth was one. The first
startup burst carried 453 attempts and 49 mutations; a later selection burst
carried 43 discovery and 124 book reads. Execution-history reads continued
without the prior `1015` recurrence. One brief acknowledged cancel was normal
in-flight churn and cleared; there was no persistent unresolved/unknown state,
stream drop, decode failure, recovery or safety deadline. This accepts the
bounded canary, not a permanent 22-slot policy. On 2026-08-31 the operator
authorized preparing one 24-slot canary. Twenty-four fills the same three
private shards as `8/8/8`; 25 is still refused. The corrected executable model
includes all three balance/positions/balance mark calls and matches the table
above. The canary remains inactive until reviewed source, positive
flat/order-free proof and a separately authorized restart. That 24-slot canary
later passed and remains the loaded runtime.

On 2026-09-01 the operator chose to prepare one direct 32-slot canary rather
than repeat staged 26/28/30 deployments. Prepared source accepts only whole
widths 20-32 and still defaults a missing/bare setting to 20; 33 is refused
before venue access. Thirty-two uses four private shards of eight, remains
below the public 100-market subscription bound and derives size-10 controls of
`$420` portfolio exposure, `$40` market loss, `$128` account loss, `$896`
seven-day reserve, `$13.33` fee observation and 40 taker exits. Quote size,
ranking, spread/price gates, inventory clocks, mutation authority and all
per-market business decisions are unchanged. The prepared code is not live
until separately reviewed, deployed and restarted from positive flat/order-free
proof.

The direct canary must reduce immediately on any capacity-linked failure or
confirmed 429; `max_waiting > 1` in two consecutive windows; missed reducer,
pre-kickoff or account-proof deadline; market/private stream coverage loss;
persistent `execution_state_unknown`/account ambiguity; unacceptable decision
latency, CPU or thermals; or a decline in reward per resting side-hour. A quiet
normal window cannot waive a busy/recovery breach. The initial response is 24;
22 remains the earlier known-good fallback if the failure matches its rollback
procedure.
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
