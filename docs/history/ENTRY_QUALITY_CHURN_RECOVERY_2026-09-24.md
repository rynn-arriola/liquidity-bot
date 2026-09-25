# Entry-quality churn recovery — September 24, 2026

## Scope and live state

The operator asked to fix the remaining repeated cancel/re-enter behavior after
the scan-to-order audit. The live bot remained running on loaded source
`ef5a73f3d`; this preparation did not stop, deploy or restart it.

The change is deliberately limited to flat-state entry planning. Quote size,
32-slot scope, four $30 settled-loss windows, reward ranking, the 70% fresh
entry-quality threshold, account proof, positions, reducers and hard exits are
unchanged.

## Evidence and mechanism

From 00:27 through 00:41 UTC on September 25, the same Menche/Yuhwan–Onycho/
Funkoz bid was submitted at $0.42 29 times and produced 21 quality-linked
cancels. Typical cycles lasted less than 30 seconds, and several lasted under
five seconds. Each cancel logged the live blocker
`entry_quality_below_minimum`, but the action reason was mislabeled
`expected_net_not_positive` because runtime economics records a zero expected-
net sentinel for a quality refusal and the planner checked that sentinel first.

Saved PLACE observations were healthy at submission (94.16% retained value),
which is consistent with a book crossing above and below the enabled 70% line
between decisions. This was one side of one market, not scanner starvation,
REST account failure or evidence that the 70% rule should be weakened.

## Repair

- A resting side still cancels immediately when the existing quality gate
  refuses it.
- Only a side actually cancelled while resting enters recovery. A never-rested
  candidate can still quote on its first healthy frame.
- Recovery requires 60 seconds of continuously healthy evaluated book state
  before re-entry. A renewed quality failure, incomplete/expensive exit,
  unknown/stale/one-sided book, account pause or other entry blocker resets the
  clock.
- Entering a position clears flat-state recovery memory before the unchanged
  inventory/reducer planner runs.
- The stressed-exit/quality reason is now evaluated before the expected-net
  sentinel, so logs name the actual refusal. The risk budget still counts only
  sides that passed all earlier entry gates, preserving prior one-sided
  capacity behavior.

The recovery uses current in-memory planner state and adds no API request,
background task, database row or source switch. Restarting clears the recovery
clock, but the normal fresh 70% action-time recheck remains mandatory.

## Verification

Focused entry-quality, reconciler and REST reducer-continuity coverage passes
116 tests. New edge cases cover: immediate first healthy entry, quality-linked
cancel attribution, continuous recovery, reset on renewed failure/unknown
state, position-lifecycle clearing, expected-net sentinel ordering and
one-sided risk-budget preservation.

The exact final source then ran the production-compatible 2,257-test suite.
All 97 code/runtime files passed; its documentation file reported only the four
root context budgets exceeded by the newly added handoff lines. Those lines
were compacted without source/test changes, and the standalone repository
documentation audit then passed all 316 Markdown files with zero issues. Under
the release workflow's evidence-reuse rule, the unchanged code results plus the
corrected documentation audit are the final verification set. The exact 247
source/test/config/script/package inputs are recorded in the
[SHA-256 manifest](ENTRY_QUALITY_CHURN_RECOVERY_2026-09-24.inputs.sha256), with
the production-compatible interpreter and installed dependencies in the
[environment snapshot](ENTRY_QUALITY_CHURN_RECOVERY_2026-09-24.environment.txt).

## Deployment and acceptance

Pushed to GitHub in `e0cf77738`; not deployed and not restarted. Final parity
therefore correctly reports Windows/GitHub current while the WSL checkout is
two commits behind at `6100f9e9` and the live process remains on loaded source
`ef5a73f3d`. On the next separately authorized safe
restart, natural acceptance should show the same-side quality cycle replaced by
at most one cancel followed by either continued refusal or a re-entry after a
full healthy minute. Verify that reducers/exits remain immediate and compare
mutation count plus useful resting time without inducing fills or weak books.
