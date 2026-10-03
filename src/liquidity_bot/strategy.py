"""Which games the bot farms: pregame, live (in-game), or both (BC-2026-10-03-A).

Operator 2026-10-03: "lets create a version of the normal incentive farming of
a live version", "dont remove pre-game we should be able to switch it on or
off", "we should optimize it for live trading", "Just bid on best price like
pregame", "We will try to make gains on trades too".

Pregame behaviour is untouched when ``live_enabled`` is false; with no
``config/strategy_policy.json`` the bot is the pregame-only bot it always was.

A market is LIVE once its game has positively started (durable event-start
evidence, or the effective clock has passed). Live markets use the rules in
``LivePolicy`` instead of the pregame kickoff deadline:

- entry needs fresh (< 30 s) evidence that the game is in play and not ended,
  an active ``live`` reward period, and a price inside ``min_price``..1-min_price;
- entries are cancelled at once while the touch is jumpy (``jumpy_touch_changes_30s``
  changes in 30 s) or the price leaves the band; they come back once it calms;
- entries rest at the touch like pregame, as GTD orders of ``entry_lifetime_seconds``;
- a fill is exited by the same passive one-tick-inside reducer as pregame, and
  crossed after ``max_inventory_age_seconds`` with no early-exit deferral (a
  live game has no later deadline to defer to); a game that ends or a market
  that closes forces the exit at once.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

DEFAULT_STRATEGY_POLICY_PATH = (
    Path(__file__).resolve().parents[2] / "config" / "strategy_policy.json")
LIVE_JUMPY_BLOCKER = "live_market_jumpy"
LIVE_PRICE_BLOCKER = "live_price_out_of_band"


@dataclass(frozen=True)
class LivePolicy:
    min_price: float = 0.15
    # Fresh-selection spread for in-play markets (pregame keeps 2c). Operator
    # 2026-10-03: "Ok we can do 1c". A one-tick book quotes both touches.
    min_entry_spread: float = 0.02
    entry_lifetime_seconds: float = 60.0
    max_inventory_age_seconds: float = 120.0
    jumpy_touch_changes_30s: int = 3
    # A live reward period has no published end; its pool is projected over
    # this assumed game length (reward projection only, never an exit clock).
    assumed_period_seconds: float = 10800.0

    def __post_init__(self) -> None:
        for name, value, low, high in (
                ("min_price", self.min_price, 0.01, 0.49),
                ("min_entry_spread", self.min_entry_spread, 0.01, 0.05),
                ("entry_lifetime_seconds", self.entry_lifetime_seconds, 60.0, 600.0),
                ("max_inventory_age_seconds", self.max_inventory_age_seconds, 30.0, 900.0),
                ("assumed_period_seconds", self.assumed_period_seconds, 1800.0, 21600.0)):
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or not low <= value <= high):
                raise ValueError(f"live {name} must be a number in [{low}, {high}]")
        if (isinstance(self.jumpy_touch_changes_30s, bool)
                or not isinstance(self.jumpy_touch_changes_30s, int)
                or not 1 <= self.jumpy_touch_changes_30s <= 50):
            raise ValueError("live jumpy_touch_changes_30s must be an integer in [1, 50]")


@dataclass(frozen=True)
class StrategyPolicy:
    pregame_enabled: bool = True
    live_enabled: bool = False
    live: LivePolicy = field(default_factory=LivePolicy)

    def __post_init__(self) -> None:
        if type(self.pregame_enabled) is not bool or type(self.live_enabled) is not bool:
            raise ValueError("strategy switches must be boolean")

    @staticmethod
    def game_started(market, now: datetime) -> bool:
        return (market.game_start is not None
                and (getattr(market, "event_started_at", None) is not None
                     or market.game_start <= now))

    def is_live(self, market, now: datetime) -> bool:
        """This market is farmed under the live rules."""
        return self.live_enabled and self.game_started(market, now)

    def entry_phase(self, market, now: datetime) -> str | None:
        """"pregame", "live", or None when no enabled strategy may enter."""
        if self.game_started(market, now):
            return "live" if self.live_enabled else None
        return "pregame" if self.pregame_enabled else None

    def allows_entry(self, market, now: datetime) -> bool:
        """The event-evidence half of entry permission for this market."""
        if not self.live_enabled:
            # Exactly the pregame-only bot's rule (clock gates live elsewhere).
            return market.event_allows_entry(now)
        phase = self.entry_phase(market, now)
        if phase == "pregame":
            return market.event_allows_entry(now)
        if phase == "live":
            return market.event_allows_live_entry(now)
        return False

    @property
    def label(self) -> str:
        return "+".join(n for n, on in (("pregame", self.pregame_enabled),
                                        ("live", self.live_enabled)) if on) or "none"


def load_strategy_policy(path: Path = DEFAULT_STRATEGY_POLICY_PATH) -> StrategyPolicy:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return StrategyPolicy()            # no file = the pregame-only bot
    raw = json.loads(text)
    if not isinstance(raw, dict):
        raise ValueError("strategy policy must be an object")
    unknown = set(raw) - {"pregame_enabled", "live_enabled", "live"}
    if unknown:
        raise ValueError(f"unknown strategy policy fields: {sorted(unknown)}")
    live = raw.get("live", {})
    if not isinstance(live, dict):
        raise ValueError("strategy live settings must be an object")
    unknown = set(live) - set(LivePolicy.__dataclass_fields__)
    if unknown:
        raise ValueError(f"unknown live strategy fields: {sorted(unknown)}")
    return StrategyPolicy(
        pregame_enabled=raw.get("pregame_enabled", True),
        live_enabled=raw.get("live_enabled", False),
        live=LivePolicy(**live))
