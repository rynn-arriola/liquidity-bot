# Exit scheduling review — September18, 2026 ET

Scope: operator requested review, not implementation. Trading source, settings,
orders and processes were not changed. Live source remains `aae337658`.

## Confirmed findings

1. **High priority — building-only orders evade urgent inventory coverage.**
   `rest_fallback.py:_position_priority_must_yield` checks whether *any*
   working order exists, not whether an exact reducer exists. A long position
   with only a BUY entry and a future deadline returns false. The ordinary
   reconciler still cancels that builder when visited; the defect is loss of
   urgency before periodic work. `_position_near_its_flatten_deadline` contains
   the same any-order shortcut. Wrong-side/undersized orders merit coverage
   tests in the repair, not an assumption that all working orders protect risk.
2. **High priority — covered positions can precede new uncovered fills.**
   `live_commissioning.py:_decision_sweep` considers other priority owners when
   the current owner is flat, but proceeds with a current positioned owner.
   A fake account refresh discovering a new building-only position while an
   already covered position is being visited evaluates the covered owner first.
   Immediate same-owner cancel/replace continuation can further consume time.
   Existing tests prioritize positions over flat entries, not these urgency
   classes among positions.

Two isolated fake-client reproductions in the ignored local review harness
passed, confirming current behavior without venue mutations. The117 existing
reconciler/REST-priority/reducer-continuity tests passed; separate self-chase
tests were also run. These are diagnosis tests, not proof of a repaired build.

## Live timing and uncertainty

Budole/pytiva position-4.03 observed00:53:54.621396Z September19 UTC.
Builder cancellation sent00:54:13.800681Z, acknowledged00:54:13.853757Z:
19.232361 seconds after initial observation. Positive cancel observed
00:54:17.593716Z; BUY reducer sent00:54:21.375979Z and acknowledged
00:54:21.446498Z, total26.825102 seconds. Hitsat/jiayua repeatedly repriced its
SELL reducer during that interval. The code gaps are consistent with delayed
priority, but do not attribute every elapsed second to one cause or prove loss.

Repeated SELL replacements at the same0.85 price were observed. Existing code
retains exact-size reducers already at current touch or desired improved price.
Logs do not capture the cancel-time touch/desired-price inputs needed to prove
each reprice unnecessary. Do not remove repricing or alter exit-price policy
solely from repeated replacement prices.

Correction to earlier status: the00:53:19.295220Z API window contained one
`balance@429` among263 attempts over77.7s (3.384/s), plus five order404s.
Later00:54:40–00:57:44Z windows recovered with zero429. This does not establish
the429 as the cause of the26.825s delay. Latest completed01:19:01–01:27:39Z
windows had zero429/cooldown, approximately3.221–3.530 requests/s; individual
order404s remained. No venue-fault or permanent zero429 claim is made.

## Recommended repair, not yet authorized

Use explicit urgency classes: due hard exits, new fills with builders or
missing/incorrect reducers, then optional reprices of already covered inventory,
then flat-entry work. Apply consistent exact-coverage evidence to the pre-periodic
urgent pass, discovery deferral and inter-position scheduling. Preserve cancel
terminal proof, unknown-state refusal, per-owner backoff, bounded fair visits,
stop handling, hard-exit budgets and all price/risk/business settings.

Prefer this focused scheduler correction over faster blanket polling or parallel
order executors: it addresses ordering without deliberately increasing REST
request rate or adding mutation races. The tradeoff is delaying optional
reprices/refill while uncovered inventory is serviced. Add cancel-time quote
diagnostics to assess churn separately. No claim that this eliminates429s.

Required repair acceptance: long/short, partial fills, builder-only/mixed/wrong
quantity, multiple simultaneous fills, due exits, unknown cancels, backoff,
retirement, shutdown, both account-source modes and exhausted visit budgets.
Require regression/reversal/full suites and authorized deployed acceptance.

## Final read-only live check

