# 2026-09-01 combined release review

## Scope

The review covers every code commit after the prior loaded scheduler commit
`171445995`, plus the inactive-market lifecycle candidate prepared later the
same day:

| Change | Intended effect | Business-output boundary |
|---|---|---|
| `773677b2f` realized-P&L replay | Remove history-squared ledger work | Exact old/new live-copy values matched with and without a cutoff; stop equations are unchanged |
| `5f1ef3369` recovery book validation | Allow recovery to inspect valid one-sided/inactive books | Entry payload validation and both-empty refusal remain; no mutation authorization is added |
| `0bcfbbc96` settlement wait | Let unrelated slots trade around one exact empty-book position | Exact owner remains barred, capacity/risk reserved and fully monitored; returned depth restores the old exit path |
| `6a3cf279e` UI-maintenance pause | Withdraw/block flat new-risk quotes during proven customer UI pause | Position reducers, cancels, reconciliation, hard exits and loss stops remain live; recovery needs three healthy observations |
| `8e06327f0` parity snapshot | Remove redundant SQLite random IO through WSL 9p | Tooling only; copied bytes retain size/SHA proof and external snapshots still receive full integrity validation |
| inactive-market lifecycle candidate | Replace exact non-open flat owners and stop impossible positioned-owner mutations | Fresh account/ownership/top-up gates remain; exact positions reserve risk until open or authoritative flat proof |

Documentation-only commits, the Windows checkout move and recorded acceptance
commits were reviewed for operational truth but contain no executable strategy
path.

## Combined-path review

The cumulative source diff was traced through startup, ordinary decisions,
position transitions, lifecycle top-up, stream/REST recovery, entry pause,
shutdown, ledger migration and parity installation.

- Market ranking, selected-market order, quote price, quote size, reward
  economics, affordability ordering, 900-second inventory timeout,
  pre-kickoff flatten, tiny-residual timing, loss limits, slot limit and API
  admission rates are unchanged.
- UI maintenance is intentionally reduce-only. It suppresses discovery/top-up
  because no new entry should be placed, but active positions remain scheduled.
  When UI health recovers, active owners are dirtied and normal top-up resumes.
- Settlement wait and inactive-market wait are mutually evidence-specific but
  share only the safe ownership rules: exact basis, known risk, no working or
  unknown order, no unresolved intent, capacity reservation and owner-drop
  refusal. Neither state can claim flatness or manufacture a price.
- An inactive positioned market that reopens is dirtied immediately and returns
  to the existing reducer/IOC planner. A still-empty reopened book remains
  fail-closed; it does not authorize mutation or slot release.
- P&L replay retains chronological per-market inventory matching and the exact
  `since` basis rule, so the faster implementation cannot change the account or
  per-market stop input. The additive index changes query access only.
- Parity snapshot changes are not imported by the runner. Native online backup
  integrity remains proven before byte-copy verification, and install still
  validates an external source before atomic replacement.

No new cross-change defect was found. The changes are not literally business-
behavior neutral: the two maintenance/lifecycle repairs deliberately reduce
unsafe or impossible actions and improve unrelated resting availability. They
do not change which healthy, entry-authorized market is economically preferred
or how an accepted quote is priced/sized.

## Verification

The final candidate was constructed in an isolated native WSL directory from
GitHub `HEAD` plus the exact working diff and run with the pinned production
virtual environment. All 1,038 tests passed in 74.754 seconds. This includes
the P&L equivalence/migration tests, recovery-book boundary, settlement wait,
UI classification/manual latch/reduce-only behavior, parity corruption and
timeout tests, scheduler/risk/business-logic suites and the new inactive-market
edge cases. Documentation audit and `git diff --check` pass; Python compile of
the changed runner passes.

The new lifecycle repair also received a deliberate reversal: restoring the
old near-position scan guard made the strengthened near-kickoff refill test fail
exactly, and restoration passed. Earlier individual release changes retain
their recorded reversals in their evidence files.

## Deployment and acceptance gate

Push and stage only the reviewed candidate. Before restart, require the normal
graceful stop plus terminal record, fresh authenticated zero-order/position
evidence when flat startup is required, zero unresolved/`UNKNOWN_ABSENT` ledger
state and exact source/marker proof. Never force-kill or call a nonflat account
flat.

After load, accept startup only if UI pause state is truthful, exact inactive or
empty-book owners remain mutation-free and risk-reserved, unrelated eligible
owners refill, P&L/stop values remain consistent, and API windows show no new
failure/rate-limit fan-out. Roll back on changed quote economics, dropped risk
ownership, unknown-data release, missed legal exit, persistent lower resting
coverage, increased `execution_state_unknown` or parity integrity disagreement.

## Deployment and initial live result

GitHub, WSL and the deployed marker now name `13ecc441b`. The exact deployed
WSL suite passed 1,038/1,038 in 72.254 seconds. The restart gate used fresh
authenticated zero-order/zero-position proof and zero unresolved/unknown
ownership; it did not rely on the preceding conservative `flat=false` terminal
record. Exactly one size-10/24 supervisor/runner pair started.

Initial loaded evidence is consistent with the review: active scope was
`RUNNING`; authenticated state showed 39 orders across 22 markets and zero
positions after refill restored the exact 24-owner scope; and a 76.7-second
post-mutation API window had 174 attempts, 25 mutations, zero failures, zero
429s and zero cooldown. No change to healthy quote output or
risk limits was observed. Positioned inactive wait, UI maintenance transition,
one-sided recovery and steady-state performance remain natural acceptance items
rather than induced tests.
