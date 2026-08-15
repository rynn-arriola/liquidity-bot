# Next session — outstanding fixes

Written 2026-08-14 at the end of a long session. Ordered by what actually
blocks earning. Every claim here is measured; see `FINDINGS.md` for the data.

**Current state**: running on `4cc4520`, flat, 0 unresolved intents, no
crashes. Quote size 5, 8 markets, $40 realised stop, reward-cost gate OFF
(maker-lab's entry rule: 2c spread plus risk caps, nothing else).

---

## 1. Quote churn — RESOLVED 2026-08-15

**The cancels were never planned actions.** They came from
`_quarantine_working_orders`, which does not log as an `[action]` — which is
why the cancel tally read zero while quotes visibly vanished. It cancelled
*every bound market*, driven by any single market reporting
`execution_state_unknown`.

Quarantine now takes a slug. Global remains for genuinely account-wide faults
(stream reconnect, rotation).

Measured before: PLACE 2597 / CANCEL 2596, ~1.5s apart, nothing rested.
Measured after (01:44–01:50Z, 8 markets): PLACE 29 / CANCEL 7, twelve orders
resting concurrently, median age 181s. Two of the seven cancels were the
`target_not_met` sign-flip below; five were legitimate repositioning.

**Residual, much smaller**: `desired_sides` still drops a side on a bare
`expected_net <= 0` sign flip. Hysteresis mirroring `spread_hysteresis` would
remove the last ~7% of churn. Not urgent now that quotes rest for minutes.

## 1b. One market's error no longer stops the session

Three paths took all eight markets down for one market's fault, all fixed
2026-08-15 and mutation-checked:

- recovery bound a market whose game had started → `no longer pregame` →
  `startup_unavailable` retry loop for 2h20m with a position held.
  `_refresh_contracts(for_exit=True)` skips gates that exist to stop us
  *opening* risk; recovery markets are marked retired so they exit, never quote.
- a planning exception on one market → whole-session terminal → now retires it.
- an unexpected action exception on one market → whole-session terminal → now
  retires it.

`RuntimeInvariantViolation` stays terminal everywhere: it is about the process,
not a market. Retirement stays bounded — ten rounds per market, and a full
slate of retirements still stops the session, so a systemic fault is not
silently absorbed.

## 1c. Old quote churn detail (kept for reference)

**Symptom**: `[action] ... kind=CANCEL reason=side_no_longer_desired` two to
three seconds after `PLACE_POST_ONLY`, repeatedly, same market and price.

**Cause**: with the reward-cost gate advisory, `expected_net_by_side` sits
near zero and flickers sign between passes. `_flat_plan` drops any side whose
`expected_net <= 0` from `desired_sides`, so the side leaves and rejoins on
consecutive passes.

**This is a second, distinct churn cause.** The first — our own contract
flipping `target_not_met` eligibility — was fixed in `rewards.py` (see
FINDINGS 6e) and confirmed: a quote rested four minutes instead of 500ms.

**Fix**: hysteresis on `desired_sides`, mirroring what `minimum_entry_spread`
already does with `spread_hysteresis` — a resting side should need a clearly
negative `expected_net` to be dropped, not merely a sign flip. `_flat_plan` in
`reconciler.py` already receives `frame.working_orders`, so it knows which
sides are resting.

**Why it matters**: reward pays for resting time. A quote cancelled after two
seconds earns nothing, and this is the last known thing between the bot and
actually collecting.

## 2. Discord noise — recovery alerts are marked critical

**Symptom**: constant alerts, most of them these two:

```
Liquidity Bot found identified one-market state from a prior process...
Liquidity Bot hit a recoverable runtime failure and proved flat; restarting.
```

**Cause**: they fire on every restart that finds leftover state, and they are
sent `critical=True`. Roughly fifteen restarts happened during the
2026-08-14 session, each after a `kill -9` deploy.

**Fix**: these are a normal, healthy recovery path, not faults. Send them
non-critical. Keep critical for: loss stops, ownership violations, retirement,
abandonment, and unexplained runner failures.

**Related**: alert volume was already reduced once for the idle scan (which
claimed "new risk is disabled until reconnect and reconciliation pass" on a
perfectly healthy bot). Same class of problem — an alert that cries wolf on a
normal state trains the operator to ignore Discord.

## 3. Graceful stop — deployed, never verified

`_venue_proves_flat()` was added so shutdown exits as soon as the venue shows
no orders and no position, rather than waiting on `risk_state_known()` (false
35% of wall-clock time during ordinary cancels).

**Deployed but never timed.** Verify: `kill -TERM` a flat runner and measure.
Before the change it exceeded two minutes and never completed.

**Why it matters**: slow stops are the root of the whole restart-noise cycle.
Slow stop → operator uses `kill -9` → leftover orders and intents → recovery
run on next start → the two alerts in item 2. Fix the stop and that entire
class disappears.

---

## Bigger levers, not bugs

### Capital cannot be deployed at the current shape

```
5 contracts x 8 markets x 2 sides x ~$0.50 = $40   of $385.88 available
```

`max_markets = 8` is the binding constraint, and it comes from plumbing: the
private WebSocket accepts 10 subscriptions per connection, and positions must
be scoped one market per subscription. 1 orders + 8 positions + 1 balance = 10.

Connections are independent and each carries its own quota (measured, FINDINGS
7), so 30 markets is 4 connections. maker-lab ran 30. That is the single
change that most closes the trade-volume gap.

### API rate is 13x maker-lab's

We run 4 req/s. maker-lab settled at **0.3 req/s** and its config carries
`API_BAN_COOLDOWN_MIN = 5`, `API_BAN_COOLDOWN_MAX_MIN = 40` and the comment
*"18/min; we measured 2.5/min"* — a project that got rate-limited and backed
off hard. We have made ~2,500 placements at 4 req/s without a ban, but this is
an untested edge we are sitting well past.

### Unresolved measurements

- **`live` vs `day_of` split of the $70.92.** The venue exposes no period
  field (confirmed against the published reference). Fill-timing cannot
  recover it — half the money came from markets that never filled. This
  decides whether a pregame-only executor can reach those numbers at all.
- **maker-lab's $40.09 ledger gap** and liquidity-bot's unexplained **+$0.64**
  on 2026-08-13. Both still unexplained. Neither blocks a small live test;
  both block any profitability claim.
- **The 0.5 reward haircut** still has no evidence behind it. The 2x cost
  margin was removed and then the whole gate made advisory, but the haircut
  remains an arbitrary number.

---

## Process notes for whoever picks this up

**Deploy without `kill -9`.** Fix item 3 first; every forced kill creates the
recovery noise in item 2.

**Mutation-check every fix.** Revert the change and confirm the test fails.
Three tests written during this session passed against the unfixed code —
they asserted on a helper rather than on the value the caller actually reads.

**The gate lives in two places.** `_evaluate_rewards` in the runner *and*
`evaluate_market_opportunity` in the selector. Changing one alone does
nothing, because a market that never becomes viable in the selector is never
bound for the runner to see. This cost a deploy cycle to discover.