Same supervisor388103/runner388121, uptime2h16m at01:27Z, no restart.
Authenticated01:27:30.327479Z:30 orders across23 markets,27 flat entries and
three exact reducers: BUY10 for chypav/kormyk-10, SELL10 for volyur/kryvit+10,
BUY3 for yidwan/elezah-3. Zero unresolved intents/unknown orders. Sequential
ledger read had33 working orders/three positions, so it is not identical-count
venue proof. Earlier01:21:56Z snapshot had four positions including a builder
and one reprice gap; it was not claimed fully protected. No API/trading mutation
was made by this review. Documentation-only handoff is committed separately.

## Follow-up: diagnostics first (operator authorized)

The operator requested item3 first so actual repricing evidence can inform a
combined repair. This authorizes observation code, not activation of the two
scheduling changes. No price, size, cap, risk, scheduler, source-selection,
polling or lifecycle policy is changed. The current process cannot acquire new
Python hooks without a restart; separate observers cannot reconstruct its exact
decision-time book. Live collection therefore awaits an authorized diagnostic
deployment/restart, not a guessed reconstruction from later public data.

Implementation adds one `[reducer-decision]` JSON row for an attempted position
action in the ordinary decision driver (both account-source modes). The exact
planning frame is copied before the driver awaits; the row is emitted after
the driver returns/raises. It records target order/price/exact remaining quantity,
signed position, current bid/offer, existing passive-price helper result,
entry basis/tick, book observation/receipt/transaction clocks, explicit unknown
flags, builder/reducer counts, exact matching/preferred orders and action reason.
The result distinguishes cancel ACK, placement ACK, no ACK and driver exception;
ACK is never described as terminal cancellation or a working replacement.
Only exception type is included, not response bodies or error text.

This is bounded event logging, not per-tick logging: one scalar record per
attempted position action, no full books/order lists, retained history, new
API requests, WebSocket subscriptions, ledger queries/writes or extra awaits.
All data comes from the existing immutable frame and original driver result.
Capture/format/output failures are isolated from execution and remain missing
evidence. Shutdown has its own driver path and is not covered by this hook;
locally deferred pre-driver actions and process death before return likewise
do not produce an acknowledgement record. Existing terminal/timing logs remain
the independent cancellation proof. Logging cannot be used as trading authority.

### Dedicated edge-case audit

- Long/short, partial size, duplicate exact reducers, builder-only exposure,
  one-sided/missing books, unknown basis, stale/future clocks, hard/maintenance
  actions and flat/critical exclusions are covered by focused tests.
- A simulated book replacement during the awaited driver proves the diagnostic
  keeps the original frame, not the newer book.
- Actual fake-client cancellation still succeeds when capture or log output
  raises. Driver exceptions still reach the original recovery path, with no
  newly authorized retry or swallowed execution error.
- Pure capture does not re-run the stateful planner, change its inputs or
  consume the result. Original self-chase and continuity/priority tests remain
  unchanged. No new memory cache exists to survive a source switch or restart.
- Restart/rollback use the existing safe ownership/deployment gates. Removing
  the hook removes only evidence; no schema migration or ledger repair is needed.
- Synchronous logging has small CPU/output overhead, so this is not a claim
  of zero runtime cost or faster trading. No full-slate diagnostic loop is added.

The64 focused tests passed. In-memory reversal disabling the capture hook
produced exactly one expected integration assertion failure (missing row),
without changing source or contacting the venue. The complete source suite
passed all1,854 tests in277.141s using the production virtual environment against
the Windows working copy through WSL. The twelve new tests include four runner
integration tests. Final diff/edge audit and documentation audit passed. No live
repricing conclusion may be claimed from these fake tests; deployment and
restart remain pending explicit authorization. Code is prepared/pushed only.

Read-only authenticated follow-up01:40:46.147748Z:35 orders across27 markets,
30 flat entries and five exact reducers. Positions: cosale/filmih+10 SELL10,
budole/pytiva-10 BUY10, masyev/hohser-10 BUY10, pysyev/cherom+10 SELL10,
yidwan/elezah-0.06 BUY0.06. Zero unresolved/unknown state; ledger agrees.
The running bot still has the original loaded code, not the new diagnostic.
