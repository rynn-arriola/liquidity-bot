"""Restart-scoped REST optimization; never changes trading or API limits."""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

DEFAULT_REST_POLLING_POLICY_PATH = (
    Path(__file__).resolve().parents[2] / "config" / "rest_polling_policy.json")


@dataclass(frozen=True)
class RestPollingPolicy:
    enabled: bool = False
    normal_poll_seconds: float = 5.0
    active_poll_seconds: float = 1.0

    def __post_init__(self):
        if type(self.enabled) is not bool:
            raise ValueError("REST polling enabled must be boolean")
        for value in (self.normal_poll_seconds, self.active_poll_seconds):
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value)):
                raise ValueError("REST polling intervals must be finite numbers")
        if not 3 <= self.normal_poll_seconds <= 5:
            raise ValueError("normal REST polling interval must be 3-5 seconds")
        if not 1 <= self.active_poll_seconds <= self.normal_poll_seconds:
            raise ValueError("active REST polling interval must be 1-normal seconds")


def load_rest_polling_policy(
    path: Path = DEFAULT_REST_POLLING_POLICY_PATH,
) -> RestPollingPolicy:
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate REST polling policy key")
            result[key] = value
        return result

    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"),
                         object_pairs_hook=unique_object)
        if (not isinstance(raw, dict) or set(raw) != {
                "enabled", "normal_poll_seconds", "active_poll_seconds"}):
            raise ValueError("REST polling policy keys are invalid")
        return RestPollingPolicy(**raw)
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        raise ValueError(f"invalid REST polling policy at {path}: {exc}") from exc
