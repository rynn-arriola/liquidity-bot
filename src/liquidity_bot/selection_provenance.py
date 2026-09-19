"""Evidence only: bounded binding provenance, never selection authority."""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
from typing import Any


def rank_snapshot(selection: Any, observed_at: datetime | None,
                  now: datetime, max_age: float) -> dict:
    result = {"observed_at": None, "snapshot_id": None,
              "reason": "selection_unavailable", "ranks": {}}
    if selection is None or observed_at is None:
        return result
    result["observed_at"] = observed_at.isoformat()
    age = (now - observed_at).total_seconds()
    if age < 0:
        result["reason"] = "selection_clock_invalid"
        return result
    if age > max_age:
        result["reason"] = "selection_stale"
        return result
    if len(selection.opportunities) > 100:
        raise ValueError("unbounded selection evidence")
    slugs = [x.market.slug for x in selection.top_viable(
        max(1, len(selection.opportunities)))]
    if len(slugs) != len(set(slugs)):
        raise ValueError("duplicate selection evidence")
    payload = json.dumps([result["observed_at"], slugs], separators=(",", ":"))
    result.update(snapshot_id=hashlib.sha256(payload.encode()).hexdigest(),
                  reason="ranked", ranks={slug: n for n, slug in enumerate(slugs, 1)})
    return result


def market_rank(snapshot: dict, slug: str) -> dict:
    rank = snapshot["ranks"].get(slug)
    return {"rank": rank, "observed_at": snapshot["observed_at"],
            "snapshot_id": snapshot["snapshot_id"],
            "reason": ("not_in_current_viable_slate" if rank is None
                       and snapshot["reason"] == "ranked" else snapshot["reason"])}


class SelectionProvenance:
    """Origins reset on removal/rebind or process restart, never backfilled.

    A retained owner keeps its original binding record even if later scans
    omit it. Each quote persists a copy in existing observation_json; this
    in-memory map is bounded by configured active scope and has no I/O.
    """

    def __init__(self) -> None:
        self.origins: dict[str, dict] = {}

    def bind(self, slugs: tuple[str, ...], snapshot: dict, now: datetime) -> None:
        self.origins = {s: v for s, v in self.origins.items() if s in slugs}
        for slug in slugs:
            if slug not in self.origins:
                self.origins[slug] = {**market_rank(snapshot, slug),
                                      "bound_at": now.isoformat()}

    def evidence(self, slug: str, snapshot: dict) -> dict:
        return {"schema": 1, "scope": "current_process_binding",
                "current": market_rank(snapshot, slug),
                "original": dict(self.origins[slug]) if slug in self.origins else None}
