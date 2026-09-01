# Inactive-market lifecycle recovery — 2026-09-01

## Incident and cause

At approximately 15:12 UTC the size-10/24 live runner had 18 working orders
across 10 markets while all 24 configured owner slots remained occupied. Nine
flat owners were receiving successful exact-market responses whose metadata
said closed, inactive or another explicit non-open status. The contract refresh
stored that response, then raised the generic `CommissioningUnavailable`
exception used for missing/failed data. It cleared incentive state and retried
once per minute forever, but never requested the already-proven lifecycle
top-up transaction. The selector therefore could not replace those owners.

This was not introduced by the day's CPU, parity or dashboard work. Git history
places the original single-market non-open exception on 2026-08-13 and the bulk
refresh classification on 2026-08-29. The 2026-09-01 maintenance episode made
many markets enter that state together and exposed the accumulated slot loss.

A second interaction blocked the obvious refill. Four nonzero positions were
also attached to markets reporting non-open. The local order builder correctly
refuses every payload—including a reducing IOC—against such metadata. Repeatedly
planning an impossible exit consumed REST/error budget, while the general
position-near-flatten guard prevented the top-up scan that could replace the
unrelated flat closed owners.

## Reviewed behavior

Exact response-backed lifecycle state is now distinct from unknown API failure.

- A flat, order-free exact non-open owner requests the normal incremental top-up.
  Release still requires the existing fresh exact-market re-fetch, complete
  account reconciliation, ownership proof, viable replacement and pre-commit
  recheck. Cached metadata alone never releases a slot.
- A nonzero exact bot-owned position enters mutation-free inactive-market wait
  only with known whole-account risk, exact matching basis, no working or
  `UNKNOWN_ABSENT` order and no unresolved intent. It remains inside active
  scope, reserves one configured slot and full risk, and keeps private/account
  monitoring.
- One-minute contract metadata is the recovery authority for an inactive wait.
  Book reads and locally impossible order attempts stop. Fresh open metadata
  immediately restores the unchanged reducer/IOC business path. Authoritative
  zero position releases the reservation and requests refill.
- Unknown, missing, malformed, transport and generic refresh failures never
  prove closure and never authorize release. After 12 consecutive failures,
  only the read cadence backs off from 60 seconds through 120/240/480 seconds to
  a 15-minute cap; the owner remains reserved.
- The top-up scan may ignore only an exact unexecutable position when deciding
  whether a scan could delay a legal flatten. Every ordinary position remains
  unconditional. Active-scope rebinding and shutdown cannot drop or call an
  isolated owner flat.

The change does not alter market ranking, quote price, quote size, reward
economics, inventory clocks, loss limits, capacity, mutation sequencing or API
rate policy. It changes lifecycle availability and eliminates reads/actions
that cannot succeed under positive non-open metadata.

## Verification and edge review

New regressions cover exact flat lifecycle refill, startup refusal boundaries,
no runtime-error inflation, no redundant book fetch, unknown-data backoff and
its 15-minute cap, exact basis/risk/ownership requirements, active-scope drop
refusal, position wait without mutation, near-kickoff scan exemption, reopen to
ordinary exit, authoritative flat release and truthful nonflat shutdown.

The live-runner module passed 396/396 before the final strengthened assertions;
the focused final lifecycle slice passed 8/8. Deliberately restoring the old
position scan guard made the near-kickoff refill regression fail because the
inactive owner blocked replacement; restoring the exact exemption made it pass.
The complete Windows suite identified only its known native-platform POSIX-flock
guard cases plus two candidate documentation/test assertions, both corrected.
The exact isolated pinned-WSL candidate passed all 1,038 tests in 74.754 seconds;
the combined release review found no new cross-change defect.

## Activation and rollback

The running process remains on its prior executable marker until a separately
verified deployment and restart. Before loading, require a clean pushed commit,
exact WSL suite, source parity and the normal live order/position/intent gates.
After load, accept only if exact non-open flat owners refill, inactive positions
remain fully visible and mutation-free, reopening resumes exit service, API
failures do not masquerade as closure, and unrelated positions retain their
ordinary deadline priority. Roll back on a dropped owner, unreserved exposure,
mutation on confirmed non-open metadata, missed reopened exit, unknown-data slot
release or lower unrelated resting coverage.

## Deployment and initial acceptance

Reviewed commit `13ecc441b` was pushed, fast-forwarded into the stopped laptop
WSL checkout and passed all 1,038 exact deployed tests in 72.254 seconds. The
deployed marker was advanced only after source/hash proof. Graceful stop had
ended conservatively `flat=false`; that record was not treated as flat. A later
independent authenticated read proved zero venue orders and positions, while
the ledger proved zero unresolved intents and `UNKNOWN_ABSENT` orders, before
one size-10/24 runner started.

The loaded lifecycle path requested refill, released four ended owners and
added viable replacements without producing the old recurring
`market_incentive_refresh ... is not open` runtime-error loop. Authenticated
acceptance at 16:07Z found 39 orders across 22 markets and zero positions after
the runner restored the exact 24-owner `RUNNING` scope. A 76.7-second
post-mutation API window completed 174 attempts including 25 mutations with zero
failures, rate limits or cooldown. No exact inactive position existed, so
mutation-free wait, reopening and flat-release acceptance remain natural-only.
