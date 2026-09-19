# Settled-only summary image — September19, 2026

## Operator scope

The operator approved the sample Discord layout, then clarified that trading
loss/P&L must use settled trades and pending rewards must remain visible in
the SAME Lifetime Results section. Pending is excluded only from arithmetic,
not from that section. No restart or trading/risk change is authorized here.
The earlier sample image was delivered to the summary webhook, explicitly
labelled sample data, before production implementation.

## Design and implementation

Reuse the existing five-minute summary, cached earnings and one queued
notification worker. The serial trading path captures existing state plus
report context; it does not query new API endpoints or render images. The
worker opens SQLite read-only, replays fills once, builds both image and text
from the same monetary values, and uses optional Pillow for a1200px PNG.
No second scheduler, permanent collector, Polymarket request, API-budget
increase, ledger mutation or risk-policy change is introduced.

Sections retain status/window loss, entry count/cap, positions/orders/age;
today's closed-fill gains/losses/fees/net and local rest; lifetime results;
market supply/blockers; capital and health. Day-of/daily hours share the
reviewed hourly report's implementation, moved without arithmetic changes
to `report_liquidity.py`; the existing script retains a compatibility import.
Unknown/mixed period coverage remains unknown/partial. REST's configured4/s
value is correctly labelled a ceiling, not measured live request rate.

Lifetime keeps gains, losses, actual fees net of rebates, trading net, paid
bot-era rewards, overall net profit/loss and pending together. Gross P&L
comes only from matched/closed inventory, including partial closes. Actual
fill fees are charged once when incurred, as in the existing closed-fill
report; open inventory's changing mark, deposits and withdrawals never enter.
Reversed fills and fills after the captured observation/receipt cutoff are
excluded. Old fills establish basis for today's ET result; DST elapsed hours
use UTC differences. The four$25 risk-window calculation is NOT modified.

One important limitation remains explicit: historical market settlements are
not all represented as closing fills, and paid rewards are date-scoped to the
bot era rather than perfectly attributed by order/program. Therefore the
lifetime section and net are labelled PARTIAL, with one concise coverage
line; no estimate is promoted to a complete total. Exact historical resolution
reconciliation remains follow-up work. No guessed payout/loss is inserted.
Reward age is visible; stale cached rewards retain their age label, absent or
future evidence is unknown. Pending never becomes paid by being displayed.

Alternatives rejected: repeating historical API calls each summary, using
account marks to fill settlement gaps, rendering on the decision loop, or
retrying an uncertain image upload as text. Critical/shutdown messages remain
text; a render/dependency error falls back before upload, while an attempted
failed upload does not create a duplicate text retry. Preparation failure
preserves operational warnings and reports unavailable instead of reverting
to the old marked-P&L headline. Internal local-path context never leaves in
the Discord payload. No webhook or API credential appears in code or output.

## Verification and edge review

23 focused tests pass. Four in-memory reversals each produce the expected
assertion failure, with zero test errors: including pending in net, treating
open acquisition cost as P&L, replacing missing history with zero, and
narrowing the canvas. Shipping files are never changed by these reversals.
The preliminary native full suite passes1,965 tests in217.167s. The final
native full suite passes1,970 tests in217.303s, including all23 focused tests.

Native sample rendering was visually inspected for clipping, hierarchy,
numeric visibility and the pending row inside Lifetime Results. Tests cover
long-warning fallback, missing/corrupt DB, no reward snapshot, stale/future
rewards, positive/negative net, fees/rebates, partial close, long/short pairing,
reversed/future fills, prior-day basis, DST, unchanged read-only DB bytes,
critical text, stripped context and one-shot failed uploads. Existing hourly
report compatibility and all runner tests are included in the full suite.

Read-only native real-ledger check: closed-fill replay0.106s, period hours
1.198s, zero venue requests. These costs occur only in the existing reporting
worker, not at every order. This is one measured sample, not a latency bound.
The runner remained1153573 on loaded6a1032a61; no deployment or restart.

Final audit reviewed success/failure, empty/partial/large values, timestamps,
queue isolation, source selection, startup/critical/shutdown routing, optional
dependency absence, upload ambiguity, configured capacity and rollback.
No migration/backfill or persistent risk latch exists. Rollback is the prior
reporting source after normal deployment gates; never restore a ledger.

## Release boundary

Prepared only for a separately authorized reviewed deployment/restart.
Because the live runner must capture the new context and notifier routing,
this is not a hot report-script-only change. Keep existing trading running.
After activation verify one natural five-minute image, matching text values,
fresh pending age, explicit partial lifetime coverage, and ongoing normal
order/exit processing. No faults or fills should be induced for acceptance.

## Command 1 activation — September19

The subsequent Command 1 authorizes the normal stop, reviewed deployment and
gated restart of this exact reporting release, without changing trading rules.
The supervisor1153551 and runner1153573 stopped normally. Terminal record
13:12:08.040545Z: operator_sigterm, flat=true. Fresh authenticated complete
reads at13:12:38.974617Z proved zero orders/positions; read-only ledger showed
zero working orders, positions, unresolved intents and unknown orders.

