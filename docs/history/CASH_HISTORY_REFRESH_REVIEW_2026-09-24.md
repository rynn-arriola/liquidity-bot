# Cash-history refresh review — September 24, 2026

## Scope and live state

Operator requested investigation and the least disruptive recommendation, not
implementation or restart. Source review, existing log aggregation and seven
existing offline tests only; no authenticated diagnostic or order mutation.
Supervisor481321/runner481337 remain on `dc8f62556`, size15/32 hybrid and four
$30 settled-loss windows. Process verified22:35Z. Current venue exposure was
not queried; the22:22:58Z authenticated acceptance is historical.

## Findings

`_refresh_balance_candidate` currently performs balance -> complete positions
-> balance -> complete cash-activity walk -> final balance. Only after the
entire bracket succeeds are balance, accounting and risk freshness published.
Periodic cadence is5s, with additional decision-age/boundary/recovery paths;
completion delays mean actual request rate differs from that cadence.

`_observe_risk_window` uses durable matched-fill P&L through the snapshot's
original observation time. Cash movements, marked positions and buying power
do not enter the current window-loss formula. Nevertheless, cash-walk failure
clears risk freshness, and entry signature/admission plus next-window release
still require a same-day non-trading adjustment. A superficial timer cache
would not correctly separate these responsibilities.

Other consumers include account-marked/realized reporting, the live P&L cache,
midnight baselines and the retained legacy non-window risk path. Buying-power
admission and withdrawal guidance use the current balance and its freshness;
they must never inherit a slower reporting clock. The legacy
`_session_realized` method has no discovered caller; its existence is not proof
that it controls the current stop.

Cash activity is ordered by creation time, but classification uses completion
time. Existing parser requires explicit EOF within four pages. A late-completed
old withdrawal cannot be discovered reliably by stopping at a known row or
using only a last-seen timestamp. Equal cash also cannot prove no transfer:
an offsetting fill or transfer can leave the balance unchanged.

Cash and execution history are two filtered calls to the same activities
endpoint and share its backoff. Separating reporting cannot bypass a real
activities cooldown when fresh trade history is required for execution proof.
No new claim about the venue's rate-limit threshold follows from this review.

## Measured traffic and bounded estimate

Fourteen complete windows ending22:19:21–22:34:30Z cover1,011.5s, including the
first window's earlier interval. Existing aggregate telemetry reports:

| Category | Requests | Per second |
|---|---:|---:|
| All attempts |3278|3.241|
| Cash history |210|0.208|
| Balances |317|0.313|
| Positions |402|0.397|
| Individual orders |877|0.867|

Cash history is6.4% of all attempts. Three429s occurred (cash history2,
positions1); these are not all-run lifetime counts. Many cash counts are twice
the apparent financial-refresh count, consistent with two-page walks; aggregate
logs alone do not prove the page count of every individual refresh.

If a30-second reporting target retains about two cash pages per report, that
is approximately0.067 cash requests/s instead of0.208: about68% fewer cash
reads and4.4% fewer total requests in an otherwise identical workload. Moving
the cash-only final balance verification to the same slower report cycle could
bring the modeled total saving to roughly6–7%, provided reports reuse a due
fresh safety bracket without adding separate position/balance collections.
Treat that extra saving as design-dependent, not measured or additive to some
other claimed improvement. Startup, retries, page growth and urgent-work
deferral change the result. No promise of zero429s or faster realized exits.

## Options and recommendation

1. Cache the cash adjustment inside the current refresh: reject. It can combine
   old cash classification with a new balance, misreport P&L and renew the wrong
   freshness authority. Existing regression covers offsetting cash/trades.
2. Slow the entire account refresh: reject. It delays buying-power/account proof
   and current settled-loss observations together with optional accounting.
3. Separate fast trading proof from slower complete account accounting:
   recommended, for the current settled-window mode only. This is a bounded
   responsibility split, not replacement of the engine or REST authority.

Proposed interfaces/state:

- Fast safety refresh retains the current balance/complete-position/balance
  bracket, original timestamps/freshness, execution-history authority and
  settled-fill window observations. Entry capacity, HARD, reduction and exit
  deadlines keep their existing rules. No automatic source switch.
- A distinct account-report snapshot holds its own matched balance/positions,
  cash adjustment, ET baseline, original observation time and completeness.
  Refresh on a configurable30-second target using a due successful safety
  bracket and full cash walk/final balance verification. Do not combine a new
  safety balance with the old reporting adjustment or create a second writer.
- Reports may show the last complete snapshot with its real age; invalidate
  incompatible/expired or wrong-day account P&L. Settled trading P&L, window
  meter, paid/pending rewards and current buying power keep their own sources
  and clocks. No stale value may be labeled current.
- Preserve full accounting proof at startup, ET midnight, explicit recovery
  and next-window release initially. Retain the old complete synchronous path
  for legacy non-window mode. Do not reinterpret existing holds or durable rows.
- Only transient reporting failure is isolated from fast safety in this mode;
  malformed/unclassified cash retains existing conservative entry handling
  initially. Failed required balance, position, execution or ownership proof
  still bars entry. Respect the shared activities cooldown; never fabricate
  trade-history availability to keep trading.
- Reporting yields to existing urgent position service. Keep the original due
  time when deferred, avoid catch-up bursts, and allow stale-report labeling
  during sustained urgent work. Do not add concurrent account writers or a
  second API budget. Use existing telemetry to validate the result.

The intentional behavior change is that unavailable report-only cash proof
need not revoke an independently valid settled-window safety proof. That needs
explicit separation of entry signatures, admission, summary freshness, risk
publication and window release, with corresponding invariant updates. It is
not safe to implement by deleting the cash call or relaxing the15s risk clock.

## Verification and implementation acceptance

Seven existing tests passed in1.116s using the installed production Python,
fake clients and temporary ledgers. Cases: settled-only HARD/exact exit;
cash-failure preservation; late/backdated transfer neutrality; next-window
proof and independent pauses; partial closes; matched-cash settlement delay;
and the old cached-transfer regression. This validates the current dependencies,
not the proposed new implementation. An earlier run also passed all seven;
its shell exit-status wrapper failed and the clean rerun supersedes it.

Future implementation must test the full fast/report split: deposit/withdrawal
plus offsetting fill, delayed completion and EOF errors, shared429 cooldown,
report failure with independently fresh safety, missing execution proof,
unknown transfers, buying-power changes, report age/summary consistency,
midnight/DST/6h rollover, held stop across restart, partial fills/busts and
urgent exit deferral. Require unchanged settlement loss/tier results and no
stale entry/withdrawal authority. Count fake-client requests before/after on
the same workload before any separately authorized live activation.

Parity passed: workstation/GitHub/WSL `af2f22c4`, loaded marker `dc8f6255`;
offline peer remains pending. Runtime code/configuration unchanged; no restart.
