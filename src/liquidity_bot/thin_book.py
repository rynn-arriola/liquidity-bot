"""Entry-only skip for thin one-tick books in high-forced-exit leagues.

Operator decision 2026-09-27 (docs/BUSINESS_CHANGES.md, BC-2026-09-27-A):
positions entered on a one-tick spread with little size ahead in these leagues
ended in the 15-minute taker exit 55-80% of the time (Sep 17-27), and the
replay showed exit timing cannot fix it (docs/history/TRADING_LOSS_TESTS_2026-09-27.md).

Only a NEW entry quote is skipped. A side already resting is never cancelled
by this rule, and reducers/exits never consult it. ``enabled: false`` restores
the previous behaviour exactly.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

from .fill_hazard import league_from_slug

DEFAULT_THIN_BOOK_POLICY_PATH = (
    Path(__file__).resolve().parents[2] / "config" / "thin_book_policy.json")
THIN_BOOK_BLOCKER = "thin_book_entry_skip"


@dataclass(frozen=True)
class ThinBookPolicy:
    enabled: bool = False
    max_spread: float = 0.01
    min_queue_ahead: float = 50.0
    leagues: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise ValueError("thin-book enabled must be boolean")
        for name, value, low, high in (("max_spread", self.max_spread, 0.0, 0.10),
                                       ("min_queue_ahead", self.min_queue_ahead, 0.0, 100000.0)):
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or not low < value <= high):
                raise ValueError(f"thin-book {name} must be a number in ({low}, {high}]")
        if not all(isinstance(x, str) and x == x.strip().lower() and x for x in self.leagues):
            raise ValueError("thin-book leagues must be lower-case slug codes")

    def blocker(self, *, slug: str, spread: float, queue_ahead: float,
                already_resting: bool) -> str | None:
        """Skip a new entry quote on a thin one-tick book in a listed league."""
        if not self.enabled or already_resting:
            return None
        if (league_from_slug(slug) or "") not in self.leagues:
            return None
        if spread <= self.max_spread + 1e-9 and queue_ahead < self.min_queue_ahead:
            return THIN_BOOK_BLOCKER
        return None


def load_thin_book_policy(path: Path = DEFAULT_THIN_BOOK_POLICY_PATH) -> ThinBookPolicy:
    """Strict startup validation; a missing file means the rule is off."""
    path = Path(path)
    if not path.exists():
        return ThinBookPolicy()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("thin-book policy cannot be read") from exc
    if (not isinstance(raw, dict)
            or set(raw) != {"enabled", "max_spread", "min_queue_ahead", "leagues"}
            or not isinstance(raw["leagues"], list)):
        raise ValueError("invalid thin-book policy")
    return ThinBookPolicy(raw["enabled"], raw["max_spread"], raw["min_queue_ahead"],
                          frozenset(raw["leagues"]))
