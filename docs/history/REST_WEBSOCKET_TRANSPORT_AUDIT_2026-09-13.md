# Whole-bot REST/WebSocket transport audit — September 13, 2026

## Scope and conclusion

Operator requested a review of the overall code for remaining REST-to-WebSocket
conversions. This is a transport/call-path audit, not a claim that every line of
the project has passed a new correctness audit. No runtime implementation,
configuration, source switch, risk change or restart was authorized/performed.

The high-impact public-book conversion is already loaded. No further large
public-stream replacement for the remaining account calls was found in the
reviewed Retail contracts. Private order/fill/position/balance streams are the
logical future replacements for frequent account polling, but the latest
19:34:50–19:35:35Z read-only private test got zero application frames across all
three subscriptions despite successful handshake and initial/final Pong.
Therefore they cannot currently supply trading authority. Public trade prints
do not identify this account's fill, order ownership or cash movements.

## Code and contract map

| Work | Current path | WebSocket decision |
|---|---|---|
| Selection, startup and active-owner books | `adapters.py:1804`, `selection_books.py:108`, `refreshing_books.py:253`; runtime resolves the current generation-fenced stream | Already uses public full-depth books. Keep bounded REST for unavailable candidate data and unbound/recovery/teardown proof. No normal REST-book calls occurred in the measured run. |
| Best prices and market statistics | Full-book parser `adapters.py:278` and `streams.py`; full depth is required for reward scoring and exact exit liquidity | Already available in the full public stream. Adding lite/BBO does not remove an existing separate REST poll; lite is not a substitute for exit depth. |
| Open orders and individual order status | `rest_fallback.py:167,230`, `account_source_runner.py:325,360`, `live_orders.py:316` | Private order snapshots/updates could replace many steady reads when healthy. Startup, missing/ambiguous orders and post-gap reconciliation still need positive proof. A REST404/open-list omission cannot become a cancel confirmation. |
| Own executions and trade lifecycle | `account_snapshots.py` execution collector, `account_source_runner.py:396`, `live_commissioning.py:2505` | Private order executions can carry live fills. Keep bounded historical backfill/reversal handling; public trade notifications are not own-account executions. REST mode currently requires history coverage. |
| Position quantities and scan interruption | `account_source_runner.py:241,360`, `account_observations.py` PositionWatch, `account_snapshots.py:352` | Private scoped positions are the intended live source. Current scan watch is a wakeup only, not authority to close/free a position. Cannot remove it while private delivery is absent. |
| Cash, buying power and marked risk | `account_snapshots.py:388,416`, `live_commissioning.py:4393,4560` | Private balance data is useful when healthy, but the current marked-risk path still performs REST in BOTH modes. Stream cash alone is not a proven replacement for venue-valued positions and classified cash flow. |
| Catalog, exact pregame proof and market metadata | `market_selector.py`, `event_guard.py:135`, `adapters.py:1795,1870,1934` | No equivalent complete Retail catalog/pregame stream identified. A public book's OPEN state is not proof the sports event has not started. Retain exact parent live/score/period checks. |
| Reward programs and earned rewards | `adapters.py:1623,1754`, report/accounting scripts | No equivalent subscribed Retail rewards channel identified. Keep bounded program/earnings reads; reward estimates cannot stand in for pending/paid account earnings. |
| Deposits, withdrawals, transfers and reporting history | `cash_activity.py`, `accounting.py`, `reward_table.py`, `daily_pnl.py`, `dashboard.py` | Private balance changes may help in future, but a live delta is not complete dated cash history or recovery coverage. Keep classification/backfill; do not count transfers as trading loss. |
| Place, cancel and IOC exit submission | `live_orders.py:294,303,429` | Retail trading mutations remain REST. WebSocket confirms events rather than replacing these commands. Embedded execution responses are already processed; this is not an unused optimization. |
| Settlement, maintenance and UI participation | `live_commissioning.py:7441`, maintenance/status/UI observers | Keep exact settlement/status and independent application evidence. No equivalent public-book proof of settlement/flatness or customer trading availability. |

