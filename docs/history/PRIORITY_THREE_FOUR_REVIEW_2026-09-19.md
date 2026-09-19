# Priority 3 and 4 measurement review — September 19, 2026

## Scope and sources

Read-only review requested after Priorities1/2. No trading source, settings,
orders, API calls, deployment or restart changed. Runner582918 was present at
approximately10:30Z with its September19 02:52:11Z start, manual REST size10/32.
The existing four$25 windows and entry caps remain unchanged. Exposure was not
refreshed for this historical analysis: current venue orders/positions are
unknown here; the separate Priority2 10:20:03Z proof is historical, not current.

Reused the saved account-wide earnings snapshot observed09:27:51.846057Z
(05:27ET), not the stale August ledger earnings cache. Consistent read-only
ledger capture finished10:30:57.990018Z. Existing economics script produced
private `data/market-economics/20260919T103058.000138Z/`, covering completed ET
dates September5–18. It reused the earlier09:27 frozen model against a later
10:30:23Z candidate scan, so no future outcomes were used as earlier predictions.
All14 per-market daily P&L sums exactly matched canonical closed-fill replay.
The verification helper's old success text says seven days; its loop actually
uses the report's first/last dates and checked all14.

P&L includes signed fill fees and recorded manual activity; it excludes open
marks, deposits, withdrawals and rewards. Unmatched settlement is not invented
as paired-fill P&L. Rest means approximate merged building-side hours, not
venue-qualified seconds; one hour on both sides counts as two. Reports are
retrospective ledger truth, not a historical as-known trading backtest.

## Priority 3 — profitability and coverage

September18, as reported in the morning earnings snapshot:

| Observed program attribution | Market-days | Entry side-hours | Paid | Pending | Closed-fill P&L | Missing reward rows |
|---|---:|---:|---:|---:|---:|---:|
| Day-of only | 212 | 315.02 | $0 | $80.72 | -$25.53 | 88 |
| Daily-event only | 38 | 20.17 | $0 | $6.83 | +$0.01 | 23 |
| Mixed/unallocated | 37 | 80.46 | $0 | $33.90 | -$6.83 | 9 |
| Total | 287 | 415.65 | $0 | $121.45 | -$32.36 | 120 |

Totals are computed before rounding. The day-of and daily rows each have one
observed exact program signature per market-day. Mixed/unallocated is retained,
not assigned by whichever reward period looks most profitable. API earnings
are market/day/status rows and cannot themselves split multiple programs.
Some historical manual/unmatched activity remains flagged within totals.

Pending plus recorded P&L is provisionally+$89.095, **not paid net profit**.
Missing reward is unknown, not proven zero. Only15 of38 pure daily market-days
have reported rewards. Daily's$6.83 versus day-of's$80.72 is not a fair earning-
rate comparison: daily had only20.17 side-hours versus315.02. Reported pending
per observed side-hour is about$0.339 versus$0.256, but incomplete coverage,
program mix, selected markets and one transition day prevent a winner claim.
There are zero training-eligible September18 rows while rewards remain pending.

Across14 days:3,071 market-days,1,815 usable rows,847 missing-reward rows,
81 manual/unattributed episode flags,41 cross-day flags,26 unmatched-inventory
flags and176 ambiguous/missing parameter flags (overlap). The26 unmatched
flags are historical rows, **not26 current positions**. All20 exact cohorts
are withheld; all have incomplete population/attribution and8 also have fewer
than20 distinct markets. None is validated for selection.

September11–17 descriptive comparison, including incomplete rows/costs:

| Group | Paid | Additional pending | Closed-fill P&L | Paid + recorded P&L | Entry side-hours |
|---|---:|---:|---:|---:|---:|
| WTT | $253.06 | $3.91 | -$56.35 | $196.71 | 459.38 |
| Setka Ukraine | $246.81 | $2.05 | -$80.48 | $166.33 | 1,059.15 |
| Dota2 | $39.61 | $0 | -$12.87 | $26.74 | 41.41 |
| MODUS | $45.00 | $0 | -$14.14 | $30.86 | 84.09 |
| CS2 | $60.92 | $0 | -$34.06 | $26.86 | 151.07 |
| ITF men | $28.19 | $1.07 | -$29.14 | -$0.95 | 373.21 |
| ITF women | $19.53 | $1.41 | -$13.50 | $6.03 | 298.61 |

WTT remains a stronger substantial historical research candidate than ITF,
not authority to ban a league or transfer old day-of results to daily programs.
Paid + recorded P&L is not certified complete bot-only profit: reward coverage,
manual activity and unmatched settlement remain limitations. The known large
manual/imported September12 gain must not be used as bot profitability.

## Priority 4 — selection measurements and limits

