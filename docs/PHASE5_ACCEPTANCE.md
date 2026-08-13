# Phase 5 readiness gate

The fail-closed readiness evaluator and execution safety primitives are
implemented. The evidence gate remains red. A separate, explicitly authorized
commissioning command can collect one-contract live evidence without changing
that verdict or permitting scale.

There are two distinct verdicts:

1. `trial_ready` permits only a supervised, bounded calibration trial. It
   requires three complete shadow days, at least 100 exit samples for the
   selected timeout, 99% stressed exit completion and loss-budget pass rates,
   passing Phase 3/4 evidence, a flat whole-account snapshot, exclusive use of
   the one API key, maker-lab stopped, a tested market-scoped kill switch, one to three
   markets, one to two contracts, pregame only, no more than 60 minutes, and
   at most 4 REST requests/second.
2. `scale_ready` additionally requires at least three attributable `PAID`
   reward days, 20 independent paid events, five controlled sessions, positive
   conservative reward-plus-trading net, P&L reconciliation within one cent,
   and zero critical live incidents.

Shadow observations cannot satisfy paid-reward calibration because no shadow
order exists at the venue. This is why a tiny trial can eventually become
eligible before scale, while scale remains impossible until payouts settle.

Current verdict: **`DO_NOT_START_TRIAL`**. See `docs/SCALE_REVIEW.md`.

Operator exception: one named market, exactly one contract per quote, 30-second
inventory age, $2 market/$5 session soft limits, $15 emergency hard stop, and a
graceful operator-controlled stop. Startup still requires the whole account flat, exclusive
key ownership, maker-lab stopped, fresh private state, and a current applicable
liquidity program. See `docs/COMMISSIONING.md`.

The same Polymarket account is permitted because the user has only one account,
but not concurrently: maker-lab must stay stopped, the key must be exclusive to
this process, and any open order, position, unresolved intent, or unknown order
ownership blocks startup.