Reviewed official sources: [Retail market streams](https://docs.polymarket.us/api-reference/websocket/markets),
[Retail private streams](https://docs.polymarket.us/api-reference/websocket/private),
[documentation index](https://docs.polymarket.us/llms.txt),
[create order](https://docs.polymarket.us/api-reference/orders/create-order), and
[earned rewards](https://docs.polymarket.us/api-reference/incentives/get-your-incentive-earnings).
The public channel lists full books, lite prices and trades. The private channel
lists orders/snapshots, positions, balances and allowlisted RFQ events; RFQ is not
an alternate balance/position feed. No Institutional FIX/gRPC/Drop Copy interface
is assumed accessible to this Retail account.

The [changelog](https://docs.polymarket.us/changelog) and
[status page](https://status.polymarketexchange.com/) were also reopened.
Current changelog v0.0.90 announces September14 04:00–07:00 ET maintenance;
v0.0.89 concerns DAY rollover cancellation, not a relocated private account
stream. The current bot uses post-only GTC and IOC, not DAY. These announcements
do not explain the account-specific observed stream delivery, and no outage or
schedule change was injected into the bot by this review.

## Measured priority, not a predicted saving

Reused the same 46 completed runner API windows through19:29:30Z, totaling
10,067 attempts over3,331.3s. This is process-local telemetry, not exchange-wide
or other-process load. No additional quota test was performed.

| Category | Attempts | Share of all attempts |
|---|---:|---:|
| Individual order reads | 3,249 | 32.3% |
| Execution-history reads | 1,574 | 15.6% |
| Open-order lists | 1,414 | 14.0% |
| Position reads | 1,202 | 11.9% |
| Balance reads | 1,120 | 11.1% |
| All remaining categories | 1,508 | 15.0% |

These five account categories total8,559 (85.0%). That is NOT an85% achievable
reduction: proof/recovery, cash accounting, initial snapshots and mandatory
history still need reads. Individual-order counts include required proofs and
retries; aggregate categories do not show how many calls are redundant.
The remaining categories include509 mutation calls,371 cash-history reads,
623 metadata/discovery/program reads, four earnings calls and one other read.
Two observed429s were positions/balance, not market books.

## Recommended next work, requiring implementation authority

1. Keep public full-depth renewal and explicit REST account selection now.
2. Extend existing account-read coordination only where equivalent proof can
   safely be shared. The project ALREADY has concurrent bounded snapshot
   collection, bulk unchanged-flat order shortcuts, inline execution ingestion
   and a coalesced just-completed equity refresh. Do not rebuild those features
   or promise generic caching will solve rate limits.
3. First separate exact-order call reasons (startup/top-up, periodic, changed,
   absent, cancel confirmation and retry) using local evidence or failure-isolated
   instrumentation, not more venue probes. Focus on repeated lifecycle/top-up
   proof: it is both expensive and outside the ordinary flat-order shortcut.
4. Consider a shared immutable observation across reconciliation/position-watch
   consumers only when source generation, account scope, complete pages and
   original timestamps match, with no intervening mutation or conflicting
   event. Mutations/unknown state/reconnect invalidate reuse. Urgent exits must
   preempt lower-priority collection; workers never write the ledger.
5. Preserve the marked-equity cash bracket. `fetch_equity_snapshot` reads cash,
   positions, cash; `_refresh_balance` then classifies cash history and reads
   cash again before publishing. Those repeated balance reads are consistency
   guards, not duplicates that can simply be deleted. An older position page
   from outside the bracket cannot be substituted to save a call.
6. When private delivery returns, verify snapshots/updates, EOF, exact scoped
   positions, original times, duplicate/conflicting events and final REST
   agreement before an operator-requested source switch. Do not automatically
   promote a Pong-connected socket. A mixed order-only acceleration mode would
   be a separate design, not an undocumented silent source switch.

Optional smaller opportunity: public book state can provide an additive
HALTED/CLOSED warning, but cannot replace exact pregame metadata. It is not
a material REST-volume reduction and should not displace account-read work.

Acceptance for any follow-up: prove actual calls saved by reason, unchanged
prices/size/ranking/$90 HARD/caps, bounded fill-to-observation-to-exit timing,
complete histories/transfer exclusion, missing-order/partial-fill/404/429 safety,
scope changes, midnight, reconnect and cancellation. Regression/reversal tests
and full suite are required before a separately authorized release. Savings,
full32-owner behavior and WebSocket-era latency remain unmeasured.

## Live state and review boundary

Same runner4022412 stayed running on source `b2b0d017e` with size10/32 REST.
Authenticated19:39:53.526028Z:34 orders across25 markets, comprising30 flat-market
entries and four position-market orders, four nonzero positions, zero unresolved
intents/unknown orders; ledger counts agree. This is a timestamped observation,
not a claim that every position order's reducer intent was audited by that read.
No runtime source/tests/config changed; no bot stop/restart or trading mutation
was performed. Review/handoff documentation only was committed. No full suite
was rerun or new live private probe stacked on the already completed45s test.
