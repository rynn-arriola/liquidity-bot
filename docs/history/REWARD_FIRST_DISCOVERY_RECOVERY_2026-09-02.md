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

## Repair

Live discovery now starts from the required active liquidity-program universe,
then fetches exact market metadata for those slugs in batches of at most 100.
The candidate set is equivalent to the old intersection: a market without an
active reward program could never authorize an order. All existing lifecycle,
six-hour clock, V2 moneyline, tick, minimum quantity, spread, current-period,
full-book, economics and ranking gates remain in the same order after the
intersection. Missing exact metadata remains ineligible.

The run loop also records one retry-not-before time after a top-up failure.
Ordinary, lifecycle, reward-end and post-flat triggers all honor it, so a new
owner signal cannot immediately restart failed discovery. The signal is delayed
rather than treated as a broken market, and normal order/position service runs
between attempts. API admission deferral retains its server-derived delay;
other failures use 60 seconds.

No quote price, target size, capacity, reward formula, ranking key, position
service, exit clock, ownership proof, risk limit, mutation authority or API
rate changed. The live runner was not stopped, restarted or deployed while the
repair was prepared.

During verification the still-old live build received a private WebSocket
disconnect at 14:08:16Z. Its existing safety recovery quarantined the slate at
14:08:18Z, explaining the operator's brief zero-order observation, then rebuilt
quotes without a process restart. A fresh authenticated snapshot during the
rebuild found 15 orders across 15 markets and three managed positions. The
later reconciled ledger held 23 working rows across 16 markets, zero unresolved
intents and zero nonzero positions. The old runner continued hitting the catalog
ceiling afterward, so this recovery does not accept the prepared source fix.

## Verification contract

Focused tests cover active-incentive enumeration, 100/100/5 metadata batching,
missing exact metadata, unsupported lifecycle suppression, scan-level API
backpressure, unchanged selector gates and the retry floor against an urgent
owner signal. The selector/top-up/census slice passes 83/83 and the complete
pinned-WSL suite passes 1,057/1,057. Deliberately widening metadata batches to
101 failed the exact boundary assertion; deliberately removing the retry floor
made an urgent owner signal bypass it. Both tests passed after restoration.

Natural acceptance after an authorized restart requires a successful selector
pass without `market catalog pagination exceeded max_pages`, refill progress,
continued position/order service, no failure fan-out, and clean API windows.
Do not induce venue failure or a position to test it.
