# Market-quality tracker implementation — September 23, 2026

## Release scope

Prepared independently on `work/market-quality-tracker`, based on main
`71ae3ee20`. No merge to main, deployment, live activation, stop or restart.
Saved hybrid and wider-scan branches remain separate. Size15/32, the four $30
settled-loss windows, pending caps, entry ranking, pricing, exits, transports
and request schedules are unchanged. This is observational, never a risk gate.

At the final process check, supervisor23308/runner23326 still existed with
the original start and deployed marker `6deb707c6a5d9e6a56cdf983840b60f202e64dc2`.
No authenticated exposure query was needed; current venue exposure is unknown.

## Implemented

- An optional `--market-quality-directory` runner argument, disabled by default.
  No launcher/config activation is made by this branch. A subsequent authorized
  deployment must explicitly pass the argument through the supervised launch;
  do not bypass the owner lease, release gate or normal start workflow.
- One bounded background SQLite writer per run. Queue32, record2MB,
  directory quota512MB and free-disk reserve512MB. Enqueue never waits for disk
  or capacity. Shutdown waits at most50ms; an undrained process exit is marked
  incomplete, never falsely certified complete. No evidence is auto-deleted.
- Source hashes, record hashes, run/sequence identity, receipt times and
  written/dropped/error/closed counters. Disk and optional-builder failures
  cannot veto trading. Archive files are private diagnostic data, ignored by git.
- Successful discovery snapshots: metadata, first refusal stage/reason,
  exact reward periods, pre-book order, deferred/evaluated state, final ranks,
  counts, page count, policy and failures already returned by the selector.
  Catalogs over1,000 rows are split into bounded records with one scan identity;
  partial/duplicate/mismatched parts cannot be mistaken for a complete scan.
- Existing evaluated-side score/share/reward estimates and execution-quality
  features, plus exact binding snapshot/original rank and active scope.
- Distinct observed events and markets per exact program/window/pool/target/
  discount identity. Bounded discovery is explicitly NOT complete pool
  membership. Book scores model competition; they do not count other traders.
- Final entry-admission observations from already-computed authority, not a
  second gate or extra account read. Earlier exceptions are not represented as
  successful admission observations.
- Post-commit order witnesses and complete-snapshot absence evidence; bounded
  deduplication suppresses unchanged polls. Execution tables/schema are unchanged.
- Entire already-fetched liquidity earnings responses, including retired
  markets, in a separate archive. Existing active-session reward persistence
  remains scoped exactly as before. This fixes a measurement coverage gap
  without relabeling account-wide rewards as bot-owned earnings.
- Offline JSON/Markdown report: immutable original-priority grades
  S1–5/A6–10/B11–20/C21–30/D31+, paid/pending separately, canonical matched-fill
  P&L with fees once, approximate resting hours, conservative local time bounds,
  feature ranges, matched program/league/pool/size cohorts and coverage.
- Mixed-grade market-days receive ONE reward row, not a payout per grade.
  Missing rewards remain unknown. Revisions replace snapshots, not add them.
  Pending, manual/unattributed, unmatched inventory and ambiguous cohorts cannot
  become finalized grade profit. Priority grades never certify profitability.

## Accuracy and limitations

The collector adds no REST calls, timers, subscriptions, retries or polling.
Integration tests compare identical selection decisions and gateway call lists
with capture off/on/broken. Reward capture uses the same single existing fetch.

The report is retrospective current-ledger evidence, not an as-known backtest.
All cohorts remain `performance=unproven`; sample floors are not confidence.
Whole-response capture means all rows returned by the adapter, not a guarantee
of complete venue history. Missing pool members are not guessed. Local witness
bounds are not venue reward-scoring seconds; gaps/unclosed archives force the
lower bound to zero. Grades join original binding, never a later rank.

Exact decimal summation fixes grouping-dependent reconciliation differences
found with real data; no tolerance hides missing money. Reconciliation proves
the grade/Mixed/Unknown partition of local rows, NOT an independent account cash
audit. Source float artifacts remain preserved; currency rounding is display-only.

This first implementation does not yet produce a frozen filter proposal,
later-period holdout decision, exposure-weighted competition model, or a complete
position-occupancy/reducer-delay report. Existing execution diagnostics retain
that evidence; no such profitability claim is made here. Preserve this distinction
before calling the broader research design fully validated. A new selection
rule requires later complete paid outcomes and explicit operator approval.

## Offline use (no credentials or API)

From the reviewed checkout, with its production-compatible Python environment:

```sh
PYTHONPATH=src python -B scripts/market_quality_report.py \
  --db /path/to/read-only-ledger.db \
  --archive /path/to/run.sqlite3 \
  --days 7 --output-root data/market-quality-reports
```

