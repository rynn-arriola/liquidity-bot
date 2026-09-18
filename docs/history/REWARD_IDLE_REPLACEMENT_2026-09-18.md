# Reward-unavailable idle slots — September 18, 2026

## Scope and evidence

The operator authorized fixing the retained idle-slot defect after requiring
proof it was real. A follow-up requested checking the number of open positions;
after the exit evidence below, the operator said to leave position handling alone
and continue the original work. No new deployment/restart authorization applies.

Before changes, Windows/GitHub/WSL HEAD was `d70a31181`; live source marker was
`6323dc302`. Parity passed at20:48:08Z with a verified private ledger backup.
Offline PC probing was explicitly skipped. Runner282322 remained live.

The prior run logged repeated successful contract refreshes that could not find
applicable rewards, including16 owners around20:20–20:22Z. Its20:19:25Z top-up
retained31 owners and released only one expired owner despite26 viable scan
markets. These observations alone do not prove all16 were empty or quantify
missed replacements/rewards. The controlled regression supplies those facts:
a full two-owner scope, one repeatedly reward-unavailable flat/clear owner and
an external viable replacement. The old code returns no top-up and leaves the
idle owner occupying capacity. The regression failed before implementation.

`git blame` dates the missing-row retention rule to August25 (`2e3c677982`),
with exact-program applicability handling on August26 (`5825db8114`). Economic
idle replacement arrived August28 (`dc71ae85c`) but did not cover missing rewards.
This predates the recent reward API compatibility work. We cannot quantify its
historical reward cost or conclude the API change caused the current symptom.

The separate new-run order-count dip included25 five-minute GTD expiries over
53 seconds and normal fills/replacements. This fix does not alter GTD renewal.

## Design and end-to-end acceptance

Do not infer that a missing program ended, remove ownership immediately, or
perform a full cancellation/rotation. Instead extend the existing optional
idle replacement transaction:

1. Track repeated successful contract reads lacking applicable rewards only
   for known-open pregame, flat/clear, non-retired owners. Entries stay blocked.
2. Require observations spanning the existing600-second idle dwell. Reset on
   success/unknown/failed reads and ownership; stale or reversed observation
   clocks cannot qualify. Reuse the existing900-second backoff plus5-second
   coalescing allowance for the maximum evidence gap.
3. On the normal900-second scan only, combine economic-idle and reward-idle
   candidates oldest-first. Fill existing free capacity first, then require
   one distinct viable replacement for each optional release.
4. Preserve the existing near-kickoff guard, complete pre-commit account proof,
   absent-order/intent checks, active-scope invariant and stream handoff.
5. Refresh only proposed reward-idle releases through the bounded8-owner bulk
   contract reader. Returned rewards, missing/unknown metadata or failed reads
   invalidate the plan before scope/streams change. A discovered position
   retains its owner. Stop during revalidation returns without binding.

Steady-state detection uses existing reads, no extra poller. A proposed swap
adds one metadata and one incentive bulk fetch per up-to-eight released owners
(underlying event enrichment/pagination can add requests); every call retains
the shared limiter and existing failure backoff. Supply-short scans never
release optional owners just to lower the active count. Urgent lifecycle and
post-flat triggers do not accelerate optional eviction.

This improves capacity reuse, not a promise of30 orders or greater realized
profit. Ranking, day_of priority, daily horizon, entry quality, quantity, pending
entry limits, four$25 risk windows, GTD, exits and source mode stay unchanged.
No schema/config migration; restart resets only this in-memory dwell. Rollback
restores conservative slot retention and still requires normal stopped/flat gates.

## Position investigation — no exit change

Authenticated20:52:23.901312Z snapshot:29 orders,25 flat entries and4 exact
reducers for four positions; zero unresolved/unknown state. Quantities were
+10 jialu/enashi, -9.94 budole/malden, +6.88 hohser/tuzole and -4 masyev/rezrom.
The two oldest observations were about12–13 minutes old, not overdue against
the normal900-second inventory target. Live logs then showed:

- budole/malden flat at20:52:32.993910Z.
- masyev/rezrom flat at20:53:45.342907Z, about844 seconds after first observation.
- hohser/tuzole cancelled its reducer at20:54:57.817046Z, observed terminal
  cancellation20:55:02.023414Z, submitted the timeout IOC20:55:02.091236Z,
  acknowledged6.758705 seconds after its durable exit deadline, and observed
  flat20:55:05.416455Z.

