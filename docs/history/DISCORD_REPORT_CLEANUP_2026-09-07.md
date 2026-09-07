# Discord report cleanup — 2026-09-07

## Scope and operating authority

The operator requested professional, report-only Discord status/error,
summary and reward messages without repetitive side notes. They also asked
why free-to-withdraw still sometimes shows unavailable. The standing
instruction is **do not restart**. This work prepares source for a later
authorized deployment; it does not update the running WSL checkout, post test
messages, alter timers or restart trading.

## Changes

- Summary: one-column sections, concise LIVE/SHADOW/DRY RUN title, one trading
  headline and closed/open split, the unchanged HARD input, positions/orders,
  market counts, explicitly lifetime paid/pending rewards, compact capital and health. Remove the
  incomplete lifetime estimate, duplicate pending note, repeated explanatory
  paragraphs, exit-sample statistics and the capital-formula breakdown.
- Rewards: retain the seven-day P&L/reward and liquidity tables with four
  period totals. Remove the duplicated today breakdown, balanced-pair
  estimate, repeated liquidity total, methodology and interval paragraphs.
  Keep a short partial/estimated/not-posted legend; estimate rows still show
  `~` and cannot finalize net. A total containing estimates is labeled.
  Entirely missing reward observations render unavailable, not a zero total.
- Detailed snapshot intervals, accounting methodology and incomplete pair
  estimates remain in terminal/audit output and the structured dashboard
  cache. No cash-flow classification, boundary selection, P&L equation,
  reserve calculation or financial authority changed.
- Operational text uses a consistent severity-colored embed. Runtime errors
  retain their component, attempt, market, error and timestamp without the
  repetitive recovery instruction or machine/commit footer.
- Identical successfully delivered noncritical operational messages are
  suppressed for 300 seconds, using a thread-safe, monotonic, 128-entry
  process-local cache. Timestamp-only changes do not bypass suppression;
  changed details or attempt counts do. Failed deliveries can retry.
  Critical alerts, including repeated non-flat shutdown warnings, and all
  explicitly routed summary/reward reports bypass this cache. Existing
  runtime retry thresholds and scheduled report cadence are unchanged.

## Withdrawal investigation

The live runner remained PID 475856, started 2026-09-07 04:06:23Z. Read-only
production logs showed `capital_proven=True capital_reason=ok` at 12:42:03Z
and 12:49:09Z. The 12:57:46Z report instead had two unresolved intents and
one unknown order; 13:05:53Z had five intents and two unknown orders. These
are report-time facts, not current authenticated exposure proof.

The affected summaries therefore withheld the value for genuine pending
order proof, not a missing arithmetic implementation. The existing formula
still computes `max(0, min(buying_power, settled_cash - retained_target))`,
rounded down to cents, only with fresh proven state. Its unchanged size-10 /
32-slot reserve is $420 exposure plus seven $90 HARD budgets = $1,050.
The display now says only `Order confirmation pending`; counters remain in
Health and technical details remain logged. No stale or fabricated amount is
substituted and no ownership/freshness requirement is relaxed.

## Verification and dedicated edge-case audit

- Focused report/transport and updated integration checks passed (76).
- Three deliberate reversals independently restored a summary side note,
  a reward side note and repeat delivery; each intended assertion failed,
  and each change was restored before the full suite.
- Coverage includes live/shadow/dry-run labels; positive/negative and absent
  trading; unknown withdrawal versus a buying-power-capped actual amount;
  pending rewards excluded from finalized net; missing reward observations;
  estimated boundaries; exact five-minute suppression boundary; failed send
  retry; changed attempt/details; simultaneous duplicate sends; bounded cache;
  fresh-instance restart; and unconditional critical/report delivery.
- Final audit: rendering and notification transport only, with no runner,
  selector, execution, risk, ledger or configuration change. No venue reads
  were added. The existing asynchronous transport still performs HTTP off
  the event loop. Critical sends bypass the noncritical lock/cache, so a
  duplicate routine notice cannot suppress a terminal warning. Empty/future/
  stale capital evidence retains refusal. Rollback requires no migration or
  persisted-cache cleanup. The current trading process cannot load these
  import-time changes without a later authorized restart.
- The first full run caught three old layout expectations; these were updated
  without removing the underlying severity, retired-market or shadow safety
  assertions. The corrected run passed all 1,160 tests in 111.689 seconds.
  The final full run, including the explicit lifetime-reward field label,
  passed all 1,160 tests in 109.824 seconds. Documentation audit passed with
  zero drift and `git diff --check` was clean; final source/test diff reviewed.

