# Market economics measurement layer — September 18, 2026

## Authorization and deployment boundary

The operator authorized building and carefully reviewing the analysis layer,
then explicitly confirmed measurement only. This does not authorize an entry
filter, league ban, risk/size/cap change, automatic source switch or restart.
Existing day_of-first/daily-spare policy, four$25 settled-loss windows, all
position handling and current selection remain authoritative.

The standalone report works against existing data without a restart. A small
optional scan logger is prepared for the next reviewed deployment/restart.
Until loaded, complete-slate comparisons correctly say
`awaiting_complete_scan_measurement`; old payout-only lines are not fabricated
into complete candidate records. No timer, collector or webhook is installed.

## Bigger-picture design and alternatives

Flow: existing order/quote/fill ledger + one dated earnings snapshot → explicit
market/ETday records and coverage flags → exact-parameter cohorts → prospective
comparison against a complete saved selection scan → private JSON/Markdown.

Rejected immediate league filtering: seven-day results reflect older day_of
programs, include manual activity and have reward-date gaps. Rejected merely
dividing headline pools by event count: allocation/share cannot be inferred
that way. Rejected a second high-frequency API collector: the current books,
incentive responses and ledger already supply inputs. Rejected dropping all
bad/missing rows from an otherwise confident ranking: that selects survivors.

Implementation:

- `scripts/market_economics.py` owns read-only analysis/CLI. It imports no
  executor or order gateway. SQLite uses URI `mode=ro`, `query_only`, a single
  consistent transaction, explicit rollback/close, and never instantiates a
  ledger writer. Default mode uses dated cache only and makes no venue read.
- Explicit `--fetch-earnings` performs an account-wide report operation at
  the shared0.25/s opportunistic lane. `--limiter-state` is mandatory to bind
  to the active owner's governor even from a different checkout. API failure
  aborts rather than replacing rewards with an empty successful result. The
  client always closes. No per-market API fanout or quota test.
- `selection_measurement.py` serializes at most100 already-evaluated books as
  one complete JSON line. It records observed time, parent event, kickoff,
  program/period/start/end, pool/target/discount, size, spread, current viable
  rank, side price/share/projections, fill-risk estimate and exit-depth result.
  This is evidence of evaluated candidates, not all possible markets or proof
  of subsequent placement. No analysis result is read by the trader.
- The sole runtime change is a failure-isolated local log call after existing
  selection telemetry. It does not change ordering, eligibility, account proof,
  cancellation, exits, startup or recovery. Serialization/nonfinite/oversize
  and optional print failures cannot abort selection. It adds bounded CPU/log
  output, not a new network request or a promise of zero performance cost.
- Reports read at most the last16MiB of a scan log, bounded by its initial
  length. Partial first/last records, old-format lines and future records do
  not become a slate; distinct scans are never combined. No live tail loop.
- Each run creates a new timestamped directory with `report.md`, `report.json`,
  `earnings.json` and `scan.json`; no source/ledger overwrite. Reports retain
  source/capture times, research thresholds and implementation SHA256. Private
  output belongs under ignored `data/`, never GitHub.

## Accounting and evidence rules

Reward amounts remain separate PAID/PENDING/SKIPPED. Missing is unknown,
duplicates/invalid status/nonfinite amounts/mixed or future observations are
flagged, and old pending cache rows are replaced only by the latest dated
market snapshot. Absent rows are never zero. No reward dates are shifted.
Mixed programs, changed quantity/pool/discount/target or missing observations
prevent attribution. Repeated pools are identities, never additive income.

All non-reversed fills before the cutoff replay chronologically, including
older basis. This agrees with the existing closed-fill report, not full
account-equity P&L. Signed fees are included once; unmatched inventory adds no
paper P&L. An owned PLACE and matching order/market/exchange identity are
required for bot attribution. Any manual/unattributed leg taints the entire
inventory episode; manual closes remain visible but never quietly become a
clean bot-only training result. Cross-day or still-unmatched inventory is
excluded from training. Deposits, withdrawals and rewards cannot enter P&L.
Closing-fill counts are not independent round trips; fees occur on their fill
day, and settlement without paired fills is not reconstructed.

