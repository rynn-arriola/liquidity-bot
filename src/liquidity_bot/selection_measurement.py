"""Bounded, local-only selection evidence. No trading consumer or I/O."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from .rewards import ENTRY_REWARD_EXIT_BUFFER_SECONDS


# Compare book receipt with the scan timestamp, never with report run time.
# Matches the five-second live book age bound without authorizing any order.
MEASUREMENT_MAX_BOOK_AGE_SECONDS = 5


def selection_measurement(selection: Any, config: Any, now: datetime) -> str:
    """Serialize one whole scan, so truncated/mixed scans cannot become a slate.

    This records estimates, NOT accrued/paid income or order authority. The
    caller must isolate failure. Reuses existing books and adds no API read.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("aware measurement clock required")
    if len(selection.opportunities) > 100:
        raise ValueError("measurement exceeds bounded book scope")
    rows = []
    rank = 0
    for item in selection.opportunities:
        if item.viable_sides:
            rank += 1
        period = item.period
        rows.append({
            "market": item.market.slug,
            "event": item.market.event_slug,
            "game_start": item.market.game_start.isoformat() if item.market.game_start else None,
            "book_observed_at": item.book.received_at.isoformat() if item.book.received_at else None,
            "program": period.program_id, "period": period.period,
            "pool": period.reward_pool, "target": period.target_size,
            "discount": period.discount_factor,
            "program_start": period.starts_at.isoformat() if period.starts_at else None,
            "program_end": period.ends_at.isoformat() if period.ends_at else None,
            "quantity": str(config.quote_size),
            "current_rank": rank if item.viable_sides else None,
            "spread": item.spread,
            "sides": [{
                "side": side.side, "price": side.price,
                "viable": side.viable, "blocker": side.blocker,
                "reward_eligible": side.projection.eligible,
                "share": side.projection.snapshot_share,
                "order_score": getattr(side.projection, "order_score", None),
                "total_score": getattr(side.projection, "total_score", None),
                "projected_reward": side.projection.projected_reward,
                "remaining_reward": side.projection.maximum_remaining_reward,
                "remaining_entry_reward": side.projection.remaining_entry_reward,
                "fill_probability": side.fill_probability,
                "stressed_exit_pnl": side.stressed_exit_pnl,
                "stressed_exit_complete": side.stressed_exit_complete,
            } for side in item.sides],
        })
    return json.dumps({
        "schema": 1, "at": now.isoformat(), "enforcement": "none",
        "scope": "evaluated_books_not_entire_market_universe",
        "projection_seconds": config.reward_projection_horizon_seconds,
        "projection_haircut": float(config.reward_projection_haircut),
        "entry_exit_buffer_seconds": ENTRY_REWARD_EXIT_BUFFER_SECONDS,
        "measurement_max_book_age_seconds": MEASUREMENT_MAX_BOOK_AGE_SECONDS,
        "candidates": rows,
    }, allow_nan=False, separators=(",", ":"))
