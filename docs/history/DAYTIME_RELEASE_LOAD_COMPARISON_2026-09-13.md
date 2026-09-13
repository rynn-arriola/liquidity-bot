# Daytime release versus approximately 3 a.m. REST — September 13, 2026

## Scope, time interpretation and state

Operator asked specifically about the large daytime changes, stating the bot
worked from around3a.m. UNTIL THE NEXT RESTART, not only during a startup probe.
The comparison is September12 Eastern time, stated in conversation. Logs record
an early03a.m. source and a04:02 restart whose full morning run lasted until the
daytime stop around11:35. Both early and full-morning samples are reported;
do not merge them into one uninterrupted process or substitute pre-WebSocket
stable code for this already-REST baseline. If the date is clarified, adjust it.

Read-only diagnosis only. No new source/test/config changes, deployment,
restart, trading API calls, ledger repair or rollback. Earlier optional-pacing
drafts remain untouched and uncommitted. Runner/supervisor absent at the fresh
WSL process check around05:25Z September13; marker4a166336a unchanged. Last
authenticated orders/positions zero04:22–04:23Z; last ambiguity proof04:16Z,
not refreshed here. $90HARD, REST, size10/32 and30flat-entry ceiling unchanged.

## Fresh reconstruction of existing production logs

Ignored `.local-tools/day_change_request_audit.py` reads only existing
`logs/commissioning.log`, no bot imports/network. It aggregates `[api-budget]`
records only when both the reported window start (end minus elapsed seconds)
and end fit the named UTC interval. Values below are completed-window averages,
not instantaneous or full-IP packet rates. Sources/date boundaries come from
release acceptance records, not merely Git commit timestamps.

| Phase, September12 ET | Loaded source | Windows | Attempts / summed seconds | Requests/s | Recorded429 |
|---|---|---:|---|---:|---:|
|Around03:00, selected07:00–07:35UTC|eb06d1b9c|11|2,537 /1,276.5|1.987|0|
|04:00–05:00|b826ff11e|22|5,950 /3,434.8|1.732|0|
|04:00–11:34, overlapping broader baseline|b826ff11e|192|47,618 /26,946.4|1.767|0|
|Initial daytime release11:56–12:05|908ba6dd6|2|750 /236.0|3.178|0|
|Daytime12:30–13:53|2f7ba0222|54|17,249 /4,858.5|3.550|5|
|Afternoon14:00–16:47, before first book-failure window|a8888b5e5|107|34,766 /9,958.5|3.491|1|

03a.m. budget records end07:17:06–07:33:58UTC; the earlier start of the first
reported window explains why summed duration exceeds last-end minus first-end.
The broad baseline includes the narrower04a.m. sample: do not add them.
Zero recorded429 is not zero errors: the broad baseline includes125 exact-order
404s and40 cash/history failures with unknown status. The earlier03a.m. source
did not yet attribute failure categories; its aggregate429 count is zero.

Comparing the broad same-source morning baseline with the daytime second run:

| Category | Morning requests/s | Daytime requests/s |
|---|---:|---:|
|Public books|0.726|1.414|
|Exact order retrieval|0.281|0.510|
|Execution history|0.162|0.391|
|Open orders|0.143|0.364|
|Positions|0.126|0.300|
|Balances|0.135|0.233|

Overall recorded request throughput approximately doubled (3.550/1.767).
This directly supports the operator's observation of a before/after load
change. Markets, positions, history size, time and venue conditions are not
held constant; it is not proof that one commit caused a particular edge rule.

First daytime recorded429 window ends16:42:59.072920UTC /12:42:59ET and
attributes one to `balance@429`. Further windows at17:07:48,17:08:50,17:10:27
and17:18:16UTC contain2cash-history and2balance429s. These precede the
afternoon urgent-exit release. The later reported public-book/Cloudflare1015
onset is20:47:40UTC /16:47:40ET on a8888b5e5, as previously reconstructed.
Do not conflate the first account429 with the later public-book incident.

## What the large release changed