- The24-hour quote report at10:30:58Z captured8,927 of8,927 required building
  PLACE observations, zero missing;397 maker-filled,8,485 terminal-unfilled,
  45 open/unresolved labels. These labels are not a current venue-order count.
  Median placement-to-fill94.3s is queue/rest duration, not bot exit delay.
- Current run through about10:34Z:3,164 entry observations, all day_of,
  complete modeled stressed-exit depth for all, minimum retained-value ratio
  70.05284%. This supports recording and admission consistency, not guaranteed
  future executable depth or proof that every attempted order rested.
- Below80% shadow threshold:1,327/8,927 observations (14.87%),42 later filled;
  993 had a recorded spare,147 no spare,187 unknown. Do not treat the report's
  sum of short-horizon projections as reward dollars saved/lost or count
  repeated placements as independent market experiments. Replacements may
  become stale before actual admission.
- Zero below70% placements is expected with the active70% filter. It cannot
  measure outcomes on rejected entries. The report's `live orders refused:0`
  means the **shadow observer** cannot veto, not that the configured live
  filter is disabled. No proposed80% rule was enabled.
- Public trade-volume fields remain absent; no extra collector is added.
  Model probabilities and lifetime filled/unfilled labels have different
  horizons and cancellation censoring. A full horizon-matched, held-out
  calibration is still pending; the existing bucket report is not that proof.
  Source `fill_hazard.py` still identifies its prior as
  `maker_lab_paid_markets_2026_08_10`; current bot observations are not
  automatically refitting it. Recalibration needs a separate reviewed proposal.
- For usable historical subsets only, paid/constant placement-rate model was
  2.275 on September16 (166 rows) and2.388 on September17 (156 rows). The model
  includes a haircut and approximates rest at fixed placement share; these
  incomplete historical ratios cannot safely multiply new daily estimates.
- Current run contains39 complete scan records through10:30:23Z,296 distinct
  evaluated markets and272 ever viable. Latest scan38 candidates/26 viable,
  all day_of;2/26 estimated at least$1 remaining and0/26 at least$1.50.
  This is a bounded evaluated slate, not a census of all venue reward markets.
  Accrued reward is omitted; low remaining estimates do not prove no payment.
- Frozen-model shadow comparison has26 insufficient-history candidates and
  12 current-strategy-ineligible candidates; **zero supported rankings**.
  The generic `comparison_only_supported_subset_per_tier` status does not
  mean any profitable replacement has been identified.
- At10:30:49Z normal scheduled top-up released one economic-idle owner and
  added one, keeping32 configured owners. It demonstrates a replacement path,
  not measured incremental reward or acceptance of every reward-idle case.

### Selection-rank measurement gap

Of3,164 entry observations,812 have rank1–20,581 above20 and1,771 (55.97%)
have no rank. Of those blanks,570 have stale selector evidence and1,201 have
a recent spare snapshot but their owner is absent from its viable slate.
`_selection_rank_for_market` explicitly returns unknown for a stale/missing
slate or absent owner; existing regressions require it. This is honest missing
**current** rank, not proof the original selection was wrong. Retained owners
need not remain in the newest fresh-entry candidate slate.

September18 market-days also move across ranks:148 top20-only,37 above20-only,
73 mixed and29 missing. Do not credit a mixed market's entire reward to its
best observed rank. Existing outputs cannot support a clean top20 versus
extra-slot causal reward comparison.

## Recommendation / next steps

1. Keep current trading rules. No new execution defect or urgent restart is
   established by Priority3/4. Priority1's prepared recovery repair and
   Priority2 performance follow-up remain separate unfinished work.
2. Reuse existing measurements and later dated earnings to follow completed
   daily periods. Resolve missing reward/program attribution explicitly;
   merely waiting three days cannot satisfy complete-population gates if
   attribution gaps persist. Do not lower evidence gates or drop losers.
3. Recommended measurement-only improvement: persist original selection
   rank/time/scan identity separately from current rank, plus a missing-rank
   reason. This would improve attribution without more API calls or changing
   which markets trade. **Not implemented/authorized by this check.**
4. Before a new ranking/filter proposal, perform horizon-matched fill/exit
   calibration, audit reward-date and manual/mixed-program gaps, then freeze
   a supported model and evaluate later completed periods. Preserve withheld
   results when clean history is unavailable; no forecast-profit promise.

Pre-documentation parity verified Windows/GitHubf3aa2a15, WSL70467880 and loaded
1273aadd. The intentional pending Priority1 source rollout prevents a runtime
fast-forward. Private backup10:31:24Z verified759,201,792 bytes/SHA prefix
b880be903cb2. OfflinePC was not contacted. Review helpers and detailed account
artifacts remain ignored; only this evidence handoff is tracked.
