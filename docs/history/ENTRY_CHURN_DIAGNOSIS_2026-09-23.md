# Entry churn diagnosis — September23

## Scope and state

Operator paused300-coverage work to prioritize low orders/rotation. Its offline
full-test process was terminated; no collector or recurring schedule was started.
Uncommitted diagnostic improvements remain in the isolated scan-review worktree.
Main trading source, live configuration and runner are unchanged. This is
diagnosis, not a deployed fix or new restart authorization.

Live source20c44a9d7, supervisor277537/runner277553 started16:00:18Z;
hybrid account state, size15/32, four$30 settled-fill windows. Public books and
market filters are unchanged.

## Observed facts

- At16:29:19.729–16:29:27.682Z,13 different market action plans cancelled
  resting entries with `side_no_longer_desired:account_new_risk_paused`.
  Positive cancel-terminal notifications followed. Replacement plans resumed.
  Therefore the earlier filter-only explanation of low orders was incomplete.
- Read-only ledger inspection through16:33:37Z found185 FAILED intents since
  startup, all `ApiAdmissionDeferred: private observation pending; request not
  sent`. These are local refusals, not185 HTTP429s or185 venue requests.
- The16:33:30Z settled window checkpoint was+$1.9153, cap30, no hard trip.
  Loss scaling therefore did not explain the contemporaneous low count.
- Authenticated16:30:33Z:12 flat entries and exact BUY7.95/BUY5 reducers for
  positions-7.95/-5; zero unresolved/unknown state.
- Authenticated16:39:21Z:26 flat entries and exact BUY5/BUY15 reducers for
  positions-5/-15; zero unresolved/unknown state. Total venue28 versus live
  ledger27 is a non-atomic observation, not proof of missing ownership.
- The16:36:26Z API window had244 calls/80.7s,3.023rps and one real
  cash-history429. Local admission failures and HTTP429 must stay separate.
- The16:29 scan had15 ranked books/12 viable opportunities; filters can
  legitimately limit supply too. Top-up retained owners, not wholesale
  profitability rotation. No reward-dollar impact has been established.

## Code trace and offline reproduction

The hybrid account checkpoint drains notifications before REST proof. Later
equity, event, book and rate-admission awaits can receive more private events.
The final synchronous POST admission refuses any pending event, including a
not-yet-classified harmless acknowledgement. Local refusal starts an entry
backoff, and subsequent planned creates can wait behind it.

An ignored offline diagnostic using the existing fake-client/temporary-ledger
fixtures passed three cases in the exact production environment:

1. An unchanged, owned NEW/no-fill ACK queued after account checkpoint blocks
   POST. Draining it classifies ACK-only and permits POST without extra REST.
2. A position hint arriving during REST proof still blocks POST.
3. An applied position hint remains blocked until complete REST reconciliation.

This proves a timing mechanism, not that every live refusal was an ACK. The
final pending-event guard itself is required; deleting it would introduce a
fill/quantity race.

The account-pause reason is a separate issue. `_entry_risk_allowed` combines
window, equity freshness, day/cash, pause and event gates into one boolean.
`_refresh_balance` immediately withdraws equity authority after a failed
settled-window proof, while the sweep suppresses repeated refresh attempts.
Consequently a failed bracket can fan out into entry cancellations. Current
logs omit the underlying exception/subreason, so the16:29 trigger cannot yet
be assigned to cash mismatch,429,age or another gate. This shared path also
exists in REST mode; it is not established as a hybrid-only defect.

## Recommended next work and acceptance

1. Capture the exact pause blocker/failed bracket category locally with bounded,
   redacted transition telemetry and no diagnostic API calls.
2. Replay ACK/fill/cancel arrivals throughout equity/book/admission waits and
   delayed/failed risk refreshes through the complete decision sweep.
3. Evaluate an ordered pre-plan notification checkpoint and a deliberate wait
   for required proof instead of repeatedly creating already-blocked intents.
   Replan from current quantity; retain the last-moment admission fence.
4. Compare unchanged REST versus hybrid read counts, durable intents, useful
   resting coverage, reducer latency and recovery. Do not force extra account
   reads per harmless ACK, retry unknown creates or relax freshness/stop rules.
5. Require focused regression/reversal tests, full suite and edge-case review
   before a repair is pushed; explicit deployment/restart authority and normal
   flat/clear gates remain necessary. No live fault induction.

No production code fix, policy change, deploy or restart occurred in this
diagnostic continuation. The300-coverage task remains paused.

## Follow-up: operator-requested fully-private stable comparison

Read the immutable `v1.2.0` tag (`a3defc014`, executable equivalent to3625927fb),
not the newer REST rollback tag. It is the last designated pre-REST checkpoint
and passed1,228 tests; by the September10 tagging time the private outage already
existed. The earlier `v1.1.0` is the separate healthy24-slot canary reference:
26 API windows/2,774 attempts/zero429s. Neither tag proves today's feed complete.
No checkout/rollback, historical database activation or live source switch made.

Direct `git show` and diff review found:

| Concern | v1.2.0 private source | Current hybrid source |
|---|---|---|
| Ordinary private updates | Projected into ledger; valid private evidence refreshed session freshness | Projected with REST gate retained; neutral ACKs keep cadence, other hints latch POST proof |
| Cancel-confirmed owner | Exact owner gets priority replan | Same base priority remains, plus scoped REST reducing proof and hybrid final admission |
| New event awaiting application | No current hybrid pending-queue fence | Any pending event bars POST; safety fence cannot simply be removed |
| Risk refresh failure | Previous mark retained until freshness expired; refresh could retry at subsequent markets | Settled-window failure immediately clears risk observation; subsequent markets in same sweep do not repeat failed bracket |
| REST use | Startup/recovery/reconciliation and periodic balance/history reads still existed | Complete account authority remains REST because private completeness is not proved |

The risk-failure change is from971f7812f (September15 six-hour-window work), not
the September22/23 hybrid change. The per-sweep failed-refresh coalescing is
froma8888b5e (September12), avoiding per-market API amplification. The old planner
already cancelled flat entries for `account_new_risk_paused`; that policy was
not introduced by hybrid. These facts narrow the investigation but do not
identify the missing exception behind the16:29 live cancellation wave.

Safe reuse: preserve exact-owner wakeups, ordered projection and harmless-ACK
handling. Do not copy old readiness assumptions, remove the pending-fill fence,
retry account reads for every market, restore$90 HARD or silently reintroduce
cash-based trading-loss calculation. Validate the candidate scheduling repair
against both the current REST baseline and the older event-driven behavior.
