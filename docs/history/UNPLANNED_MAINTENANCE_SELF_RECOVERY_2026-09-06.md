# Unplanned maintenance self-recovery — September 6, 2026 ET

## Request and production evidence

After the reporting repair, the operator requested automatic recovery when
unexpected Polymarket maintenance prevents cancels/exits, without a manual
restart and without changing ordinary trading rules. The existing no-restart
instruction remains in force. All work here is review-tree code and synthetic
tests; no real outage, order, fill, cancel, restart or position was induced.

Retained production logs show the September 5 unplanned failure began around
17:46Z with HTTP 500. Three complete account-health samples passed by 19:52:42Z
but 40 open orders remained. Waiting for cancel proof exhausted 12 market
reconnect attempts at 20:02:01Z and ended non-flat at 20:17:12Z.
The [cancel-proof repair](CANCEL_PROOF_STREAM_RECOVERY_2026-09-05.md) already
fixed that particular budget defect in `78656af90`, which is contained in the
current loaded `99bb436f9`. Do not misreport it as a new unpatched incident.
September 6 logs separately show ordinary outage health recovery at 10:55:32Z
and scheduled resume health completion at 12:20:15Z. The later empty slate was
the already-fixed goodwill classification/startup-position incident.

The official [changelog](https://docs.polymarket.us/changelog) was re-opened:
its latest Retail maintenance entry is v0.0.83 for September 5–6, 02:00–06:00
ET. The linked status page and public JSON returned tool retrieval errors
during this audit; no claim of current venue-wide health is drawn from them.

## Additional reproduced recovery gaps

1. The main consumer handles a failed market-data task before its private task.
   A terminal cancellation can already be held by that private task or arrive
   in its socket queue during quarantine. Market recovery runs outside the main
   event drain and previously never consumed those events. A lagging GET-by-ID
   could then exhaust recovery even though positive terminal proof was available.
2. Once an outage hold already existed, HTTP 429 from a read-only health probe
   escaped as a terminal exception. Rate limiting is not proof of maintenance,
   but neither should it terminate an existing no-entry recovery hold.
3. The outage classifier followed implicit Python exception context. A new
   schema error raised while handling an old disconnect inherited that old
   disconnect text and could be retried indefinitely as maintenance. The new
   error must be judged from itself and explicitly chained adapter causes.

These are code/test reproductions, not claims that all three were observed in
the September 5 account. The user-visible symptom has multiple mechanisms.

## Repair and authority boundary

Market-only recovery takes ownership of the private consumer: it applies a
completed task's oldest event first, or cancels/awaits a pending read before
draining. A failed private reader routes through the existing both-stream
recovery. Quarantine then drains before its first reconciliation and again
after cancellations, before the strict terminal read. The normal caller creates
exactly one replacement private consumer when recovery returns. Ordinary
quarantine callers keep draining disabled because their task-held event could
otherwise be overtaken. Parent cancellation is not swallowed.

The existing no-entry hold resets its healthy streak on a confirmed 429 and
keeps its one-minute poll interval. It still requires three consecutive complete
orders/positions/balance reads; these probes never apply order absence as proof.
Only explicit exception causes can classify a wrapped outage. Unknown schema,
ownership/integrity errors and genuine reconnect bounds retain their refusal
paths. Cancel acknowledgements remain nonterminal; retries still require the
existing positive OPEN protocol. No duplicate create is introduced.

No change was made to price/size, selection, slot caps, pending-order policy,
HARD, scheduled evacuation, one-hour scheduled hold, position exit clocks,
IOC/depth constraints, supervisor restart policy or manual-stop behavior. The
fix cannot make a down endpoint accept cancellations or force liquidity into
an empty/closed market. Unknown exposure stays unknown and new entry stays
blocked until the existing proof gates pass.

## Verification and dedicated edge audit

Regression coverage includes terminal proof already held by a private task,
proof arriving in the cancellation response window while GET-by-ID returns 404,
pending-reader cancellation, fresh reader handoff, both streams failed, zero
new creates, actual cancel count, 14 pending-proof iterations beyond the old
12-attempt ceiling, a 429 between healthy probes resetting the streak, and a
new schema error inside the old disconnect handler refusing immediately.
An explicitly wrapped timeout still classifies as a transport outage.

Three deliberate reversals each failed: disabling the recovery drain exhausted
the reconnect budget; disabling the 429 continuation raised the probe error;
restoring implicit-context classification retried the schema error until the
test's protective ceiling. All fixes were restored before the complete suite.
The combined final review tree passed all 1,138 tests in 110.470 seconds under
the production WSL Python environment. This includes the held $90 policy and
reporting repair; neither is loaded into the running bot.

Dedicated final review checks wire-order preservation, a task completing during
cancel/await handoff, empty/full queue, missing drain compatibility, cancelled
parent, dual-stream loss, true invariant failure, no terminal proof, prolonged
outage, healthy-streak resets, recovery after confirmed cancel/fill, operator and
HARD stops, restart ownership gates and all configured slots. The added drains
reuse authoritative private events and add no REST request. No schema, persisted
data, recovery budget or trading risk has been loosened. Rollback is source-only
to the preceding reviewed build; the separately prepared reporting/$90 changes
must not be accidentally deployed or reverted.

## Deployment status

Final authenticated evidence at 2026-09-07 03:24:47.315287Z found 27 open
orders, two nonzero positions, zero unowned orders and zero unresolved/unknown
ledger state. Supervisor 394690 and runner 394699 were still the same processes.

The subsequent authorized Command 1 deployed/restarted `43e9db826`; native
tests passed 1,138/1,138. The new $90 HARD correctly ended the run flat with no
new order because the inherited ET-day loss was $104.2022. See the
[deployment evidence](REPORTING_RECOVERY_DEPLOYMENT_2026-09-06.md). Still observe
natural recovery, terminal order proof, retained position management and resumed
ordinary entries. Do not induce maintenance or claim production acceptance from
unit tests. An actual API outage can prevent every exit; automatic software
recovery cannot guarantee flatness during venue downtime.
