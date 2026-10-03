#!/usr/bin/env python3
"""Late-favourite calibration (read-only): replay every recorded live match through the
trader's rules with many settings and rank which values make the most money.

Operator 2026-10-03: "We should have a calibration research for this live process so we
change values which is the best measure for us".

Input: the tracker's change log (data/late-favorite/tracker.db: game state per event,
best bid/ask per market, settlements) and the trader's real trades (trader.db).
For one setting, each finished event is walked in time order; the first moment the
trigger and the price/spread rules hold, it "buys" the favourite exactly as the trader
would (at the ask, or 1 - bid for the other side, plus the taker fee) and holds to the
settlement. One trade per event. Output: data/late-favorite/calibration.md|json.
"""
from __future__ import annotations

import argparse
import itertools
import json
import sqlite3
import sys
from collections import defaultdict
from dataclasses import replace
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from liquidity_bot.late_favorite_trader import (  # noqa: E402
    LateFavoritePolicy, SportRule, almost_over, load_policy, pick_entry, realized_result)

OUT = Path("data/late-favorite")
TRADABLE = ("SPORTS_MARKET_TYPE_MONEYLINE", "SPORTS_MARKET_TYPE_DRAWABLE_OUTCOME")
MIN_PRICES = (0.90, 0.92, 0.93, 0.95, 0.97)
MAX_SPREADS = (0.02, 0.03, 0.05, 0.10)
TRIGGERS = {
    "soccer": [("85'", SportRule(min_minute=85)), ("88'", SportRule(min_minute=88)),
               ("90'", SportRule(min_minute=90))],
    "hockey": [(f"<={m}min", SportRule(periods=("P3", "OT"), max_clock_minutes=m)) for m in (5, 3, 2)],
    "basketball": [(f"<={m}min", SportRule(periods=("Q4", "OT"), max_clock_minutes=m)) for m in (5, 3, 2)],
    "football": [(f"<={m}min", SportRule(periods=("Q4", "OT"), max_clock_minutes=m)) for m in (5, 3, 2)],
    "tennis": [(f"set>={g}", SportRule(kind="sets", units_to_win=2, min_current=g)) for g in (4, 5)],
    "table-tennis": [(f"pts>={p}", SportRule(kind="sets", units_to_win=3, min_current=p)) for p in (6, 8, 10)],
    "esports": [("map point", SportRule(kind="maps", units_to_win=2))],
}


