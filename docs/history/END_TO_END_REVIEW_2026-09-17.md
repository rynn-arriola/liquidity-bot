# End-to-end code review — September 17, 2026

Review target: Windows checkout `7082e3ac4`. Review-only: no trading-code,
configuration, ledger or process mutation; no deployment or restart. The local
WSL supervisor432/runner446 were still present at the final process check.
Current venue exposure was not re-read for this code review and is unknown.

## Scope and evidence

Traced market selection and stream books, lifecycle/entry gates, durable order
submission and reconciliation, passive and timed exits, REST/private-source
handoff, six-hour risk accounting, reporting, and recovery/launcher paths.
This is a cross-component review, not a claim that every line or live failure
mode was exhaustively exercised.

The production Python environment ran the Windows checkout's complete unittest
suite through WSL with `PYTHONPATH=src`: **1,740 tests, zero failures/errors**
in249.405s. Separate in-memory planner and temporary-ledger probes below expose
coverage gaps; they did not contact the venue or mutate the live ledger.

## P1 — Deferred timed exit cancels the reducer without replacing it

`reconciler.py:542–641` first cancels all working orders after the inventory
deadline, before checking whether an aggressive exit is permitted. With no
orders remaining, a thin book or excessive early crossing loss returns a
critical result before reaching the normal passive-reducer branch.
`live_commissioning.py:9482` retires/retries the price-cap case on a60-second
cadence without restoring a passive order. The position remains exposed with
no resting exit while the deferral persists.

Isolated reproduction using `test_reconciler` fixtures: two long contracts,
entry0.90, bid0.05/offer0.07, age60s, configured age15s, early-loss cap0.50,
and an exact two-contract offer reducer. The first plan returned
`cancel/hard_timeout_cancel_confirm_first`; the next frame, after removing
the positively cancelled order, returned `critical/hard_exit_price_cap_exceeded`
and no replacement. The existing expensive-cross test starts with no orders
and does not assert that a reducer remains working.

Recommended repair: explicitly preserve or restore the exact passive reducer
when an early IOC is deferred, while retaining builder cancellation, positive
terminal proof, no overlapping mutations, and kickoff/maintenance/HARD priority.
Do not relax the operator's exit-price or inventory-age settings.

## P1 — Poll-start timestamps can omit a closing loss at window rollover

`rest_fallback.py:318–343` passes reconciliation start time into the base
collector. After awaiting snapshots/history, `live_commissioning.py:2554`
records fills with that earlier `received_at=now`. Risk accounting at4568ff
selects the new window using current time, then filters those receipt stamps.

Reproduction: entry ten contracts at0.80; the REST poll begins one second
before the window boundary; an exit at0.20 occurs one second after it and
arrives before the next mark. The ledger reports total closed P&L **-6.00**,
but the new-window receipt filter reports **0**. If the old window's last
mark preceded discovery, neither active risk observation charges that loss.
This contradicts the documented durable-receipt boundary guarantee.

Recommended repair: separate network-proof observation time from the actual
durable fill-receipt time. Preserve old timestamps for freshness checks and
deduplicated fills; test a real async poll spanning each ET boundary, delayed
history, restart, and the following balance mark. Do not backdate a newly
durable fill to the beginning of a network request.

## Reporting/risk coverage limitation — Market resolutions are not fills

`account_snapshots.py:219` requests only `ACTIVITY_TYPE_TRADE`.
`execution_ledger.py:2054` and `scripts/reward_table.py:283` replay only
non-reversed execution fills. A position resolved directly by the exchange
without an offsetting fill has no resolution payout/cost-basis realization in
these calculations. An unmatched ten-contract0.80 entry yields zero gross
realized P&L in the isolated replay; position disappearance alone cannot
establish whether its resolution won or lost.

This is a coverage limitation of the deliberately narrower closed-fill metric,
not evidence of a newly measured live loss or a reason to reintroduce cash or
open-position marks into HARD. The daily Trading label must not imply complete
realized trading P&L if such outcomes are omitted. Reconcile authoritative
resolution activity and cost basis for complete reporting; explicitly agree
how genuine resolution P&L should enter the operator's settled-only risk policy.

## Remaining work at review completion

No fixes are applied by this review. Add focused regressions and deliberate
reversal tests for the two defects, audit the resolution coverage decision,
then run the full suite and deployment gates only under implementation/restart
authorization. A passing current suite is not live-readiness proof for these
uncovered cases. No synthetic fills, disconnects or rate-limit bursts were
induced against production.

