# Readiness repairs and measured CPU bottleneck — September 12, 2026

## Scope and operating boundary

Continues the [whole-bot fixes](WHOLE_BOT_FIXES_2026-09-12.md) and
[second readiness review](LIVE_READINESS_REVIEW_2026-09-12.md). The operator
authorized fixing confirmed relevant issues without dropping earlier work when
the conversation is steered. This does not authorize deploying, restarting,
stopping, changing risk or altering venue orders. All repairs here are prepared
offline; the loaded runtime remains `b826ff11e`, REST size 10 / 32.

## Readiness A: validate the clock after collecting inputs

The final entry check now collects scope, event metadata, position, ownership
and pending-entry reservations before sampling the current time. It evaluates
the original risk/source/event/book/deadline/cap conditions after that collection.
The REST wrapper also performs its proof work before the final callback. No
unused full planner frame is rebuilt at the last network boundary; planning and
exposure checks still run normally earlier in the same serial engine.

The extracted in-memory session-evidence predicate does not grant ownership:
ordinary `session_ready` still checks it, and the final entry check separately
requires the collected ownership proof. The current SUBMITTING reservation is
counted once. Reducers and cancels bypass only the flat-entry gate, not their
existing exact-owner authority.

Regression coverage includes both sources, 0/4.99/5/5.01/16-second local delays,
source/event/equity expiry, kickoff, ET midnight, stop, cap and changed position.
Five seconds remains the inclusive book-age bound; expired evidence cannot be
made fresh by slow local computation. This cannot make remote submission atomic.

## Readiness B: critical NOT-SENT is distinct from ordinary delay

`OrderAdmissionFailed` retains critical local escalation and the original cause
while proving the request never reached the client's network call. Both place
and cancel persist FAILED before rethrowing it. Ordinary admission deferrals
remain short recoverable delays. Exceptions after the network boundary still
produce UNCERTAIN, with no automatic create retry. A failed cancel cannot erase
the original placement's unresolved ownership.

Eight additional readiness tests brought the boundary module to 27 tests. It
passed in 6.221 seconds. Two deliberate reversal rounds produced ten expected
failures/errors; all reversals were restored. The complete readiness-only suite
passed **1,496 tests in 139.931 seconds** before performance changes.

## Performance: evidence selected the repair

A native isolated derivative of the verified 13:57:31Z online ledger backup
was profiled without live credentials or mutation capability. A 32-market frame
sweep with representative fixture books took 0.089602 seconds; full-history
realized P&L took 0.036890 seconds. This did not explain the large production
CPU windows and does not represent a full live-market replay.

