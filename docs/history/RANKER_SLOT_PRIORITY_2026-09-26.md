# Ranker slot priority — September 26, 2026

**Operator request:** "what we want to do with this ranker get slot priority … after we get all that rewarded market we just make sure they get priority slots", then "they still have to go to the checks we are doing", then "Build it".

**State:** built and tested; **not loaded**. It loads at the next Command 1 (trader source changed). Nothing waits for a window boundary: size, stops, caps and risk policy are unchanged.

## What it does

1. **Ranker** (`scripts/market_ranker.py`, cron every 30 min) now also writes `data/market-rankings/priority.json` atomically:
   - its best markets in rank order, capped at 50;
   - only positive corrected net value per hour;
   - never-paid leagues are left out;
   - markets the bot can never quote (not moneyline, excluded league, minimum quantity above our size) are left out. A narrow spread keeps its place because it can widen.
2. **Bot** (`src/liquidity_bot/ranker_priority.py`, `market_selector.py`):
   - **Book reads:** ranked markets are read first, in ranker order, ahead of the normal head and the rotating tail. The tail keeps at least half of the 100-book budget.
   - **Slots:** among markets that pass every live gate, ranked ones sort first in ranker order; unranked ones keep the old order after them. The same order applies to fast refills (`revalidate_viable`).
   - **Checks unchanged:** viable markets still sort ahead of non-viable ones, so a ranked market that fails any check (spread, 2-cent rule, entry quality, price floor, reward period, timing, exclusions, risk or capacity) gets no slot.
   - **Held markets are not evicted.** Only free slots and existing replacement paths use the order.

## Fallback

If the file is missing, malformed, from an unknown schema, older than 90 minutes or dated more than 5 minutes in the future, the bot uses no priority. That is exactly the previous ordering (pinned by a test). Loading can never raise into selection.

## Evidence in logs

- `[ranker-priority] status=loaded|stale|missing|invalid|error …` is printed only when the state changes.
- `[selection-scan] … ranker_priority=<markets in file> ranker_priority_viable=<of them passing every gate>`.

## Preview on real data (ranking 22:35Z)

- 150 scored; **39 markets** would be on the list.
- Top: MLB CHC–BOS $6.22/h, several Setka UA/MD, Dota 2 and ITF markets.
- Most of the top 15 were blocked at that moment by the live spread or entry-quality rules. They are read first but get no slot until their book passes.

## Tests

- `tests/test_ranker_priority.py`: fresh, stale, future, invalid, schema, cap, log-on-change and never-raises.
- `tests/test_market_selector.py::RankerSlotPriorityTests`:
  - ranked markets are read first and sorted first;
  - a ranked market failing a live gate gets no slot;
  - no priority, a failing priority source or an unknown slug reproduces the old order;
  - refill revalidation uses the same order.
- `tests/test_market_ranker.py`: priority-file contents and atomic write.
- Full suite **2,351 PASS**.

## Rollback

Delete `data/market-rankings/priority.json` (the bot falls back at its next scan) or stop the ranker cron (it falls back within 90 minutes); neither needs a restart. A code rollback needs a Command 1.