def ts(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def load(db_path: Path):
    c = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    events = {r[0]: {"sport": r[1], "ended": r[2], "last_seen": r[3]} for r in c.execute(
        "SELECT event_slug, sport, ended_at, last_seen FROM events WHERE last_live_at IS NOT NULL")}
    markets = defaultdict(list)
    for m, e, t, st, fee in c.execute(
            "SELECT market_slug, event_slug, market_type, settlement, fee FROM markets "
            f"WHERE market_type IN {TRADABLE}"):
        if e in events:
            markets[e].append({"slug": m, "type": t, "settlement": st, "fee": fee})
    timeline = defaultdict(list)                          # event -> [(at, kind, payload)]
    for e, at, live, ended, period, elapsed, score in c.execute(
            "SELECT event_slug, observed_at, live, ended, period, elapsed, score FROM event_obs"):
        if e in events:
            timeline[e].append((at, 0, (live, ended, period, elapsed, score)))
    owner = {m["slug"]: e for e, ms in markets.items() for m in ms}
    for m, at, status, bid, ask in c.execute("SELECT market_slug, observed_at, status, bid, ask FROM market_obs"):
        e = owner.get(m)
        if e is not None:
            timeline[e].append((at, 1, (m, status, bid, ask)))
    for rows in timeline.values():
        rows.sort(key=lambda r: (r[0], r[1]))
    return events, markets, timeline


def replay(events, markets, timeline, policy: LateFavoritePolicy) -> list[dict]:
    trades = []
    for e, ev in events.items():
        sport = ev["sport"]
        if sport not in policy.rules:
            continue
        settled = {m["slug"]: m for m in markets.get(e, []) if m["settlement"] is not None}
        if not settled:
            continue
        end = ev["ended"] or ev["last_seen"]
        state, quotes = None, {}
        for at, kind, data in timeline.get(e, []):
            if kind == 0:
                state = data
            else:
                m, status, bid, ask = data
                quotes[m] = (status, bid, ask)
            if state is None or not quotes:
                continue
            live, ended, period, elapsed, score = state
            event = {"slug": e, "eventState": {"type": sport, "live": bool(live), "ended": bool(ended),
                                               "period": period, "elapsed": elapsed, "score": score},
                     "markets": [{"slug": m, "sportsMarketTypeV2": settled[m]["type"], "status": q[0],
                                  "feeCoefficient": settled[m]["fee"],
                                  "bestBidQuote": {"value": q[1]}, "bestAskQuote": {"value": q[2]}}
                                 for m, q in quotes.items() if m in settled]}
            cand = pick_entry(event, policy)
            if cand is None:
                continue
            mk = settled[cand.market_slug]
            result = realized_result(cand.side, cand.cost, 1.0, mk["settlement"], cand.fee_coefficient)
            trades.append({"event": e, "sport": sport, "market": cand.market_slug, "cost": cand.cost,
                           "won": result > 0, "pnl": result,
                           "hold_min": max(0.0, (ts(end) - ts(at)).total_seconds() / 60)})
            break
    return trades


def summarize(trades: list[dict], risk: float) -> dict:
    n = len(trades)
    if not n:
        return {"trades": 0}
    pnl_contract = sum(t["pnl"] for t in trades) / n
    per_trade = sum(t["pnl"] * risk / t["cost"] for t in trades) / n     # $ at the trader's size
    holds = sorted(t["hold_min"] for t in trades)
    return {"trades": n, "won": sum(t["won"] for t in trades), "win_rate": sum(t["won"] for t in trades) / n,
            "avg_cost": sum(t["cost"] for t in trades) / n, "pnl_per_contract": pnl_contract,
            "usd_per_trade": per_trade, "usd_total": per_trade * n, "median_hold_min": holds[n // 2],
            "upsets": [t["market"] for t in trades if not t["won"]][:5]}


def real_trades(path: Path) -> dict:
    if not path.exists():
        return {}
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    rows = c.execute("SELECT game_state, result FROM trades WHERE mode='live' AND result IS NOT NULL").fetchall()
    by = defaultdict(lambda: [0, 0, 0.0])
    for game, result in rows:
        sport = (json.loads(game or "{}") or {}).get("type")
        by[sport][0] += 1; by[sport][1] += int(result > 0); by[sport][2] += result
    return {s: {"trades": v[0], "won": v[1], "usd": round(v[2], 2)} for s, v in by.items()}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--min-trades", type=int, default=20, help="sample needed before recommending")
    a = ap.parse_args(argv)
    base = load_policy()
    events, markets, timeline = load(OUT / "tracker.db")
    rows = []
    for sport, variants in TRIGGERS.items():
        for (label, rule), lo, spread in itertools.product(variants, MIN_PRICES, MAX_SPREADS):
            policy = replace(base, min_price=lo, max_price=0.99, max_spread=spread, rules={sport: rule})
            s = summarize(replay(events, markets, timeline, policy), base.risk_per_trade_usd)
            rows.append({"sport": sport, "trigger": label, "min_price": lo, "max_spread": spread, **s})
    current = summarize(replay(events, markets, timeline, base), base.risk_per_trade_usd)
    best = {}
    for sport in TRIGGERS:
        ok = [r for r in rows if r["sport"] == sport and r.get("trades", 0) >= a.min_trades]
        best[sport] = max(ok, key=lambda r: (r["usd_total"], r["usd_per_trade"])) if ok else None
    finished = sum(1 for e in events if any(m["settlement"] is not None for m in markets.get(e, [])))
    out = {"generated_at": datetime.now().astimezone().isoformat(), "finished_events": finished,
           "current_settings": current, "best": best, "real": real_trades(OUT / "trader.db"), "grid": rows}
    (OUT / "calibration.json").write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")

    def fmt(r):
        if not r or not r.get("trades"):
            return "| - | - | - | - | - | - |"
        return (f"| {r['trades']} | {r['win_rate']:.0%} | {r['avg_cost']:.3f} | {r['usd_per_trade']:+.2f} | "
                f"{r['usd_total']:+.2f} | {r['median_hold_min']:.0f} |")
    lines = ["# Late-favourite calibration (replay of every recorded live match)", "",
             f"Generated {out['generated_at']}; {finished} finished events with results. "
             f"$ figures at ${base.risk_per_trade_usd:g} per trade. Recommendations need >= {a.min_trades} trades.", "",
             "## Current settings (replayed)", "",
             "| Trades | Won | Avg cost | $/trade | $ total | Median hold (min) |", "|---|---|---|---|---|---|",
             fmt(current), "", "## Best setting per sport", "",
             "| Sport | Trigger | Min price | Max spread | Trades | Won | Avg cost | $/trade | $ total | Hold (min) |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for sport, r in best.items():
        lines.append(f"| {sport} | " + ("not enough data | | " + fmt(None) if r is None else
                     f"{r['trigger']} | {r['min_price']:.2f} | {r['max_spread']:.2f} " + fmt(r)))
    lines += ["", "## Real trades so far", "", "| Sport | Trades | Won | $ |", "|---|---|---|---|"]
    lines += [f"| {s} | {v['trades']} | {v['won']} | {v['usd']:+.2f} |" for s, v in out["real"].items()]
    lines += ["", "## Full grid (sports with trades)", "",
              "| Sport | Trigger | Min price | Max spread | Trades | Won | Avg cost | $/trade | $ total | Hold (min) |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    lines += [f"| {r['sport']} | {r['trigger']} | {r['min_price']:.2f} | {r['max_spread']:.2f} " + fmt(r)
              for r in rows if r.get("trades")]
    (OUT / "calibration.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Late-favourite calibration: {finished} finished events; current settings {fmt(current)}; "
          f"wrote {OUT / 'calibration.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
