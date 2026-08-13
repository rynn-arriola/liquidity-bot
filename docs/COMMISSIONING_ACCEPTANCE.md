# Commissioning prelaunch acceptance — 2026-08-13

No mutation or Discord endpoint was called during these checks.

## Deterministic verification

- 227 unit/integration tests passed under Python 3.14 on Windows.
- A fake venue accepted only an exact one-contract, post-only payload.
- Fake 06:00 New York cutoff completed flat with zero new orders.
- A $15 marked-equity drawdown selected the named emergency hard-stop path.
- A persisted owned order restarted in recovery-only mode, was canceled, and
  exited flat without adding risk.
- An unowned exchange order refused startup and made no mutation.

## Bounded real read-only verification

Observed at `2026-08-13T04:53:19Z` through the shared 4 requests/second gate:

- 19,179 sports-market catalog rows normalized;
- 584 pregame markets started after the 06:00 New York cutoff and were inside
  the 36-hour discovery window;
- 37 had a current applicable liquidity program;
- 5 passed one-contract tick/minimum, both-sided reward eligibility, and
  stressed full-depth exit checks;
- account open orders: 0;
- account nonzero positions: 0;
- authenticated marked-balance schema: valid;
- public market WebSocket full book: received;
- private WebSocket account-balance snapshot: received.

Selected prelaunch candidate:
`aec-wnba-atl-conn-2026-08-13`. At the observation it had an active `early`
liquidity period, a $200 published pool, 10,000-contract target, $0.01 tick,
$0.01 minimum quantity, $0.81/$0.82 touch, both one-contract sides eligible,
and approximately -$0.0196/-$0.0185 stressed one-contract exit P&L. The event
clock was `2026-08-13T23:00:00Z`, after the shutdown cutoff.

Pool and eligibility are not guaranteed payout or profitability. The live
runner revalidates market, incentive, book, account, balance, and private-stream
state immediately before its first order.

The first server start attempt at approximately `2026-08-13T05:01Z` refused
before order entry because the market metadata endpoint returned the observed
open value `MARKET_STATUS_OPEN` while validation recognized only the book
endpoint's `MARKET_STATE_OPEN`. The accepted open set was narrowed to exactly
those two observed values and covered by a regression test; no arbitrary status
or substring matching was introduced.

The next bounded attempt placed one exact one-contract post-only order and then
canceled it as eligibility/state changed. The exchange proved zero open orders
and zero positions, but its terminal retrieve response omitted `updateTime` and
reported `lastTransactTime`. The ledger correctly rejected terminal content
reusing the original creation clock and remained in recovery. The operator
process was stopped only after independent REST checks proved canceled, no open
orders, and flat. Normalization now consumes the observed terminal clock, and
startup directly retrieves every persisted exchange ID before snapshots; a
restart with any prior place intent is recovery-only and cannot resume risk.

A subsequent fresh-ledger attempt placed one one-contract quote, reconciled its
order response, and then hit an empty-transition bug: the same loop pass tried
to take the oldest timestamp after REST had cleared the final unresolved
intent. It raised `ValueError`, entered shutdown, canceled the quote, proved
zero open orders/positions, and exited without restart. The loop now treats an
empty post-reconciliation set as success and proceeds, with a regression test
and persistent traceback output for future caught failures.

The next fresh-ledger attempt placed two exact one-contract quotes and exited
flat on `critical:book_stale`. The market WebSocket is change-driven and had no
book change inside the five-second decision limit. A quiet feed now receives a
REST full-book refresh at three seconds through the shared 4 requests/second
governor; a newer WebSocket book suppresses that fallback call.

The next attempt remained mechanically healthy until the private freshness
window expired: after its initial authenticated balance snapshot, no account
state changed, so the change-driven private stream was silent. It canceled two
one-contract quotes and exited flat on `critical:execution_state_unknown`.
While that socket remains connected, the runner now performs a complete REST
order/position reconciliation every ten seconds. REST and private timestamps
remain distinct; either recent proof can maintain known state, and both aging
out still disables risk.

The following attempt remained healthy beyond the private threshold and had
two exact one-contract post-only quotes, but later stopped flat on another book
freshness edge. The decision reused its pre-await clock and stamped REST books
at request start; under live API latency, later account work could exhaust the
five-second budget. Books are now stamped at response receipt, refreshed last,
and evaluated against a newly read clock. Any intent reconciliation starts a
fresh loop before a new quote decision.

That attempt stopped at the one-minute incentive refresh boundary because the
book-due comparison still used the clock captured before the incentive calls.
The runner now rereads time immediately before deciding whether the final book
refresh is due; upstream REST latency therefore cannot hide an aged book.

One more attempt exposed a scheduler edge: a due check could see a WebSocket
book just under three seconds old, skip REST, and then schedule three seconds
from the check time, allowing the five-second limit to arrive first. The next
deadline is now anchored to the book's actual receipt timestamp.

