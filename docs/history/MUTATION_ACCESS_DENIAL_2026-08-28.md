# Mutation access denial incident — 2026-08-28

This is the detailed evidence record for the 20-slot collapse that began near
22:07 UTC. Current operational state remains in `STATUS.md`.

## What happened

- The runner started clean on `2ece3c3`: 20 bound owners, 18 quoting markets,
  34 working orders, zero positions and zero unresolved intents.
- From 22:07 UTC, order creates began returning Polymarket Retail HTTP 403,
  structured error code 7: the connection looked like a VPN or proxy and the
  venue could not verify its location.
- The old mutation gateway labeled every failed POST as outcome-uncertain.
  `LiveOrderExecutor.place` therefore persisted each rejected create as
  `UNCERTAIN`, even though the response positively proved rejection.
- Each owner then reached `unresolved_mutation_timeout` and retired. By
  22:27 UTC all 20 slots were retired and the private recovery reported one
  working venue order. The same selection snapshot still had 27 viable
  markets, so this was not low supply.
- After that final order disappeared and positions were flat, SIGTERM waited
  300 seconds without entering shutdown. The no-ID repair had no ids to await,
  ready stream tasks kept completing synchronously, and the runner held about
  67% CPU. The reviewed force path was used only after order/position proof.

## Alternatives disproved

- The post-flat fast-refill optimization never triggered: no
  `[top-up-request]` or `[top-up-cache]` preceded the incident. The 22:11 UTC
  refill was the ordinary scheduled path.
- Private-shard silence was concurrent but not causal. The first 403 preceded
  it, scoped recovery retained orders with `cancels=0`, and 403 responses
  continued after the shard recovered.
- Public Polymarket status reported operational and the August 28 changelog
  contained no matching maintenance notice. That does not prove account
  mutation access, but it rules out an announced platform-wide event.
- This exact venue message occurred on August 26 and cleared after a host
  reboot while the same account/IP later traded. The evidence does not support
  a permanent ban, though only a successful later mutation proves recovery.

## Repair

1. A response-backed HTTP 403 is now `OrderMutationRejected`, not an ambiguous
   transport outcome. PLACE and CANCEL intents transition from `SUBMITTING` to
   `FAILED`; no retry reuses the intent and no order/cancel state is inferred.
2. A denied mutation opens an account-wide 60-second new-entry circuit; later
   entry slots stop before the API and one later desired entry probes recovery.
   Cancels and reducers bypass an entry denial. If their own request is denied,
   risk-reducing mutations use a separate 10-second cooldown. Success clears
   only the matching circuit.
3. The first denial sends one critical operations alert. Repeated probes remain
   in the log without flooding Discord.
4. Startup repairs only legacy `UNCERTAIN` rows containing all three durable
   response tokens: `PermissionDeniedError`, `status=403`, and `code=7`.
   Timeouts, 5xx, generic 403, missing IDs, absence and near-matches remain
   unresolved and continue through the stopped-only proof path.
5. The terminal shutdown path is not converted into an entry loop. It retains
   its existing risk-reduction attempts and positive flatness requirements.
6. Every outer run-loop pass now yields for 10ms. When every active owner is
   blocked by a no-ID intent, the loop also idles 250ms instead of running a
   useless decision sweep. This adds no REST call and preserves normal stream,
   account, summary and two-second unresolved-service cadence.

## Verification

- Focused coverage proves raw SDK 403 metadata, already-normalized errors,
  PLACE and CANCEL failure states, exact legacy repair, near-match refusal,
  entry-circuit aggregation, cancel/reducer bypass, scoped recovery, no market
  retirement, one API call across repeated entry decisions, and unchanged
  timeout ambiguity.
- Deliberately disabling the 403 classifier reproduced the production result:
  one `UNCERTAIN` no-ID intent, quarantine/recovery and failure of both exact
  regression tests. Restoring it passed the focused set.
- Disabling the full-no-ID detector made both idle/stop regressions fail;
  restoring it passed. Disabling the cancel/reducer bypass made its circuit
  regression fail. The complete local suite passed: 776 tests.

## Activation state

Commit `31f815c` is pushed and deployed. All 13 file hashes matched, all 776
local/server tests passed and the server documentation audit found no issue.
An authorized start at 22:56Z loaded `31f815c` and repaired exactly 20 legacy
intents before the orphan gate. It bound 20 owners. Entry probes at 22:57:26,
22:58:39 and 22:59:51Z each received the same HTTP 403/code-7 VPN/proxy denial;
all later entry decisions backed off locally. At 23:00Z the dashboard showed
20 bound, zero retired, zero unresolved, zero positions and zero orders. This
accepts the containment path but not access recovery.

The operator then withdrew restart authorization. Normal SIGTERM completed in
about 30 seconds and the 23:01Z terminal record proved `flat=true`. Supervisor,
runner and maker-lab are stopped. Do not restart without new authorization;
only a successful later mutation proves the server's access recovered.
