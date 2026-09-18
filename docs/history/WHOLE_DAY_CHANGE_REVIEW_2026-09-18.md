# Review of September 18 changes

## Scope and outcome

The operator requested a review of all changes made today. Reviewed the full
`ebba92509..66c63eea9` diff (42 files), including maintenance timing, daily-event
support and its 24-hour horizon, idle-owner replacement, payout telemetry and
the standalone economics analysis. Also reviewed the September 17 reducer and
fill-receipt changes activated in today's runtime. This task is review only:
no trading source/configuration change, deployment of trading code or restart.

Two reproducible measurement defects remain. Neither output is consumed by
live order selection, risk, reducers or execution. No additional execution or
risk defect was established by this review; passing tests do not establish
future profitability, WebSocket-equivalent latency or permanent absence of429.

## P2: displayed remaining reward includes the pre-kickoff evacuation interval

`RewardMarketOpportunity.maximum_remaining_reward_total` sums each side's
projection through kickoff. The new $1/$1.50 counters and payout log use this
value, but the executor stops flat entry quoting 300 seconds before kickoff.
An offline reproduction with six minutes to kickoff and two viable sides
produced $1.20 remaining; the same reward rate through the bot's actual
one-minute remaining entry window produces $0.20. The counter can therefore
count a market as clearing $1 using time during which it cannot keep entries.

This is a report/measurement error, not a payout claim from the venue and not
a live minimum-payout gate. The separate offline shadow scenario already clips
at the five-minute boundary. Recommended repair: use that same accessible-time
definition for the new telemetry, with cutoff/expiry/midnight regressions,
without changing the established live ranking projection or exit deadline.

## P2: shadow comparison does not validate candidate book timestamps

`selection_measurement` records `book_observed_at`, but `shadow_rank` checks
only the overall scan timestamp. An otherwise supported synthetic candidate
with a book dated one day before the scan, or one day after it, receives
`positive_observational`, rank1 and a numeric reward scenario. This test uses
lower research sample thresholds only to exercise the supported-model branch;
it does not certify the production historical sample.

An up-to-date envelope cannot make stale or future input current. Recommended
repair: validate individual book timestamps and define an explicit bounded
measurement freshness rule; unavailable, future or over-age inputs must withhold
that candidate's scenario/rank. Do not renew their timestamps or add a venue
probe. Live placement already has its separate freshness gates.

## Verification and limits

Reran212 existing installed-environment tests:44 economics,28 rewards,
41 selection,14 maintenance,9 deferred-exit/receipt and76 reconciler tests;
all passed. The two new counterexamples above expose gaps in those passing
tests. Reused the unchanged release's1,835-test full-suite evidence and207
deployment hashes rather than claiming another full-suite run. Inspected
day-of priority, midnight/expiry admission checks, typed reward unavailability,
flat/clear release rechecks, exact reducers, settled-loss boundaries, shared
request admission and measurement failure isolation.

Initial read-only live check at22:40:09Z overlapped fills/top-up:28 orders,
three positions, only two exact reducer matches and two local absent rows.
This was not reported as clean acceptance. Logs showed new-position handling
and replacement orders; the independent follow-up at22:40:46Z proved32 orders,
28 flat entries and four positions, each with an exact correctly directed
10-contract reducer, zero unknown orders and zero unresolved intents.
Supervisor350908/runner350932 remained unchanged. The22:40:19Z API window had
167 requests/67.8s (2.464/s), zero failures/429s. A preceding busy window ran
3.779/s; post-flat refill took60.7s. These are scoped observations, not proof
of sustained performance improvement or that today's changes caused latency.

Pre-review parity matched Windows/GitHub/WSL HEAD66c63eea9 and loaded source
f2e6aba5b; verified online-backup copy captured22:39:34Z. The known offline PC
was explicitly skipped. Review findings are recorded for follow-up; both
measurement repairs remain outstanding and no strategy enforcement is enabled.
