# Authorized 70% entry-quality trial — September 7, 2026

## Decision and scope

After reviewing the preliminary reward tradeoff, the operator said to enable
the filter today and subsequently authorized Command 1. This supersedes the
earlier disabled-delivery decision, not any other rule. The trial uses
`enabled: true`, `minimum_retained_value: 0.70`, size 10 / 32, the same $90 HARD,
durable ET-day baseline/ratchet, time/loss order caps and 900-second exit clock.
The operator was told there is no automatic midnight disable; no scheduler,
additional trading rule, monitoring automation or automatic rollback is added.

Existing position reduction, shutdown, maintenance and recovery remain intact.
Only the prepared entry-only filter and actual-action estimate repair deploy.
The reward-aware scenario was unfavorable before replacements and substantially
pending; this live trial does not turn that uncertainty into a savings claim.
[Implementation](ENTRY_QUALITY_FILTER_2026-09-07.md) ·
[Tradeoff](ENTRY_QUALITY_REWARD_TRADEOFF_2026-09-07.md).

## Stop and positive reconciliation

- Stopped supervisor `770963`, then gracefully signaled runner `770972`.
  Its terminal record at 15:12:53.564199Z truthfully says `flat=false`,
  `operator_sigterm`: existing shutdown policy parked two passive exits rather
  than crossing far from kickoff. Another owner was flattened by normal shutdown.
- At 15:13:23Z the venue still had two orders/one position; at 15:14:34Z one
  order/one position. These failed the start gate and no runner was launched.
- By 15:15:53Z the venue independently proved zero orders/positions. Both exits
  filled naturally; no additional forced-close request was executed. Two local
  working rows remained because the old process had stopped before their fills.
- A stopped-runtime SQLite-online parity backup at 15:17:47.105361Z was verified
  before reconciliation: 288,722,944 bytes, SHA prefix `fb199255271f`.
- Ignored `.local-tools/reconcile_stopped_trial.py` verified no runner, current
  venue flatness and no unresolved/unknown state, then used existing executor
  GET-by-ID reconciliation for exactly the two owned rows. It added two new
  positively reported execution fills, applied returned trade states and fresh
  complete account snapshots through existing ledger methods. At 15:18:21.409500Z
  venue orders/positions and local working/unknown/unresolved state were zero.
  No venue mutation, manual SQL correction, baseline reset or guessed absence.

## Review and tests

Activation changes only the JSON boolean. The committed-policy regression now
expects the authorized enabled value; legacy lifecycle fixtures explicitly
select the off path, while dedicated entry-quality tests exercise enabled
selection, same-policy wiring, one-side refusal, owned bad-entry cancellation,
actual-action recheck, forged-shadow isolation and unchanged reducers/exits.

The 39-test entry-quality/commissioning slice passed. Reversing the config to
false made the activation regression fail; restored true before the complete
review-tree suite: **1,173 tests passed in 110.837 seconds**. Reuse the earlier
three implementation reversals and completed edge-case audit for unchanged
source. Final deployment diff review confirms no additional source, launcher,
report schedule, capital, order-cap, loss-baseline or exit-policy change.

Review includes disabled rollback, immutable startup loading, invalid config,
one/both/unknown sides, no viable candidates, 32-slot ceiling, positive flat and
ownership proof, unchanged maintenance/recovery/shutdown authority, actual
action price/quantity/fresh book and no additional venue request from the filter.
Next: push, reviewed native deployment/hash/full-suite verification, fresh
flat/clear start gate, normal explicit `auto 10 32` launcher and live acceptance.

## Deployment and restart

- Pushed `b089b3a6e8e99fb60196d63c042a5f76dc96e705`, then fast-forwarded the
  clean tracked laptop WSL checkout from `b4f22b1ae`. Expected untracked
  `DEPLOYED_COMMIT` and editable-install metadata were preserved.
- All **163** tracked source/test/script/web/config file hashes matched after
  normal line-ending normalization. The exact native checkout passed
  **1,173 tests in 81.369 seconds**. Then updated `DEPLOYED_COMMIT` to the
  tested installed release. Config and selector-policy construction both
  independently reported enabled=true, minimum=0.70 at size 10 / 32.
