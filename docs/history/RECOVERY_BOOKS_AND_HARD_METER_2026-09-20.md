# Recovery books and summary HARD meter — September20

## Authority and operational state

Operator requested investigation/fix of the stopped bot and restoration of the
summary HARD meter. Work is prepared only: no deploy, restart, source switch,
OS-power change or venue mutation. The14:33:41Z read-only account check proved
zero orders/positions/unknown/unresolved; no trader/supervisor was present.
Loaded runtime remains3fc44a674. Existing next-restart image sizing/alignment
changes are preserved. Four$25 settled-loss windows and entry tiers are unchanged.

## Investigation

The previous normal runner's log ends without its normal terminal result.
WSL kernel evidence at10:14:28ET records a canceled p9 operation, followed by
systemd shutdown at10:14:29 and a new init startup at10:14:30. Windows records
an application update at10:13:57 and a Modern Standby exit at10:17:31. These
are correlated host events, not proof which action caused the subsystem stop;
do not blame the application update or assert an OOM/application crash.

Host recovery subsequently ran on the saved REST source. Startup book reads
hit Cloudflare1015/HTTP429 repeatedly14:14:52–14:18:18Z, although complete account
health reads succeeded. It eventually canceled orders, closed the remaining
position and finished flat14:19:06Z with `startup_recovery_only`. Staying stopped
after host recovery is intentional safety policy, not another crash. The
precise host-shutdown trigger and external rate-limit threshold remain unknown.

## Confirmed code gap and design

`_refresh_bound_books` deliberately gave the public collector an empty scope
whenever any recovery owner existed. This forced recovery onto REST books,
unlike ordinary scans/startup, before the runtime public stream was started.

Reuse the existing public full-book collector for the identified recovery
scope. It owns no account state/mutations, performs no discovery and does not
enable entry. It retains exact market identity, original receipt age under
three seconds, scope bound, bounded connection/data waits, stop checkpoints,
cleanup/join and the same gated REST fallback. Account state remains REST.
Transport silence never becomes an empty-book/settlement claim; failed fallback
still blocks unsafe actions. No rate/budget/freshness/depth rule is relaxed.

Compared with simply retrying REST longer, this removes a needless dependency
when public streaming works. Compared with replacing private account data, it
does not depend on the still-unverified balance stream. Tradeoff: a failed
public connection can add the existing bounded collection wait before fallback.
This cannot prevent Windows/WSL termination or guarantee no future429s.

## Summary meter

The text summary retained HARD, but its image conversion rendered only the
loss/limit rows. Restore a labelled percent-used bar below those rows in the
existing1200x1382 canvas. Use only `hard_stop_pnl_usd` and the configured limit;
daily P&L, open marks, rewards, deposits/withdrawals cannot enter the meter.
Unknown/nonpositive limits show unavailable, positive P&L shows0%, overshoot
keeps its real percent while bar width caps at100%. Keep risk state/window,
dollars, budget remaining, cap and position age. Extra risk rows fall back to
complete text instead of overlapping the meter. No additional API calls.

## Verification and edge-case audit

- 558 focused tests passed, plus two new end-to-end recovery cases in both
  WebSocket and REST account modes: cancel identified orders, prove flat, no
  new entries/selection and zero REST book calls despite simulated429.
- New scope regressions cover recovery receipt preservation, stale/future
  data, failed/missing stream, REST fallback429, owner retention and operator
  stop/collector cleanup. Existing exact empty/inactive/one-sided recovery,
  unknown ownership, capacity and shutdown tests remain in the focused suite.
- Meter regressions cover window-vs-day/open marks, unknown, zero limit,
  profit, exact100%, overshoot, labels and bounded drawing coordinates.
- Three in-memory reversals detect REST-only recovery, incorrect meter value
  and removal of the visual meter; source restored after each.
- Sample PNG visually inspected: meter fits without obscuring values; previous
  section alignment and reward-image dimensions preserved. No Discord send.
- Full suite: 1,982 tests passed in302.982s. The two subsequently added
  end-to-end recovery tests passed separately. No live recovery was induced.
- Operator-requested follow-up checked the HARD calculation and percentage
  entry reducer together: 55 risk/summary tests passed in5.555s, including a
  new runner-to-image regression across all exact tiers and just-below values.
  Durable matched fills produce the same percent in the summary and the
  expected pending-entry cap: loss0/5/10/15/20/25 maps to30/25/20/15/10/0.
  Thirty flat entries plus one positioned reducer retain the reducer at every
  tier; only overflow entries are selected for cancellation. New entry is
  blocked at the ceiling. Existing tests cover cancellation action execution,
  ambiguous reservations, partial closes, old basis, late fills, transfers,
  stale proof, restart persistence, ET/DST boundaries and next-window release.
  This is a pending-order count reducer, not contract-size reduction.
- Meter means percentage USED; policy fractions mean budget REMAINING.
  Closed-trade net P&L includes observed fill fees (fees on an open fill are
  already incurred); paper marks and open inventory value remain excluded.
  The cap ratchets down for the rest of its window even if profits reduce the
  displayed net loss. A tripped HARD likewise remains held until a later
  window and fresh flat proof. No calculation/policy defect was found and no
  risk implementation/configuration was changed by this follow-up.

Remaining operational work: reviewed deployment/start only when separately
authorized, then natural recovery/exit/stream acceptance. Host-shutdown cause
needs additional host evidence; changing sleep settings or automatic entry
resumption is not part of this repair.
