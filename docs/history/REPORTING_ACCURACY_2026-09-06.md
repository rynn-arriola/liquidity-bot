# Reporting accuracy audit — September 6, 2026 ET

## Scope and runtime

The operator reported understated Discord daily losses and repeated unavailable
withdrawal guidance, and explicitly reiterated that deposits/withdrawals must
not affect trading losses. Reporting-only corrections are prepared; no deploy,
restart, ledger repair, venue mutation or Discord test post was performed.
The earlier $90 HARD change also remains prepared, not loaded.

At 2026-09-07 03:09:35Z, authenticated laptop reads found 26 open orders,
one nonzero position and zero orders absent from local ownership records.
Cash was $1,084.1791 and buying power $1,084.1591. At 03:10:01Z the read-only
ledger had zero unresolved intents and zero account unknown orders. The same
supervisor 394690 / runner 394699 remained running at size 10 / 32, loaded
`99bb436f9` with $128 HARD. These are timed samples, not permanent exposure.
Under that loaded reserve ($1,316), a clean withdrawal calculation is $0.00,
not unavailable. Prepared $90 HARD would change the reserve to $1,050 only
after a separately authorized deployment/restart; no current withdrawal advice
is inferred from the prepared configuration.

## Proven daily-loss defect

The hourly cache generated 02:07:12Z showed every completed seven-day row as a
matched-pair estimate. Some were nevertheless labeled finalized net. The
estimator omitted 136 unbalanced markets and can miss settlements/manual exits.
Historical account reconstruction rejected every carried-position boundary,
while today's row already used the full account mark. This mixed two different
measurements in the same trading column and understated some losses.

One bounded authenticated cash-only walk reached EOF. Net non-trading cash was
$198.04 on August 31 (three credits $205.67/$146.09/$246.28 less $400 withdrawal),
$25 on September 1, $403.78 on September 4 (credits
$106.28/$72.68/$69.58/$155.24), and $100 on September 6. There was none on
September 2, 3 or 5. Completion time, not reward earning date, assigns this cash.

| Completed ET day | Old pair estimate | Account marked trading |
|---|---:|---:|
| September 1 | -$58.7911 | -$54.5887 |
| September 2 | -$49.7401 | -$51.5251 |
| September 3 | -$66.9033 | **-$120.3914** |
| September 4 | -$50.5387 | -$63.7217 |
| September 5 | -$34.4532 | -$41.9194 |

September 3 is `792.7224 - 913.1138 - 0 = -120.3914`.
The opening equity includes $16.2567 of carried position value. This is
account-wide marked trading (consistent with HARD), not closed-only realized
P&L or a claim that every dollar belongs exclusively to automated trades.

The September 4 opening row landed at 04:07:34.628633Z, later than the normal
two-minute ET tolerance. The retained
[policy-deployment evidence](SCALABLE_ENTRY_ORDER_POLICY_2026-09-04.md) proves
the prior runner stopped flat at 03:49:28.179179Z, independent authenticated
zero orders/positions at 03:50:00.787970Z and again at 04:05:47.515078Z, before
new placements at 04:07:49Z. A read-only fill query found zero executions from
03:49:28Z through 04:07:35Z, and the completed cash walk found no cash movement
across that interval. Only the exact equity/cash `792.7224`, timestamp and
`fresh_et_day` source are accepted as this audited delayed boundary.
The August 31 baseline is 31 minutes late and has not been similarly proven;
that daily row and an incomplete seven-day total must remain unavailable.

## Corrections

- Daily history uses adjacent proven marked-equity boundaries, including open
  inventory, minus signed non-trading cash. Withdrawals are added back; deposits,
  paid rewards and other proven credits are removed. No trading/risk logic changes.
- Missing account history no longer falls back to matched pairs. Pair P&L remains
  a separately labeled diagnostic. A missing day makes the period total unavailable.
- Net finalization requires a completed day, proven trading, paid reward and no
  pending reward. Finalized-net totals are explicitly covered-day subtotals;
  no covered day means unavailable, not zero. Dashboard rows share these rules.
- Discord lifetime bot net is explicitly an incomplete ledger estimate. Missing
  current-day cash/baseline evidence renders unavailable rather than false zero;
  prior-day inputs cannot claim today's P&L.
- Summary callers pass the outer decision-loop timestamp, but account refreshes
  occur inside a potentially long sweep. Comparing their newer balance against
  that older time gives a negative age and falsely rejects withdrawal guidance.
  Both scheduled deferral and post-await rendering now read the current clock.
  Missing/stale/future balance, missing guard, order ambiguity and integrity
  faults remain fail-closed with a specific visible/logged reason. The old
  60-second ambiguity deferral and zero-additional-API contract remain intact.

This timing defect is reproduced deterministically in tests. Exact historical
Discord bodies were not persisted, so it is not proof that every unavailable
post had this cause; genuine stale data or in-flight orders remain valid blockers.

## Verification and dedicated final edge audit

59 focused reporting/notification/clock tests passed. Deliberately restoring
the flat-only history restriction, the incomplete-pair fallback and both old
report clocks caused all three targeted regressions to fail for the expected
reasons; fixes were restored. The complete reviewed Windows checkout passed
1,134 unittest tests in 107.991 seconds under the production WSL environment.

Dedicated review covered success, empty/missing history, carried inventory,
signed deposits/withdrawals/rewards, immutable boundary pinning, unknown cash,
pagination EOF/exhaustion, day rollover, current-day nonfinality, pending/unposted
reward, missing/future/stale capital evidence, in-flight order proof, the exact
60-second bound, shutdown reporting and Discord field-size limits. No ownership,
quote/rank, slot/cap, exit, risk or maintenance authority changed. All reporting
reads remain bounded/read-only and add no runner venue traffic. No schema or
ledger data changes were made. Rollback restores the preceding reporting files;
it must not accidentally activate or revert the separately prepared HARD policy.

## Remaining acceptance

Do not deploy or restart until the operator authorizes it. After deployment,
verify a naturally generated Discord table shows September 3 -$120.39 when in
range, missing boundaries unavailable, and withdrawal guidance uses fresh
render-time evidence or explains its genuine blocker. Historical posted messages
have not been edited. The operator additionally requested an unplanned-maintenance
self-recovery review after this reporting fix; that is a separate in-scope task,
not permission to bypass reconciliation, invent cancellation or restart now.
