# Market-quality tracker readout — September 26 (00:34Z)

Operator asked whether the quality tracker could explain breadth and the
2-cent rule. `scripts/market_quality_report.py --db data/commissioning.db`
with all 16 `data/market-quality/*.sqlite3` archives, `--days 7`, output to
`/tmp`: read-only ledger (`mode=ro`), **0 API requests**, ET 2026-09-18..24.
Measurement only; performance remains unproven and no filter is promoted.

## Paid reward by priority grade (size 15, usable rows)

| Grade | Usable/all market-days | Paid | Trading net | Paid net | Net/side-h |
|---|---:|---:|---:|---:|---:|
| S (rank 1-5) | 252/382 | 182.78 | -61.41 | 121.37 | 0.26 |
| A (6-10) | 113/198 | 58.86 | -6.54 | 52.32 | 0.20 |
| B (11-20) | 96/177 | 26.27 | -12.27 | 14.00 | 0.08 |
| C (21-30) | 29/38 | 6.31 | 0.67 | 6.98 | 0.07 |
| D (31+) | 5/5 | 0.60 | 0.14 | 0.74 | 0.09 |

Size 10 shows the same ordering (S 0.30, A 0.21, B 0.07 net/side-h). Top-10
priority carries most realized net; lower ranks add small positive net.

## Tight versus wider books (derived from report JSON rows)

Rows with known paid status and a recorded quote spread (558 without paid
status and 83 without spread excluded). "Tight" means the minimum observed
quote spread on that market-day was one cent (markets were still selected at
the 2-cent fresh gate and later tightened under the one-tick touch rule).

| Cohort | Market-days | Side-h | Paid | Trading | Net | Net/side-h | Fills/side-h |
|---|---:|---:|---:|---:|---:|---:|---:|
| Tightened to 1¢ | 1,097 | 2,802.9 | 891.74 | -205.37 | 686.37 | 0.245 | 2.30 |
| Always ≥2¢ | 197 | 143.1 | 50.77 | -26.75 | 24.02 | 0.168 | 1.63 |

Tight market-days filled more often but still produced higher net per resting
side-hour and nearly all realized net. Confounded, not causal: tight books are
typically more liquid and higher-ranked; the always-wide cohort is small; this
says nothing about markets that are already one cent at discovery (never
selected). It does indicate that retained tight markets are valuable, which
raises the value of preserving already-selected markets across restarts.

## Authenticated liquidity earnings (read-only, 00:4xZ September 26)

One account-wide `GET /v1/incentives/earnings` (`program_type=liquidityProgram`,
`start_date=2026-08-14`, report limiter) plus one targeted
`2026-09-25..26` query. Account-wide rows: manual activity cannot be
excluded, but the account is otherwise bot-dedicated in this era.

- 6,208 rows, 2026-08-14..2026-09-24, **all PAID, $5,142.20**; zero PENDING or
  SKIPPED. 4,795 PAID rows are under $1 (latest 2026-09-24), so sub-$1
  market/day rewards are being paid in practice despite the September 18
  support wording; no $1 filter is justified by payout evidence.
- September 25 rows are not posted yet (targeted query returned 0).
- Daily paid fell: 17th $188.08, 18th $146.69, 19th $201.63, 20th $187.35,
  21st $116.37, 22nd $125.15, 23rd $127.37, 24th $67.51, while paid
  market-days stayed 173-238. Average per market-day fell $0.97 → $0.39;
  share under $0.25 rose 43% → 58%. League moves: WTT $22-59/day through the
  19th then $0 on 21st-23rd; ATP $19-30 on 19th-20th then ~0; modus $0 after
  the 20th; setkameua $31.72 on the 24th versus $50-84 before. ET 2026-09-24
  also contained about seven runner starts (touch/quality/deferred-exit
  releases). Cause is not attributed: venue program availability/pool sizes and
  bot restarts/releases are both plausible and confounded.
- Bot era PAID by league: setkameua $1,735.20 (2,367 market-days, avg $0.73),
  cs2 $726.46 (avg $2.30), wtt $590.05, setkamemd $480.07, itfme $356.40
  (avg $0.38), itfwo $204.48 (avg $0.36).
- September 18-24 reward per tracker resting side-hour: itfme $0.104/h and
  itfwo $0.092/h used 854 side-hours (~25% of measured resting time) versus
  setkameua $0.298/h, wtt $0.453/h, atp $0.461/h, cs2 $0.317/h.

## Per-market paid versus rested (joined offline, no further API calls)