## Authorized implementation follow-up — prepared for next restart only

The operator subsequently authorized these fixes, explicitly forbidding a
restart. Only two runtime files change: the reconciler and the existing REST
history projection in the shared runner. No live file, ledger, configuration,
process or order is modified. Exchange-resolution accounting remains a
separate coverage/policy item; this patch does not silently redefine HARD.

### Chosen design and tradeoffs

- Evaluate the current IOC depth/loss decision before cancelling passive
  protection. Route an early deferral through the SAME passive-reducer helper
  used before the inventory deadline. Restoring after a critical/retirement
  would retain an unnecessary cancellation gap, lose queue priority and add
  requests; retaining first avoids those costs. Builder/duplicate/wrong-size
  cancellation, in-flight cancel proof and bounded IOC sequencing are unchanged.
- Stamp one newly collected history batch with the clock after its await.
  Keep snapshot/read-start times unchanged for account freshness. This avoids
  falsely renewing entry proof and preserves event ordering within descending
  batches. Existing fill receipts, venue event times, historical day reports,
  risk tiers and holds remain immutable; no historical ledger repair occurs.
- No new API calls, subscriptions, polling cadence, retry loop, dependency or
  database migration. Repeated deferred book evaluation adds only local book
  arithmetic; an ordinary deferred position no longer causes critical recovery
  or60-second retirement. Absolute exits and unknown-state refusal remain.

### Verification

Focused suite: **107 tests passed**. Nine added test methods exercise long and
short inventory; thin/expensive crossing; missing/exact/duplicate/oversize
reducers; partial fills; pending cancels and unknown state; price recovery;
kickoff, maintenance and HARD priority; and real runner reducer submission and
retention without retirement in BOTH account modes. Async reconciliation is
tested across all four ET boundaries in REST and WebSocket modes, followed by
the actual balance/risk update and a later-window duplicate replay.

Deliberate in-memory reversals of the original methods detected the defects:
the old planner produced four assertion failures; the old reconciliation clock
produced eight. There were no reversal test errors. Current methods were
restored automatically; production and source files were never reverted.

Complete suite: **1,749 tests passed**, zero failures/errors in252.713s, using
the production Python environment against the prepared Windows source checkout
through WSL. Documentation audit and `git diff --check` also passed. These are
offline acceptance checks, not a guarantee against every live fault.

Final process verification still found supervisor432/runner446 with their
September16 starts unchanged. The production checkout has no tracked changes;
its existing untracked deployment marker/package metadata are untouched.
No authenticated exposure read was necessary for this offline implementation;
current venue orders/positions remain unknown, not assumed flat. GitHub and
Windows will contain the prepared change; live deployment intentionally waits.
The offline PC remains pending, not repeatedly probed. Pre-work parity matched
both HEADs at7082e3ac4 and preserved the prior review-only dirty documentation.

### Dedicated final edge-case audit

- Empty/flat and healthy entry paths retain the original planner. Exact
  reducers at touch are kept, partial/incorrect sizes are replaced only after
  cancellation proof, and a missing side cannot invent a passive price.
- Unknown account/book/ownership, ambiguous mutations, pending cancels and
  submitted-but-unproved IOC remain gated. A planning feasibility calculation
  is not order authority; the existing admission checks still own submission.
- Early price/depth deferral cannot bypass kickoff, maintenance or HARD. Tiny
  residue keeps its displayed-depth fallback; actual IOC budgets remain bounded.
- Delayed history counts on actual batch receipt, not poll start. Duplicate
  replay cannot shift receipt across restart/windows, and new receipt does not
  reset snapshot age, entry capacity, durable loss caps, holds or source state.
- Reporting keeps venue event-time attribution. No deposit, withdrawal,
  reward, open mark or unmatched basis is added to the loss arithmetic.
- Both source modes share the repaired logic; source handoff, recovery and
  shutdown continue through existing proof. No new worker/loop or concurrency.
- Configured capacity and all exit prices, quantities, timing thresholds and
  loss dollars are unchanged. No API quota or zero-crash guarantee is claimed.
- Rollback requires normal stopped-known-state review; no schema rollback is
  needed, but restoring old code would restore these defects. Exact native
  deployment tests and natural live acceptance remain for a later authorized
  restart, not this staging-only task.