The final attempt ran for 614 seconds. Its first maker fill was durably applied,
then the documented `positionSubscription` update omitted `marketSlug`. The
private projector rejected the otherwise exact decimal position update and the
fail-safe canceled the opposite quote, flattened one contract, proved the
account flat, and stopped. Private events now carry their subscription scope;
the projector may use an omitted slug only for an explicitly singleton market
subscription. Account-wide and multi-market ambiguity still fails closed, and
whole-account REST reconciliation remains in place.

For the next operator-authorized session, recoverable market/private adapter
failures keep the process alive in a no-new-risk recovery state. Known quotes
are canceled, the affected stream is recreated, and whole-account REST plus
fresh stream state must pass before quoting resumes. Runtime errors retain a
credential-redacted stack in `commissioning.log` with bounded Discord alerts.
The former 06:00 cutoff is replaced by graceful operator stop; the $2 market,
$5 session, and $15 hard loss stops remain terminal.

The first operator-controlled run later exposed an IOC confirmation race. The
30-second exact-position exit filled, but the next strategy pass saw its local
position before REST confirmation and treated `hard_exit_sent_but_inventory_remains`
as terminal. Shutdown reconciled the fill and proved flat, but the process had
already committed to exit. That state is now a no-new-risk confirmation loop:
the runner reconciles the submitted IOC, resumes when flat, or permits another
exact-position IOC only after the prior order is proven terminal with inventory
remaining. It never duplicates an ambiguous exit request.

Review of that change found the terminal path could refuse to act. Shutdown
seeded its exit counter from the quoting loop, so a session that stopped
*because* it exhausted three IOC attempts reached shutdown already at its
ceiling, never attempted a flatten, alerted once, and then held the position
silently forever. That is the unmatched-settlement failure the whole design
exists to prevent. Shutdown now carries its own three-attempt budget, repeats
its critical alert every 300 seconds while it cannot prove flat, and ends the
process non-flat after a 900-second window so the supervisor reports exit 3
instead of a quiet hang. Total taker exposure is therefore bounded at three
runtime plus three shutdown submissions rather than three overall.

A follow-up boundary audit found that the first recovery implementation treated
every strategy `CRITICAL` and every planning/action exception as transient. That
contradicted the ownership and market-identity invariants and could leave an
unexitable one-contract position in an infinite recovery loop. Runtime recovery
now uses an explicit allow-list (`execution_state_unknown` and `book_stale`);
unknown strategy conditions and ownership, cross-market, exact-position,
planning, inventory-basis, and hard-exit failures end maker quoting. The hard
exit is limited to three IOC submissions within 30 seconds, each with a
10-second confirmation deadline. Recoverable action/strategy retries back off
exponentially under the shared REST governor. Boundary tests cover both the
recoverable and terminal sides.

## Reward-economics correction - 2026-08-13

The first continuous one-contract run completed two mechanically safe but
economically losing cycles. Each maker bid filled at $0.81, the passive
reducing offer did not fill within 30 seconds, and the exact taker exit sold at
$0.81 with a $0.01 fee. Gross price P&L was zero and recorded execution P&L was
-$0.02 before delayed rewards. Because flat state was treated as success and
the existing dollar loss limits were much larger, the runner quoted again.

Read-only verification at `2026-08-13T18:02:59Z` showed why reward eligibility
was insufficient. The selected WNBA market still had a $200 `day_of` pool and
both one-contract touch orders scored, but the target-size walk reached roughly
25,744 contracts on the bid and 56,945 on the offer. One contract's normalized
share could earn only a fraction of a cent over the remaining period and could
not cover the then-assumed exit cost. Authenticated account earnings showed
$32.06 paid, $5.39 pending, and $0.39 skipped across prior activity; none was
treated as income attributable to this session.

The former `1e-9` profitability sentinel is removed. Commissioning now uses the
current full-book competing score, at most half the period pool per side, a 50%
haircut, a 30-second projection, and a stressed exit-cost margin. It assumes
zero pair-completion spread and zero maker rebate. A
side that fails any term remains idle even when venue reward-eligible. Passive
reducers cannot quote through basis, authenticated market earnings are stored
separately every 15 minutes, and the first confirmed taker exit ends maker
quoting rather than beginning another cycle.

## Automatic reward-market selection - 2026-08-13

The fixed-slug runner could stay healthy forever on one economically rejected
market. Automatic commissioning now bulk-loads active sports metadata, applies
the open/tick/quantity/proven-six-hour-clock gates in memory, and requests
incentives only for those symbols in 40-market batches. No per-reward-slug
metadata loop is allowed. It applies exact one-contract economics to a 25-book
bounded pass, retains the best/current candidates, and rotates a window through
lower-ranked programs on later 15-minute passes.

A read-only production-contract measurement at approximately `2026-08-13T21:00Z`
completed in 42.1 seconds through the unchanged 4 requests/second governor. It
normalized 18,220 catalog markets, retained 2,565 executable six-hour
candidates, matched 2,304 to rewards, evaluated exactly 25 books, and reported
zero adapter failures. The former implementation would have made thousands of
individual metadata reads. None of the 25 measured books cleared the
one-contract reward-versus-exit gate, so the strategy correctly authorized no
order.

