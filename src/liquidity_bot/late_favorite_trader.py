"""Late-favourite trading rules (BC-2026-10-03-B): buy the near-certain favourite when a
match is almost over and hold it to settlement.

Operator 2026-10-03: "I want to do it now with 10 usd risk per trade with max 50 open
positions", "50 usd a day is stop", "90-99 is also ok if there is profit on 99", rules
on clear game clocks first (soccer 85'+, hockey P3 / basketball Q4 / football Q4 near the
end). Pure logic only; the process is ``scripts/late_favorite_trader.py``.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from decimal import ROUND_DOWN, Decimal
from pathlib import Path

DEFAULT_POLICY_PATH = Path(__file__).resolve().parents[2] / "config" / "late_favorite_policy.json"
TRADABLE_TYPES = frozenset({"SPORTS_MARKET_TYPE_MONEYLINE", "SPORTS_MARKET_TYPE_DRAWABLE_OUTCOME"})


@dataclass(frozen=True)
class SportRule:
    periods: tuple[str, ...] = ()          # period labels that count as "last" (clock sports)
    max_clock_minutes: float | None = None  # countdown clock at or below this
    min_minute: int | None = None          # soccer-style match minute at or above this
    # Score-based sports (operator: "we are doing all sports market"):
    # kind "sets": tennis / table tennis score "6-4, 3-5:30-15" or "11-8, 9-4";
    #   almost over = the leader needs only the current unit and has >= min_current in it.
    # kind "maps": esports maps score "1-0"; almost over = the leader needs one more map.
    kind: str | None = None
    units_to_win: int | None = None
    min_current: int | None = None
    # Lead needed in the current unit (operator, after the 8-5 table-tennis comeback:
    # "Ok maybe 6+ points").
    min_lead: int | None = None
    # Per-sport floor on the middle price (esports "map point" is often mid-map: 97c).
    min_price: float | None = None
    # Soccer (operator: "we should actually bet on winners and not the 3 options its
    # too risky"): only "Yes, <team> wins" - no draw markets and no "No" sides.
    winner_only: bool = False
    # Baseball (kind "innings", operator "yes add basesball"): period "Top 9th" /
    # "Bot 10th" / "Mid 9th" / "End 8th"; almost over = inning >= min_inning and the
    # leader is up >= min_lead runs.
    min_inning: int | None = None


@dataclass(frozen=True)
class LateFavoritePolicy:
    enabled: bool = False
    live_orders: bool = False
    risk_per_trade_usd: float = 10.0
    max_open_positions: int = 50
    daily_loss_stop_usd: float = 50.0
    min_price: float = 0.90
    max_price: float = 0.99
    poll_seconds: float = 10.0
    # Operator 2026-10-03 (CS2 trade paid 98c on a 2-6c spread while the mid was
    # ~96c): only trade tight books so the price paid matches the market's view.
    max_spread: float = 0.02
    rules: dict = field(default_factory=dict)
    # Combo Builder legs (operator: "maybe for this one halfway?"): looser "halfway"
    # stage rules used only for the shared candidates file, never for the trader.
    combo_rules: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool or type(self.live_orders) is not bool:
            raise ValueError("enabled/live_orders must be boolean")
        for name, value, low, high in (
                ("risk_per_trade_usd", self.risk_per_trade_usd, 1, 100),
                ("daily_loss_stop_usd", self.daily_loss_stop_usd, 1, 1000),
                ("min_price", self.min_price, 0.5, 0.99),
                ("max_price", self.max_price, 0.5, 0.99),
                ("poll_seconds", self.poll_seconds, 5, 120),
                ("max_spread", self.max_spread, 0.001, 0.10)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
                raise ValueError(f"late-favourite {name} must be in [{low}, {high}]")
        if not isinstance(self.max_open_positions, int) or not 1 <= self.max_open_positions <= 200:
            raise ValueError("max_open_positions must be an integer in [1, 200]")
        if self.min_price > self.max_price:
            raise ValueError("min_price must not exceed max_price")
        for sport, rule in self.rules.items():
            if not isinstance(rule, SportRule):
                raise ValueError(f"rule for {sport} is invalid")
        for sport, rule in self.combo_rules.items():
            if not isinstance(rule, SportRule):
                raise ValueError(f"combo rule for {sport} is invalid")


def _parse_rules(section: dict) -> dict:
    rules = {}
    for sport, r in (section or {}).items():
        unknown = set(r) - {"periods", "max_clock_minutes", "min_minute",
                            "kind", "units_to_win", "min_current", "min_lead", "min_price",
                            "winner_only", "min_inning"}
        if unknown:
            raise ValueError(f"unknown rule fields for {sport}: {sorted(unknown)}")
        if r.get("kind") not in (None, "sets", "maps", "innings"):
            raise ValueError(f"unknown rule kind for {sport}")
        rules[sport] = SportRule(periods=tuple(r.get("periods", ())),
                                 max_clock_minutes=r.get("max_clock_minutes"),
                                 min_minute=r.get("min_minute"), kind=r.get("kind"),
                                 units_to_win=r.get("units_to_win"), min_current=r.get("min_current"),
                                 min_lead=r.get("min_lead"), min_price=r.get("min_price"),
                                 winner_only=bool(r.get("winner_only", False)),
                                 min_inning=r.get("min_inning"))
    return rules


def load_policy(path: Path = DEFAULT_POLICY_PATH) -> LateFavoritePolicy:
    raw = json.loads(path.read_text(encoding="utf-8"))
    rules = _parse_rules(raw.pop("rules", None))
    combo_rules = _parse_rules(raw.pop("combo_rules", None))
    unknown = set(raw) - set(LateFavoritePolicy.__dataclass_fields__)
    if unknown:
        raise ValueError(f"unknown late-favourite fields: {sorted(unknown)}")
    return LateFavoritePolicy(rules=rules, combo_rules=combo_rules, **raw)


def match_minute(*texts) -> int | None:
    """Soccer minute from "85'", "90+3'" (-> 90) etc."""
    for text in texts:
        m = re.match(r"\s*(\d{1,3})(?:\+\d+)?'", str(text or ""))
        if m:
            return int(m.group(1))
    return None


def clock_minutes(text) -> float | None:
    """Countdown clock "05:37" / "5:09" / "09:00" -> minutes left."""
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", str(text or ""))
    return int(m.group(1)) + int(m.group(2)) / 60 if m else None


def _pairs(text) -> list[tuple[int, int]] | None:
    out = []
    for part in str(text or "").split(","):
        part = part.strip()
        if not part:
            continue
        m = re.fullmatch(r"(\d{1,3})\s*-\s*(\d{1,3})", part)
        if not m:
            return None
        out.append((int(m.group(1)), int(m.group(2))))
    return out or None


def sets_almost_over(score, units_to_win: int, min_current: int, min_lead: int = 1) -> bool:
    """Leader has units_to_win-1 completed units, >= min_current and a lead of >= min_lead
    in the current one."""
    units = _pairs(str(score or "").split(":")[0])
    if not units:
        return False
    done, (a, b) = units[:-1], units[-1]
    won = (sum(x > y for x, y in done), sum(y > x for x, y in done))
    lead = max(1, min_lead)
    return any(won[i] == units_to_win - 1 and (a, b)[i] >= min_current
               and (a, b)[i] - (a, b)[1 - i] >= lead for i in (0, 1))


def innings_almost_over(period, score, min_inning: int, min_lead: int) -> bool:
    m = re.match(r"\s*(?:top|bot|bottom|mid|middle|end)\s+(\d{1,2})(?:st|nd|rd|th)?",
                 str(period or ""), re.IGNORECASE)
    units = _pairs(score)
    if not m or not units or len(units) != 1:
        return False
    return int(m.group(1)) >= min_inning and abs(units[0][0] - units[0][1]) >= max(1, min_lead)


def maps_almost_over(score, units_to_win: int) -> bool:
    units = _pairs(score)
    return bool(units) and len(units) == 1 and max(units[0]) == units_to_win - 1


def almost_over(sport: str | None, period: str | None, elapsed: str | None,
                rules: dict, score: str | None = None) -> bool:
    rule = rules.get(sport or "")
    if rule is None:
        return False
    if rule.kind == "sets":
        return sets_almost_over(score, rule.units_to_win or 2, rule.min_current or 0,
                                rule.min_lead or 1)
    if rule.kind == "maps":
        return maps_almost_over(score, rule.units_to_win or 2)
    if rule.kind == "innings":
        return innings_almost_over(period, score, rule.min_inning or 9, rule.min_lead or 2)
    if rule.min_minute is not None:
        minute = match_minute(period, elapsed)
        return minute is not None and minute >= rule.min_minute
    if rule.periods and (period or "") not in rule.periods:
        return False
    if rule.max_clock_minutes is not None:
        left = clock_minutes(elapsed)
        return left is not None and left <= rule.max_clock_minutes
    return bool(rule.periods)


@dataclass(frozen=True)
class Candidate:
    event_slug: str
    market_slug: str
    side: str            # "long" (buy first outcome at the ask) or "short" (other outcome)
    price: float         # long-frame order price: the ask for long, the bid for short
    cost: float          # what one contract costs us
    fee_coefficient: float | None


def _quote(value):
    if isinstance(value, dict):
        value = value.get("value")
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if 0 < x < 1 else None


def pick_entry(event: dict, policy: LateFavoritePolicy) -> Candidate | None:
    """The one favourite to buy in this event now, or None."""
    state = event.get("eventState") if isinstance(event.get("eventState"), dict) else {}
    get = lambda k: state.get(k) if state.get(k) is not None else event.get(k)
    if get("live") is not True or get("ended") is True:
        return None
    if not almost_over(state.get("type"), get("period"), get("elapsed"), policy.rules,
                       score=get("score")):
        return None
    rule = policy.rules.get(state.get("type") or "")
    if rule is not None and rule.kind is None:
        # Clock/minute sports (soccer, hockey, basketball, football): a tied game in the
        # final minutes is never decided (Vaasan Sport 2-2 with 1:29 left, 16:34Z).
        units = _pairs(get("score"))
        if not units or len(units) != 1 or units[0][0] == units[0][1]:
            return None
    best = None
    for m in event.get("markets") or []:
        if (not isinstance(m, dict) or m.get("sportsMarketTypeV2") not in TRADABLE_TYPES
                or m.get("status") != "MARKET_STATUS_OPEN" or m.get("closed") is True):
            continue
        winner_only = rule is not None and rule.winner_only
        if winner_only and str(m.get("slug", "")).endswith("-draw"):
            continue
        bid, ask = _quote(m.get("bestBidQuote")), _quote(m.get("bestAskQuote"))
        if bid is None or ask is None or ask - bid > policy.max_spread + 1e-9:
            continue                     # one-sided or wide book: the price is not reliable
        try:
            fee = float(m["feeCoefficient"]) if m.get("feeCoefficient") is not None else None
        except (TypeError, ValueError):
            fee = None
        # The trigger is the MIDDLE of the book (the market's view); the price limit is
        # what we pay. A thin 89/99 book has a 94c middle and is skipped at 95c.
        mid = (bid + ask) / 2
        floor = max(policy.min_price, (rule.min_price or 0) if rule is not None else 0)
        options = []
        if floor <= mid and ask <= policy.max_price:
            options.append(("long", ask, ask))
        if not winner_only and floor <= 1 - mid and 1 - bid <= policy.max_price:
            options.append(("short", bid, round(1 - bid, 6)))
        for side, price, cost in options:
            # Only buy when a win still clears the taker fee.
            if (1 - cost) - (fee if fee is not None else 0.07) * cost * (1 - cost) <= 0:
                continue
            cand = Candidate(event["slug"], m["slug"], side, price, cost, fee)
            if best is None or cost < best.cost:     # the cheapest favourite pays most
                best = cand
    return best


def contracts_for(risk_usd: float, cost: float, minimum_qty: float) -> Decimal:
    """Whole multiples of the venue minimum so that cost x qty <= risk."""
    step = Decimal(str(minimum_qty))
    raw = Decimal(str(risk_usd)) / Decimal(str(cost))
    return (raw / step).to_integral_value(rounding=ROUND_DOWN) * step


def realized_result(side: str, cost: float, qty: float, settlement: float,
                    fee_coefficient: float | None) -> float:
    won = settlement >= 0.99 if side == "long" else settlement <= 0.01
    fee = (fee_coefficient if fee_coefficient is not None else 0.07) * cost * (1 - cost) * qty
    return (qty * (1 - cost) if won else -qty * cost) - fee


def daily_stop_hit(realized_today: float, policy: LateFavoritePolicy) -> bool:
    return realized_today <= -policy.daily_loss_stop_usd + 1e-9 and math.isfinite(realized_today)


def combo_candidates(events: list[dict], policy: LateFavoritePolicy, floor: float = 0.90) -> list[dict]:
    """Late-stage favourites for the Combo Builder (shared scan, BC-2026-10-03-C).

    Same rules as the trader (stage, ties, spread, per-sport floors, winners-only
    soccer) with the price floor lowered to ``floor``; one leg per event.
    """
    from dataclasses import replace
    relaxed = replace(policy, min_price=min(policy.min_price, floor), max_price=0.99,
                      rules=policy.combo_rules or policy.rules)
    out = []
    for event in events:
        cand = pick_entry(event, relaxed)
        if cand is None:
            continue
        state = event.get("eventState") if isinstance(event.get("eventState"), dict) else {}
        market = next((m for m in event.get("markets") or [] if m.get("slug") == cand.market_slug), {})
        bid, ask = _quote(market.get("bestBidQuote")), _quote(market.get("bestAskQuote"))
        mid = None if bid is None or ask is None else (bid + ask) / 2
        out.append({
            "event": cand.event_slug, "market": cand.market_slug, "side": cand.side,
            "cost": cand.cost, "bid": bid, "ask": ask,
            "fav_mid": None if mid is None else round(mid if cand.side == "long" else 1 - mid, 4),
            "sport": state.get("type"), "period": state.get("period") or event.get("period"),
            "score": state.get("score") or event.get("score"),
        })
    return out
