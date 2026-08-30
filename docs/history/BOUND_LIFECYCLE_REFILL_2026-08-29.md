# Bound lifecycle refill — 2026-08-29

## Outcome

The source repair is complete and tested but is not deployed or loaded by the
live runner. A bound market that positively reaches kickoff now requests one
safe incremental top-up instead of waiting for the ordinary 15-minute selector.
The live bot, dashboard, venue orders and positions were not changed while the
repair was developed.

## Live evidence

At 02:00 UTC eight bound markets reached kickoff together while two positions
were being exited. Working orders fell to 16/17 across 11 markets. The refresh
path logged the expected `no longer pregame` condition 12 times, but did not
schedule replacement. The relevant transport windows contained zero API
failures and zero 429s, and selection still had 81 viable candidates.

The ordinary 02:12:52 UTC selector later replaced eight expired owners and one
economic-idle owner. It restored 20 active markets and 33 working orders with
zero positions, unresolved intents or unknown orders. Recovery used the same
runner PID and no restart. This isolates the missing mechanism to lifecycle
scheduling rather than supply, API pressure, a VPN/proxy response or venue
maintenance.

## Root cause and repair

Startup and live refresh shared one `no longer pregame` exception. Rejecting an
already-started startup candidate is correct, but a bound live owner reaching
its expected kickoff is normal lifecycle evidence. Repeatedly treating that
transition as a runtime error neither improved safety nor requested refill.

Live bound refresh now clears the ended owner's incentive/economics and records
one `reason=lifecycle` top-up request. Startup retains the strict exception.
The request joins the existing incremental top-up transaction and is consumed
only through its bounded cadence.

## Safety boundary

The repair does not cancel retained orders, release an owner directly, alter
position handling, raise the 20-slot ceiling or add an API request type. An
urgent near-deadline position still prevents selection. Replacement still
requires flat/order/unknown/unresolved proof, complete reconciliation, fresh
metadata/incentive/book viability, release revalidation and safe stream handoff.

## Verification

- Focused `TopUpSafetyTests`: 48 passed.
- Deliberate reversal: removing the live lifecycle acceptance reproduced the
  missing request and the regression failed exactly.
- Full Windows suite: 844 passed in 57.538 seconds.
- Deployment completed after the operator-authorized flat stop. The 02:40:36Z
  start gate proved zero orders, positions, unresolved intents and unknown
  orders; all 844 WSL tests passed in 59.440 seconds. Marker `859b891` started
  at size 13 and reached 20 markets without an API failure/429. Natural kickoff
  acceptance of `reason=lifecycle` remains pending.
