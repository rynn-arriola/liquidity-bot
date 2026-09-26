"""Restart-scoped league exclusions for automatic reward-market selection.

Operator-owned business filter. An empty list (the shipped default) changes
nothing. A listed league is refused at the selector's metadata-only gate, so
its markets are never sent to incentive or book reads and never bound. It never
touches exits, reducers, retained positions or any order already working.

Motivating evidence (2026-09-26 readout): UTR and R6 markets were ranked by the
reward model yet had zero PAID liquidity earnings across the whole bot era.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

DEFAULT_SELECTION_EXCLUSIONS_PATH = (
    Path(__file__).resolve().parents[2] / "config" / "selection_exclusions.json")

_LEAGUE = re.compile(r"[a-z0-9]{1,24}")


@dataclass(frozen=True)
class SelectionExclusions:
    excluded_leagues: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not isinstance(self.excluded_leagues, frozenset) or not all(
                isinstance(league, str) and _LEAGUE.fullmatch(league)
                for league in self.excluded_leagues):
            raise ValueError(
                "excluded leagues must be lowercase slug league tokens")


def load_selection_exclusions(
    path: Path = DEFAULT_SELECTION_EXCLUSIONS_PATH,
) -> SelectionExclusions:
    """Strict loader: a malformed file refuses startup instead of guessing."""

    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("selection exclusions cannot be read") from exc
    if (not isinstance(raw, dict) or set(raw) != {"excluded_leagues"}
            or not isinstance(raw["excluded_leagues"], list)):
        raise ValueError("invalid selection exclusions fields")
    leagues = raw["excluded_leagues"]
    if len(set(leagues)) != len(leagues):
        raise ValueError("selection exclusions repeat a league")
    return SelectionExclusions(frozenset(leagues))