The900-second target can defer when existing early-exit depth/loss constraints
fail; kickoff flatten overrides that early deferral. No claim that900 seconds
is an unconditional venue-fill guarantee. This sample establishes working exits,
not a proof that every future execution will succeed. No position/risk changes.

## Verification and final edge audit

Focused tests cover replacement at capacity, repeated-read599/600-second
boundary, no replacement, urgent-scan exclusion, missing/returned rewards,
missing metadata, unknown clock/day, failed reads, stale evidence, fill race,
working/absent orders, unresolved intents, contract forgetting and oldest-first
one-for-one swaps. Existing lifecycle/economic top-up tests remain applicable.
The deliberate reversal returning false from `_reward_idle_and_flat` made the
original reproduction fail again (exit1); the repair was restored.

Final review covers success/failure, empty/full/supply-short capacity, clocks,
position races, exact ownership, read pagination, shared limiter/backoff,
recovery/retirement resets, shutdown before scope commit, in-memory restart and
rollback. No new quote/exit authority, cancellation path, secret or ledger edit.
The complete local suite using the production WSL interpreter passed all1,790
tests in263.314 seconds. The initial focused top-up suite passed62 tests; two
additional methods cover real order/intent ownership and nonapplicable/stale-day
distinction. A fixture initially supplied a not-yet-ended program at its first
timestamp; correcting that historical bound made its focused test pass, and the
full suite verifies all64 top-up methods. Documentation audit and diff checks
passed. No source/config/launcher was installed into the running WSL checkout.

Authenticated20:58:15.178873Z follow-up proved30 flat entries and one exactSELL10
reducer for the sole+10 jialu/enashi position; ledger31 orders/one position agreed,
with zero unresolved/unknown state. Runner282322 remained the original process.
Prepared changes require separately authorized reviewed deployment and restart;
natural reward-idle replacement acceptance remains pending that activation.

## Command 1 deployment — September18

The operator subsequently issued Command1, authorizing the reviewed stop,
deployment and restart workflow. Supervisor282304 was stopped before SIGTERM
to runner282322. The old runner ended21:03:23.275705Z with `flat=false`, having
parked two exact reducers under the existing shutdown policy. This was not
treated as flat proof.

Authenticated21:04:05Z reads then found zero venue orders/positions, but the
stopped ledger still held two orders/positions. The stopped-only prestart helper
acquired the exclusive lease, proved the venue flat and both exact orders
`CANCELED` at21:04:35Z, then ran normal account/history reconciliation with venue
mutation entry points disabled. No raw ledger repair or venue mutation occurred.
The21:05:26.852077Z gate proved zero venue and ledger orders/positions plus zero
unresolved/unknown state. Stopped-runtime backup captured21:05:20Z was verified
at712,433,664 bytes/SHA-256 prefix5dc935217c40.

Reviewed/pushed `59691b29b4192fb36551d94ad206454fc15d170d` was deployed by
fast-forward from a local git bundle, preserving untracked runtime artifacts.
All204 normalized source/test/config/script hashes matched Windows and the
reviewed commit. All1,790 tests passed in171.321 seconds in the exact installed
checkout; only then was `DEPLOYED_COMMIT` updated. A fresh authenticated
21:09:07.465884Z gate again proved zero orders/positions/ambiguity on venue and
ledger. Restart used explicit auto/size10/32/REST, supervisor302233 and
runner302251. Existing persisted risk and source settings were not reset.
The21:09:40Z startup scan used4 event pages/52,719 nested rows,716 executable,
602 rewarded,33 books ranked and23 viable;1 remaining estimate cleared$1 and
none cleared$1.50. Selection and validation books used public WebSocket data
with no REST fallback. Known-started/unproved-pregame owners remained gated.

The first121.2-second API window through21:11:10Z made250 attempts at2.063/s,
including28 mutations and four individual-order404s; zero429/cooldown. This is
initial acceptance, not a long-term rate-limit guarantee. Persisted settled
window P&L was-$6.38 against$25, entrycap25, no HARD/released flags; restart did
not erase losses or restore the higher ceiling.

Authenticated21:11:48.153931Z state:26 orders across20 markets, consisting of25
flat entries and one exactOPEN SELL10 reducer for the sole+10 mocdav/urevad
position. Ledger26 orders/one position agreed; zero unresolved/unknown state.
This proves initial quoting/coverage, not a complete exit cycle or a natural
reward-idle swap. The new dwell starts fresh with the process and optional
replacement remains on normal scans; natural acceptance must not be induced.
