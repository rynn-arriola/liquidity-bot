# Exact settlement-wait recovery — 2026-09-01

## Incident and evidence

The laptop runner stopped gracefully at `11:45:16Z` with zero orders but did
not prove flat. Authenticated reads through `12:23Z` continued to show one
`10.0000`-contract position in
`aec-itfme-pathom-julbie-2026-09-01`. At `12:22-12:23Z`, public Polymarket
health and the official status page were operational and an authenticated
account read succeeded. The exact market metadata was active/open again, but a
valid Retail book wrapper contained neither bids nor offers and settlement was
not found. The position was therefore real and attributable but had no price
at which code could reduce it.

The deployed startup correctly refused to invent a book, settlement or flat
state, but the refusal also disabled every unrelated reward market. Global
venue health does not imply that every individual market is executable.

## Operator decision

The operator authorized a workaround so one exact unpriceable position does
not make the bot entirely unavailable. This is an availability exception, not
permission to ignore or add to the position.

## Prepared behavior

`EmptyBookError` distinguishes a valid response-backed empty book from
transport, 5xx, schema and identity failures. A recovery owner may enter
settlement wait only when all of these are true:

- the persisted ledger identifies it inside the configured active scope;
- authoritative position is nonzero and exact inventory basis matches it;
- there is no working or `UNKNOWN_ABSENT` order and no unresolved intent;
- whole-account risk state is known;
- current metadata supplies a kickoff that is already past; and
- the exact book response is valid but has neither side.

The owner remains permanently barred from quoting, keeps its private position
subscription and complete account reconciliation, reserves one configured
market slot and its full basis-derived portfolio exposure, and receives a
read-only book probe no faster than every 60 seconds. Unrelated markets may use
only the remaining capacity. A returned book immediately restores the existing
reducer/IOC recovery path. An authoritative zero position releases the wait and
requests normal capacity refill. Any ambiguity or ownership drift removes the
exception and returns to ordinary fail-closed recovery.

Shutdown may finish promptly only after a fresh complete reconciliation proves
that these exact owners are the sole remaining risk and that no working/absent
order or unresolved intent remains. It explicitly reports nonflat state and
retains the non-restarting exit result; no position is called flat or settled.

## Review and verification

Focused regressions cover typed empty-book parsing, unrelated-market startup,
full risk and one-slot reservation, owner-drop refusal, bounded polling, exact
book recovery, positive flat release, ambiguity refusal, expected versus
unexpected selector failures, monitor-only startup, truthful shutdown and the
pregame refusal boundary. A deliberate reversal disabled the scoped startup
authorization; the main availability regression failed `RECOVERY_ONLY !=
TRADE` and passed after restoration.

The final pinned-WSL suite passed 1,010/1,010 in 99.881 seconds after the
critical nonflat shutdown notification was separately reviewed and corrected.
The documentation audit, compile check and `git diff --check` pass. `ruff` is
not installed in the pinned environment, so no ruff result is claimed. No bot
process, venue mutation, production database or deployment marker was changed
while preparing the repair.

The explicit edge audit covered success (remaining slots trade), empty/full
capacity, zero/nonzero/sign-changing position snapshots, pregame and missing
metadata, working/absent/unresolved ownership, selector read failure versus
local defects, book recovery, timer starvation, private/account scope,
portfolio reservation, shutdown, restart reconstruction and rollback. The
exception cannot widen authorization or make a venue call from the action
planner; only the existing read-only selector, book and account paths run.

## Activation gate

Do not restart without explicit operator authorization. Before loading, require
a clean reviewed commit, GitHub/WSL parity, unchanged stopped process state and
a fresh authenticated account snapshot. After the authorized start, accept
only if logs show exactly one settlement-wait owner, at most 23 unrelated owners
at configured capacity 24, no mutation for the waiting slug, exact portfolio
exposure reservation, continued account/private proof and normal exit planning
if a book returns. Roll back and stop on any ownership ambiguity, dropped owner,
unreserved exposure, quote on the waiting slug or missed returned-book exit.

## Live activation

Operator-authorized startup at `13:43Z` loaded executable `0bcfbbc96` at size
10 / 24 slots. The runner reconstructed exactly one settlement-wait owner for
the persisted `10.0000` position and logged `capacity_reserved=1`. Its first
full cycle reached 24 total owners; authenticated acceptance found 38 open
orders across 22 unrelated markets, zero orders for the waiting slug and no
other position. The first 72.2-second mutation window made 183 attempts,
including 44 ordinary mutations, with zero failures, rate limits or cooldown.
Natural returned-book exit and flat slot-release behavior remain uninduced.