- The 15:22:45.452869Z pre-start gate again proved zero authenticated orders,
  zero positions, zero working/unknown orders and zero unresolved intents.
- Normal `start_commissioning.sh auto 10 32` launched supervisor **816479**
  and runner **816489**, started **15:22:45Z**. Process arguments confirm the
  intended quantity/width; no alternate entry-policy path overrides the
  checked-in enabled file. The existing independent dashboard remains up.
- Post-start quote/API acceptance is pending below; a successful launch alone
  is not acceptance. No additional policy, schedule or risk baseline changed.

## Live acceptance and operator-requested filter verification

- Initial scan at **15:23:43Z** found **59 viable candidates** after the new
  gate, with two event pages and 91 ranked books. This is enough candidate
  supply for configured 32 capacity, not proof of a fixed number of markets
  removed compared with the earlier differently timed scan.
- Official maintenance evidence was `HEALTHY/NORMAL`. At **15:25:54Z** ordinary
  lifecycle replacement released two expired owners, retained 30 and added
  two, preserving **32 active owners**; this was not quality-driven churn.
- Live action logs positively show `entry_quality_below_minimum` on refused
  sides while the passing side places normally. Examples: at 15:25:03Z
  `aec-setkamecz-mardom-oplpav-2026-09-07` blocked offer and placed bid; at
  15:25:07Z `aec-setkameua-levana-plapav-2026-09-07` blocked bid and placed offer.
- A read-only ledger check after the first window examined **26 new building
  PLACE observations across 26 markets** since startup. All 26 had a retained
  estimate at/above 70%; minimum **76.16%**, zero missing ratios, zero below
  threshold and zero actual-stake/estimate inconsistencies. This proves initial
  natural acceptance, not perpetual book quality or future profitable exits.
  Reproduction: ignored `.local-tools/entry_trial_acceptance.py`.
- First API window at **15:25:55.675571Z**: **189.4 seconds, 436 attempts,
  2.302/s, 26 mutations, zero failures, zero 429s**, no lane cooldown and peak
  queue depth one. No synthetic fault, fill or test webhook was induced.
- Authenticated acceptance at **15:26:17.081356Z**: **26 open orders across 26
  markets, zero nonzero positions, zero unknown orders**, 26 local working
  orders and **one in-flight intent**. Do not misstate that live snapshot as
  zero unresolved intents; the pre-start gate was entirely flat and clear.
- Windows/GitHub/WSL/installed-marker parity passed on the code release; backup
  PC remains offline/pending. Following documentation-only updates do not
  alter the tested source/config or require another restart.

The trial is enabled and trading. Reward-adjusted performance, more restrictive
book transitions, both-side dark time and future maintenance/exit behavior
remain natural evaluation items. The operator controls when to disable it;
the setting does not expire automatically at midnight.

## Read-only trade review at 17:50Z / 13:50 ET

The operator asked whether the new change is working on actual trades. No
trading code/configuration, risk state, order or position was changed, and no
restart was performed. Runner 816489 / supervisor 816479 remained active with
installed marker `b089b3a6e`; available Windows/GitHub/WSL tracked parity passed
at documentation HEAD `d5a4fdb00`. The backup PC was offline/pending.

Authenticated reads at **17:50:45.157195Z** found **22 orders across 17 markets,
four positions, zero unknown orders and zero unresolved intents**, with 22
local working orders. The scheduler still reported configured scope 32; order
count is neither simultaneous slot count nor a count of entries alone.

### Measurement scope and results

Reused the existing full-history fill replay and consistency assertions through
ignored `.local-tools/entry_trial_trade_review.py`, not a new accounting method.
Source: SQLite-online parity snapshot at **17:50:57.300904Z**, 291,512,320 bytes,
SHA-256 `54d70b1a909e3820d99ae25639b073f5d7a8cea318b22c473738ab4bfd8b23e2`.
Native integrity and copied hash/size passed. The ignored rolling backup may
subsequently advance; this hash identifies the actual analyzed capture.

Window: **15:22:45Z activation to 17:50:57Z**, about 2 hours 28 minutes
(11:22 AM–1:50 PM ET). Completed trades mean flat-to-inventory-to-flat episodes
whose first fill occurred after activation; partial fills are combined and
signed recorded fees/rebates included. Unfinished positions, rewards and
unrelated account cashflows are excluded, so this is not today's account loss.

