"""Late-favorite tracker: record every live sports market to test "buy the ~90% favourite
when the match is almost over and hold to settlement" (read-only, no orders).

Operator 2026-10-03: "I want something that secures winning trade we stay at the trade
until its almost over and we buy that trade like when its 90 percent winning and there is
not much time left", "This dont need to be reward market", "I think we should observe our
data first then check how that gonna work on polymarket side", "make sure its all the live
sports market so we get alot of data".

Source: the public sports event listing, which carries each event's live state (period,
clock, score, per-period scores, live/ended) and every market's best bid/ask for its long
(first) outcome. Final results come from the market settlement endpoint. Only changes are
stored. Buying the long side costs the ask; buying the other side costs 1 - bid.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime


def _num(value) -> float | None:
    if isinstance(value, dict):
        value = value.get("value")
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if 0 <= x <= 1 else None


@dataclass(frozen=True)
class EventState:
    event_slug: str
    sport: str | None
    series: str | None
    title: str | None
    start_time: str | None
    live: bool
    ended: bool
    period: str | None
    elapsed: str | None
    score: str | None
    period_scores: str | None      # compact JSON

    def key(self) -> tuple:
        return (self.live, self.ended, self.period, self.elapsed, self.score, self.period_scores)


@dataclass(frozen=True)
class MarketQuote:
    market_slug: str
    event_slug: str
    market_type: str | None        # SPORTS_MARKET_TYPE_*
    sports_market_type: str | None
    outcomes: str | None
    question: str | None
    fee_coefficient: float | None
    tick: float | None
    status: str | None
    best_bid: float | None
    best_ask: float | None

    def key(self) -> tuple:
        return (self.status, self.best_bid, self.best_ask)


def parse_event(event: dict) -> tuple[EventState | None, list[MarketQuote]]:
    """One listed event -> its live state and market quotes (never raises)."""
    if not isinstance(event, dict) or not isinstance(event.get("slug"), str):
        return None, []
    state = event.get("eventState") if isinstance(event.get("eventState"), dict) else {}

    def pick(name):
        value = state.get(name)
        return event.get(name) if value is None else value

    scores = state.get("periodScores")
    ev = EventState(
        event_slug=event["slug"], sport=state.get("type"), series=event.get("seriesSlug"),
        title=event.get("title"), start_time=event.get("startTime"),
        live=pick("live") is True, ended=pick("ended") is True,
        period=pick("period"), elapsed=pick("elapsed"), score=pick("score"),
        period_scores=(json.dumps(
            [[p.get("label"), [s.get("score") for s in p.get("scores", []) if isinstance(s, dict)]]
             for p in scores if isinstance(p, dict)], separators=(",", ":"))
            if isinstance(scores, list) else None))
    quotes = []
    for m in event.get("markets") or []:
        if not isinstance(m, dict) or not isinstance(m.get("slug"), str):
            continue
        try:
            fee = float(m["feeCoefficient"]) if m.get("feeCoefficient") is not None else None
        except (TypeError, ValueError):
            fee = None
        try:
            tick = float(m["orderPriceMinTickSize"]) if m.get("orderPriceMinTickSize") is not None else None
        except (TypeError, ValueError):
            tick = None
        quotes.append(MarketQuote(
            market_slug=m["slug"], event_slug=ev.event_slug,
            market_type=m.get("sportsMarketTypeV2"), sports_market_type=m.get("sportsMarketType"),
            outcomes=m.get("outcomes") if isinstance(m.get("outcomes"), str) else None,
            question=m.get("question"), fee_coefficient=fee, tick=tick, status=m.get("status"),
            best_bid=_num(m.get("bestBidQuote")), best_ask=_num(m.get("bestAskQuote"))))
    return ev, quotes


def favourite_entry(bid: float | None, ask: float | None, threshold: float):
    """(side, price) to buy the favourite at or above ``threshold``, else None.

    "long" = the first outcome (bought at the ask); "short" = the other (1 - bid).
    """
    if ask is not None and threshold <= ask < 1:
        return "long", ask
    if bid is not None and 0 < bid <= 1 - threshold:
        return "short", round(1 - bid, 6)
    return None


def trade_result(side: str, price: float, settlement: float, fee_coefficient: float | None) -> float:
    """Per-contract result of buying at ``price`` (taker) and holding to settlement."""
    won = settlement >= 0.99 if side == "long" else settlement <= 0.01
    fee = (fee_coefficient if fee_coefficient is not None else 0.06) * price * (1 - price)
    return (1 - price if won else -price) - fee


def summarize(rows: list[dict], thresholds=(0.85, 0.90, 0.95)) -> list[dict]:
    """rows: per settled market, its chronological observations
    {market_slug, sport, market_type, settlement, fee, obs: [(at, period, bid, ask, minutes_before_end)]}.

    For each market and threshold, the first observation at or above the threshold in each
    stage bucket counts once. Stage buckets: by period label, and by minutes before the
    market's last live observation (hindsight; for reference only)."""
    out: dict[tuple, dict] = {}

    def add(key, side, price, settlement, fee):
        cell = out.setdefault(key, {"trades": 0, "won": 0, "pnl": 0.0, "price": 0.0})
        r = trade_result(side, price, settlement, fee)
        cell["trades"] += 1
        cell["won"] += int(settlement >= 0.99 if side == "long" else settlement <= 0.01)
        cell["pnl"] += r
        cell["price"] += price

    for m in rows:
        for thr in thresholds:
            seen_period, seen_late = set(), set()
            for at, period, bid, ask, mins_left in m["obs"]:
                entry = favourite_entry(bid, ask, thr)
                if entry is None:
                    continue
                side, price = entry
                if period not in seen_period:
                    seen_period.add(period)
                    add((m["sport"], m["market_type"], thr, "period", str(period)),
                        side, price, m["settlement"], m["fee"])
                late = ("<=5" if mins_left <= 5 else "<=10" if mins_left <= 10
                        else "<=20" if mins_left <= 20 else ">20")
                if late not in seen_late:
                    seen_late.add(late)
                    add((m["sport"], m["market_type"], thr, "minutes_before_end", late),
                        side, price, m["settlement"], m["fee"])
    result = []
    for (sport, mtype, thr, kind, bucket), c in sorted(out.items(), key=lambda kv: [str(x) for x in kv[0]]):
        result.append({"sport": sport, "market_type": mtype, "threshold": thr, "stage_kind": kind,
                       "stage": bucket, "trades": c["trades"], "won": c["won"],
                       "win_rate": c["won"] / c["trades"], "avg_price": c["price"] / c["trades"],
                       "pnl_per_contract": c["pnl"] / c["trades"]})
    return result
