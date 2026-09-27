# Reward Pulse and the September 24–26 payout drop — September 27, 2026

**Operator requests:** "check pending rewards and see why past days are low check individual markets", then "how can we have a tracker … good, average or low? Maybe 5 levels … name it", "show me first market we rested and not get paid", "Build it".

## Why payouts fell (MEASURED, read-only)

**Daily totals (paid + pending):**

| Market day | Paid + pending | Status |
|---|---:|---|
| Sep 19–20 | $187–202 | paid |
| Sep 21–23 | $116–127 | paid |
| Sep 24 | $70 | paid |
| Sep 25 | $32 | pending |
| Sep 26 | $25–26 | pending |

- Each day's amount appears about a day later and barely changes after that (Sep 24: $68 → $70), so the pending amounts are close to final.
- **Pools were cut about in half across nearly every league between Sep 24 and 26.** Prior week → Sep 26:

  | League | Pool before | Pool Sep 26 |
  |---|---:|---:|
  | Setka UA/CZ/MD | $35 | $15 |
  | ITF | $30 | $15 |
  | CS2 | $100 | $50 |
  | WTA | $200 | $100 |
  | Dota 2 / LoL | $100 | $50 |
  | ATP doubles | $50 | $25 |

- **The crowd grew at the same time.** In Setka UA the size ahead of our quote (median) went 61 → 101 → 210 → 393 → 769 (Sep 23–27), and our share of the reward score fell from 6–10% to 1–2%.
- **Setka UA paid** $83.52, $33.36, $10.19 and $2.78 (Sep 23–26) on 165–193 order-hours a day. The bot's own predicted reward fell the same way, so the market shrank; we were not underpaid.
- **Rested but unpaid, by market day** (matched by market, so next-day games are not miscounted):

  | Market day | Unpaid markets / rested | Share of our hours unpaid |
  |---|---|---:|
  | Sep 21 | 36/201 | 2% |
  | Sep 22 | 25/212 | 3% |
  | Sep 23 | 63/282 | 3% |
  | Sep 24 | 49/255 | 14% |
  | Sep 25 | 74/232 | 10% |
  | Sep 26 | 84/191 | 31% (may still fill in) |

  - UTR: 53/53 unpaid (already excluded).
  - ITF Men: 52/172 unpaid.
- **Unpaid despite a real share** (watch list): `aec-cs2-is-btf-2026-09-26` (8.2 h, 50%), `aec-itfme-rudmol-sebdom-2026-09-26` (7.2 h, 26%), `aec-itfme-piefel-karibr-2026-09-24` (8.3 h, 11%).

## Reward Pulse (built; restart-free)

`scripts/reward_pulse.py` runs daily at 05:10 via cron. It reads the ledger and the newest archived reward snapshot, makes no API call and has no trading consumer.

- **Measure:** paid + pending $ per resting order-hour. Each market is graded on its own reward day (the slug date).
- **Normal:** the same league's three prior market-day weeks weighted 50/25/25 (the ranker weights). Leagues with under 20 order-hours use the overall normal; a league that never paid grades Dead.
- **Levels:** 🔥 Hot ≥1.5× · 🟢 Good 1.1–1.5× · 🟡 Average 0.75–1.1× · 🟠 Low 0.4–0.75× · 🔴 Dead <0.4×.
- **Per league, the drivers:** median pool, size ahead and our share, against the prior 7 market days.
- **Lists:** best and weakest markets, plus "unpaid despite a real share" (2+ hours, 10%+ share, $0).
- **Schedule:** grades yesterday and the two days before and rewrites them, because payouts land late.
- **Output:** `data/reward-pulse/<day>.md|json` and `latest.md`.
- **First grades:**

  | Market day | Level | vs normal |
  |---|---|---:|
  | Sep 20 | Good | 1.28× |
  | Sep 21 | Average | 0.92× |
  | Sep 22 | Average | 0.76× |
  | Sep 23 | Average | 0.78× |
  | Sep 24 | Average | 0.77× |
  | Sep 25 | Dead | 0.23× |
  | Sep 26 | Dead | 0.16× |

- **Tests:** `tests/test_reward_pulse.py` (6).

## Discord reward table (operator: "add it on discord reward table summary")

- The hourly reward post (`scripts/reward_table.py --post-discord`, image via `scripts/reward_image.py`) now has a third section, **Reward Pulse**.
- **Row format:** `Date · Level (Hot/Good/Avg/Low/Dead, colour-coded) · vs normal · $/order-hour`.
- **Source:** the local `data/reward-pulse/<day>.json` grades. It makes no extra venue call; payouts per market come from the reward snapshot the bot already archives every 15 minutes.
- **No grades:** without them the post keeps its two-table layout. A day without a grade shows `--` (today, or yesterday before the 05:10 run).
- **First rows:** 9/21–9/24 Avg (0.92×, 0.76×, 0.78×, 0.77×), 9/25 Dead 0.23×, 9/26 Dead 0.16×.
- **Full per-market grades** for each day stay in `data/reward-pulse/<day>.md`.
- **Deployed restart-free** in `b172e6e55`; 2,358 tests PASS.