Only building orders contribute entry resting time; reducers and rejected
orders are excluded. Intervals split at DST-aware ET midnight and overlaps on
one market side are merged. One latest placement observation owns any overlap,
preventing duplicate predicted income. Quantity-hours, entry-time bands and
selection ranks are retained. Start is local intent time, not exact venue
acknowledgement; partial fills, downtime and changed eligibility remain known
limitations. Modified/unknown orders cannot train.

The model's placement reward rate uses the validated historical30-second
projection horizon and holds that rate constant only as an approximate
calibration. It cannot recreate second-by-second venue reward scoring. Repeated
scans are never summed, earlier accrued reward is unknown, and market-day
earnings are not allocated to individual entry/time/price bands by guesswork.

## Cohorts and comparison

Exact cohort signature: period, program, league label from slug, pool, target,
discount and nominal quantity. Old day_of history cannot calibrate new daily
programs merely because the sport matches. Research defaults are20 distinct
markets,3 completed ETdays and10 side-hours. These are measurement thresholds,
not a statistical confidence interval or an approved strategy setting.

All matching population rows must be usable, with no unmapped same-league
rows, before a cohort is marked sample-ready. Pending/missing rewards, manual
legs, program ambiguity and unmatched inventory do not disappear from the
population. This deliberately withholds ranking where completeness is unproved.
The report shows both all-row totals and the smaller usable subset separately.

For a supported prospective candidate, the observational score is current
projected reward/side-hour × the smallest completed-day paid/model conversion
(capped at1), minus the largest observed daily trading-cost/side-hour. This is
a transparent conservative scenario, NOT a confidence bound or guarantee.
Current policy-ineligible candidates are never promoted. Unknown stays unknown,
not zero or last. Supported candidates rank separately inside day_of/daily
tiers; this is not a replacement complete slate and does not estimate profit
on trades never taken.

Comparisons refuse scans older than20minutes, future scans, duplicate candidates,
expired programs, existing exit boundaries, changed signatures and history
older than14days. Rewards must already have been observed at the scan time;
future outcomes cannot explain an earlier decision. The entire model's knowledge
timestamp, including ledger capture, must precede the scan; checking only reward
timestamps would leak late fill/reconciliation evidence. Use `--model-json`
with a previously frozen report and a later scan. Continuous-rest scenarios clip at
ET midnight for daily programs, explicit program end and kickoff minus the
existing five-minute flatten buffer. Accrued income stays unknown; no$1 gate.

The ledger replay is retrospective current truth, NOT an as-known historical
backtest. Later busts/reconciliation can revise it. Frozen report artifacts and
later scan records are needed for prospective evaluation. Historical software
changes, manual activity and selection bias remain limitations; no profit gain
or statistical significance is established by this implementation.

## Operator commands (active WSL checkout)

```bash
# No network: inspect the local cache and current ledger.
PYTHONPATH=src .venv/bin/python -B scripts/market_economics.py --days 14

# Fresh dated rewards, sharing the existing live governor.
PYTHONPATH=src .venv/bin/python -B scripts/market_economics.py --days 14 \
  --fetch-earnings --limiter-state data/api-rate-limiter.state

# Later, reuse that earnings snapshot and compare a subsequent complete scan.
# Replace RUN with the actual timestamped output directory printed above.
PYTHONPATH=src .venv/bin/python -B scripts/market_economics.py --days 14 \
  --earnings-json data/market-economics/RUN/earnings.json \
  --model-json data/market-economics/RUN/report.json \
  --scan-log logs/commissioning.log
```

No automatic schedule is created. Run measurement on demand while accumulating
complete new daily periods. A later business-rule change requires independent
validation/holdout, an operator decision and normal reviewed deployment gates.

## Real-data and test evidence

September18 21:50Z fresh earnings plus a consistent read-only ledger yielded
1,501 market-days for September11–17;921 were usable observational rows.
449 had unreported rewards,89 lacked same-day entry rest/program attribution,
51 had manual/unattributed episodes,20 cross-day inventory and12 unmatched
inventory at the cutoff (flags overlap). These are NOT live-position counts.
All18 cohorts were withheld: coverage/sample criteria were not satisfied.

The captured API snapshot reported$782.97 PAID/$0 PENDING for these dates,
versus earlier pending observations; it is not proof of a fresh cash deposit.
An offline rerun at21:55:48Z reproduced1,501/921. Every day's sum of per-market
P&L exactly matched the canonical Discord closed-fill replay. No strategy
authority is inferred from this agreement.

