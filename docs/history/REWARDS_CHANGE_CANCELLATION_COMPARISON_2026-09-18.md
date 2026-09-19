# Before/after rewards-change cancellation review

September18 ET, captured September19 02:21:08Z. Operator requested investigation
before further changes because cancellations appeared to begin with today's
rewards API adaptation. Read-only diagnosis: no production code/configuration,
deployment, restart, order mutation or ledger repair.

## Result

Exit repricing existed before today's adaptation, and is substantially more
frequent in the current run. Ordinary entry-touch cancellations are lower in
the matched comparison. These are distinct paths; neither "all cancellations
are new" nor "nothing got worse" fits the evidence.

The actual exit-price planner and REST position-service files are byte-identical
between the pre-reward release `0e097b51a` and loaded `aae337658`. The majority
of current exit reprices are concentrated in three old day_of markets, not the
daily24h fallback. No direct new reward-rule-to-exit-cancel defect was proved.
The wider discovery horizon did accompany substantially slower scans, which
can add serial scheduling pressure. That is a relevant latency concern, not
proof of the reason for every reprice or of a venue-side defect.

## Matched-clock evidence

Both intervals are19:10:47–22:21:08 ET, September17 versus September18
(23:10:47Z–02:21:08Z on the following UTC date), approximately3h10m each.
Read the existing124,296,995-byte runner log once to its initial length; no
new venue queries were used for the historical comparison.

| Metric | September17 | September18/current |
|---|---:|---:|
| Exit reprice CANCEL decisions |113|352|
| Entry left-touch CANCEL decisions |456|219|
| Reward-ineligible entry CANCEL decisions |1|5|
| Timed-exit CANCEL decisions |16|21|
| Position-initial observation events |52|73|
| Successful scan records |16|13|
| Median scan duration |8.4s|35.7s|
| Maximum scan duration |11.7s|41.3s|
| Observed weighted REST requests/s |3.311|3.201|
| Logged HTTP429s |14|10|

Counts are `[action] kind=CANCEL` plans classified by exact reason, not proof
of completed exchange mutations or unnecessary cancellations. Position-initial
events are not guaranteed independent round trips. API windows are assigned
by their end timestamps and may straddle interval boundaries; rates are
sum(attempts)/sum(seconds), not an average of averages. Different market mix,
position exposure, books and restarts prevent a controlled causal comparison.

The current run has3.12x the exit reprice plans, versus1.40x the position-initial
events. This warrants attention but is not an exposure-time-normalized effect
estimate. Aggregate request rate did not rise in this comparison; latency and
request composition matter separately. Neither interval was429-free.

Across the full September17 ET day the log contains651 exit-reprice decisions.
Today before the first daily-support restart it contains another202, the last
at14:42:33Z. The September13 archived review also documented repeated same-price
reducer cancellation/repost and its then-fixed self-chase defect. Thus the
behavior predates today's rewards work; those earlier actions are not all
asserted to be erroneous.

## Release separation

Boundaries are verified deployment/restart handoffs, not git commit timestamps.
Intervals include stop/review gaps and must not be compared as equal exposure.

| Loaded stage begins UTC September18 | Exit reprice decisions before next stage |
|---|---:|
|17:19:51 daily support, six-hour scope `af811c874`|120|
|18:45:19 daily24h `34f52b1d7`|40|
|20:33:01 payout telemetry `6323dc302`|18|
|21:09:07 idle replacement `59691b29b`|41|
|22:23:59 economics logger `f2e6aba5b`|15|
|23:10:47 measurement correction `aae337658`, through02:21:08Z|352|

The measurement-only final release did not alter exit pricing. The large
current count is not a uniform immediate step at the first daily24h activation.

## Concentration and old/new reward programs

| Current market | Exit reprice plans | Building-placement observations | Recorded period | Entry hours before kickoff |
|---|---:|---:|---|---|
|hitsat/jiayua|149|66|day_of|2.67–5.79|
|sibzhu/miuhir|53|20|day_of|3.36–5.60|
|qihzho/bendud|45|52|day_of|2.64–5.79|

