"""Conservative first-fill hazard calibrated from paid maker-lab markets.

The calibration is intentionally small and explicit.  It contains only the
2026-08-10 maker-lab order lifetimes for markets that later produced a PAID
liquidity-reward row.  Exposure is censored at the first fill.  The model uses
the upper end of a 95% Wilson interval, so sparse groups increase estimated
risk rather than creating false confidence.

This is a ranking and quote-economics input, not a claim that fills are
independent or stationary.  Unknown groups fall back to the global estimate.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import sqrt


CALIBRATION_ID = "maker_lab_paid_markets_2026_08_10"
CALIBRATION_INTERVAL_SECONDS = 30.0


@dataclass(frozen=True)
class HazardCell:
    fills: int
    exposure_seconds: float
    orders: int

    def __post_init__(self) -> None:
        if self.fills < 0 or self.orders < 0 or self.exposure_seconds <= 0:
            raise ValueError("hazard calibration values are invalid")
        if self.fills > self.exposure_seconds / CALIBRATION_INTERVAL_SECONDS:
            raise ValueError("fills cannot exceed 30-second exposure units")

    def upper_probability(self, *, z: float = 1.96) -> float:
        """Return the Wilson upper bound per 30 seconds of exposure."""

        trials = self.exposure_seconds / CALIBRATION_INTERVAL_SECONDS
        observed = self.fills / trials
        denominator = 1.0 + z * z / trials
        centre = observed + z * z / (2.0 * trials)
        radius = z * sqrt(
            observed * (1.0 - observed) / trials
            + z * z / (4.0 * trials * trials)
        )
        return min(1.0, (centre + radius) / denominator)


@dataclass(frozen=True)
class FillHazardEstimate:
    probability: float
    global_probability: float
    driver: str
    horizon_seconds: float
    calibration_id: str = CALIBRATION_ID


GLOBAL_CELL = HazardCell(114, 372_259.527, 301)

LEAGUE_CELLS = {
    "cs2": HazardCell(0, 1_976.469, 4),
    "itfme": HazardCell(35, 172_764.461, 116),
    "itfwo": HazardCell(24, 103_287.139, 80),
    "setkamecz": HazardCell(2, 6_380.478, 5),
    "setkamemd": HazardCell(10, 18_630.637, 22),
    "setkameua": HazardCell(20, 26_290.366, 31),
    "valorant": HazardCell(4, 6_007.348, 12),
    "wtt": HazardCell(19, 36_922.629, 31),
}

PRICE_CELLS = {
    "05_20": HazardCell(10, 45_403.816, 24),
    "20_40": HazardCell(19, 57_892.988, 66),
    "40_60": HazardCell(32, 88_727.199, 73),
    "60_80": HazardCell(26, 94_027.395, 69),
    "80_95": HazardCell(27, 86_208.128, 69),
}

QUEUE_CELLS = {
    "under_5": HazardCell(18, 27_481.514, 54),
    "5_25": HazardCell(18, 35_138.753, 39),
    "25_100": HazardCell(30, 96_758.985, 85),
    "100_500": HazardCell(26, 164_683.844, 71),
    "500_plus": HazardCell(22, 48_196.430, 52),
}

SIDE_CELLS = {
    "bid": HazardCell(57, 188_960.196, 144),
    "offer": HazardCell(57, 183_299.331, 157),
}

SPREAD_CELLS = {
    "under_2c": HazardCell(6, 35_485.282, 8),
    "2_4c": HazardCell(58, 153_962.956, 136),
    "4_7c": HazardCell(21, 49_797.046, 55),
    "7_12c": HazardCell(15, 39_820.030, 30),
    "12_25c": HazardCell(9, 30_248.780, 38),
    "25c_plus": HazardCell(5, 62_945.433, 34),
}

TIME_TO_EVENT_CELLS = {
    "under_15m": HazardCell(21, 53_321.061, 36),
    "15_30m": HazardCell(39, 97_497.167, 116),
    "30_60m": HazardCell(30, 69_014.857, 62),
    "60_120m": HazardCell(6, 29_296.967, 38),
    "120m_plus": HazardCell(18, 123_129.474, 49),
}


HISTORICAL_REWARD_YIELD_PER_CONTRACT_HOUR = {
    "cs2": (0.2131, 2),
    "setkamecz": (0.0914, 2),
    "valorant": (0.0599, 3),
    "wtt": (0.0480, 11),
    "setkamemd": (0.0429, 7),
    "setkameua": (0.0422, 11),
    "itfwo": (0.0248, 22),
    "itfme": (0.0217, 28),
}
GLOBAL_REWARD_YIELD_PER_CONTRACT_HOUR = 0.0304
REWARD_PRIOR_MARKETS = 10


def league_from_slug(slug: str) -> str | None:
    parts = str(slug).strip().lower().split("-")
    return parts[1] if len(parts) > 2 else None


def historical_reward_yield_prior(slug: str) -> float:
    """Return a shrunk Monday reward-yield prior for ranking only."""

    measured = HISTORICAL_REWARD_YIELD_PER_CONTRACT_HOUR.get(
        league_from_slug(slug) or "")
    if measured is None:
        return GLOBAL_REWARD_YIELD_PER_CONTRACT_HOUR
    value, markets = measured
    return (
        value * markets
        + GLOBAL_REWARD_YIELD_PER_CONTRACT_HOUR * REWARD_PRIOR_MARKETS
    ) / (markets + REWARD_PRIOR_MARKETS)


def _horizon_probability(per_interval: float, horizon_seconds: float) -> float:
    if horizon_seconds <= 0:
        raise ValueError("fill-hazard horizon must be positive")
    intervals = horizon_seconds / CALIBRATION_INTERVAL_SECONDS
    return min(1.0, 1.0 - (1.0 - per_interval) ** intervals)


def _price_bin(price: float) -> str | None:
    if .05 <= price < .20:
        return "05_20"
    if .20 <= price < .40:
        return "20_40"
    if .40 <= price < .60:
        return "40_60"
    if .60 <= price < .80:
        return "60_80"
    if .80 <= price <= .95:
        return "80_95"
    return None


def _queue_bin(queue_ahead: float) -> str:
    if queue_ahead < 5:
        return "under_5"
    if queue_ahead < 25:
        return "5_25"
    if queue_ahead < 100:
        return "25_100"
    if queue_ahead < 500:
        return "100_500"
    return "500_plus"


def _spread_bin(spread: float) -> str:
    if spread < .02:
        return "under_2c"
    if spread < .04:
        return "2_4c"
    if spread < .07:
        return "4_7c"
    if spread < .12:
        return "7_12c"
    if spread < .25:
        return "12_25c"
    return "25c_plus"


def _time_to_event_bin(seconds_to_event: float) -> str:
    if seconds_to_event < 15 * 60:
        return "under_15m"
    if seconds_to_event < 30 * 60:
        return "15_30m"
    if seconds_to_event < 60 * 60:
        return "30_60m"
    if seconds_to_event < 120 * 60:
        return "60_120m"
    return "120m_plus"


class MondayFillHazardModel:
    """Estimate a conservative first-fill probability over a short horizon."""

    def estimate(
        self,
        *,
        slug: str,
        side: str,
        price: float,
        spread: float,
        queue_ahead: float,
        seconds_to_event: float,
        horizon_seconds: float,
    ) -> FillHazardEstimate:
        side = str(side).strip().lower()
        if side not in {"bid", "offer"}:
            raise ValueError("fill-hazard side must be bid or offer")
        if not 0 < price < 1 or spread < 0 or queue_ahead < 0:
            raise ValueError("fill-hazard market inputs are invalid")

        candidates: list[tuple[str, HazardCell]] = [("global", GLOBAL_CELL)]
        league = league_from_slug(slug)
        if league in LEAGUE_CELLS:
            candidates.append((f"league:{league}", LEAGUE_CELLS[league]))
        price_bin = _price_bin(price)
        if price_bin is not None:
            candidates.append((f"price:{price_bin}", PRICE_CELLS[price_bin]))
        queue_bin = _queue_bin(queue_ahead)
        spread_bin = _spread_bin(spread)
        time_bin = _time_to_event_bin(seconds_to_event)
        candidates.extend((
            (f"queue:{queue_bin}", QUEUE_CELLS[queue_bin]),
            (f"side:{side}", SIDE_CELLS[side]),
            (f"spread:{spread_bin}", SPREAD_CELLS[spread_bin]),
            (f"time_to_event:{time_bin}", TIME_TO_EVENT_CELLS[time_bin]),
        ))

        probabilities = [
            (name, _horizon_probability(
                cell.upper_probability(), horizon_seconds))
            for name, cell in candidates
        ]
        driver, probability = max(probabilities, key=lambda item: item[1])
        global_probability = _horizon_probability(
            GLOBAL_CELL.upper_probability(), horizon_seconds)
        return FillHazardEstimate(
            probability=probability,
            global_probability=global_probability,
            driver=driver,
            horizon_seconds=horizon_seconds,
        )