Tracker rested market-days (Sept 18-24 ET, by rest date) joined to earnings by
exact market slug: 1,852 rested market-days; 1,294 have a same-date reward
row. Of the 558 without one, 187 were paid under a different date (the game
date: e.g. rested 09-18, market `...-2026-09-19`, paid dated 09-19), so rest
date and payout date differ for overnight/next-day games. 104 never-paid
markets have game dates 09-25+ (not yet posted). **263 markets / 155.5
side-hours (~4.4% of measured rest) are genuinely unpaid** on posted dates:
itfme 46 (54.4 h, 13% of itfme rest), itfwo 27 (25.7 h), **utr 7 (21.9 h,
100%)**, setkameua 83 (8.5 h, 1%), wta 7 (7.1 h, 37%), **r6 9 (5.6 h,
100%)**.

**UTR and R6 have zero PAID rows in all 6,208 bot-era liquidity earnings**,
including any slug containing `utr`/`r6`. Bot UTR order actions by UTC date:
09-18 25, 09-24 1,629, 09-25 677, 09-26 54 so far; tonight's bound set
includes UTR markets. ET 09-24, the day UTR activity jumped, was the lowest
paid day ($67.51). Causation is not proven and 09-25 UTR rows are not yet
posted, but the venue has never paid this account for UTR/R6 liquidity while
the selector's reward model ranks them. Excluding a league is a business
selection change for the operator to decide; the next check is the 09-25
posting.

Examples: best 09-19 `aec-nhl-tor-mon` paid $46.01 over 3.0 side-h (net
$38.91); worst 09-24 `aec-setkameua-saavih-matvas-2026-09-25` 5.0 side-h,
unpaid so far, trading -$5.47. Paid amount per paid rested market-day: <$0.10
388, $0.10-0.50 422, $0.50-2 354, ≥$2 130.

## Is selection choosing the best markets? Model versus realized (offline)

Markets rested on with posted game dates 09-18..09-24 (1,580 markets, 3,350
side-h), joined per market across all dates. Model = tracker
`placement_model_reward`; paid = PAID earnings for that slug; trade = closed
fill P&L. Overall paid/model 1.59 (model conservative); paid $0.289/side-h,
trading -$254.38, net $0.214/side-h.

Ranking signal is real: market-level Spearman(model $/h, paid $/h) = 0.78
(n=1,060 with >0.5 side-h); model quartiles paid $0.044, $0.140, $0.346,
$0.820 per side-h. Two blind spots: (1) no feedback from actual payouts — R6
model $20.60 / paid $0, UTR $8.51 / $0, modus and pdcdarts ~0.5×; (2) ranking
uses reward, not realized net of trading: net/side-h wta 0.56, wtadb 0.49,
atp 0.46, atpdb 0.41, wtt 0.36, setkameua 0.25, setkamemd 0.26, setkamecz
0.17 versus cs2 0.022 (paid 0.336, trade -26.78), itfme 0.046, itfwo 0.011
(ITF 815 side-h ≈ 24% of rest), dota2 0.030, pdcdarts -0.234, utr -0.047, r6
-0.335. Limits: one week, account-wide earnings, approximate rest hours,
small samples for several leagues, supply constraints (a lower-value market
may still beat an empty slot) and no causal proof.

## Pool crowding and market-maker competition versus payout (offline)

1,060 rested markets (≥0.5 side-h, game dates 09-18..09-24; 1,012 `day_of`,
48 `daily_event`). Pool/target/discount from tracker signatures; crowding =
distinct events/markets observed carrying the same program on that ET day in
`[market-measurement]` logs (observed only, not complete venue membership);
competition fields are per-side maxima/medians recorded at quote placement.
Spearman against paid $/side-h (net in parentheses); quartile paid $/h:

| Factor | Spearman | Q1 → Q4 paid $/h |
|---|---:|---|
| Our share of reward score | +0.62 (+0.31) | 0.072, 0.239, 0.365, 0.674 |
| Competition: total reward score on side | -0.41 (-0.27) | 0.575, 0.330, 0.260, 0.184 |
| Queue ahead of our order | -0.40 (-0.26) | 0.524, 0.360, 0.283, 0.182 |
| Advertised pool $ | +0.21 (+0.15) | 0.182, 0.278, 0.359, 0.531 |
| Target size | -0.22 (-0.16) | no monotonic pattern |
| Discount factor | +0.14 (+0.03) | no monotonic pattern |
| Book depth | +0.08 (0.00) | no pattern |
| Events sharing the program that day | +0.04 (+0.02) | no pattern |
| Pool $ per observed event | -0.02 (0.00) | no pattern |

The advertised program pool is not split across the program's events in
practice: 689/968 paid markets (71%) were paid more than pool/observed-events
(e.g. a setkameua market paid $6.70 = 19% of a $35 pool shared by 298 observed
events). Program crowding therefore does not predict payout; in-market
competition does. The selector's projection already uses order share and total
score, consistent with its 0.78 market-level rank correlation (higher than
share alone, 0.62). No crowding penalty is indicated. Limits: one week,
placement-time snapshots, approximate rest time, account-wide earnings,
correlational.
