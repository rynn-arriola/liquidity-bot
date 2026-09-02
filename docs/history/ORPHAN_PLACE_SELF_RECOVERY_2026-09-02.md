# No-ID PLACE self-recovery — 2026-09-02

## Symptom and mechanism

After authenticated maintenance health checks recovered, public market-stream
recovery repeatedly raised `runtime recovery cancel is not yet reconciled` for
one market. The only unresolved mutation there was actually a PLACE whose 5xx
response carried no exchange order id. Generic
`market_has_unresolved_intent()` made quarantine treat every operation as a
cancel, so the unchanged local ambiguity consumed all 12 stream-recovery
attempts and terminated the runner. The next startup correctly refused the
same ambiguity, requiring the stopped-only resolver even though independent
venue reads showed zero open orders and zero positions.

## Repair

The runner now distinguishes an `UNCERTAIN` PLACE with no exchange id from a
CANCEL. It never resends that PLACE. Each complete REST reconciliation can add
one proof only when the venue reports no open order and no nonzero position in
the exact market, no local order row exists for the intent client id, and the
ledger has no execution in that market at or after intent creation. Three
consecutive proofs spanning at least ten seconds terminalize the abandoned
mutation with an error that explicitly preserves the unknown original HTTP
outcome. Any positive order, position, local-order or execution fact clears the
accumulated proof and leaves the intent unresolved.

Startup treats proof accumulation as an unbounded, operator-interruptible flat
wait instead of a refusal/restart loop. Public-stream recovery treats the same
wait separately from its bounded transport/schema retry budget; once proof
completes, it reconnects with a fresh book. Genuine stream failures still
exhaust at the existing bound. The compatibility cleanup path was also made
safe when automatic startup has not selected its first market yet.

The manual resolver remains available only as a stopped-runner fallback. Its
wording now correctly claims proof of no *surviving live risk*, not proof that
the original create never happened.

## Safety boundary

- no uncertain PLACE is resent;
- no proof advances from a failed account read;
- any open order or position in the market blocks clearance;
- any local order record or later recorded execution blocks clearance;
- CANCEL and every identified-order path retain their existing reconciliation;
- HARD and operator stop remain terminal during the wait;
- pricing, selection, sizing, risk thresholds and normal order decisions are
  unchanged.

## Verification

Focused tests cover startup convergence, exact public-stream recovery, no
duplicate create/cancel, open-order and position proof resets, later-execution
blocking, CANCEL isolation, the ordinary bounded stream-failure path and the
stopped-only resolver. The full live-commissioning class passes 396 tests and
the authoritative WSL repository suite passes 1,052 tests in 107.828 seconds.
A deliberate reversal that disabled candidate repair failed the startup
recovery regression with `startup orphan recovery did not converge in-process`.

Final commit/deployment state is recorded in the release handoff. The live bot
was not restarted while this change was prepared.
