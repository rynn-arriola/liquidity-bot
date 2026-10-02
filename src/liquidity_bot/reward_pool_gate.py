"""Entry pause while the reward pools on our markets are too low to pay.

Operator decision 2026-10-02 (docs/BUSINESS_CHANGES.md, BC-2026-10-02-A):
pause NEW entries while the Reward Pool Tracker's "ours" score is at or below
5/10 ("5 under means not profitable") and resume when it is above 5. The score
comes from ``data/reward-pool-tracker/latest-score.json``, written hourly by
``scripts/reward_pool_tracker.py``; this module never calls the network.

The gate only removes new-risk authority through the runner's existing entry
pause, so cancels, reducers and position exits keep running. A missing,
unreadable or stale score means "no pool evidence", which falls back to normal
trading (logged): a tracker outage must not silently stop the bot for days.
``enabled: false`` restores the previous behaviour exactly.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_REWARD_POOL_GATE_POLICY_PATH = (
    Path(__file__).resolve().parents[2] / "config" / "reward_pool_gate_policy.json")
REWARD_POOL_PAUSE_REASON = "reward_pool_low"


@dataclass(frozen=True)
class RewardPoolGatePolicy:
    enabled: bool = False
    pause_at_or_below: float = 5.0
    max_score_age_seconds: float = 10800.0
    check_interval_seconds: float = 60.0
    score_path: str = "data/reward-pool-tracker/latest-score.json"

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise ValueError("reward-pool gate enabled must be boolean")
        for name, value, low, high in (
                ("pause_at_or_below", self.pause_at_or_below, 1.0, 10.0),
                ("max_score_age_seconds", self.max_score_age_seconds, 600.0, 86400.0),
                ("check_interval_seconds", self.check_interval_seconds, 1.0, 3600.0)):
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or not low <= value <= high):
                raise ValueError(
                    f"reward-pool gate {name} must be a number in [{low}, {high}]")
        if not isinstance(self.score_path, str) or not self.score_path:
            raise ValueError("reward-pool gate score_path must be a path")


def load_reward_pool_gate_policy(
    path: Path = DEFAULT_REWARD_POOL_GATE_POLICY_PATH,
) -> RewardPoolGatePolicy:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("reward-pool gate policy must be an object")
    allowed = set(RewardPoolGatePolicy.__dataclass_fields__)
    unknown = set(raw) - allowed
    if unknown:
        raise ValueError(f"unknown reward-pool gate fields: {sorted(unknown)}")
    return RewardPoolGatePolicy(**raw)


@dataclass(frozen=True)
class RewardPoolReading:
    paused: bool
    score: float | None
    detail: str


def read_reward_pool_score(
    path: Path, policy: RewardPoolGatePolicy, now: datetime,
) -> RewardPoolReading:
    """Classify the tracker's latest "ours" score; never raises."""

    if not policy.enabled:
        return RewardPoolReading(False, None, "disabled")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        at = datetime.fromisoformat(str(raw["at"]))
        score = raw["ours"]["score"]
    except FileNotFoundError:
        return RewardPoolReading(False, None, "score_missing")
    except (OSError, ValueError, TypeError, KeyError):
        return RewardPoolReading(False, None, "score_unreadable")
    if (at.tzinfo is None or isinstance(score, bool)
            or not isinstance(score, (int, float)) or not math.isfinite(score)):
        return RewardPoolReading(False, None, "score_unreadable")
    age = now - at.astimezone(timezone.utc)
    if age > timedelta(seconds=policy.max_score_age_seconds):
        return RewardPoolReading(
            False, float(score), f"score_stale_{int(age.total_seconds() // 60)}m")
    if age < -timedelta(minutes=10):
        return RewardPoolReading(False, float(score), "score_from_future")
    if score <= policy.pause_at_or_below:
        return RewardPoolReading(True, float(score), "pool_low")
    return RewardPoolReading(False, float(score), "pool_ok")


class RewardPoolGate:
    """Throttled file reader used by the live runner's entry-pause refresh."""

    def __init__(self, policy: RewardPoolGatePolicy, *, root: Path | None = None):
        self.policy = policy
        path = Path(policy.score_path)
        self.path = path if path.is_absolute() or root is None else root / path
        self.reading = RewardPoolReading(False, None, "not_checked")
        self._next_check_at: datetime | None = None

    @property
    def paused(self) -> bool:
        return self.reading.paused

    def refresh(self, now: datetime) -> RewardPoolReading | None:
        """Re-read when due; return the new reading only when it changed."""

        if self._next_check_at is not None and now < self._next_check_at:
            return None
        self._next_check_at = now + timedelta(
            seconds=self.policy.check_interval_seconds)
        previous = self.reading
        self.reading = read_reward_pool_score(self.path, self.policy, now)
        if _change_key(self.reading) == _change_key(previous):
            return None
        return self.reading


def _change_key(reading: RewardPoolReading) -> tuple:
    # A stale reading's age grows every check; only its state is a change.
    kind = "score_stale" if reading.detail.startswith("score_stale") else reading.detail
    return reading.paused, reading.score, kind
