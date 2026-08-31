# Hard-exit sequential cancel repair — 2026-08-31

## Production symptom and evidence

Two markets entered the hard inventory-timeout path with two working orders,
but only one order was actually cancelled. The action log made the mismatch
visible: `working=2 pending=2` while the live action driver is intentionally
limited to one mutation per market pass.

On `aec-setkameua-rehdmy-kobvit-2026-08-31`, a size-13 bid at 0.64 had already
filled 6.75 contracts. At 15:50:30Z the planner returned two cancels with the
offer first; the driver cancelled that reducer and did not execute the second
list item. The remaining position-building bid later filled its final 6.25,
increasing inventory from 6.75 to 13 while the market cycled through
retirement. The eventual size-13 exit filled at 0.03 after an earlier 0.11 IOC
expired, for approximately -$7.91 on the episode. The same `working=2
pending=2`, reducer-first pattern occurred on
`aec-itfme-erkayd-marsav-2026-08-31` and preceded repeated retirement with an
8.16-contract position.

This was not an API-rate or venue-outage event. The relevant windows had no
HTTP 429 and no request-failure burst, and the first cancellation reached a
terminal state promptly. The defect was the contract mismatch between a
planner that returned every cancellation and a driver that deliberately
executes only the first mutation.

## Repair

The hard-exit planner now returns exactly one cancellation. It selects the
position-building side first (bid for a long, offer for a short), which removes
the only order that can increase exposure before touching the passive reducer.
After the driver successfully creates the cancel intent, the reconciler records
that exact exchange order ID and starts confirmation grace at submission time.
While that order remains working, planning waits; it never retries the cancel.
Only after the runner's ordinary unresolved-intent gate and terminal ledger
proof allow planning again may the reconciler select the next working order.
When all orders are terminal, the existing exact-position, depth-checked,
price-capped IOC path runs unchanged.

Flat-state cleanup and retirement-budget restart now clear both the per-order
target and timer. Planning alone does not start grace, because local API
admission can defer an action before any request crosses the network boundary.
Ambiguous create/cancel outcomes remain quarantined and are never inferred
terminal from snapshot absence.

The repair adds no venue read, write, WebSocket, slot, selection or summary
work. It changes only sequencing inside an already-triggered hard exit.

## Edge cases and verification

Focused regressions cover:

- long inventory with `(reducer, builder)` tuple order: builder cancels first;
- short inventory: the building offer cancels first;
- exactly one mutation per plan and per-order waiting before grace expires;
- no grace clock before successful driver submission;
- critical escalation only when the submitted target itself remains working
  beyond grace;
- terminal first target followed by a distinct second cancellation and then an
  exact-position IOC;
- flat/restart cleanup of stale per-order cancellation state; and
- runner-to-reconciler submission handoff.

The old multi-action return was deliberately restored and the exact two-order
regression failed `2 != 1`. The repair was restored and all seven focused tests
passed; the broader reconciler/runner slice passed 63 tests. The native-Windows
complete suite reached 952 tests: 948 passed and the four expected authenticated
measurement constructors refused because Windows cannot provide the required
POSIX host lock. A disposable copy of this exact reviewed tree then passed all
952 tests in the laptop Ubuntu/WSL runtime in 67.491 seconds. Deployment
verification is recorded below after it completes.

## Deployment and acceptance boundary

Source commit `31c2574` was pushed before deployment. The laptop WSL checkout
was fast-forwarded to the reviewed main history and matched it exactly; its
production virtual environment passed all 952 tests in 67.847 seconds. The
supervisor/runner remained PIDs 837439/837452, both started at 09:14 ET, and
the loaded marker remained `3120a631`. No stop or restart occurred.

Updating the laptop checkout does not reload the already-running Python
process, so the live runner remains on its previous loaded marker until the
operator separately authorizes a restart.
Natural acceptance after that restart requires a real two-order hard exit to
show one building-side cancel, positive terminal proof, then the next cancel;
it must not show `pending=2`, position growth from an untouched builder, or a
retirement caused by the shared cancel timer.

## Activation

At 17:43Z the old runner's final position closed through its existing exit
path. Graceful stop then cancelled its working slate and emitted `flat: true`.
An independent authenticated read at `2026-08-31T17:45:43Z` proved zero open
orders and zero positions. No force-kill or manual flatten was used.

No `src/` or `tests/` file changed after source commit `31c2574`, so the
deployment marker advanced to that exact source commit before startup. Exactly
one size-13/22-slot supervisor PID 937731 and runner PID 937743 started. At
17:50:44Z the read-only local dashboard reported 22 bound markets, 38 working
orders across 19 markets, zero positions, zero unresolved intents and zero
unknown orders. The startup API window recorded 362 attempts at 2.156/s with
zero failures, zero 429s and queue depth one. This accepts ordinary startup;
the specific two-order hard-exit path remains natural-only and pending.
