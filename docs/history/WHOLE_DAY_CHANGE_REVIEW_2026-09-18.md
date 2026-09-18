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

## Authorized measurement repair (subsequent task)

The operator then requested both fixes. They are prepared in the Windows
checkout, not loaded into the running trader. No stop, restart, order mutation,
API probe, ledger repair or risk/strategy change is part of this repair.

The reward calculator now supplies a separate optional `remaining_entry_reward`
measurement through the earlier of program expiry, daily ET midnight and the
existing kickoff-minus-300-second boundary. The payout counters and log consume
that field; established projected reward, raw maximum and ranking inputs are
unchanged. Missing, negative or nonfinite measurement is unavailable, not a
fallback to the overstated raw amount. Logging failures remain isolated.
Scan JSON retains the original field and adds the corrected measurement plus
its timing constants; existing schema-1 records remain readable.

Offline shadow comparison requires each candidate's timezone-aware book receipt
to be between zero and five seconds old at the recorded scan time. Missing,
invalid, naive, future and over-age books receive no scenario, score or shadow
rank. Validation cannot be widened by editing the logged age setting. A later
report time does not invalidate a book that was fresh at the scan. This can
withhold more research candidates after long scans, intentionally; it does not
refresh books, add API requests or reject live entries.

Focused verification passed123 tests including runner logging/failure isolation.
Deliberately removing the cutoff, restoring the old raw telemetry source and
disabling book-age rejection caused seven expected failures across three
regression tests. Restoring all repairs returned the123-test set to green.

Dedicated edge review covered zero/empty/blocked sides, both reward periods,
expiry before kickoff, ET midnight, 23/24/25-hour DST days, exact300-second exit
boundary, missing/negative/nonfinite measurements, absent/malformed/naive book
timestamps, exact zero/five-second freshness, microsecond stale/future bounds,
mixed valid/invalid candidates and unchanged input objects. Existing eligibility,
ranking, quantities, entry capacity, reducers, ownership, loss accounting,
recovery/shutdown and source selection remain authoritative. No persistent schema
migration or background task is added; rollback requires no ledger rewrite.
The first complete run passed all code tests but failed the documentation audit:
BUGFIXES briefly exceeded its100-line budget while the suite was running. That
index was compacted, and the final full suite passed all1,842 tests in177.799s
using the production virtual environment against an isolated native copy whose
368 tracked-file hashes matched the reviewed Windows tree. Focused tests and
deliberate reversals ran directly against that Windows source. The documentation
audit and final diff check pass. No production source was overwritten for testing.

At23:01:39Z the original supervisor350908/runner350932 were still running with
their22:23:58/59Z start times; loaded source remainsf2e6aba5b. No authenticated
exposure read was repeated for this measurement-only repair, so22:40:46Z remains
the last dated venue proof, not a current order/position assertion. Pre-work
parity matched Windows/GitHub/WSL d77cac1a1 and verified an online backup captured
22:45:56Z; the known offline PC was skipped. Corrected runtime telemetry requires
reviewed deployment and a separately authorized safe restart. The standalone
offline script is usable from the reviewed source without restarting trading.