A separate 45-second, 10-Hz nonblocking `py-spy 0.4.2` sample of runner `649372`
captured 191 active stack samples, with three sampling errors. **182 samples
were at `resolve_client_order_id`'s exchange-ID intent query**: 124 under history
execution normalization and 58 under open-order normalization. These are sampled
stacks, not 95% of all wall time or a complete latency attribution. The profiler
was installed only in an isolated `/tmp` directory; production dependencies and
source were not changed. No locals or credentials were collected. Nonblocking
sampling avoids pausing the target but can return incomplete samples; see the
[profiler's documented behavior](https://github.com/benfred/py-spy#how-can-i-avoid-pausing-the-python-program).

The query searched 117,626 historical intents without an exchange-ID-leading
index, then sorted matching rows. Every normalized known order/fill can repeat
that work. The prepared additive index on `(exchange_order_id, created_at)`
retains the exact query, earliest-intent semantics and rowid tie ordering. It
does not cache identities, skip ownership checks or infer absent orders.

On the isolated backup, three identical 127-lookup sweeps measured:

| State | Sweep seconds | Query plan |
|---|---|---|
| Before | 2.914599 / 2.882445 / 2.914749 | Full intent scan plus temporary sort |
| Indexed | 0.000550 / 0.000359 / 0.000284 | Ordered exchange-ID index search |

Every returned identity matched. Building the test index took 0.095126 seconds;
the diagnostic transaction was rolled back afterward. These are local lookup
costs, not an achieved fill/exit speedup. The index benefits shared normalization
in both account modes and adds no API request. Writable startup adds it to old
ledgers; read-only reports do not migrate. It adds storage and small index-update
costs, and startup must finish its normal gates before trading.

Three regressions cover the indexed query plan, duplicate/time-tied identities,
unknown/empty IDs, order-table fallback, read-only nonmigration and writable
reopen. The 65-test ledger module passed. Removing the index deliberately made
the query-plan regression fail with full scan/temporary sort; the index was
restored before the combined suite.

The exact combined offline suite passed **1,499 tests in 142.888 seconds**.
All eleven new deliberate reversal detections were restored before this run.
The source/test diff and `git diff --check` were reviewed afterward.

## Dedicated final edge-case audit

- Success/empty/full: normal entry remains possible, stale or missing proof
  refuses, current reservations count once, and positioned orders/reducers/exits
  remain outside the 30-flat-entry ceiling. No size/cap/loss-policy change.
- Timing/races: all synchronous ledger reads precede the final entry clock;
  REST and source-generation wrappers finish before it. No await is introduced
  between that clock and the client's call. Existing critical stop paths remain.
- Partial/unknown/ownership: exact ledger identity resolution and adoption logic
  are unchanged by the index. Existing positive terminal proof, fill conflict,
  missing-history and ambiguity protections remain. No negative absence inference.
- Failures: local callback failures cannot fabricate a remote uncertain send;
  post-network uncertainty cannot be mislabeled NOT-SENT. Cancel and reducer
  bypass do not grant new entry or reset IOC attempts/deadlines.
- API/load: unchanged 4/s shared governor and 5/1-second targets, no added reads,
  retries, threads or second writer. Index costs are additive; no archival row is
  deleted. Full-width live tail latency still needs natural acceptance.
- Recovery/shutdown/restart/rollback: earlier private-tail/fill-conflict repairs
  remain; source selection is manual, no permanent inactive stream is added.
  The additive index is compatible with old readers and rollback source, but
  rollback would restore old readiness defects. No live migration was performed.
- Final source/test diff and whitespace review found no further confirmed issue
  in this patch. Passing tests are not proof of future venue availability or
  guaranteed execution, rewards or profitability.

## Remaining performance work, not silently discarded

The dominant measured CPU scan is now repaired offline. The proposed single-flight
account watch, bounded/interruptible catalog scheduling and transition-specific
latency instrumentation remain follow-on work; they are not claimed implemented
by this index. Preserve the original order of immutable collection followed by
one authoritative projection/executor. In particular, current normalization can
call `resolve_client_order_id`, whose unresolved-intent adoption branch writes
the ledger; even a method named `fetch` is not automatically a pure collector.
EventAwareGateway also projects durable event facts. Neither may be moved to
a background writer unchanged.

Use this measured correction as the next baseline before attributing remaining
delay or changing cadence. At unchanged rates, REST still waits for a poll and
response; it cannot guarantee the push-delivery speed of healthy WebSocket.
Deployment/restart and live performance acceptance remain separately gated.

## Live observation during verification — unresolved exit, not acceptance

The unchanged runner remained alive at 14:22Z. Authenticated reads at
14:20:09Z showed 28 flat orders/one position, then **14:22:28Z showed 27 flat
orders and a -6.0300 position with no resting reducer**. Its builder cancellation
and reducer replacement were logged; the pre-kickoff/timed-exit sequence canceled
the reducer at 14:20:00Z. Three IOC actions followed at 14:20:49.219,
14:21:03.250 and 14:21:10.912Z (21.7 seconds first-to-third). Ledger terminal
observations recorded all three EXPIRED with zero fill, buy-long limit 0.26 and
quantity 6.03; each durable intent was RECONCILED. This is not an ambiguous
missing-order inference or proof that the latency repair is live.

At 14:21:19Z the existing bounded recovery retired this owner for exhausted
attempts while keeping it managed. A separate authenticated book read at
14:23:23Z still showed bid 0.25/1505 and offer 0.26/15. That later displayed
depth cannot prove executable depth at an earlier IOC. The discrepancy needs
follow-up; neither an API error nor a local price defect is established from
these observations alone. Do not widen the price cap, reset the live ledger or
manually close under review-only authority. The operator was explicitly told
the position remained open. Preserve this incident for the remaining exit and
scheduling work; it is not a successful flatten/healthy-live sign-off.
