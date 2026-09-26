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
