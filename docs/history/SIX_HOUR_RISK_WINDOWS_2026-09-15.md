# Six-hour confirmed-loss risk windows — September 15, 2026

## Decision and scope

The operator explicitly replaced the $90 daily HARD with four fixed $25
windows: 00:00–06:00,06:00–12:00,12:00–18:00,18:00–00:00 America/New_York.
Percentages mean **budget remaining**, not loss consumed. The pending-entry
ceiling starts at30 and ratchets to25/20/15/10 at $5/$10/$15/$20 confirmed net
loss. At $25, cancel entries, evacuate positions through existing exact exit
proof, and hold until a later window plus fresh flat/clear account proof.
Positions and reducer/IOC orders are excluded from the pending-entry count.
This replaces the older overnight/daytime clock bands and daily loss tiers.
Quote quantity, configured market breadth, reward selection, entry-quality,
normal exit timing, source selection and API limits are unchanged.

The latest instruction requires waiting for positions to settle before charging
loss. Here "settle" means an executed close reflected in venue account state,
not waiting for the sports event to resolve. Partial closes contribute only
their realized portion. Open-position price swings do not consume the budget.

Implementation/review is authorized; this task does not deploy, stop or restart
the live process. Loaded runtime remains the earlier $90 policy until a
separately authorized rollout. Four $25 budgets total $100 nominally per day,
not a guaranteed maximum: in-flight fills and evacuation may add losses.

## Design and alternatives

Reusing the daily marked baseline would charge paper loss and contaminate the
daily reward/reporting scope. Fill-ledger-only risk would miss known account
activity and attribution gaps. The selected design therefore keeps daily
reporting intact and adds a small restart-persistent window meter using the
existing complete authenticated account snapshot.

The confirmed coordinate is fiat cash plus all open-position cost, minus
positively classified non-trading cash. Entry spend is added back, open marks
are ignored by the cap, and closing proceeds/cost reductions recognize the
actual result. Deposits, withdrawals and reward credits remain excluded.
The original cash/positions/cash bracket plus final equal cash read is reused;
missing basis, unknown transfers, stale observations or a boundary-crossing
read withdraw entry authority without charging a guessed loss. These checks
depend on venue field correctness: they cannot prove that consistently stale
upstream responses are current or guarantee an exchange settlement timestamp.

An open marked loss reaching $25 may provisionally block/cancel entries while
normal exits continue. Its breach-time market cohort persists until all those
positions close, including fills arriving during cancellation. It does not
ratchet the durable cap or itself trigger immediate HARD evacuation. This
retains the existing provisional-vs-confirmed distinction. Waiting for actual
loss necessarily tolerates open risk; existing exposure/deadline guards remain.

`execution_risk_windows` stores immutable window openings, policy fingerprint,
ratcheted cap, confirmed holds and provisional cohorts. A singleton checkpoint
stores continuous transfer-neutral raw/normalized coordinates. Repeated
snapshots and restart cannot double-count losses. A subsequent window starts
from the last pre-boundary checkpoint, so a closing result in the observation
gap is conservatively charged to the new window, not silently forgiven.
This is observation-based attribution, not an exact per-trade execution-time
allocation. After a long offline gap, all unobserved changes conservatively
reach the next observed window; a bounded history scan may refuse rather than
guess. First-ever activation establishes a fresh observed starting coordinate;
rollout must explicitly accept that activation baseline (no retroactive claim).

Cash adjustments compare cumulative proven history against the persisted
cash total, not timestamps since the last poll. This catches a same-day
deposit/withdrawal published later with an older completion time, without
double-counting it on subsequent polls. Midnight's first successful bracket
retains the previous cash anchor to bridge the change of daily baseline.
This reuses the existing day-wide history scan; it does not add a polling loop.
Retroactive venue corrections dated before a finalized daily/activation anchor
remain an upstream-accounting limitation, not a guaranteed automatic repair.