| Evidence | Result |
|---|---:|
| New building-order observations | 343 across 68 markets over time |
| Below 70% / missing ratio / inconsistent actual stake | 0 / 0 / 0 |
| Lowest placement retained-value estimate | 70.3701% |
| Observed entry orders with at least one recorded fill | 37 |
| Lowest estimate among filled entry orders | 72.5636% |
| Completed trade episodes | 32: 10 winners, 22 losers |
| Net recorded completed-trade P&L | -$11.3145 |
| Matched quantity / net per 10 matched contracts | 253.85 / -$0.4457 |
| Episodes with at least one taker reducer | 28 of 32 |
| Median completed holding time | 14.8 minutes |
| Unfinished post-activation replay episodes | 4, excluded from closed P&L |

The preceding equal-duration completed cohort was 21 episodes, 164.24 matched
contracts and -$15.1785 (-$0.9242 per 10). This is an uncontrolled comparison,
not proof of filter savings: different markets/times, restart/cap transitions,
and four unfinished current episodes make causal attribution inappropriate.
Reward sacrifice/replacement benefit is not measured by this short-window
fill comparison; do not claim the trial improves overall net returns.

### Natural enforcement and remaining losses

- At **17:47:35Z**, `aec-setkameua-khaser-kulhry-2026-09-07` cancelled a
  resting bid with `side_no_longer_desired:entry_quality_below_minimum`.
  The filter checks resting entry quality as well as initial placement.
- Recent API windows ending **17:49:22Z, 17:50:42Z and 17:52:05Z** had
  158/159/158 attempts at 1.918–1.974/s, zero failures/429s/cooldown and
  maximum waiting depth one. This is evidence for those windows, not a claim
  that every API request throughout the day succeeded.
- Worst completed episode: `aec-setkameua-lazrus-shcden-2026-09-07`, first
  fill **16:45:11.994411Z**, final fill **17:03:03.426209Z**, 7.38 matched
  contracts, **-$4.3004**, despite an initial estimate of **92.2246%**.
  Logs show normal passive reducers, then `hard_timeout_cancel_confirm_first`
  at 17:02:30Z and `PLACE_IOC hard_inventory_timeout` at 17:03:02Z. The exit
  path remained active despite an entry-quality blocker. The estimate is a
  contemporaneous stressed-book check, not a guaranteed later liquidation
  value or a cap on loss after entry.
- Other large completed losses were `haivol-kagvit` **-$2.85**, `wtadb-hsiost-chajoi`
  **-$2.27**, and `cs2-tit-tsab` **-$2.09**. All had passing entry estimates.

Conclusion: mechanical enforcement and continued entry/exit activity are
verified; profitability or reward-adjusted improvement is not established.
Keep the authorized settings unchanged for this read-only check. Longer-window
reward/trading evaluation remains in NEXT; no monitoring automation was added.

## Why the pending-entry ceiling became 25 at 17:52Z

Read-only follow-up found an explicit `loss-entry-order-cap` transition at
**17:52:35.265120Z / 13:52 ET**: ET-day adjusted marked loss **$69.9090 / $90
= 77.68%**, previous cap 30, new cap **25**. This is the existing 70%-HARD tier,
not a new quality-filter limit or a reduction from 32 configured market slots.
The daytime ceiling 40 is subordinate to the tighter durable loss ceiling.
Open-position-market orders/reducers are excluded; unresolved building creates
reserve capacity. The tightest tier persists until the ET-day rollover even
if the meter recovers. The daily marked meter differs in interval and open-mark
inclusion from the earlier -$11.3145 post-activation completed-trade cohort.

At **17:58:40.843519Z**, authenticated reads found **23 orders across 21 markets
and two positions**, zero unknown orders, four unresolved intents and 24 local
working rows. Reads occur sequentially while trading continues; differing
counts are not proof of cancellation. Runner 816489 remains active with size
10 / 32 and installed `b089b3a6e`; the 17:57:39Z API window had zero failures,
429s and cooldown. Available parity passed; backup PC remains pending. No
code/configuration change, restart or repair was performed. Quality and other
eligibility gates can keep actual entries below 25; it is a maximum, not a
target or a total-order limit.
