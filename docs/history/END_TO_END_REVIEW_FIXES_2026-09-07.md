# End-to-end review repairs — September 7, 2026

## Scope and authority

The operator requested an end-to-end review, then instructed the AI to stop
before implementing its recommended repairs. The operator subsequently
authorized push/deployment and restart after tests and final review. No
strategy experiment, risk reset, ledger repair or forced shutdown is included.

The graceful stop completed before code changes. Authenticated venue proof at
03:47:43Z September 7 showed zero orders and positions. Windows/GitHub/WSL HEAD
was `0b5023427`; the previous executable marker was `43e9db826`. Pre-work parity
captured a verified 282,058,752-byte SQLite online backup at 03:46:02Z. The
backup-PC SSH peer was unavailable and remains pending, not falsely synchronized.

## Findings and repairs

1. **P1 — cached transfers could corrupt a fresh HARD mark.** A completed
   withdrawal inside the old 15-second cash cache could appear as trading loss;
   a credit could conceal loss. Every published account mark now includes a
   bounded non-trading cash scan and the final equal-cash bracket. Same-balance
   offsetting credit/trading is covered; detecting only a changed raw balance
   would not suffice. Failed reads retain the prior proven mark and let its
   normal freshness expire. The $90 HARD and loss ratchet are unchanged.
2. **P1 — one owner's failed shutdown book could starve other owners.** Working
   orders are serviced first, and scoped adapter/depth failures are isolated
   within the shutdown owner loop. Other identified owners can cancel or exit.
   Ownership/invariant exceptions still reach the account-wide handler.
3. **P1 — three global shutdown IOCs were insufficient for multiple positions.**
   The three-submission bound now belongs to each owner, independent of runtime
   counters. Each owner gets at most one submission per round, counted before
   send, within the unchanged total shutdown deadline. Fresh reconciliation,
   terminal order/intent proof and exact-position sizing remain mandatory.
   Deadline is checked both before servicing an owner and immediately before
   submission. Existing passive-reducer parking policy is unchanged.
4. **P2 — delayed midnight balances used mismatched calendar-day cash.** Cash
   changes now use the actual opening/closing snapshot interval, with the lower
   endpoint exclusive and upper endpoint inclusive. The historical scan covers
   the closing snapshot, including the short post-midnight gap. A closing
   snapshot beyond the cash-scan cutoff cannot publish a value. The same
   boundary correction applies to today's report. Delayed daily snapshots
   display `~`, retain interval metadata, and cannot finalize calendar-day net.
   Exact field-pinned previously audited midnight exceptions remain accepted;
   missing/late/unproven boundaries do not fall back to incomplete ledger pairs.
5. **P2 — provisional HARD closure could deadlock across ET midnight.** While
   the breach-time cohort is unresolved, fresh adjusted equity continues against
   the old baseline/cash boundary. Entry remains blocked. A confirmed old loss
   still stops; only positive post-close reconciliation plus recovered equity
   clears the cohort, after which the next normal refresh may establish the new
   ET day. Midnight cannot forgive an unresolved loss.
6. **P2 — dashboard overall P&L lacked an estimate label.** The card and its
   detail now explicitly identify the incomplete ledger plus paid-gain estimate.
   Its formula is unchanged; it is not authenticated lifetime account P&L.

## Regression and reversal evidence

Offline tests cover the production size-10/32-slot $90 configuration, a
withdrawal inside five seconds, credit masking, unchanged-cash offsetting loss,
failed fresh cash scans, both recovered and confirmed HARD after midnight,
four independently bounded shutdown owners, a bad book beside another cancel
and exit, and account-wide ownership failure. Reporting tests cover a deposit
inside the delayed midnight gap and at the exact snapshot endpoint, today's
already-included credit, interval metadata, estimate labels and withheld net.

All six deliberate reversals failed their corresponding regression by
assertion (R1: three subcases; R2/R3/R5/R6: one each; R4: two boundary subcases).
Every reversal was restored before the full suite. An initial crude R1
mutation also broke startup and was discarded as insufficient evidence; the
recorded R1 reversal preserves startup and reproduces stale-cache behavior.

## Dedicated final edge-case audit

- **Success/empty/full:** empty flat shutdown still returns through whole-account
  proof; one owner retains its three-attempt bound; four owners can each act;
  the configured 32-owner scope is unchanged. No entry-order capacity changes.
- **Failure/partial/unknown:** no unknown create retry or inferred cancellation
  is added. Reconciliation still precedes every shutdown round. Only scoped
  adapter errors continue to the next owner; ledger/ownership/invariant faults
  remain global. Book/depth failure spends no IOC submission. Post-send failure
  spends the attempt and the durable intent must resolve before another IOC.
- **Timing/races:** final cash equality is mandatory; failed reads cannot
  ratchet phantom loss. ET changes during the cash scan refuse the candidate.
  Provisional closure must be newer than complete reconciliation. Snapshot cash
  endpoints match exactly; delayed calendar rows are not labeled exact.