These three account for247/352=70.2% of current exit-reprice plans. All examined
current-run placement observations for them were day_of and inside six hours;
none relied on the expanded24h daily gate. These are placement observations,
not independent fills or proof that reward parameters never changed between
observations. Market-specific book behavior still needs exact cancel-time data.

## Source-flow audit

Reviewed all ten runtime modules changed between `0e097b51a` and `aae337658`:

- `adapters.py`/`models.py`: conservative incentive fetch receipt timestamp;
  no incentive endpoint/auth replacement or new private-stream behavior.
- `commissioning.py`: approved daily fallback/horizon configuration; quantity,
  loss controls, exit clocks and source limits unchanged.
- `rewards.py`: explicit daily period/day denominator and ET-day proof;
  separate measurement cutoff. Existing day_of remains separately selected.
- `market_selector.py`: wider discovery, day_of-first candidate/viable order,
  bounded rotating book scan and report-only payout counters. This changes
  available workload/market mix, not reducer pricing.
- `live_commissioning.py`: carry period rules through contracts/economics/
  entry admission; optional flat/clear reward-idle replacement; isolated
  measurement calls. Exit-mode contract application returns before reward
  eligibility checks. Idle replacement cannot release a positioned owner.
  Decision sweep and position action driver have no diff in this range.
- `market_state.py`: reward-unavailability dwell evidence for optional idle
  replacement, not a new exit state machine.
- `discord_summary.py`, `reward_census.py`, `selection_measurement.py`:
  presentation/observation, not trade authority.

Identical git blobs across both releases:
`reconciler.py`=`472213c7c78ec53aabcf10e43a4648f700e3a581`;
`rest_fallback.py`=`70181ae5e98995cae91d5ab7e23a89fc91af3fdf`.
The exact-touch self-chase protection remains present. The two prepared
coverage/inter-position-priority fixes address weaknesses already present
before rewards adaptation, not new execution code inserted by it.

## External evidence and limits

Reopened the [official changelog](https://docs.polymarket.us/changelog): the
latest listed entry remainsv0.0.91 for September18 maintenance; no matching
published reward-cancellation change was found. This is not evidence the venue
changed nothing. The linked live status page and summary API were inaccessible
through the browser tool, so current platform health is not asserted.
The earlier exact Retail daily_event observation proves availability at its
capture time, not the venue's precise rollout time. See the
[canonical reference](../POLYMARKET_API_REFERENCE.md).

## Recommended next work

Preserve the confirmed exact-coverage/urgency repairs, but do not claim they
eliminate every cancellation. On approved activation collect exact decision
frames for the three concentrated markets: current touch, target remaining
quantity, candidate price and book time at each cancel. Distinguish real quote
movement, self-owned touch visibility, partial-size changes and stale/missing
book/proof. Measure scan overlap with uncovered/due exits separately before
changing scan scheduling. Do not disable legitimate repricing, roll back all
reward support or change the six/24h policy based on correlation alone.

The [next-day checklist](../MEASUREMENT_CHECKLIST.md) now routes this baseline.
No new strategy decision is requested until a causal defect or a documented
business tradeoff is established. Existing prepared code remains unactivated.

## Current-process check, separate from historical comparison

Supervisor388103/runner388121 remained running on `aae337658` (up3h08m at the
initial check). Sequential authenticated02:23:47Z orders/positions crossed a
new qihzho/bendud fill and did not prove continuous reducer coverage. Runtime
observed+10 at02:23:47.700025Z, confirmed the old offer cancellation and received
a new SELL reducer ACK02:23:52.825611Z,5.126s after observation.

Authenticated02:25:46.777492Z:26 orders,25 flat entries plus one OPEN SELL10
exactly covering sole qihzho/bendud+10. Zero unresolved intents but one local
unknown order at that instant; not a clear restart gate or a claim of no
account ambiguity. Volyur/kryvit had flattened naturally. The task changed
only investigation documentation and ignored read-only analysis helpers.
