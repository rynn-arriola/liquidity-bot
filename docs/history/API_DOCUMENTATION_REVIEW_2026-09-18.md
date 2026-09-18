# September 18 API documentation and reward-period review

## Scope and evidence

The operator asked to inspect the complete official API documentation, update
the canonical reference, support both reward flows, and prioritize the old
day-of flow with daily-event markets using spare capacity. This is source work;
it does not authorize deployment, restart or other strategy changes.

The [official index](https://docs.polymarket.us/llms.txt) yielded 304 distinct
Markdown URLs, all successfully retrieved at capture
2026-09-18T16:49:03.502613+00:00. The [inventory](API_DOCUMENTATION_INVENTORY_2026-09-18.md)
records every URL and content hash prefix. Public source copies are retained
outside the repository in a local temporary review cache. Inventory/retrieval
is not a claim that every schema field was audited or every API was tested.

| Coverage | Pages | Review depth |
|---|---:|---|
| Shared concepts and app help | 62 | Interface/rule map and relevant trading distinctions |
| Retail API | 74 | Detailed affected contracts: auth, limits, incentives, orders, portfolio, discovery and streams |
| SDKs | 21 | Resource/transport and field-shape comparison |
| Institutional, trader guides, gRPC and FIX | 103 | Scope/access screening; not a Retail migration or acceptance test |
| Incentives | 6 | Liquidity windows, scoring and payout distinctions |
| Data/reports | 10 | Scope and research applicability |
| Partners | 27 | Scope/access/funding/reconciliation screening; not implementation |
| Changelog | 1 | Recent entries and relevant historical changes |

Detailed source links and findings are in the
[canonical reference](../POLYMARKET_API_REFERENCE.md). Key findings:

- Retail authenticated/public general limits remain20 requests/s; the Retail
  incentives overview separately states5/s per incentives endpoint. Preserve
  the bot's aggregate4/s and existing lower lanes, not a quota increase.
- The generated incentives page imports an Institutional schema/host. Other
  generated earnings/auth and WebSocket naming examples conflict with the
  Retail overview/detailed contracts. Preserve the verified Retail adapter.
- Retail batches accept up to20 orders, but cancel/modify IDs echo input and
  exchange outcomes remain independent. These are a future design candidate,
  not permission to batch safety mutations or assume cancellation success.
- No replacement Retail private balance/position feed or confirmed repair was
  found. Institutional resumable streams/JWT/FIX/partner ledger services need
  separate access and are not a drop-in fix. No private retest was run here.
- Existing event discovery is not deprecated by sports-v2 routes. Price
  history contains displayed quotes, not full books or realized executions.
- Daily-per-event documentation describes midnight-to-midnight ET pools for
  events without fixed starts, yet the live Retail endpoint also attaches
  `daily_event` to scheduled sports. Pool membership is not independent dollars
  per market; exact cross-event/program allocation remains unconfirmed.

At16:44:58Z (12:44:58 ET), one bounded report-lane period diagnostic through the
unchanged WSL runtime found138 in-horizon moneylines and136 rewarded markets:
all136 carried daily_event, zero day_of. This is a timed sample, not a permanent
removal of day_of. Runner136224 still existed during this review; that process
check is not current venue exposure proof. Earlier account checks are historical;
orders/positions were not freshly authenticated for this source/document task.

## Chosen implementation and alternatives

Retaining day_of-only refuses the current slate. Treating daily_event as day_of
would incorrectly compress a daily pool into six hours. Expanding to all-day or
live trading changes strategy and is not authorized. Stacking simultaneous
programs could overstate estimated income. Chosen design:

1. Preserve exact six elapsed hours before kickoff, moneyline, fresh parent
   pregame state, price/depth and all current entry-quality gates.
2. Select the existing applicable day_of period first. Only when none applies,
   consider one active, time-valid daily_event row. Duplicates remain ambiguous.
3. Prioritize eligible day_of candidates before daily candidates in both the
   bounded book scan and viable ranking. Rotation cannot bypass that priority.
   Existing healthy owners are retained; this is not forced eviction/churn.
   If day_of candidates exhaust the book budget, daily candidates are deferred;
   this may leave capacity unfilled rather than guessing viability of unread books.
4. Daily projection uses the actual ET day (23/24/25 elapsed hours), with
   remaining opportunity capped at midnight, kickoff and explicit end. Partial
   program days do not inflate the denominator. Projection is an estimate,
   not guaranteed reward or authoritative program attribution.
5. Carry the same opt-in policy through selector, startup/contracts, live
   economics and entry observations. Configured default is enabled by this
   approved implementation; the pure lifecycle helper defaults off for other
   consumers. Read-only commissioning diagnostics reflect the same policy.
   Legacy standalone probe/shadow entry points intentionally keep their
   original lifecycle-only scope; they are not daily-selector acceptance tools.
6. `allow_daily_event_rewards` and the direct runner disable flag provide a
   restart-scoped control. The detached launcher does not pass that flag;
   supervised rollback needs reviewed wiring/source rollback. No hot-toggle,
   persisted new setting, DB migration or automatic source switch was added.

## Dedicated edge-case and final-review audit

- Success/failure: old-only, daily-only, mixed, duplicate active rows, missing
  periods, disabled fallback, inactive/future/expired rows and unknown generic
  daily remain explicit; an invalid old candidate does not gain guessed terms.
- Capacity: empty and capped scopes, higher daily pools, repeated rotating scans,
  retained daily selection and spare book capacity are covered. The existing
  owner/pending-order capacity and settled-loss ratchet are unchanged.
- Time: exact six-hour boundary, kickoff, completion after a book wait, ET
  midnight, both DST transitions and partial program bounds are covered.
  Receipt freshness and actual-live evidence remain separate entry gates.
- Ownership/exits: live authorization loss still clears eligibility; exit-mode
  contract reads do not require a reward row. No reducer, cancellation,
  liquidation, reconciliation or ledger ownership logic changed.
- API/load: same bounded event/symbol discovery and at-most100 full-book scope;
  no new recurring request, retry, socket or admission allowance. More eligible
  markets can cause normal trading work; this does not guarantee lower load.
- Recovery/shutdown/restart: no lifecycle/position clocks or loss state reset.
  Current runner retains its loaded code. Activation needs an authorized stop,
  fresh exposure/ownership proof, reviewed deployment/native suite and monitored
  startup. Disabling future daily entries never grants permission to abandon
  existing daily positions.
- Accounting: settled trading loss, deposits/withdrawals, reward payout status,
  unallocated program attribution and Discord reporting are unchanged.
- Secret/diff review: public documentation only; no credentials, database,
  logs, webhook, private host coordinates or new dependency enters tracked work.

## Verification

Initial focused86 tests passed. Reversing the daily fallback caused both core
regressions to fail; restoring it passed. The added scan-priority regression
also failed when its tier reservation was deliberately removed, then was
restored. The subsequent focused selector/rewards/config suite passed86 tests.

The initial complete suite ran1,764 tests: all code tests passed; its one
documentation-audit failure was caused by downloaded upstream Markdown links
being interpreted as local repository links (and the not-yet-created review
link). The public cache was moved outside the repo, rather than weakening the
audit. The final complete suite passed all1,765 tests in260.086s using the
production WSL Python against the Windows review checkout (not the live source).
The restored scan/live-toggle regressions also passed together. Documentation
audit and `git diff --check` passed. No release or natural live acceptance is
implied by unit tests.

## Remaining work

Superseded September17 root handoffs were preserved in the
[September18 rollover](INDEX_ROLLOVER_2026-09-18.md), not discarded.

After a separately authorized deployment/restart, verify actual old-first/daily
selection, normal entry and position exits, bounded load and natural midnight
transition. Do not induce fills/failures. Confirm cross-event daily pool
allocation with the venue before claiming precise reward income. Batch orders,
new discovery routes and Institutional migration remain research, not approved
implementation in this change.
