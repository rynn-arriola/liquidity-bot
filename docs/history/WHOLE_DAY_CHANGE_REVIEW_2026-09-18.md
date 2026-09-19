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

## Command 1 deployment and restart

The operator subsequently authorized Command1. The supervisor was stopped first
and the runner exited gracefully at23:05:53Z with `flat=true`. Authenticated
23:06:49Z reads independently proved zero venue orders/positions and zero
unresolved intents, unknown orders, working ledger rows or ledger positions.
The stopped-runtime online backup captured23:06:34Z was verified privately.

Windows/GitHub release `aae33765855ebb7a03dd79336a1a709acc960362` was already
pushed and reviewed. WSL fast-forwarded only that prepared commit; all207
runtime/test/config/script hashes matched it and the Windows source. Reused the
completed regression/reversal/edge review; the exact installed checkout then
passed all1,842 tests in176.017s. The deployed marker was advanced only after
those checks. A second authenticated flat/clear gate passed at23:10:47Z.

Restarted supervisor388103/runner388121 at23:10:47Z using saved REST account mode,
size10 and32 market slots. Four$25 settled-loss windows, loss-scaled pending-entry
caps, entry quality, day_of priority, daily spare-capacity admission, reducers
and exits remain unchanged. No ledger/risk reset or source promotion occurred.

The23:11:27Z complete scan records38 candidates and70 measured sides; all70
cutoff-aware amounts are lower than their retained raw projections. The record
explicitly reports `enforcement=none`, exit buffer300s and book-age bound5s.
Initial scans used39 and30 public-WebSocket books with zero REST fallback.
The23:11:45Z risk meter retained loss0/hard25/cap30, no hold, matched-fill basis
and open orders/positions excluded. Initial deployed parity passed with the
runtime and marker onaae337658; known offline PC remained unchecked.

Authenticated23:13:26Z acceptance showed29 flat-market entry orders across27
markets, zero positions and zero unknown orders. One in-flight create remained
at that instant and was positively resolved in the23:13:26.270Z runner record
with remaining0; subsequent cancel/create transitions also resolved normally.
The first141.6-second API window made282 attempts (1.992/s), zero429/cooldown
and five ordinary individual-order publication404s. Startup decision latency
was high (p9585.458s); this sample is startup acceptance, not steady-state
latency proof. Corrected telemetry has live acceptance, while reward accuracy
still requires prospective complete-period payout comparison; no profitability
or sustained zero429 claim is made.

## Post-restart order-count dip (read-only diagnosis)

The operator reported fewer orders. The same runner/supervisor remained live.
Authenticated23:18:46Z showed20 flat entry orders plus two exact reducers for
positions+10/-7, zero unresolved intents/unknown orders. Read-only ledger evidence
found26 EXPIRED orders observed23:17:37–23:18:38Z; their event times match the
existing five-minute GTD lifetimes of the clustered startup placements. This
is the September15 host-outage safeguard, not either new measurement change.
Position-priority cancellation/repricing and entry-quality/touch changes also
overlapped the refill; replacement entries appear throughout23:18Z.

The23:19:15Z settled-risk checkpoint was+$1.75, cap30, no hard-stop trip. Completed
API windows through23:19:08Z all show zero429/cooldown; last window3.684/s with
two ordinary order-retrieve404s. There is no evidence this dip was a loss-cap
reduction, global recovery or new measurement enforcement.

A23:19:59Z account read overlapped replacement of the-7 position's reducer and
showed no reducer in that order snapshot; it was not called protected then.
Subsequent logs show cancel confirmation and replacement acknowledgements.
Authenticated23:20:40Z independently confirmed28 flat entries plus one OPEN
BUY7 reducer for the sole-7 position, zero unknown orders, one in-flight intent.
The earlier+10 position had naturally returned flat23:19:03Z. Counts and the
ledger can differ transiently because these are sequential reads during trading.

The expiry/refill gap is real. Repeated reducer repricing was also observed,
but this read-only diagnosis does not establish that each reprice was unnecessary.
A follow-up may review renewal distribution/refill scheduling and reducer churn
without weakening expiry or position priority; no change is authorized by this
status question. No trading code/configuration change, deployment or restart.

## September18 20:58 ET log and account follow-up

Same supervisor388103/runner388121 remained live. Authenticated00:58:05Z
(September19 UTC) read:30 flat entry orders plus exact BUY4.03 for-4.03
budole/pytiva and SELL7 for+7 hitsat/jiayua; zero unresolved intents or unknown
orders, ledger32 orders/two positions agrees. At00:58:15Z settled window P&L
was+$0.0217, cap30 and no HARD trip. Recent completed API windows through00:57:44Z
had zero429/cooldown; individual-order404s continued, with latest window zero
failures. Another position naturally flattened at00:57:06Z.

Not an error-free run: at00:55:58Z one xinyao/prihon strategy-state read raised
execution_state_unknown; subsequent00:56:18Z positive cancel observation and
the clear authenticated follow-up show no remaining account ambiguity. Hitsat's
exit quote was repeatedly canceled/replaced. Budole's first reducer was
acknowledged26.825s after initial position observation, including cancellation
of its risk-building order. Both current positions have exact working reducers,
but this does not certify optimal repricing or consistently fast exits.
Continue the separately recorded refill/reducer-scheduling investigation before
claiming nothing needs improvement. Status/log review only; no code/config
change, trading mutation or restart.