One initial development-checkout report read used that checkout's default
limiter path; review caught this before release. The CLI now requires the
active owner's explicit shared governor for authenticated reports, with a
regression verifying0.25/s/report class and exact state path. Subsequent
validation reuses saved earnings without venue calls.

Focused44 measurement tests and481 runner tests pass. Deliberately removing
manual-episode taint made its regression fail; restoring it and bypassing
population completeness made the missing-loser regression fail. Both restored.
The final complete suite passed all1,835 tests in272.844s using local reviewed
source with the production WSL Python environment. The earlier1,834-test
full run also passed before the final no-lookahead/bounded-log additions.

## Dedicated edge audit

Reviewed empty/no-reward/no-fill cases, zero pending, exact boundaries, NaN/
Infinity, duplicates, stale/future/partial scans, DST/ET midnight, parameter
changes, both sides/overlaps, rejected/modified/unknown orders, signed fees,
LONG/SHORT price frames, partial/manual/cross-day/reversed fills, prior basis,
unmatched inventory, subset/whole-population bias, no-lookahead, API failure,
read-only DB/no-create, JSON serialization, capacity bounds and unchanged
trading selection. Restart is not required for standalone analysis; scan
logging activation awaits authorized deployment/restart. Removing the optional
logger leaves trading behavior unchanged; saved reports remain independent.

## Final prepared state

The final22:01:12Z artifact and22:05:33Z frozen-model CLI replay both contain
1,501/921 rows and correctly await a complete new scan. They reuse the saved
21:50 earnings snapshot without another API request. The final artifact again
passes all seven canonical daily P&L comparisons. Private outputs remain under
ignored `data/market-economics/`; no account artifact is committed.

At22:05Z supervisor302233/runner302251 were still present, WSL HEAD49523298
and loaded marker59691b29. No deployment, process stop/restart, venue mutation,
trading-rule change or fresh exposure check occurred. Current orders/positions
were not asserted from historical ledger rows. Windows/GitHub contain prepared
work; WSL must remain on its existing reviewed source until a later authorized
deployment/restart. Documentation and final diff audits pass.

## Command 1 deployment and live acceptance

The operator subsequently issued Command1. The supervisor and runner stopped
normally; the terminal record at22:13:10Z said `flat=true`. An independent
authenticated22:16:29Z snapshot proved zero open orders and zero positions.
Parity captured a verified stopped-ledger recovery copy before deployment.

Pushed commit `f2e6aba5bcb0b88c4d8a9707cba3a41764c3bf48` fast-forwarded the clean
WSL checkout through a verified local git bundle, preserving every untracked
runtime artifact. The exact installed checkout passed all1,835 tests in
173.584 seconds. Its marker and all207 normalized source/test/config/script
hashes then matched Windows and the reviewed commit. A fresh22:23:50Z gate
proved zero venue orders/positions, zero ledger working orders/positions and
zero unresolved/unknown ownership before restart.

Supervisor350908 and runner350932 started22:23:58–22:23:59Z using explicit
size10/32/REST. The first scan used24 public-WebSocket books with zero REST
fallbacks, found19 viable markets and emitted the new complete
`[market-measurement]` record with `enforcement=none`. No analysis result was
read back by selection. The scan logger therefore received live acceptance as
an isolated observer, not reward-estimate accuracy or strategy acceptance.

The first API window made256 attempts at1.863/s with zero429; a report-lane
earnings timeout was isolated and later windows ran3.507/3.141/3.002/s with
zero429. Normal individual-order publication404s reconciled. Authenticated
22:27:21Z state showed30 flat-entry orders and two position-market orders,
zero unresolved/unknown state. At22:28:18Z both -10 positions had exactly one
matching BUY10 reducer. Final parity matched Windows, GitHub, WSL HEAD and the
loaded marker; the offline PC remained explicitly pending.

Existing four$25 settled-loss windows, cap30/25/20/15/10, day_of priority,
daily spare-capacity entries, entry-quality rule, ownership, reducers and exits
are unchanged. Continue collecting complete prospective periods; estimates
remain unvalidated until later paid outcomes and coverage thresholds support
them.
