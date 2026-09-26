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
