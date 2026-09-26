"""Conservative reward calibration using attributable PAID rows only."""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class DailyCalibration:
    date: str
    market_slug: str
    status: str
    reward: float
    score_seconds: float
    attributable: bool


@dataclass(frozen=True)
class CalibrationResult:
    ready: bool
    days: int
    rows: int
    mean_reward_per_score_second: float
    lower_reward_per_score_second: float

    def forecast(self, score_seconds: float) -> float:
        if not self.ready or score_seconds <= 0:
            return 0.0
        return self.lower_reward_per_score_second * score_seconds


class RewardCalibrator:
    def __init__(self, *, minimum_days: int = 3, z_score: float = 1.96):
        if minimum_days <= 0 or z_score < 0:
            raise ValueError("calibration settings are invalid")
        self.minimum_days = minimum_days
        self.z_score = z_score

    def fit(self, rows: Iterable[DailyCalibration]) -> CalibrationResult:
        valid = [row for row in rows
                 if row.status.upper() == "PAID" and row.attributable
                 and row.reward >= 0 and row.score_seconds > 0]
        days = len({row.date for row in valid})
        rates = [row.reward / row.score_seconds for row in valid]
        if not rates:
            return CalibrationResult(False, 0, 0, 0.0, 0.0)
        mean = statistics.fmean(rates)
        if len(rates) == 1:
            lower = 0.0
        else:
            standard_error = statistics.stdev(rates) / math.sqrt(len(rates))
            lower = max(0.0, mean - self.z_score * standard_error)
        ready = days >= self.minimum_days and lower > 0
        return CalibrationResult(ready, days, len(valid), mean, lower)