Repeat `--archive` for separate runs. `--earnings-json /path/to/earnings.json`
explicitly supplies a saved complete response instead of the latest archived
one. Without either, rewards remain unknown; it never silently uses the old
session cache. Output JSON includes source hash/age, local ledger capture times,
archive health, every excluded row and reconciliation. Review JSON, not just the
usable-subset Markdown table, before discussing profitability.

## Verification evidence

- Forty-two new focused tests plus three existing scan-compatibility tests pass. Scope: grade boundaries, mixed/unknown provenance,
  original versus later rank, future timing, empty/pending/duplicate/revised
  rewards, fees once, unmatched/reversed inventory, unioned rest bounds,
  missing capture, record conflicts/checksums, bounded queue/disk failure,
  post-commit failure isolation, event deduplication and unchanged API calls.
- Six deliberate reversals failed the intended tests: restricting reward
  capture to active markets, inexact grouped sums, subtracting fees twice and
  assigning a mixed day to one grade, and requiring optional competition scores
  on older scan inputs, and accepting incomplete multipart catalogs. The last
  reversal used an isolated child-process function replacement, leaving the
  candidate source untouched; all other reversals were restored before the suite.
  The first full candidate suite caught three score-field compatibility errors;
  absent legacy scores now remain null, not zero or a collector crash.
- Real offline read-only report: September16–22 ET, captured September23
  14:45:05Z, 1,755 market-days,18 groups,831 missing reward market-days.
  Used the pre-existing September22 02:04Z earnings file, not a fresh API read.
  Paid$355.93/pending$502.67/local closed-fill P&L-$297.3851 and approximately
  3,574.1983 side-hours reconcile across all grade buckets. These are incomplete
  historical report-scope totals, NOT current account or finalized profit.
  No grade group had eligible finalized coverage with this snapshot.
- Local serialization/enqueue microbenchmark:1,000 records each containing100
  small candidate rows; median0.135ms, p95 0.213ms, maximum1.064ms;
  6,156,288 archive bytes, all1,000 written, zero drops/errors. Queue enlarged
  to1,100 for that artificial burst. This measures only enqueue/serialization,
  not whole-builder cost or live decision/exit latency. No live speed claim.
- Alternating off/on selector replay:20 pairs of100 fake-book candidates,
  identical gateway call lists and selection results. Off median2.612ms,
  p95 3.692ms; capture median5.342ms, p95 6.632ms. All20 records persisted,
  1,650,688 bytes, zero drops/errors. Roughly2.73ms median added per successful
  fake scan, not per order or API request; this is not a live latency guarantee.
- Live log inspection (read-only) showed12,311 catalog rows despite only448
  executable markets. Added multipart capture to prevent the ordinary catalog
  from exceeding a single record limit. A fake12,311-row scan captured all rows
  in13 records/6,193,152 bytes, zero drops/errors/incomplete scans; elapsed163.61ms.
  The default512MB directory quota fits roughly82 such snapshots, not indefinite
  collection. Before continuous activation, size the bounded sampling/storage
  plan from actual scan cadence and review this CPU cost; do not claim zero
  latency impact merely because API requests are unchanged. Retention/deduplication
  and larger-catalog asynchronous construction remain optimization follow-ups.

Final frozen candidate suite passed all2,041 tests in285.768s using the actual
production virtualenv against this separate worktree. Documentation audit and
final diff/edge-case review passed. No deployment, activation or restart occurred.

## Dedicated edge-case review

Success/empty/full: no viable candidates and empty rewards remain valid evidence;
unknown is not zero; bounded records/queue refuse excess without blocking.
Ordering/races: callbacks run after ledger commit; stale/deduplicated ledger
events retain original semantics; frozen payloads cannot change after enqueue;
reward revisions and archive identities are verified. Restart uses a new run ID;
binding provenance remains original within its existing process scope.
Partial/manual state: approximate time and tainted/mixed attribution stay visible,
withheld from final grade profit. Fee/P&L replay is the existing canonical model.
Failure/recovery/shutdown: disk/queue failures affect diagnostics only; incomplete
drain is visible; no network recovery path or account source was added.
Capacity/ownership: no entry cap, reducer exclusion, ownership proof or risk
predicate changed. Rollback means disabling/removing optional capture on a later
authorized restart; archives and execution ledger are not repaired or deleted.

Before activation: review any combined branch diff, safely wire the explicit
opt-in through the existing supervisor, run production deployment/hash gates,
then check archive health, actual decision/exit latency and same request rates.
Natural shadow acceptance and later paid-cohort validation remain pending.