Confirmed HARD survives restart and recovered equity. Release needs a later
window, fresh reconciliation, a newer complete flat account mark, no working,
absent or unresolved orders, exact ownership and all other admission gates.
An active legacy daily HARD/provisional hold refuses migration; it cannot be
cleared by changing config or deleting rows. Policy changes within a used
window likewise refuse instead of rebasing. Old rollback binaries ignore the
new tables and require explicit stopped-safe risk review, not blind rollback.

## Load, reporting and configuration

No new API endpoint, poll loop or extra steady-state read is added. The existing
non-trading history read supplies both daily and checkpoint-relative adjustments.
One small local transactional checkpoint write is added per proven observation.
Entry admission uses in-memory state. A boundary requires the usual fresh proof.
REST/WebSocket handoff keeps the same meter and persistent state.

`config/risk_window_policy.json` validates ET boundaries, finite positive dollars,
integer counts and descending remaining-budget tiers before live startup. It is
restart-scoped. The legacy entry policy remains only for historical replay;
production caps use the new policy. The weekly reserve display follows seven
days × four × $25 = $700, while exposure funding remains separate. Daily P&L
and reward tables remain midnight ET. Discord HARD uses only fresh confirmed
window P&L and names the window; unavailable must never fall back to daily P&L.
The operator also explicitly requested updating Discord: the concise summary
now includes window loss/$25, remaining dollars/percent, pending entries/limit
(excluding positions/reducers), and pause state. The diagnostic dashboard reads
the same fresh persisted window checkpoint rather than relabeling daily loss.

## Verification and final audit

Focused regressions cover all four boundaries; spring/fall DST; exact dollar
thresholds; ratchet recovery/restart; cross-midnight transfers; boundary-gap
closure; paper vs partial/full realized loss; late-fill provisional cohorts;
invalid policy/clock/basis; stale/cross-boundary/failed cash proof; fresh next-
window flat release and other pauses; positioned-order exclusion; Discord
daily/window separation; and REST→WebSocket→REST preserving the same budget.
Deliberately replacing cash+cost with marked equity fails the closing-loss
regression (`70 != 100`); the correct implementation was restored.
Reversing the cash anchor to the most recent poll likewise makes the late-
withdrawal regression fail (`70 != 100`); cumulative-history handling was restored.

Final isolated native Linux suite: **1,709 tests passed in259.016s** at12:21Z;
all197 source/test/script/config files match the Windows review tree by SHA-256.
The37-test focused Windows set passed. Both deliberate reversals failed and
were restored before the final native suite. The initial Windows full run's
four POSIX-lock failures are platform safeguards, not removed protections;
the corrected fixture/dashboard expectations and all native tests now pass.
The documentation audit and final diff whitespace check pass. No live fill,
loss, maintenance event or source handoff was induced.

Dedicated final edge-case review covered success/failure, empty/partial/full
positions, exact threshold and DST boundaries, same-window recovery, long
offline gaps, source handoff, cancellation races, unknown/stale cash or basis,
stop/shutdown/rollback, owner reservations and configured capacity. Entry-only
caps leave exact reducers/exits available; neither a new window nor source
switch overrides maintenance/manual/ownership gates. No new API budget or
network task was introduced. Window baselines are observation-based and venue
publication correctness/exit slippage remain explicit limitations, not promises.

At12:19:33Z the original supervisor3353887/runner3353918 were still alive,
with deployment marker2166dd2634582934e42c6f9fec0d4845ac2297d7. No production
source, configuration, ledger or process was changed. Current venue exposure
was not re-read during this implementation; last authenticated11:31Z state
was25 flat entries plus one -10 position and its exact reducer, not current proof.

Remaining rollout gates: approve activation timing/baseline, normal graceful
stop and fresh authenticated flat/clear ownership, reviewed deployment and
hashes, native installed full suite, authorized restart and natural acceptance.
The live $90 policy must not be represented as already replaced.
