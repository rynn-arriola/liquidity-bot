# Exit scheduling review — September18, 2026 ET

Scope: operator requested review, not implementation. Trading source, settings,
orders and processes were not changed. Live source remains `aae337658`.

## Confirmed findings

1. **High priority — building-only orders evade urgent inventory coverage.**
   `rest_fallback.py:_position_priority_must_yield` checks whether *any*
   working order exists, not whether an exact reducer exists. A long position
   with only a BUY entry and a future deadline returns false. The ordinary
   reconciler still cancels that builder when visited; the defect is loss of
   urgency before periodic work. `_position_near_its_flatten_deadline` contains
   the same any-order shortcut. Wrong-side/undersized orders merit coverage
   tests in the repair, not an assumption that all working orders protect risk.
2. **High priority — covered positions can precede new uncovered fills.**
   `live_commissioning.py:_decision_sweep` considers other priority owners when
   the current owner is flat, but proceeds with a current positioned owner.
   A fake account refresh discovering a new building-only position while an
   already covered position is being visited evaluates the covered owner first.
   Immediate same-owner cancel/replace continuation can further consume time.
   Existing tests prioritize positions over flat entries, not these urgency
   classes among positions.

Two isolated fake-client reproductions in the ignored local review harness
passed, confirming current behavior without venue mutations. The117 existing
reconciler/REST-priority/reducer-continuity tests passed; separate self-chase
tests were also run. These are diagnosis tests, not proof of a repaired build.

## Live timing and uncertainty

Budole/pytiva position-4.03 observed00:53:54.621396Z September19 UTC.
Builder cancellation sent00:54:13.800681Z, acknowledged00:54:13.853757Z:
19.232361 seconds after initial observation. Positive cancel observed
00:54:17.593716Z; BUY reducer sent00:54:21.375979Z and acknowledged
00:54:21.446498Z, total26.825102 seconds. Hitsat/jiayua repeatedly repriced its
SELL reducer during that interval. The code gaps are consistent with delayed
priority, but do not attribute every elapsed second to one cause or prove loss.

Repeated SELL replacements at the same0.85 price were observed. Existing code
retains exact-size reducers already at current touch or desired improved price.
Logs do not capture the cancel-time touch/desired-price inputs needed to prove
each reprice unnecessary. Do not remove repricing or alter exit-price policy
solely from repeated replacement prices.

Correction to earlier status: the00:53:19.295220Z API window contained one
`balance@429` among263 attempts over77.7s (3.384/s), plus five order404s.
Later00:54:40–00:57:44Z windows recovered with zero429. This does not establish
the429 as the cause of the26.825s delay. Latest completed01:19:01–01:27:39Z
windows had zero429/cooldown, approximately3.221–3.530 requests/s; individual
order404s remained. No venue-fault or permanent zero429 claim is made.

## Recommended repair, not yet authorized

Use explicit urgency classes: due hard exits, new fills with builders or
missing/incorrect reducers, then optional reprices of already covered inventory,
then flat-entry work. Apply consistent exact-coverage evidence to the pre-periodic
urgent pass, discovery deferral and inter-position scheduling. Preserve cancel
terminal proof, unknown-state refusal, per-owner backoff, bounded fair visits,
stop handling, hard-exit budgets and all price/risk/business settings.

Prefer this focused scheduler correction over faster blanket polling or parallel
order executors: it addresses ordering without deliberately increasing REST
request rate or adding mutation races. The tradeoff is delaying optional
reprices/refill while uncovered inventory is serviced. Add cancel-time quote
diagnostics to assess churn separately. No claim that this eliminates429s.

Required repair acceptance: long/short, partial fills, builder-only/mixed/wrong
quantity, multiple simultaneous fills, due exits, unknown cancels, backoff,
retirement, shutdown, both account-source modes and exhausted visit budgets.
Require regression/reversal/full suites and authorized deployed acceptance.

## Final read-only live check

Same supervisor388103/runner388121, uptime2h16m at01:27Z, no restart.
Authenticated01:27:30.327479Z:30 orders across23 markets,27 flat entries and
three exact reducers: BUY10 for chypav/kormyk-10, SELL10 for volyur/kryvit+10,
BUY3 for yidwan/elezah-3. Zero unresolved intents/unknown orders. Sequential
ledger read had33 working orders/three positions, so it is not identical-count
venue proof. Earlier01:21:56Z snapshot had four positions including a builder
and one reprice gap; it was not claimed fully protected. No API/trading mutation
was made by this review. Documentation-only handoff is committed separately.
