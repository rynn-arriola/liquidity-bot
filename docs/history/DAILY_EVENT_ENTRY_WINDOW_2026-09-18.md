# Daily-event entry window — September 18, 2026

## Decision and scope

The operator approved active daily-event rewards rather than restricting entry
to games on today's ET calendar date, while keeping established day-of markets
first and emphasizing profitability. Implementation uses a bounded rolling
24-hour lookahead, including tomorrow's games in that range. This scope was
explained before implementation; it is not an unbounded future-event search.
Day-of keeps its six-hour phase. No deployment or restart is part of this task.

Existing quality/exitability gates and projected reward-share ranking are
preserved. Within each priority tier, projected proportional reward (not the
headline pool or nominal day length) ranks viable books. Net-value estimation
remains advisory because `enforce_reward_cost_gate=False`; no profit guarantee
or newly enabled EV veto is claimed. Healthy owners are not forcibly evicted.
Actual-live exclusions,300-second pregame flatten,900-second inventory clock,
size10/32, max30 flat pending entries excluding position orders/reducers and
four settled-fill $25 risk windows remain unchanged.

## Bigger-picture design

- Rechecked [official liquidity rules](https://docs.polymarket.us/incentives/liquidity)
  and [Retail incentive overview](https://docs.polymarket.us/api-reference/incentives/overview).
  Day-of is six hours; daily pools are ET calendar days, not rolling24-hour
  payout guarantees. Current status and explicit start/end remain binding.
- Rejected a today-only game-date rule: it can exclude tomorrow's already-active
  reward pool. Rejected unbounded reward/catalog enumeration: earlier production
  exceeded its20,000-program safety ceiling. Preserve bounded event discovery.
- A shared policy supplies the maximum enabled discovery window. Only active
  daily rows may use the wider timing; early/live/generic daily do not gain
  authority. Custom narrower day-of policy remains enforced after reward lookup.
- Day-of retains priority in book allocation, rotation and final viable ranking.
  Projection still uses23/24/25-hour ET days and clips accessible earnings to
  midnight, kickoff and explicit program end; no concurrent pools are summed.
- Incentive models carry the conservative start of their complete fetch.
  Expanded daily selection, revalidation, runtime contracts, economics and quote
  observations require the same current-ET-day evidence. Fetches spanning
  midnight cannot stamp old parameters as today's. Ordinary contract refresh
  supplies new proof; no new poll or immediate retry loop is added.
- Send-time entry admission locally rechecks daily authority after governor
  waits. A crossed midnight, ended/ambiguous row or changed24-hour boundary
  refuses the unsent entry, never a cancel/reducer/exit. Stale reward proof is
  not positive authorization-loss evidence and cannot reclaim an owner.
- Existing ten-event-page ceiling, exact-symbol incentive batching,100-book
  maximum, background admission and aggregate4/s remain. Wider discovery can
  increase metadata/incentive work and scan latency within these limits.
  This implementation does not establish improved realized net or zero429s.

## Verification and edge-case audit

- Focused rewards/selector/config/adapter group passed171 tests before the final
  receipt test; runtime group passed469 before the final send-time test.
- New coverage includes24h/24h+1s, six-hour priority, tomorrow's games, unsupported
  early/live/generic daily, disabled/6h rollback, narrower day-of horizon,
  pending/future/ended/duplicate rows, small pool with superior reward share,
  bounded books, cached revalidation, long scans crossing kickoff/midnight,
  missing/future receipts, DST elapsed time and existing23/24/25h projection.
- Runtime tests cover24-hour contracts/economics/observation consistency,
  previous-day proof refusing entry without false retirement, fresh-proof
  recovery, exit bypass under stale/disabled timing, and send-time expiry.
- Deliberately restored six-hour authorization/discovery and bypassed send-time
  protection: all three selected regressions failed (six assertions). Separately
  bypassed daily receipt validation: both midnight regressions failed (four
  assertions). Restored every change before the complete suite.
- Complete local-source suite in the production WSL Python environment passed
  all1,778 tests in256.579s after restoring every reversal. CLI help accepts the
  new6/24-hour option; documentation audit and `git diff --check` pass.
- Dedicated final diff/edge review covered success/failure/empty/full scopes,
  midnight/DST/kickoff/expiry, admission waits, partial/unknown evidence, retained
  ownership, API fan-out, recovery, shutdown, restart and rollback. No strategy
  drift beyond the approved daily timing was found; runtime acceptance and
  actual net/load effects remain explicitly unmeasured.
- No ownership, durable mutation, position, shutdown, reconciliation, accounting,
  limiter or account-source rule was relaxed. Empty/unknown data remains
  fail-closed; no order/position state is inferred from reward metadata.

## Rollout and acceptance

Initial parity passed: Windows/GitHub/WSL HEAD `3af7a334`, loaded marker
`af811c874`; verified private ledger copy captured18:04:24Z,701,059,072bytes.
Read-only process check found supervisor186455/runner186471, started13:19:55ET.
No authenticated account snapshot was requested by this code-preparation task,
so current exposure is unverified; prior snapshots are not current proof.

Prepared defaults are daily24h with `allow_daily_event_rewards=True`. The
direct runner accepts `--daily-event-entry-hours 6` for old timing or
`--disable-daily-event-rewards` for day-of only. Detached launchers do not
forward either flag; supervised rollback requires a reviewed configuration/
launcher change or source rollback. Legacy pure/shadow callers keep6h defaults.

Later activation requires operator restart authority, reviewed installed tests/
hashes, positive flat/known-state gates and normal single-owner deployment.
Naturally observe widened-scan pages/latency/API load, early daily resting orders,
mixed-program priority, ET rollover and unchanged exits. Do not induce fills or
failure. Compare paid rewards plus closed trading P&L per resting side-hour
before claiming better profitability; pending rewards remain provisional.

## Later Command 1 release

The operator subsequently authorized Command 1. The old runner stopped
gracefully, two authenticated flat/clear gates passed, source parity matched,
and all1,778 installed tests passed in169.852s. Source `34f52b1d7` started
September18 18:45:19Z as supervisor239275 / runner239293, REST size10/32.
Initial scan:45,989 event rows,402 executable,376 rewarded,100 public-WS books,
63 viable and zero REST book fallback. Five-minute acceptance:30 orders,
zero positions/unresolved/unknown state. These are historical observations;
later current exposure requires fresh venue proof. The later payout-measurement
task confirms the same running process without restarting it.
