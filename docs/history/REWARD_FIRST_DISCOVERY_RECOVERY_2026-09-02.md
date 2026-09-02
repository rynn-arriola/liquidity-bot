# Reward-first discovery recovery — 2026-09-02

## Incident

The live size-10/32 runner remained healthy but its working slate fell to 19
orders across 19 markets. It had no unresolved intent or unknown-order state,
and API windows remained below the shared four-request/second gate with zero
confirmed 429s or cooldown. The failure was inside local discovery.

The last successful full selector pass at 13:12Z saw 47,377 active catalog
rows. Beginning at 13:16Z, `list_sports_markets()` repeatedly exhausted its
fixed 100-page by 500-row bound and raised `market catalog pagination exceeded
max_pages`. Twenty-two failures accumulated by 13:52Z. Every attempt spent 100
background reads and never reached reward, book or replacement selection.
Event-driven owner signals could bypass `next_selection`, so the next long scan
often began within seconds and starved ordinary market service and refill.

## Rejected first repair

Commit `d3782b00d` first replaced the catalog scan with an enumeration of
nominally active incentive groups followed by exact market reads. Unit tests
proved the intended join, but the live API contract disproved its boundedness:
the first authorized startup exhausted the separate 200-by-100 incentive-page
ceiling and failed with `incentives pagination exceeded incentive_max_pages`.
No order had been placed. The supervisor retried the same startup, so Command 1
stopped the supervisor and runner again before proceeding. At 14:56:18Z a
fresh authenticated read proved zero open orders and zero nonzero positions;
the ledger had only `FAILED` or `RECONCILED` intents and no working or
`UNKNOWN_ABSENT` orders.

This is an API-contract correction, not a larger page-limit request. Passing
the documented `statuses=active` filter did not produce a suitably bounded
selection universe: more than 20,000 market groups were still returned. The
documented market-list `categories=sports` filter likewise returned at least
60,000 rows without reaching a short page, and the documented
`sportsMarketTypes=SPORTS_MARKET_TYPE_MONEYLINE` combination returned only one
row despite hundreds of locally observed open moneylines. None of those
observed-unreliable filters now authorizes discovery.

## Corrected repair

Live discovery pages the documented active/open sports-events endpoint over a
bounded event-time superset, flattens its nested market metadata, applies the
existing market-level lifecycle, six-hour, V2-moneyline, tick and minimum-size
gates, and only then makes symbol-filtered incentive reads for that exact small
set. Reward periods, catalog spread, full-book, economics and ranking gates
remain unchanged.

The event clock is only a discovery hint. A narrow exact-six-hour event query
missed seven locally executable delayed/rescheduled markets because event
`startTime` lagged nested market `gameStartTime` by roughly a day. The selector
therefore queries one day before and one day after the strategy horizon, then
continues to use `market.game_start` as the execution authority. The event
adapter follows `offset` pagination until a short page and fails closed after
ten pages instead of silently accepting a truncated window. Every successful
full scan emits `event_pages` and `event_page_ceiling` in `[selection-scan]`,
along with event/executable/rewarded/book/viable counts, so a rising page trend
is visible before the next hard ceiling. Telemetry is observational: missing
optional counters default to zero and cannot block selection or order flow.

Live read-only comparison at 14:48Z found 263 exact-window events and 221
executable markets. The complete one-day-buffered event set had 736 events and
228 executable markets, exactly matching the executable set found by a
complete all-active-sports-event census while avoiding that census's 2,148
events and 72,954 nested rows. A later end-to-end run of the corrected selector
returned 7,961 nested market rows, 226 executable and 226 rewarded markets with
two event reads, six symbol-filtered incentive reads and one deliberately
limited book read. It did not call the global market or incentive enumerators.

The run loop also records one retry-not-before time after a top-up failure.
Ordinary, lifecycle, reward-end and post-flat triggers all honor it, so a new
owner signal cannot immediately restart failed discovery. The signal is delayed
rather than treated as a broken market, and normal order/position service runs
between attempts. API admission deferral retains its server-derived delay;
other failures use 60 seconds.

No quote price, target size, capacity, reward formula, ranking key, position
service, exit clock, ownership proof, risk limit, mutation authority or API
rate changed. The existing shared top-up retry floor from `d3782b00d` remains
part of the corrected repair.

During verification the still-old live build received a private WebSocket
disconnect at 14:08:16Z. Its existing safety recovery quarantined the slate at
14:08:18Z, explaining the operator's brief zero-order observation, then rebuilt
quotes without a process restart. A fresh authenticated snapshot during the
rebuild found 15 orders across 15 markets and three managed positions. The
later reconciled ledger held 23 working rows across 16 markets, zero unresolved
intents and zero nonzero positions. The old runner continued hitting the catalog
ceiling afterward, so this recovery does not accept the prepared source fix.

## Verification contract

Focused tests cover event offset pagination and its hard ceiling, the exact
one-day discovery bounds, local structure/horizon filtering before reward
fan-out, empty/non-rewarded sets, unsupported lifecycle suppression,
scan-level API backpressure, unchanged selector gates and the retry floor
against an urgent owner signal. The focused discovery slice passes 94/94, and
the affected adapter/selector/census/runner group passes 501/501. The complete
pinned-WSL suite passes 1,061/1,061 in 102.184 seconds. Deliberately removing
the one-day clock-skew buffer, complete event pagination, exact-symbol
incentive lookup or the page-headroom telemetry made their exact regression
tests fail; all four passed after restoration. The full suite also exposed and
then verified a defensive logger fallback for legacy/minimal selection test
doubles. The Markdown audit covers 81 files and 961 mandatory lines with zero
issues.

The reported startup-blocking PLACE for
`aec-setkameua-kvaiho-slomyk-2026-09-02` was rechecked before deployment. Its
latest PLACE intent is durably `FAILED`, its other intents are `RECONCILED`,
its orders are terminal, and the account-wide ledger has no unresolved intent
or working/unknown order. The stopped-only orphan resolver is therefore neither
needed nor authorized for this restart.

Natural acceptance after the already-authorized Command 1 restart requires a
successful selector pass without either catalog or incentive page-ceiling
error, a low `event_pages` value against the ten-page ceiling, refill progress,
continued position/order service, no failure fan-out, and clean API windows.
Do not induce venue failure or a position to test it.
If acceptance fails, stop and remain flat; `d3782b00d` is not an operational
rollback because its live startup contract is already disproved. The last
earlier build also retains the now-overflowing global catalog scan, so this
incident requires a fix-forward rather than an automatic code rollback.
