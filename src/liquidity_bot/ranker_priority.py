"""Slot priority from the rewarded-market ranker (operator, 2026-09-26).

``scripts/market_ranker.py`` scores every rewarded market starting within six
hours on calibrated reward plus league trading history and writes its best
markets to a small file. The selector uses that file only to ORDER work: a
ranked market's book is read first and it is offered a free slot first. Every
live gate (spread, reward period, event timing, exclusions, risk, capacity)
still applies unchanged, so a ranked market that fails a check is skipped.

A missing, stale, future-dated or malformed file yields no priority, which
leaves the established selector ordering exactly as before. Loading never
raises into selection.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

RANKER_PRIORITY_PATH = Path("data/market-rankings/priority.json")
# The ranker runs every 30 minutes and a run can take about 15 minutes; two
# missed runs are tolerated before the bot falls back to its own ordering.
MAX_PRIORITY_AGE_SECONDS = 90 * 60
MAX_FUTURE_SKEW_SECONDS = 5 * 60
# Half the default 100-book budget: the rotating tail keeps discovering
# markets the ranker has not scored yet.
MAX_PRIORITY_MARKETS = 50
PRIORITY_SCHEMA = 1


def _parse_generated_at(value) -> datetime:
    stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("generated_at must be timezone-aware")
    return stamp.astimezone(timezone.utc)


def parse_priority(payload: dict) -> tuple[datetime, dict[str, int]]:
    """Return (generated_at, {slug: rank}) from the ranker's priority file."""

    if payload.get("schema") != PRIORITY_SCHEMA:
        raise ValueError("unknown priority schema")
    generated = _parse_generated_at(payload["generated_at"])
    ranks: dict[str, int] = {}
    for row in payload["markets"]:
        slug = str(row["slug"]).strip()
        if slug and slug not in ranks:
            ranks[slug] = len(ranks) + 1
        if len(ranks) >= MAX_PRIORITY_MARKETS:
            break
    return generated, ranks


class RankerPriority:
    """Read the ranker priority file; cache by file identity; log changes."""

    def __init__(
        self,
        path: Path = RANKER_PRIORITY_PATH,
        *,
        max_age_seconds: float = MAX_PRIORITY_AGE_SECONDS,
        log: Callable[[str], None] | None = None,
    ):
        self.path = Path(path)
        self.max_age_seconds = max_age_seconds
        self._log = log or (lambda line: print(line, flush=True))
        self._file_key: tuple[int, int] | None = None
        self._parsed: tuple[datetime, dict[str, int]] | None = None
        self._last_report: tuple | None = None

    def ranks(self, now: datetime) -> dict[str, int]:
        try:
            return self._ranks(now)
        except Exception as exc:  # never let evidence loading stop selection
            self._report(now, ("error", type(exc).__name__), f"status=error error={type(exc).__name__}")
            return {}

    def _ranks(self, now: datetime) -> dict[str, int]:
        try:
            stat = self.path.stat()
        except FileNotFoundError:
            self._file_key, self._parsed = None, None
            self._report(now, ("missing",), "status=missing")
            return {}
        key = (stat.st_mtime_ns, stat.st_size)
        if key != self._file_key:
            self._file_key, self._parsed = key, None
            try:
                self._parsed = parse_priority(
                    json.loads(self.path.read_text(encoding="utf-8")))
            except (OSError, ValueError, KeyError, TypeError) as exc:
                self._report(now, ("invalid", key), f"status=invalid error={type(exc).__name__}")
                return {}
        if self._parsed is None:
            return {}
        generated, ranks = self._parsed
        age = (now - generated).total_seconds()
        if age > self.max_age_seconds or age < -MAX_FUTURE_SKEW_SECONDS:
            self._report(now, ("stale", generated),
                         f"status=stale generated_at={generated.isoformat()} age_seconds={age:.0f}")
            return {}
        self._report(now, ("loaded", generated),
                     f"status=loaded markets={len(ranks)} generated_at={generated.isoformat()} "
                     f"age_seconds={age:.0f}")
        return dict(ranks)

    def _report(self, now: datetime, state: tuple, detail: str) -> None:
        if state == self._last_report:
            return
        self._last_report = state
        self._log(f"[ranker-priority] at={now.isoformat()} {detail} path={self.path}")
