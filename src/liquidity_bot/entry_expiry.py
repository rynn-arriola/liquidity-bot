"""Restart-scoped exchange-side lifetime for new-risk quotes only."""
from dataclasses import dataclass
from datetime import datetime, timedelta
import json
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "config/entry_expiry_policy.json"


@dataclass(frozen=True)
class EntryExpiryPolicy:
    enabled: bool = True
    lifetime_seconds: int = 300

    def __post_init__(self):
        if type(self.enabled) is not bool:
            raise ValueError("entry expiry enabled must be boolean")
        # 600 s ceiling (operator, 2026-09-26): each expiry forces a proof
        # and a replacement gap, while unattended-host exposure grows with the
        # lifetime. Ten minutes halves the churn without a longer silent risk.
        if (type(self.lifetime_seconds) is not int
                or not 60 <= self.lifetime_seconds <= 600):
            raise ValueError("entry expiry lifetime must be 60..600 seconds")

    def deadline(self, now: datetime, game_start: datetime | None,
                 before_start_seconds: int) -> datetime | None:
        if not self.enabled:
            return None
        if now.tzinfo is None or game_start is None or game_start.tzinfo is None:
            raise ValueError("entry expiry requires aware current and game times")
        return min(now + timedelta(seconds=self.lifetime_seconds),
                   game_start - timedelta(seconds=before_start_seconds))


def load_entry_expiry_policy(path: Path = DEFAULT_PATH) -> EntryExpiryPolicy:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or set(raw) != {"enabled", "lifetime_seconds"}:
        raise ValueError("invalid entry expiry policy fields")
    return EntryExpiryPolicy(**raw)
