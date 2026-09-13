# Public-book refresh design handoff — September 13, 2026

## Authority and state

Following the [successful stopped probe](PUBLIC_BOOK_REFRESH_PROBE_2026-09-13.md),
the operator authorized implementation and twice requested bigger-picture review
as a permanent engineering rule. That rule is now in `AI_ENGINEERING.md`, routed
by `AI_OPERATOR.md` and recorded in `DECISIONS.md`. This handoff is design work,
not an implemented fix or production acceptance. No runtime files, configuration,
deployed marker or process were changed by this documentation update. Last
authenticated flat/clear proof remains17:25:11Z; no new account read was made.

Pre-work parity found Windows/GitHub/WSL at `7c7e6f21f`, with runtime marker
`927676d14134fc6d3e0b176af6f301f4961c6b3b`. The private online recovery copy
completed at17:37:33Z. Remote peer was not verified. The failed live run remains
stopped; fresh authority and normal gates are required for deployment/restart.

## Whole-workflow problem

Public WebSocket collection fixed selection scan load, but steady-state quiet
book refresh still called REST and reproduced429s. Choosing another REST rate
without reviewing every book consumer would not address that dependency.
Compare a bounded batched refresh on the existing public full-book stream with
an independent shared collector and narrowly classified REST fallback. Prefer
one lifecycle owner where practical; do not introduce a second executor, ledger
writer or automatic private-account source promotion.

Inventory all book paths before choosing the integration boundary:

- Selection and cached candidate revalidation use `SelectionBookSource` scopes.
- Startup bound-owner collection currently permits REST for missing/stale books;
  recovering positions deliberately bypass collection for exact empty-book proof.
- Runtime quiet-book service and just-in-time entry/exit checks call the gateway.
- Rotation/top-up replace stream instances; providers cannot retain old instances.
- Recovery, inactive/settlement wait and shutdown also request books. A discovery
  checkpoint that rejects a stop request cannot be reused for shutdown exits.

## Candidate architecture and review questions

The measured candidate is full-book unsubscribe/resubscribe with unique request
IDs on the existing public socket. The probe observed one late old-ID frame,
so generation fencing is required, not optional. This is a candidate until the
following design/tests settle its complete lifecycle:

- Coalesce refresh demand across the subscribed owner scope, not one socket or
  subscription per market. Bound control cadence, waits, concurrency and memory.
  The probe tested five-second renewal; faster cadence needs its own evidence.
- Maintain a non-consuming latest-book cache so a direct freshness read cannot
  steal the normal event consumer's message. Preserve actual receipt time; Pong,
  timer expiry and dequeuing do not renew book evidence. Reject future receipts.
- Fence obsolete generation data, including queued/task-held books, so an older
  delivery cannot overwrite a newer accepted snapshot after renewal/reconnect.
- Distinguish positively empty depth from silence, malformed payloads, missing
  market data and socket failure. Preserve exact settlement/recovery proof rules.
- Revoke untrustworthy cache state on disconnect/error; retain owner isolation
  and bounded recovery without silently reintroducing routine REST polling.
- Define any emergency REST fallback explicitly and keep its limiter/backoff.
  Do not manufacture no-REST guarantees if a preserved safety path still uses it.
- Check cancellation during connect/renewal/wait/teardown, including shutdown
  while holding positions. Read workers must never gain ledger/mutation authority.
- Preserve existing account proof and action-time guards. Faster book transport
  does not authorize stale positions, stale lifecycle metadata or broader exits.

Preserve manual REST account mode, $90 HARD, size10/capacity32, max30 flat pending
entry orders excluding positioned-market orders/reducers/exits, lower time/loss
caps, candidate scan limit100, ranking, price/size rules and all entry/exit clocks.

## Acceptance still required

Implement only after selecting and recording the approach/tradeoffs. Add focused
regressions for the lifecycle above, prove deliberate reversals fail, restore,
run the complete suite and perform the permanent dedicated edge-case audit.
Include synthetic32-owner coverage, unchanged ranking/account/risk behavior,
bounded API/control-frame load and no premature stale-book entry authority.
Use a bounded stopped read-only test of the actual production implementation;
the prior standalone nine-market probe is not production-class acceptance.
Future authorized live acceptance must cover startup plus steady-state books,
API failures, useful order progress and natural position exits without inducing
fills or faults. Preserve measured limits and do not promise WebSocket-era
latency or permanent rate-limit elimination before comparable evidence exists.
