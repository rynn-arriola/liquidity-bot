"""Trade Scorer entry filter: a second way to run the bot (BC-2026-10-02-B).

Operator decision 2026-10-02: while reward pools are low, stop normal reward
farming and rest only the entries the Trade Scorer rates at least 60% likely to
exit at a profit (trading gains only; rewards still accrue on what rests).
Normal reward farming is NOT removed: ``mode: "reward"`` in
``config/trade_score_policy.json`` restores it exactly.

The model is the daily ``latest-model.json`` written by the separate
trade-scorer project (``--export-model``). The scoring below must stay
identical to ``trade_scorer/features.py`` + ``model.py`` (``raw_numeric``,
``FeatureSpec.row``, ``predict_proba``); the parity test pins it.

The score is an entry-only filter on top of every existing check: a side
already resting is never cancelled by it and reducers/exits never consult it.
With no usable model the scorer mode places no new entries (fail closed).
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

DEFAULT_TRADE_SCORE_POLICY_PATH = (
    Path(__file__).resolve().parents[2] / "config" / "trade_score_policy.json")
TRADE_SCORE_BELOW_MINIMUM = "trade_score_below_minimum"
TRADE_SCORE_MODEL_UNAVAILABLE = "trade_score_model_unavailable"
MODES = ("reward", "scorer")
ET = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class TradeScorePolicy:
    mode: str = "reward"
    min_win_probability: float = 0.60
    model_path: str = "data/trade-scorer/latest-model.json"
    max_model_age_hours: float = 72.0
    min_model_test_auc: float = 0.70
    check_interval_seconds: float = 600.0

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise ValueError(f"trade-score mode must be one of {MODES}")
        for name, value, low, high in (
                ("min_win_probability", self.min_win_probability, 0.0, 1.0),
                ("max_model_age_hours", self.max_model_age_hours, 1.0, 720.0),
                ("min_model_test_auc", self.min_model_test_auc, 0.5, 1.0),
                ("check_interval_seconds", self.check_interval_seconds, 1.0, 86400.0)):
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or not low <= value <= high):
                raise ValueError(
                    f"trade-score {name} must be a number in [{low}, {high}]")
        if not isinstance(self.model_path, str) or not self.model_path:
            raise ValueError("trade-score model_path must be a path")

    @property
    def scorer_mode(self) -> bool:
        return self.mode == "scorer"


def load_trade_score_policy(
    path: Path = DEFAULT_TRADE_SCORE_POLICY_PATH,
) -> TradeScorePolicy:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return TradeScorePolicy()          # no switch file = normal reward farming
    raw = json.loads(text)
    if not isinstance(raw, dict):
        raise ValueError("trade-score policy must be an object")
    unknown = set(raw) - set(TradeScorePolicy.__dataclass_fields__)
    if unknown:
        raise ValueError(f"unknown trade-score policy fields: {sorted(unknown)}")
    return TradeScorePolicy(**raw)


# --- identical to trade_scorer/features.py ------------------------------------

def _num(value) -> float | None:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _log(value) -> float | None:
    x = _num(value)
    return None if x is None else math.log1p(max(x, 0.0))


def raw_numeric(t: dict) -> dict[str, float | None]:
    price = _num(t.get("quote_price"))
    spread = _num(t.get("spread"))
    side = t.get("side")
    own = _num(t.get("bid_depth") if side == "bid" else t.get("offer_depth"))
    other = _num(t.get("offer_depth") if side == "bid" else t.get("bid_depth"))
    seconds = _num(t.get("seconds_to_event"))
    return {
        "price": price,
        "price_extremeness": None if price is None else abs(price - 0.5),
        "spread_cents": None if spread is None else spread * 100,
        "log_minutes_to_kickoff": None if seconds is None else math.log1p(max(seconds, 0) / 60),
        "log_size_ahead": _log(t.get("observed_queue_ahead")),
        "log_depth_at_quote": _log(t.get("depth_at_quote")),
        "log_depth_better": _log(t.get("depth_better_than_quote")),
        "depth_imbalance": (None if own is None or other is None
                            else math.log((own + 1) / (other + 1))),
        "entry_quality": _num(t.get("retained_value_ratio")),
        "model_fill_probability": _num(t.get("fill_probability")),
        "touch_moves_30s": _num(t.get("touch_changes_30s")),
        "touch_moves_120s": _num(t.get("touch_changes_120s")),
        "log_trade_volume_30s": _log(t.get("trade_volume_30s")),
        "log_trade_volume_120s": _log(t.get("trade_volume_120s")),
        "log_seconds_since_touch_move": _log(t.get("seconds_since_touch_change")),
        "ticks_from_best": _num(t.get("ticks_from_best")),
        "order_size": _num(t.get("quantity")),
        "stressed_exit_pnl": _num(t.get("stressed_exit_pnl")),
    }


def hour_band(observed_at: datetime) -> str:
    h = observed_at.astimezone(ET).hour
    return ("12am-6am", "6am-12pm", "12pm-6pm", "6pm-12am")[h // 6]


def league_of(slug: str) -> str:
    parts = slug.split("-")
    return parts[1] if len(parts) > 2 else "other"


@dataclass(frozen=True)
class TradeScoreModel:
    trained_at: datetime
    trades: int
    test_auc: float
    numeric: tuple[str, ...]
    means: dict
    stds: dict
    leagues: tuple[str, ...]
    sides: tuple[str, ...]
    hours: tuple[str, ...]
    coefficients: tuple[float, ...]

    @classmethod
    def from_json(cls, d: dict) -> "TradeScoreModel":
        if d.get("schema") != 1:
            raise ValueError("unsupported trade-score model schema")
        f = d["features"]
        model = cls(
            trained_at=datetime.fromisoformat(str(d["trained_at"])),
            trades=int(d["trades"]),
            test_auc=float(d["test_metrics"]["auc_win"]),
            numeric=tuple(f["numeric"]), means=dict(f["means"]), stds=dict(f["stds"]),
            leagues=tuple(f["leagues"]), sides=tuple(f["sides"]), hours=tuple(f["hours"]),
            coefficients=tuple(float(b) for b in d["win_coefficients"]))
        if model.trained_at.tzinfo is None:
            raise ValueError("model trained_at must be timezone-aware")
        width = 1 + len(model.numeric) + len(model.leagues) + len(model.sides) + len(model.hours)
        if len(model.coefficients) != width:
            raise ValueError("model coefficients do not match its features")
        known = set(raw_numeric({}))
        for name in model.numeric:
            if name not in known:
                raise ValueError(f"model uses unknown trait {name}")
            if not float(model.stds[name]) > 0 or not math.isfinite(float(model.means[name])):
                raise ValueError(f"model standardization for {name} is invalid")
        if not all(math.isfinite(b) for b in model.coefficients):
            raise ValueError("model coefficients must be finite")
        return model

    @property
    def label(self) -> str:
        return self.trained_at.astimezone(timezone.utc).strftime("%Y%m%dT%H%MZ")

    def row(self, traits: dict, observed_at: datetime) -> list[float]:
        raw = raw_numeric(traits)
        x = [1.0]
        for name in self.numeric:
            v = raw.get(name)
            x.append(0.0 if v is None else (v - self.means[name]) / self.stds[name])
        league = traits.get("league")
        x += [1.0 if league == lg else 0.0 for lg in self.leagues]
        x += [1.0 if traits.get("side") == s else 0.0 for s in self.sides]
        band = hour_band(observed_at)
        x += [1.0 if band == h else 0.0 for h in self.hours]
        return x

    def win_probability(self, traits: dict, observed_at: datetime) -> float:
        z = sum(b * v for b, v in zip(self.coefficients, self.row(traits, observed_at)))
        if z >= 0:
            return 1.0 / (1.0 + math.exp(-z))
        e = math.exp(z)
        return e / (1.0 + e)


class TradeScorer:
    """Model holder for the live runner: throttled reload, fail-closed reads."""

    def __init__(self, policy: TradeScorePolicy, *, root: Path | None = None):
        self.policy = policy
        path = Path(policy.model_path)
        self.path = path if path.is_absolute() or root is None else root / path
        self.model: TradeScoreModel | None = None
        self.detail = "not_loaded"
        self._loaded_mtime: float | None = None
        self._next_check_at: datetime | None = None

    def refresh(self, now: datetime) -> str | None:
        """Reload when due and changed; return a log line when the state changed."""

        if self._next_check_at is not None and now < self._next_check_at:
            return None
        self._next_check_at = now + timedelta(seconds=self.policy.check_interval_seconds)
        before = (self.model.label if self.model else None, self.detail)
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            mtime = None
        if mtime is None:
            if self.model is None:
                self.detail = "model_missing"
        elif mtime != self._loaded_mtime:
            self._loaded_mtime = mtime
            try:
                candidate = TradeScoreModel.from_json(
                    json.loads(self.path.read_text(encoding="utf-8")))
                if candidate.test_auc < self.policy.min_model_test_auc:
                    raise ValueError(f"test AUC {candidate.test_auc:.3f} below "
                                     f"{self.policy.min_model_test_auc:g}")
                self.model = candidate
                self.detail = "ok"
            except (OSError, ValueError, TypeError, KeyError) as exc:
                # Keep the last good model; a bad retrain must not change trading.
                self.detail = f"rejected:{type(exc).__name__}:{exc}"[:160]
        if self.model is not None:
            age = now - self.model.trained_at
            if age > timedelta(hours=self.policy.max_model_age_hours):
                self.detail = f"model_stale_{int(age.total_seconds() // 3600)}h"
            elif self.detail.startswith("model_stale"):
                self.detail = "ok"
        after = (self.model.label if self.model else None, self.detail)
        if after == before or (before[1].startswith("model_stale")
                               and after[1].startswith("model_stale")):
            return None
        auc = f"{self.model.test_auc:.3f}" if self.model else "none"
        return (f"[trade-score-model] at={now.isoformat()} model={after[0] or 'none'} "
                f"test_auc={auc} usable={str(self.usable).lower()} detail={self.detail}")

    @property
    def usable(self) -> bool:
        return self.model is not None and not self.detail.startswith("model_stale")

    def win_probability(self, traits: dict, now: datetime) -> float | None:
        if not self.usable:
            return None
        return self.model.win_probability(traits, now)
