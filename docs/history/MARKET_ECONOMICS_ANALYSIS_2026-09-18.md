# Market economics analysis — September 18, 2026

## Evidence and scope

Read-only analysis requested to inform future market selection. One authenticated
account-wide earnings read at17:25:07 ET used the shared opportunistic report
lane. Production SQLite was opened mode=ro. Seven completed ET dates:
September11–17. Private detail/helper are ignored under `.local-tools/`:
`market-economics-2026-09-18.json` and `market_economics_analysis.py`.
No strategy, configuration, order, deployment or restart change.

Recorded closed-fill P&L replays all older non-reversed fills to establish basis,
then assigns matches and signed fees by event ET date. Per-market sums exactly
match the existing Discord arithmetic on all seven days. Open marks, deposits,
withdrawals and rewards are excluded. This is not complete account or bot-only
P&L: manual/imported fills can be present; resolution without matched fill legs
is not reconstructed.

Approximate resting time runs from earliest PLACE intent to terminal order event,
merges overlaps per market/side and clips at ET midnight. Rejected orders are
excluded. These are local side-hours, not exchange-qualified scoring seconds;
both sides resting one hour count as two. Size, capital and eligibility differ.

## Results

Contribution means reported paid+pending reward plus recorded closed-fill P&L.
It is provisional, NOT finalized net profit or an expected future return.
Unreported reward is not proven zero; the comparison includes costs on markets
without reported reward rather than selecting only reward winners.

| Group | Unique markets | Paid | Pending | Closed-fill P&L | Provisional contribution | Approx. contribution/side-hour |
|---|---:|---:|---:|---:|---:|---:|
| WTT table tennis | 190 | $197.67 | $55.39 | -$56.35 | $196.71 | $0.41 |
| Dota 2 | 15 | $34.48 | $5.13 | -$12.87 | $26.74 | $0.58 |
| MODUS darts | 40 | $36.61 | $8.39 | -$14.14 | $30.86 | $0.35 |
| CS2 | 63 | $53.65 | $7.27 | -$34.06 | $26.86 | $0.16 |
| Setka Cup Ukraine | 500 | $159.73 | $87.08 | -$80.48 | $166.33 | $0.15 |
| ITF men | 124 | $25.93 | $2.26 | -$29.14 | -$0.95 | approximately $0.00 |
| ITF women | 96 | $18.35 | $1.18 | -$13.50 | $6.03 | $0.02 |

WTT had six positive days and one zero; Setka Ukraine seven positive days but
much more resting time. Dota had five positive observed days and one zero,
only15 markets. MODUS had three negative and three positive observed days.
CS2 had six positive days but September17 was negative. Daily signs remain
sensitive to reward-date alignment/finalization and are not independent trials.

All-group earnings were $594.65 paid and $188.32 pending. Account-ledger
closed-fill P&L -$33.21015 includes a previously audited manual/imported
+$230.4549 `caoc` round trip. That headline is NOT bot trading performance.
The table omits that unrelated family, not losses in its listed families;
listed families are not guaranteed bot-only attribution either.

## Pool/membership and coverage

1,412 market-days mapped to one observed historical program each, across39
program-days. Recorded pool fields included table tennis$35, esports$100,
darts$35, ITF$30, ATP/WTA$200, cricket$75 and doubles$50. These are historical
program/period observations, not verified current pools or amounts to multiply
by event count. Membership here is the observed/traded subset, not the complete
venue universe. Repeated program-pool fields are never summed per market.

The [official Retail overview](https://docs.polymarket.us/api-reference/incentives/overview)
provides market membership, event start, program ID/time bounds, pool, target
size and discount factor. Earnings aggregate by market/ETdate without program
ID, so multiple-program allocation is not directly available. The
[scoring guide](https://docs.polymarket.us/incentives/liquidity) describes price,
size and competing liquidity determining share; headline pool alone is not
an earnings forecast.

- All mapped historical observations are `day_of`. Today's new daily-event
  setup needs its own completed samples before claiming similar economics.
- 82 positive-reward market-days totaling$24.58 lack same-ET-day resting
  intervals. Yesterday's seven total$0.57; all have orders ending the previous
  ETday. Investigate date alignment; this does not prove the API uses UTC and
  does not authorize shifting reward dates or inventing intervals.
- Absent/late earnings are unknown, not zero. No minimum-payout filter is added.
- Replay leaves59 historical unmatched inventories at cutoff, NOT59 live
  positions. Manual/imported activity and resolution can differ from matched
  local fills. Current exposure was not refreshed for this historical task.
- Seven days, pending rewards, changing software, variable quantities and
  selection bias prevent causal claims or a reliable estimate of gains from
  replacing every weak slot with a stronger group.

## Recommendation and handoff

WTT is the strongest substantial historical candidate; Dota is promising with
less evidence; MODUS is promising but inconsistent. ITF is a lower-priority
candidate, not an immediate ban. Preserve day_of-before-daily and safety gates.
Proposed ranking: current valid reward share and eligible remaining time,
historical reward conversion and closed-trade cost, adjusted for confidence.
Validate in shadow on subsequent days before any separately authorized change.

Supervisor302233/runner302251 remained present around21:26Z; no restart or
trading-code change. Last authenticated exposure remains the separate21:11:48Z
release check. Pre-work parity matched Windows/GitHub/WSLfc9250ce, loaded
marker59691b29; verified private backup21:26:02Z. OfflinePC skipped.
