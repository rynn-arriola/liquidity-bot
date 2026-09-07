"""Restart-scoped, entry-only stressed retained-value policy."""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

from .shadow_math import ExitResult

DEFAULT_ENTRY_QUALITY_POLICY_PATH = (
    Path(__file__).resolve().parents[2] / "config" / "entry_quality_policy.json")


@dataclass(frozen=True)
class EntryQualityPolicy:
    enabled: bool = False
    minimum_retained_value: float = .70

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise ValueError("entry quality enabled must be boolean")
        value = self.minimum_retained_value
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or not 0 < value <= 1):
            raise ValueError("minimum retained value must be a number in (0, 1]")

    def blocker(self, result: ExitResult) -> str | None:
        """Consume a freshly computed exit, never a saved observation label."""
        if not self.enabled:
            return None
        if (not result.complete or result.entry_cost <= 0
                or not math.isfinite(result.entry_cost)
                or not math.isfinite(result.pnl)):
            return "entry_quality_unknown"
        ratio = (result.entry_cost + result.pnl) / result.entry_cost
        if not math.isfinite(ratio):
            return "entry_quality_unknown"
        if ratio + 1e-12 < self.minimum_retained_value:
            return "entry_quality_below_minimum"
        return None


def load_entry_quality_policy(
    path: Path = DEFAULT_ENTRY_QUALITY_POLICY_PATH,
) -> EntryQualityPolicy:
    """Strict startup validation, before credentials/ledger/network access."""
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate entry quality policy key")
            result[key] = value
        return result

    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"),
                         object_pairs_hook=unique_object)
        if (not isinstance(raw, dict)
                or set(raw) != {"enabled", "minimum_retained_value"}):
            raise ValueError("entry quality policy keys are invalid")
        return EntryQualityPolicy(**raw)
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        raise ValueError(f"invalid entry quality policy at {path}: {exc}") from exc
