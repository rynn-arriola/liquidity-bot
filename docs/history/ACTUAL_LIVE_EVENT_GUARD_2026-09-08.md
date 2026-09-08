# Actual-live event protection — September 8, 2026

## Authorization and incident

Operator: “Lets fix it then restart again.” The bot had already stopped at
16:22:11Z; the operator manually flattened and the 16:27:40Z authenticated
snapshot proved zero open orders/positions. No risk-setting change is authorized.

The preceding schedule-only fix `2193e0e85` did not carry parent-event state.
Exact official parents showed Mutti/Pinto live in S4, Picard/Yilmaz in S5 and
Vivarelli/Georgieva in S3, with played scores, despite future market/question
clocks. This is a lifecycle omission, not local UTC/ET conversion.
[Original evidence](PREGAME_CLOCK_CONFLICT_2026-09-08.md).

## Repair and tradeoff

- Execution-only gateway preserves exact nested membership from discovery;
  refreshes exact parent slugs in batches of at most 100, with 30-second
  freshness, 10-second failure backoff and the existing shared REST governor.
  The market slug convention is only a lookup hint, never identity proof.
- Top-level/eventState/metadata.gameState live/ended/period/score are separate
  from schedules. Positive play dominates contradictory flags; marketGroups
  flags are not parent lifecycle. Unknown schema/status is not pregame proof.
- Positive started evidence is monotonic in an additive ledger table. Later
  false flags, future clocks, manual flattening or restart cannot reopen entry.
- Selection and refresh use completed-read time. Quiet owners wake when event
  evidence expires. Runtime blocks/cancels flat entries and tightens the normal
  pre-kickoff deadline for existing cancel-confirm-exact-IOC exits. Detection
  wakes retired owners once without clearing exit budgets or ambiguity.
- After mutation-admission waits, an additional zero-I/O guard refuses stale
  entry before network send; its durable intent is FAILED, not UNCERTAIN.
  Reducers bypass this entry-only gate. Contract reads remain available to exits
  even when the event read fails. Shutdown cannot park a live/unproven owner.
- A missing event in an otherwise valid partial batch bars that owner while
  preserving independent fresh proof for returned owners. A failed transport
  invalidates affected freshness. No order absence is treated as cancellation.

This can reduce reward activity when event evidence is unavailable and adds
roughly one batch per 30 seconds for steady 32-owner service, plus initial/new
owner reads. It cannot eliminate venue publication lag, fills before a cancel
is confirmed, API outages or insufficient exit depth. The bot cannot guarantee
zero exposure after an unannounced actual start.

## Dedicated edge-case review

Reviewed success/failure, empty and 32/100-owner batches, exact 30-second and
future-clock boundaries, partial/mismatched/malformed responses, false→live→false,
missing/removed membership, sticky restart evidence, first-start wakeups,
shutdown, stale admission, stop during read, and reducers with unknown events.
Existing ownership, exact IOC direction/quantity, terminal cancel proof,
inactive/settlement capacity reservations and risk baselines remain unchanged.
Size 10 / 32, reward priority, 70% entry quality, pending-entry bands/ratchets,
900-second ordinary inventory deadline and $90 HARD are unchanged.

Rollback must leave the bot stopped: the prior release lacks this safety gate.
The additive table is compatible with older readers but must not be deleted or
used to reset risk, fills, positions or order ownership.

## Verification

- Focused event/runner tests cover all three measured incident states, fresh
  pregame, stale/unknown cancellation, urgent live-position IOC, passive reducer
  availability, live shutdown cancellation, scheduler expiry, stop races,
  post-admission expiry with exact FAILED intent and persistent restart latch.
- Deliberately changed positive `started` back to `pregame`: the incident test
  failed for all three markets. Restored before final testing.
- At 16:51:23Z the patched read-only exact batch rejected all three offenders.
  At 16:51:32Z two discovery pages contained 6,539 markets, including 123
  executable pregame markets; direct exact refresh retained 32/32 sampled
  fresh pregame contracts. No order was sent by this probe.
- Complete pinned-venv review-worktree suite: **1,226 tests passed**, 120.178s.
- At 16:55:09Z an online SQLite recovery snapshot was integrity-checked `ok`
  (300,969,984 bytes). The stopped-only reconciliation positively retrieved the
  three stale working-order terminal states, ingested actual executions, and
  then positively re-read two snapshot-absent orders. At 16:56:17Z the final
  authenticated account had zero orders/positions and the ledger had zero
  working, unresolved or unknown orders. No cancel/create or invented terminal
  state was used. Native tests/hashes and live acceptance follow below.
