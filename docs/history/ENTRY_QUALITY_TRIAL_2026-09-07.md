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