The around03a.m. source already had permanent manual REST, bulk order proofs,
bounded concurrent collectors and configured5/1-second polling. The04a.m.
b826ff11e follow-up fixed immediate cancel retries. Neither REST itself nor
the5/1-second policy was newly introduced by the later daytime release.

The large package prepared through8ab1738bf and released in908ba6dd6 at
15:56:29UTC /11:56:29ET included:

1. **A proven CPU bottleneck removal:**93ec46391 adds the exchange-ID-leading
   intent index. Recorded same-data127-lookups fell from approximately2.9s
   to below1ms. The lookup sits under order/history normalization. It adds no
   endpoint request but removes time that previously slowed the whole loop.
2. **A faster working history query:**908ba6dd6 filters execution-history
   pages to trades. The release investigation measured unfiltered timeouts
   around15.15s versus successful filtered reads around0.21–0.28s. Restoring
   unfiltered timeouts is not an acceptable load-control strategy.
3. **Extra position observation during scans:**8ab1738bf adds one bounded
   in-flight watcher at existing5/1-second cadence. It protects fills from
   discovery delays but consumes some additional account budget.
4. **Safety/proof changes:** clock-safe final admission, original equity age,
   serialized identity adoption and retained private events. These affect
   correctness and scheduling, not a higher configured wire-rate ceiling.

No config diff exists from b826ff11e through a8888b5e5. Inspection of adapter
changes in that range finds telemetry/comments and scan checkpoints, not a
raised aggregate rate. The committed selector remains unchanged. These facts
do NOT rule out increased real request throughput from faster surrounding work.

**Best-supported hypothesis:** removing CPU/query delays allowed the existing
proof/book cycle to run much nearer the unchanged4/s governor, while the scan
watch added traffic. The measured before/after rates support this mechanism;
they do not isolate each contribution or establish Polymarket's quota.

The urgent-position pass in a8888b5e5 was deployed at18:00UTC, AFTER the
first daytime account429s. It can change ordering/load for due positions but
cannot explain those earlier failures. Later book429 backoff0323dc4c6 and
JIT-book4a166336a also postdate the initial public-book rejection.

## Correction to earlier explanation and next step

"The selector did not change" was too narrow to answer the user's question.
The appropriate baseline is the earlier working REST release, and the logs
now show a substantial throughput increase after the daytime package. Keep
the local regression/performance explanation open; do not default to a venue
outage or claim that REST was always producing this failure.

Recommend target request scheduling/call amplification enabled by the speed-up,
preserving fast CPU work and exact exits. Do not blindly roll back all daytime
safety fixes or remove the index to make the bot artificially slow. The older
release had documented position/history/admission defects. A full rollback is
neither authorized nor shown safe. Optional-entry/discovery load controls need
separate design/review; account-level freshness and exits cannot be traded away.
No new fix is selected or accepted by this diagnosis.

Current public status at05:25:57.6910619Z still reports operational; the reopened
[changelog](https://docs.polymarket.us/changelog) still leads with Sept10v0.0.88.
Neither identifies the current limit. Support's exact recorded Ray investigation
remains useful, but must not substitute for the now-demonstrated local load shift.

Evidence routes: [03/04a.m. release](ACCOUNT_SOURCE_RELEASE_2026-09-12.md),
[CPU measurement](READINESS_PERFORMANCE_FIXES_2026-09-12.md),
[daytime deployment/history query](ACCOUNT_PERFORMANCE_RELEASE_2026-09-12.md),
[scan watch](ACCOUNT_WATCH_EXIT_REVIEW_2026-09-12.md),
[afternoon release](REST_THROTTLE_EXIT_SCHEDULING_2026-09-12.md),
[initial book incident](PUBLIC_BOOK_BACKOFF_2026-09-12.md).

Pre-work parity: tracked laptop/GitHub/WSL820ddf08 match, production marker
4a166336a; paused local code drift and offline desktop explicitly pending.
Only this diagnosis/handoff is committed and synchronized, not the code drafts.
