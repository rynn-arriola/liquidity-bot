# Normal startup scan reproduction — September 13, 2026

Operator asked whether the rate-limit behavior can be tested and whether recent
fixes caused it. Diagnostic only: bot stayed stopped, no live source/config/risk
change, deployment, key replacement, order mutation or ledger write.

## Bounded live read-only reproduction

Ignored `.local-tools/stopped_book_recovery_test.py --startup-pattern` runs ONE
actual `RewardMarketSelector.select` using the current size10/32 selection policy
and existing background2/s lane under the shared4/s host governor. This differs
from the earlier report-lane0.25/s test. It does not run the trading engine,
account watcher, execution paths, continuous loop or full startup gates.

GET-only hook, stopped-process checks before each call/network request, one
attempt per call,130-request/120-second bounds, abort on first HTTP error or
admission deferral. No cooldown reset, hidden extra retries or alternate key/IP.

Started04:34:38.510244Z. Ended04:34:55.724609Z after17.22seconds:

| Calls | Result |
|---|---|
|3 event pages|HTTP200|
|9 incentive calls|HTTP200|
|First5 books|HTTP200|
|Sixth book|HTTP429, `Retry-After:10`|

Total18GETs,17successes,1failure,0mutation requests. Book response times were
04:34:53.177324,53.676738,54.170116,54.702161,55.175002 and55.670260Z.
These are response times, not a packet-level capture of all IP request starts.
Failed endpoint:
`GET /v1/markets/aec-setkameua-marand-solser-2026-09-13/book`.
Host `gateway.polymarket.us`; Ray `a3a4713999a11906-EWR`.
No response body or authentication secret was printed. This capture proves429,
not a newly inspected Cloudflare1015 body. Whole scan stopped immediately;
no subsequent probe or account read was stacked after the rejection.

## Fix attribution and evidence limits

The first reported September12 public-book rejection at20:47:40Z happened on
`a8888b5e5`, before book-specific backoff `0323dc4c6` and just-in-time book
service `4a166336a`. Their diff affects adapters, runner and REST dispatch,
not market-selector code or its2/s scheduling. They cannot have caused that
earlier onset, but their presence does not establish recovery.

Earlier local defects DID amplify demand: inline rejected-book retries and
full-slate book reads aging before decisions. Those are proven and repaired;
the first venue rejection's exact threshold remains unproven. Broader REST
adaptation/performance changes may contribute demand, but the different market,
load and venue conditions prevent attributing the original trigger to one
commit from timing alone. Historical429s also exist before REST mode.

Fresh native offline unittest checks passed10 book-backoff,11 REST-scheduling
and8 JIT-book tests. The32-owner JIT case confirms one decision book per owner
after slow account proof; safety/failure/stop cases remain covered. These are
mocked tests, not live execution/load acceptance. Initial pytest invocation
could not run because pytest is not installed; project unittest runner passed.

The current public status reports operational and the re-opened
[changelog](https://docs.polymarket.us/changelog) still lists Sept10v0.0.88.
The [Retail rate page](https://docs.polymarket.us/api-reference/rate-limits)
does not identify the matched edge rule. Six books is NOT a proven quota;
prior rolling/IP traffic and cache/rule behavior are uncontrolled.

## Next decision

Normal selection traffic alone is sufficient to encounter the current limit;
active trading/position mutations are not necessary to reproduce it. Review
startup request concentration/pacing without delaying mandatory position/cancel
proof; seek venue rule scope/window/threshold using the Ray. No repeated
threshold hunt, source/key rotation or restart until reviewed and authorized.
No new account proof: latest complete orders/positions zero04:22–04:23Z;
latest ledger ambiguity proof zero04:16Z. Runner/supervisor absent throughout.
Pre-work laptop parity passed, desktop offline/pending; marker4a166336a unchanged.
