# Scoped strategy recovery — September19

## Scope and design

The operator approved the recommended fix after the Priority1 investigation.
This task prepares code only: no deployment or restart authorization. The
running source remains `1273aadd7`; no risk or strategy setting changes.

The preceding [review](PRIORITY_ONE_REVIEW_2026-09-19.md) correlated two
29.8/36.3-second pregame-deadline IOC acknowledgement delays with scoped
execution-state recovery. An isolated reproduction established a local cause
of avoidable delay: two strict full REST proofs, even when the first already
cleared the affected owner, with no intervening urgent-position service. It
does not prove every millisecond of those live delays or the initial fault.

Three approaches were reviewed:

- Forward every requested scope through the general REST wrapper: rejected;
  it could turn partial proof into fresh account-wide entry authority.
- Enable global-quarantine preemption indiscriminately: rejected; strategy
  recovery must not place a reducer and immediately cancel it again, recurse
  into itself, or alter global/private recovery semantics.
- Add a narrow strategy-quarantine hook: selected. Keep the first strict
  account proof; service other urgent positions serially; cancel only the
  recovering owner's known orders; confirm using existing exact-owner reads
  and complete orders/positions/history without advancing the entry clock.

If no target orders, absent orders or unresolved intents remain after proof
and service, return without a duplicate pass. The recovering owner is excluded
only from nested position priority while its quarantine is in progress. A
finally block restores its ordinary scheduling even on failure/cancellation.
Nested recovery is deferred to normal backed-off scheduling, not recursively
expanded. Stop and terminal outcomes return to the existing caller/shutdown.
Source generation, scope, public-stream state and ownership are rechecked
after awaits; expired/incomplete authority remains closed. Terminal proof
never infers cancellation from an acknowledgement or missing row.

The ordinary strict REST wrapper, startup, global quarantine, rebind,
shutdown, WebSocket mode, prices, quantities, 900-second/pre-kickoff deadlines,
four $25 windows, loss-scaled entry caps, reward selection, source controls,
4/s governor and endpoint backoff remain unchanged. No new API endpoint,
cache, worker, trading rule or configuration is introduced.

## Verification

Fake-venue tests exercise the actual strategy-recovery call chain, strict
REST reconciliation, exact-read workers and serial projector; only venue read
and cancellation leaves are fake. The accounts/ledgers are isolated fixtures,
not production. The focused native Python3.12 run passed93 tests, including25
new regressions and existing REST, priority and shared-stream recovery suites.

In the capacity-pressure fixture with32 owned orders, the initial strict pass
reads all32 and the cancel confirmation reads only its target:33 exact reads
instead of64. Both complete account snapshot/history passes remain. In the
already-clear target fixture with31 other orders, one31-read full proof and
urgent checkpoint replaces two full proofs/62 reads. Synthetic admission time
is not a prediction of production duration or a promise of zero429s.

Deliberately routing REST strategy recovery back through the original global
wrapper made both key regressions fail: duplicate account work returned and
the urgent checkpoint disappeared. The repair was restored before final
validation. Both complete native Python3.12 suites passed all1,903 tests
(197.824s and final198.134s). Final review retained the existing quarantine
diagnostic in the new path; the final suite includes its regression. Exact
Windows/native-test hashes match for both changed source files and the new
test file. Documentation audit and whitespace review passed. No secrets,
ledger, configuration or production source was modified.

At10:07:54.179450Z a single bounded authenticated report-lane check proved30
flat entry orders across18 markets, zero positions, zero unresolved intents
and zero unknown orders; the ledger agreed30/0. Runner582918 remained alive
at the later process check, up7h17m45s. This is loaded-source status, not
acceptance of the prepared repair. No deployment, stop or restart occurred.

## Dedicated edge-case audit

- Success/empty/full: clear owner skips redundant work; full-width fixture
  keeps the first exact account proof and every unrelated resting order.
- Cancellation: acknowledgement-only remains unresolved; positive FILLED
  winning the cancel race retains inventory; partial3.25 fill retains the
  remaining working order; missing row cannot prove terminal; unknown no-ID
  PLACE is not retried.
- Freshness/failure: initial history failure prevents priority service and
  target cancellation; terminal-history failure revokes authority; priority
  work that outlives the30-second account proof defers further quarantine.
- Timing/control: stop before proof, during service and after cancel preserves
  shutdown ownership; public disconnect yields shared recovery; source
  generation and scope changes prevent subsequent cancellation.
- Scheduling: real position-only sweep visits the other held owner, not flat
  entries; recovering owner is restored afterward; existing owner backoff
  remains; nested recovery cannot create an unbounded proof/decision stack.
- Ownership: new foreign working order is account-wide terminal; generic
  scoped REST callers still cannot skip owned exact reads or renew entry
  authority from a narrow result.
- Restart/rollback: no database/config migration or persistent new latch;
  no execution counters or settled-loss state reset. Existing reviewed source
  is the rollback. Activation requires a separately authorized safe deployment
  and restart, exact installed suite/hashes and natural live acceptance.

## Remaining acceptance

After an authorized activation, inspect natural recovery at configured
capacity: exact read counts, urgent handoff timing, cancellation-to-terminal
and replacement intervals, IOC acknowledgement versus deadline and actual
flat time, REST429/backoff and exposure. Do not induce fills/faults or claim
WebSocket-equivalent latency from unit tests. Repricing suppression and the
other measurement priorities remain separate, not silently enabled here.