## Handoff state

Reviewed changes are committed/pushed for the operator's next deployment.
The production checkout remains `2a69c131d`, loaded marker `1f9bacd92`, with
the same runner PID 475856. Final parity must therefore report the newer
runner imports as pending reviewed deployment, not silently fast-forward
production. The backup-PC peer was unavailable at the initial parity check.
No authenticated exposure read was needed for this presentation task;
current venue orders/positions are not claimed from old logs or the ledger.

## Remaining acceptance

Deploy only after operator authorization, verify exact deployed hashes and
the native suite, restart only through the normal authorized safety gates,
then inspect naturally generated reports. No test webhook spam, induced
order ambiguity, restart, withdrawal or trading-policy change is authorized
by this presentation task. Backup-PC parity remains pending while offline.

## Authorized deployment without restart — 2026-09-07

The operator subsequently said to push and deploy for the next restart,
without restarting now. The already-pushed `f7816c324` was fast-forwarded
into the clean tracked WSL checkout. No new source change was necessary;
the earlier final review, reversals and full review-tree tests were reused.

- All **160** source/test/script/web/config files match the review checkout
  by SHA-256 after expected line-ending normalization.
- The exact native production checkout passed **1,160 tests in 80.407s**.
- `DEPLOYED_COMMIT` now names installed release
  `f7816c3247ed9223690347fb6e05929930f24a15`. This is on-disk deployment proof,
  **not** proof that the existing runner re-imported notification code.
- Original runner **475856**, started **04:06:23Z**, stayed running. No stop,
  restart, risk/config change, venue mutation, test webhook send or schedule
  change was performed. Current authenticated exposure was not re-probed.
- Status/error imports and duplicate suppression activate at the next
  independently authorized restart. The separate hourly reward process reads
  the new report layout on its next existing scheduled run.
- Final deployment diff is the same reviewed release. Only documentation
  follows it; native tests and hashes remain valid for those unchanged files.
  Backup-PC parity remains pending while the peer is unavailable.

## Command 1 restart and trading acceptance — 2026-09-07

The subsequent **Command 1** explicitly authorized the normal stop/review/
deploy/restart workflow, superseding the prior no-restart instruction for
this run only. No additional source, configuration or risk change was made.

- Supervisor 475847 was stopped first and runner 475856 received SIGTERM.
  Its terminal record at **14:08:55.653397Z** proved `flat=true`, with reason
  `operator_sigterm`.
- Authenticated gates at **14:09:25.425982Z** and **14:09:57.669795Z** each
  proved zero venue orders/positions, zero unresolved intents, zero unknown
  orders and zero working ledger orders. Parity also captured a verified
  stopped-runtime private recovery snapshot.
- Local/GitHub/WSL tracked state matched. All **160** source/test/script/web/
  config hashes matched; `git diff f7816c324 HEAD -- src tests scripts config`
  was empty. Reused the already-completed three reversals, final review-tree
  1,160-test run and exact native **1,160 tests / 80.407s** instead of repeating
  unchanged verification.
- The normal launcher restarted `auto 10 32`: supervisor **770963**, runner
  **770972**, process start **14:09:57Z**. Installed/loaded release remains
  **f7816c324**. $90 HARD, the durable ET-day loss state, daytime/overnight caps,
  all position/exit rules and the independent report timers are unchanged.
- Startup scanned two event pages and found 77 viable markets. The official
  schedule coordinator reported `HEALTHY/NORMAL`; normal preflight completed,
  including successful startup notification delivery, before order entry.
- The first API window at **14:13:46.388433Z** covered **227.9 seconds**, **489
  attempts**, **2.146/s**, zero failures, zero rate-limit errors, zero cooldown
  in every lane, and peak queue depth one.
- Authenticated acceptance at **14:13:55.638466Z** found **39 orders across 28
  markets, zero positions and zero unknown orders**. One current intent was
  awaiting confirmation; do not represent that live snapshot as zero pending
  intents. Both pre-start gates above were completely clear.
- The new reporting imports are loaded. Next naturally scheduled summary and
  reward layouts, and future duplicate/critical-message paths, remain natural
  acceptance observations; no synthetic webhook messages or induced failures
  were used. The backup-PC peer remains pending/offline.