Windows/GitHub matched clean3fc44a674. The stopped-runtime recovery copy
captured13:12:48.892128Z passed integrity and destination verification:
767,303,680 bytes, SHA prefix751e46e156c1. The offline PC was not contacted.
WSL clean tracked HEAD9eb5df4a fast-forwarded through a verified bundle to
3fc44a6749e8fc9d014d6b2cd3327c91fff7a2c2 under the hourly-report lock.
All223 installed source/test/config/script hashes match release and Windows.
Existing untracked deployed marker and egg-info were preserved. No ledger,
risk baseline, setting, credential or unrelated source was changed.

The installed environment passed all1,970 tests in191.947s. Artifact hashes
were rechecked before advancing DEPLOYED_COMMIT to3fc44a674. Fresh complete
authenticated13:17:09.785337Z reads again proved zero orders, positions,
unresolved intents, unknown orders and ledger exposure. The existing launcher
`auto 10 32 saved` started supervisor1211173/runner1211201; the independent
TV observer was already running. Persisted settings remain size10/32 and
manual REST; current06–12ET settled loss$10.1849/$25 selects cap20 and is not
reset. New summary presentation adds no venue reads or execution decisions.

Authenticated13:21:59.459980Z acceptance found20 flat entry orders across18
markets, zero positions, zero unresolved intents and zero unknown orders;
ledger agrees. The first two API windows reported178/112.4s and292/109.6s
(1.584/2.664rps), zero429/cooldown and five individual-order404s. One exact
market lacked confirmed pregame data at13:18:10Z and recovered eligibility
at13:19:12Z; other markets resumed orders. No trading-code change was needed.
This flat sample does not establish position-exit or recovery performance.
The new scheduled summary's visual delivery still needs natural confirmation;
absence of a notification failure is not independent Discord-image proof.

## Follow-up: match rewards image size — prepared only

The operator observed that the summary did not match the reward attachment's
size and then explicitly reserved the correction for the NEXT restart. Do not
deploy or restart for this follow-up. Runner1211201 remained live on3fc44a674;
no current exposure claim is made from the earlier acceptance snapshot.

Both renderers already used1200px width, but the standard summary sample was
1793px tall versus1382px for two complete seven-day reward tables. Main values
used28px versus rewards34px. Matching width alone missed aspect ratio and
type scale. The renderer now uses1200x1382 and34px main values, with a compact
two-column Lifetime section: gains/losses/fees/trading net beside paid/net/
pending. Pending remains in that section and excluded from net. Short display
labels preserve the original text/model meaning; no monetary math changes.
Status, risk, today's metrics, capital, markets, health, partial-history and
reward-age notes remain. Oversized diagnostics fall back to complete text
before upload instead of clipping or dropping safety information.

Only renderer and presentation tests change. No API request, ledger change,
trading decision, upload retry, source selection or restart is added. Shrinking
the entire old bitmap was rejected because it also shrinks already-small text.
The native rendered sample was visually checked: no overlap/clipping, all
lifetime values readable and pending beside paid/net.26 focused tests pass;
the added tests compare actual summary/reward dimensions, retain every lifetime
label/value and enforce expanded-diagnostic fallback. Three in-memory reversals
(old height, old type size, disabled overflow guard) each fail an assertion
without errors. The final isolated native full suite passed1,973 tests in206.255s.

Edge review: eight-row top cards, twelve-row bottom maximum, unknown/large
values, long warnings, stale/partial labels, negative net, optional Pillow,
text fallback and unchanged upload ambiguity handling. Same existing worker,
no execution/risk changes, migration or state rollback. Future authorized
deployment must recheck hashes/tests and normal startup safety gates.

## Follow-up: align every section — prepared for next restart

The operator requested properly aligned numbers in every section. Markets and
Capital/Health were still rendered as whole space-padded strings in a
proportional font, so their values did not share a column edge. More spaces
would not reliably fix that. The renderer now recognizes the existing text
row format before trimming/abbreviating labels, preserves each value verbatim,
and draws all metrics with the same right edges (566/1146px) and shared label/
value baselines. This also standardizes top cards and Lifetime rows.

Nested reason counts, large currency values with no separating whitespace,
UNAVAILABLE/UNKNOWN and capital explanation notes remain intact. Unrecognized
notes remain full-width text; overflow still selects the complete text report.
1200x1382 dimensions, main34px type, pending beside paid/net, accounting and
upload behavior are unchanged. No API/ledger/decision/startup change occurs.
Runner1211201 was still present during this work; no exposure query or current
flat claim was needed for the isolated display change. No deployment/restart.

29 focused tests pass. Added cases verify exact coordinate/anchor/baseline
alignment across all sections, parsing without value changes and capital
unavailability explanations. Two in-memory reversals (left-anchored values,
whole-string rendering) cause expected assertion failures, not test errors.
Native preview visually verified for consistent columns, no collisions and
retained information. Final native full suite passed1,976 tests in206.783s.
Dedicated review includes signs/decimals/ratios/units, long/unknown amounts,
indentation and nested reasons, absent capital proof, row/width overflow,
unchanged read-only worker and fallback-before-upload. No trading, risk,
source or rollback-state change; the operator's next-restart-only boundary
continues to apply.
