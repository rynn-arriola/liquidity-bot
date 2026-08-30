#!/usr/bin/env python3
"""Which reward PERIODS does each league currently offer?

Answers "why is the bot quoting so little?" when the funnel says the drain is
`no applicable period`. A pregame executor may only quote `day_of` -- invariant
39, because that is the one period with a provable per-market clock -- so a
league that carries only `live` is invisible to this bot however good its books
look.

Written 2026-08-20, when order flow collapsed and the cause turned out to be
the venue moving every league this bot earns in to live-only:

    ONLY [live]                    [day_of, live]
      setkameua  x22                 lol      x10
      itfwo      x10                 modus     x6
      setkawoua   x8                 atp       x5
      wtt         x7                 cs2       x4
      itfme       x6                 valorant  x1
      setkamecz   x6                 wta       x1
      setkamemd   x5

FINDINGS 6h measures the first seven as 93% of every reward dollar this account
has earned, while atp and mlb -- still carrying day_of -- paid one cent each.

**Re-run this before concluding anything is permanent.** One reading cannot
tell a program change from a transient. If `day_of` returns to setka and itf,
the collapse was a blip and the bot recovers on its own.

Costs one catalog read plus batched incentives (~3 venue calls). Cheap enough
to run daily; still shares the 4 req/s governor, so do not loop it.
"""

from __future__ import annotations

import asyncio
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "src")
sys.path.insert(0, "/root/liquidity-bot/src")

from liquidity_bot.adapters import (  # noqa: E402
    AsyncReadOnlyGateway, shared_api_limiter)
from liquidity_bot.commissioning import CommissioningConfig  # noqa: E402
from liquidity_bot.probe import client_from_environment  # noqa: E402

LEAGUE = re.compile(r"^aec-([a-z0-9]+)-")

# Where reward has actually come from, measured in FINDINGS 6h. Flagged in the
# output so a league going live-only is read with its earning history attached
# rather than as one more row.
EARNING_SHARE = {
    "setkameua": "29.0%", "setkamemd": "21.3%", "itfme": "18.2%",
    "itfwo": "12.2%", "wtt": "7.4%", "valorant": "3.5%",
    "setkamecz": "3.2%", "cs2": "3.2%", "setkawoua": "1.9%",
    "atp": "$0.01", "mlb": "$0.01",
}


async def main() -> int:
    gateway = AsyncReadOnlyGateway(
        client_from_environment(), limiter=shared_api_limiter(),
        default_traffic_class="background")
    policy = CommissioningConfig.diagnostic_defaults().reward_selection_policy()
    now = datetime.now(timezone.utc)
    window = now + timedelta(seconds=policy.selection_horizon_seconds)

    catalog = await gateway.list_sports_markets()
    in_window = [
        m for m in catalog
        if m.active and not m.closed and m.game_start
        and now < m.game_start <= window and m.is_supported_moneyline
        and m.tick_size is not None and m.minimum_trade_qty is not None]

    print(f"at {now.isoformat()[:19]}Z   horizon "
          f"{policy.selection_horizon_seconds / 3600:.0f}h")
    print(f"in-horizon moneylines: {len(in_window)}")
    if not in_window:
        print("nothing in the horizon: a schedule trough, not a program change")
        return 0

    incentives = await gateway.fetch_incentives(sorted(m.slug for m in in_window))
    by_league: dict[str, Counter] = defaultdict(Counter)
    for slug, incentive in incentives.items():
        match = LEAGUE.match(slug)
        league = match.group(1) if match else "?"
        periods = ",".join(sorted({str(p.period) for p in incentive.periods}))
        by_league[league][periods] += 1

    quotable = 0
    print()
    print(f"{'league':14} {'earned':>8}  periods offered")
    for league in sorted(by_league, key=lambda k: -sum(by_league[k].values())):
        combos = by_league[league]
        share = EARNING_SHARE.get(league, "")
        rendered = "   ".join(f"[{k}] x{v}" for k, v in combos.most_common())
        quotable += sum(v for k, v in combos.items() if "day_of" in k)
        print(f"  {league:12} {share:>8}  {rendered}")

    print()
    print(f"markets offering day_of (quotable by this executor): "
          f"{quotable} of {len(incentives)}")
    if quotable == 0:
        print("NONE. A pregame executor has no addressable universe right now.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