- **API/load:** the fresh adjustment adds a bounded activity scan plus one cash
  read on refreshes that previously reused the timer cache. All use the existing
  shared safety governor; no new endpoint, traffic lane or mutation retry.
  This increases risk-read load and requires a natural post-start API window.
  Historical reports extend the existing bounded read through the captured
  report time; dashboard remains zero-API and display failures remain isolated.
- **Recovery/shutdown/restart:** each owner has a separate bounded exit budget,
  but the total wall-clock deadline remains. Passive parking, exact reducing
  quantity, stressed depth, kickoff, maintenance and startup gates are unchanged.
  Restart requires fresh authenticated flat/known proof, explicit 10/32 settings
  and the normal $90 limit; no baseline or ratchet is edited.
- **Rollback/security:** no database schema migration or credential change. Old
  code can read the existing ledger and added report fields are backward-safe.
  A rollback would reintroduce these defects and still requires stopped-safe
  operator authority. No private data, log, database or secret enters Git.
- **Policy:** selection, touch quoting, sizes, timers, risk limits, maintenance,
  30/40 clock ceilings and 30/25/20/15/10 loss tiers are untouched. The explicitly
  approved shutdown recommendation changes only the scope of its attempt bound.
- **Limits of evidence:** tests are synthetic, not a forced live outage or fill.
  Estimates are not promoted to exact money claims. No claim of profitability
  or exhaustive absence of all project defects is made.

## Release evidence

Full review-tree test result, pushed commit, deployment hashes, native suite,
fresh start gate and post-restart acceptance are recorded below as completed.

- Review-tree complete suite: **1,147 tests passed in 108.205 seconds** on the
  pinned WSL Python 3.12 environment. The subsequent dashboard-only explanatory
  tooltip receives the focused frontend regression before push.
- Final whitespace/diff and documentation audit passed. Nine new regression
  methods were added; existing clock/cash fixtures were updated without
  weakening their money, ownership, attempt-bound or alert assertions.
- Release `baae566d434daf36c2886796924b542fb1761fdb` was pushed before the
  stopped WSL checkout fast-forwarded. Its `DEPLOYED_COMMIT` matches. All **159**
  tracked source/test/script/web/config files SHA-256 match Windows and WSL
  after line-ending normalization, with both tracked trees clean against Git.
- The exact native production checkout passed **1,147 tests in 80.788 seconds**;
  dependency consistency passed. Post-push parity correctly held the runtime
  change for reviewed deployment; the private backup remained verified.
- At **04:06:15Z**, authenticated venue proof was zero orders/positions and the
  read-only ledger had zero unresolved intents, unknown orders and working rows.
  The launcher then started supervisor `475847` and runner `475856` with explicit
  `auto 10 32`. Actual quoting/API acceptance follows below, not inferred from
  the successful launcher command.
- Natural startup scanned two event pages, found 89 viable markets and loaded
  the 30-order overnight ceiling with loss cap 64. Active scope is `RUNNING`,
  capacity 32, publisher `475856`. First orders were submitted at 04:08:11Z.
- First API window at **04:09:51Z**: 207.5 seconds, 469 attempts, 2.260/sec,
  39 mutations, **zero failures/429s/cooldown**, queue peak one and wait p99
  0.477 seconds. The new cash path accounted for 11 history reads. This is
  initial healthy-load acceptance, not proof of peak-load performance.
- At 04:09:50Z authenticated reads found **25 orders across 24 markets and zero
  positions**. Seven live intents/six absent-order rows were in reconciliation
  during cancellations; these are not terminal proof or new startup ambiguity.
  Subsequent logs show ordinary re-entry after reconciliation at 04:10:01Z.
- Final authenticated acceptance at **04:11:28Z** found **27 orders across 22
  markets, zero positions, zero unresolved intents and zero unknown orders**;
  the ledger also had 27 working rows. The earlier cancellation ambiguity
  cleared naturally without a restart or manual ledger repair.

## September 6 report question

The operator asked why September 6 trading was unavailable after restart.
The report snapshot was generated at **04:07:13Z**, before September 7's
baseline was saved at **04:07:54.512370Z**. No closing boundary existed when
that report ran. The later baseline is 7m54s after ET midnight, outside the
two-minute tolerance, so simply rerunning the report will not establish exact
calendar-day P&L. September 6's own opening was 04:01:46.645731Z.

Stored cash/equity is $1,090.8613 for September 6 and $1,086.6591 for September 7.
The previously authenticated September 6 result was **-$104.2022 through
23:34 ET**, after excluding the proven $100 goodwill credit. This is the last
verified within-day result, not newly proven exact midnight-to-midnight P&L.
The bot was stopped across midnight for the authorized review deployment.
Unavailable preserves that evidence gap instead of erasing the loss or
silently claiming the late snapshot was midnight. A separate gap audit or
explicitly labeled wider-interval report remains possible; no ledger baseline,
audited exception or report number was changed in response to the question.

The operator also asked about the **$13.33 ET-day execution-fee reference**.
Code and active decisions confirm `$10 * (size / 10) * (slots / 24)`, rounded
to cents: $13.33 at size 10 / 32. It is `observe_only`, not a second stop,
order ceiling or independent loss deduction. The sole money-loss authority
remains $90 adjusted marked-equity HARD. This clarification changed no policy.
