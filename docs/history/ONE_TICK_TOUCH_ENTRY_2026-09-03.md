# One-tick touch entry — 2026-09-03

## Operator decision and deployment boundary

The operator chose the recommended one-tick entry behavior, initially asked that
the bot not restart, and later explicitly authorized restart with the change.
When a market already selected on the unchanged 2-cent fresh-selection gate
later has a live book exactly one market tick wide, both building quotes use the
touch. A 49/50 book therefore quotes 49/50 rather than 49/51. The reviewed
behavior is now loaded on the laptop.

This is a strategy choice, not a defect correction. At size 10 it gives up at
most `$0.10` of gross matched-pair spread when the moved offer also fills. The
intended benefit is a better chance that the opposite maker side balances the
first fill, plus touch-level reward score instead of the one-tick discount or
an outside-cutoff refusal.

## Evidence that led to the choice

A read-only active-ledger study through 2026-09-03 11:03 UTC classified the
decision-time quote snapshot for each owned building order:

| Observed live book / quote | Placed orders | Filled orders | Filled rate | Filled quantity |
|---|---:|---:|---:|---:|
| One tick, bid at touch | 102 | 17 | 16.7% | 150.23 |
| One tick, offer backed away | 78 | 9 | 11.5% | 78.23 |
| Two ticks or wider, bid at touch | 308 | 25 | 8.1% | 229.32 |
| Two ticks or wider, offer at touch | 239 | 15 | 6.3% | 128.07 |

Order age and market mix differ, so these rates show association rather than a
causal counterfactual. A chronological fill replay gives the same caution:
29 completed episodes whose first fill came from a one-tick book lost
`$26.8231` (`-$0.925` each) and 22 used a taker reduction; 35 wider-book
episodes lost `$32.2315` (`-$0.921` each) and 28 used a taker reduction. Tight
books were not worse per completed episode. The change intentionally tests
fill symmetry rather than claiming that the one-tick back-off caused the day.

## Exact behavior

- Fresh discovery/selection still requires the configured 2-cent full-book
  spread. The change does not admit a new market discovered at one tick.
- An exact one-tick live book in an already-selected market quotes both best
  prices. The tick comes from market metadata, so 0.005 markets use a 0.005
  boundary rather than a hard-coded cent.
- A locked zero-gap book retains the existing 2-cent non-crossing back-off.
  An overlapping book remains untradeable.
- Wider books remain quoted at their touches.
- Economics and placement share `entry_quote_price`, so reward projection and
  the submitted order use the same price.
- Inventory reducers, the 900-second timeout, kickoff exit, HARD loss guard,
  exposure, quote size and 32-slot capacity are unchanged.

## Verification and edge audit

Nine focused entry/economics tests passed initially. A deliberate reversal to
the old back-off made both dedicated one-tick tests fail exactly because the
offer returned to 0.51; restoring the change made them pass. The final focused
set also proves that only a stale backed offer is replaced, a valid bid is
retained, a deep touch remains reward-eligible, and a locked book outside the
reward cutoff is not quoted.

The final candidate passed all 1,078 tests under the WSL Python/runtime while
executing directly from the edited Windows checkout. The Windows suite reached
all 1,077 then-existing tests; its only four errors were the intentional native
Windows refusal of authenticated executables that lack POSIX `flock`.

The permanent edge review covered exact and non-cent tick boundaries, locked
and overlapping books, wider books, reward/planner price agreement, cancel and
replace behavior, and unchanged reducer/timeout paths. The change adds no API
call, worker, slot or order per market. It can increase fills and therefore
inventory activity; that is the business tradeoff requiring live measurement.

## Deployment and initial live acceptance

The old supervisor/runner stopped gracefully at 11:24:25Z with terminal
`flat=true`. Fresh authenticated proof then found zero open orders and zero
positions; the ledger had zero unresolved intents, working orders or unknown
absences. Commit `001a7faa7` was pushed before deployment. The first native WSL
suite exposed a wording-only regression: the refusal still said `two-cent`,
while the existing assertion required `two cents`. Commit `2c3ce0e54` corrected
that message. The focused test failed under a deliberate reversal and passed
after restoration; the broader 474-test commissioning/reconciler set and the
complete 1,078-test native production suite passed.

Immediately before restart, WSL HEAD and `DEPLOYED_COMMIT` both resolved to
`2c3ce0e5417c1a46594f247e9156d96392731458`; repeated authenticated and ledger
proof was again completely empty and known. The bot restarted at the unchanged
size 10 / 32 under supervisor `2751977` and runner `2751986`.

The first 221.4-second API window ended at 11:35:19Z with 474 attempts at
`2.141/s`: 416 reads, 58 mutations, zero failures, zero rate limits, zero
cooldown and maximum queue depth one. The acceptance read found 58 authenticated
open orders across 31 markets, zero positions, and an exact ledger match with
58 working orders, zero unresolved intents and zero unknown absences.

No exact-one-tick book appeared among the new run's quote observations during
this initial window. Natural strategy-path acceptance therefore remains: require
both sides at `ticks_from_best=0`, no post-only rejection or mutation ambiguity,
and unchanged reducer availability. Compare matched-pair completion,
taker-reduction rate, reward side-hours and marked trading P&L against the
baseline above. Do not attribute an improvement to this change from one fill or
one quiet window.