A second full rewarded-universe measurement joined 13,220 active reward groups
to 18,313 active/open sports catalog rows. Of 12,124 joined reward markets,
every row reported two market sides, confirming that side count cannot identify
a head-to-head. The V2 structure distribution was 4,327 futures, 2,861 props,
2,206 spreads, 1,900 totals, 776 moneylines, and 54 drawable outcomes. Inside
the executable six-hour window there were 2,338 rewarded rows: 1,249 props, 503
totals, 474 spreads, 97 moneylines, and 15 drawable outcomes.

The 97 rewarded six-hour moneylines were then evaluated without mutation. Of
91 applicable pregame books, 83 had spreads below 2 cents and only eight met or
exceeded 2 cents; none cleared the conservative reward-versus-exit model. The
measured spread buckets were 83 below 2 cents, three from 2 to under 4 cents,
three from 4 to under 7 cents, one from 12 to under 25 cents, and one at 25
cents or wider. This makes the explicit spread rule a correction to the prior
gate: because the old model used immediate unwind cost as a negative term, a
tighter book mechanically looked cheaper and therefore more attractive even
though maker-lab's measured sub-2-cent fills were its only negative spread
regime.

Commissioning now admits only the exact V2 moneyline enum, skips known-tight
catalog rows before book fan-out, and rechecks the 2-cent threshold against the
fresh full book. Resting building orders use a 1.5-cent pull threshold; reducers
and hard exits never inherit the entry gate. Midpoint distance is a ranking
tie-break only, while pair-completion spread, maker rebate, and markout remain
zero in the profitability gate. Selection scan and full flat-rotation time are
instrumented rather than moving discovery into an unprioritized background
task that could starve reconciliation.

## Probability-weighted economics correction - 2026-08-13

The earlier $1-per-market gate was incorrect. Authenticated earnings for
2026-08-10 contained 86 paid market rows totaling $32.06; 37 individual rows
were below $0.10. Commissioning now passes a zero per-market floor to the
projection and displays the $1 venue aggregate floor as reporting-only.

The same paid-market sample supplied a bounded first-fill calibration: 301
resting order lifetimes, 114 first fills, and 372,259.527 exposure-seconds. The
global 30-second empirical hazard is 0.9187%, and its 95% Wilson upper bound is
1.1024%. Each proposed side uses the maximum upper bound from the applicable
global, league, price, queue, side, spread, and time-to-event cells. Conditional
effects are not multiplied. The reward term is multiplied by the no-fill
probability, while stressed unwind loss and the second safety-margin copy are
multiplied by fill probability.

Monday paid yield is used only to rank current candidates, with league values
shrunk toward the $0.0304/contract-hour global result using a ten-market prior.
It cannot bypass current spread, target-depth, reward-projection, exit-depth,
or positive expected-value checks. A replay at a two-cent exit cost blocks the
global average and the observed $0.0097/contract-hour low-yield market, while
the observed $0.3176/contract-hour high-yield market clears the probability-
weighted comparison. This is calibration evidence, not proof of future profit
and not permission to increase one-contract size.

The completed probability-weighted implementation was then exercised through
its actual read-only selector at `2026-08-13` production state. It normalized
18,002 catalog rows, retained 95 executable moneyline catalog candidates,
matched 94 to rewards, skipped 80 known-tight catalog BBOs, and fetched only
eight full books. The scan completed in 15.5 seconds through the shared 4
requests/second governor with zero adapter failures. All eight were evaluated
with 30-second fill estimates between 1.2120% and 3.0846%; zero markets passed
the corrected economic gate, so this observation authorized no order.

Execution exposure remains one market. Rotation cancels known quotes, performs
whole-account REST reconciliation, and requires exact flat, no working order,
no unresolved intent, and known ownership before rebinding singleton market and
private streams. Restart recovery follows an identified ledger market before
new discovery. Unit boundaries cover catalog/incentive joining with zero
individual metadata reads, low-pool inclusion, deferred-candidate rotation,
flat-only rebinding, nonzero-position refusal, and ledger-first recovery.

A bounded live API probe returned 100 program groups on the first page with a
nonempty next token. The visible rows were active golf programs using
`tournament_with_cutoff`, including multiple simultaneous period rows. Official
liquidity documentation defines Early, Day-of, Live, and Daily but not that
venue value. If such a lifecycle appears among otherwise executable pregame
candidates, it is counted and reported but remains read-only; no duration or
pool split is guessed.

## Post-review runtime corrections - 2026-08-13

Live score-seconds now measure only confirmed owned working orders that remain
economically authorized. Venue-eligible candidates with no order, and working
orders newly blocked for cancellation, contribute no future score-seconds.
Unsupported market structures record an explicit blocker instead of returning
without a diagnostic.

`RewardMarketSelection.selected` now means at least one side has positive
lower-bound expected value. A zero-viable startup refuses, and a zero-viable
refresh retains the existing market streams rather than rebinding to another
monitoring-only market. Catalog BBO remains an optional optimization: malformed,
locked, or crossed values are discarded while the authoritative full book
continues to make the entry decision. Reward-evaluation invariant exceptions
are routed through the terminal strategy handler so ownership and identity
codes remain visible.
