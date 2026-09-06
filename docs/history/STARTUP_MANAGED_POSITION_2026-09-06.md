# Startup-managed recovery position — 2026-09-06

## Incident

After the goodwill repair was deployed, authenticated startup found zero
orders and one exact bot-owned `-10.0000` position in
`aec-itfwo-miczun-zoedol-2026-09-06`. Its post-kickoff book was readable but
did not have enough stressed depth for shutdown IOC. Startup classified every
ordinary readable recovery position as `RECOVERY_ONLY`, immediately entered
shutdown, and therefore never opened unrelated reward slots. Existing
exceptions covered only exact empty-book settlement wait and non-open market
wait.

## Narrow repair

One post-kickoff recovery position may now reserve one configured slot while
the remaining slots start normally, only when complete account reconciliation
proves all of the following: exactly one nonzero position, exact bot-owned
basis equal to its quantity, known whole-account risk, zero working or absent
orders, zero unresolved intents, and a valid current market and book. The
recovery owner remains permanently barred from building, keeps the unchanged
passive/IOC exit manager and full portfolio exposure, and cannot be dropped
from scope.

Pregame, unreadable, unattributed, multiple-position, ambiguous-order and
unknown-risk states retain recovery-only or refusal behavior. Selection,
ranking, quote price/size, entry-order caps, HARD, stressed-depth rules, exit
pricing, mutation semantics and configured capacity are unchanged.

## Verification

Regressions prove normal trading mode around the managed owner, exact
32-slot reservation, post-kickoff/readable-book boundaries and ambiguity
refusal. Empty-book settlement wait and order-only startup recovery retain
their prior behavior. Disabling the one-position scope reproduced
`RECOVERY_ONLY`. The complete POSIX review suite passes 1,130 tests.
Commit `99bb436f9bcd3521ed5332b883cdff0693bf07c4` was pushed, deployed
and loaded. Supervisor `394690` and runner `394699` started at size 10 / 32.
The old position remained barred and exit-managed while authenticated entry
orders reached 29 across 29 unrelated markets. The first 202.6-second API
window completed 445 attempts and 39 mutations with zero failures or 429s.
