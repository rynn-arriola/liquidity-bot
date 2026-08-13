"""Accelerated multi-hour Phase 4 acceptance with a simulated venue."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from .models import BookLevel, BookSnapshot, MarketMetadata
from .reconciler import (
    ActionKind,
    DesiredStateReconciler,
    MarketFrame,
    PlanAction,
    ReconcilerPolicy,
    WorkingOrder,
)


class SimulatedVenue:
    def __init__(self):
        self.orders: dict[str, WorkingOrder] = {}
        self.position = Decimal("0")
        self.position_opened_at: datetime | None = None
        self.entry_price: float | None = None
        self.next_order_id = 1
        self.entries = 0
        self.ioc_exits = 0
        self.critical: list[str] = []

    def apply(self, action: PlanAction) -> None:
        if action.kind == ActionKind.CANCEL and action.order_id:
            self.orders.pop(action.order_id, None)
        elif action.kind == ActionKind.PLACE_POST_ONLY:
            identity = f"sim-{self.next_order_id}"
            self.next_order_id += 1
            self.orders[identity] = WorkingOrder(
                identity, str(action.side), float(action.price),
                Decimal(action.qty))
        elif action.kind == ActionKind.PLACE_IOC:
            if self.position != 0:
                self.position = Decimal("0")
                self.position_opened_at = None
                self.entry_price = None
                self.ioc_exits += 1
        elif action.kind == ActionKind.CRITICAL:
            self.critical.append(action.reason)

    def inject_fill(self, side: str, at: datetime) -> bool:
        row = next((item for item in self.orders.values()
                    if item.side == side), None)
        if row is None or self.position != 0:
            return False
        self.orders.pop(row.order_id)
        self.position = (row.remaining_qty if side == "bid"
                         else -row.remaining_qty)
        self.position_opened_at = at
        self.entry_price = row.price
        self.entries += 1
        return True


def run_scenario(*, logical_hours: int, horizon_seconds: int) -> dict:
    start = datetime(2026, 8, 13, 0, 0, tzinfo=timezone.utc)
    market = MarketMetadata(
        slug="dry-run-market", game_start=start + timedelta(hours=logical_hours + 1),
        tick_size=.01, active=True, closed=False,
        status="MARKET_STATE_OPEN", sports_market_type="moneyline",
        sports_market_type_v2="SPORTS_MARKET_TYPE_MONEYLINE")
    policy = ReconcilerPolicy(
        quote_size=Decimal("2"),
        max_inventory_age_seconds=horizon_seconds,
        max_market_worst_loss=5,
        max_portfolio_worst_loss=15,
        max_exit_loss=5,
        exit_depth_haircut=.5,
        cancel_confirm_grace_seconds=5)
    planner = DesiredStateReconciler(policy)
    venue = SimulatedVenue()
    max_inventory_age = 0.0
    next_fill = 10
    fill_side = "bid"

    for second in range(logical_hours * 3600):
        now = start + timedelta(seconds=second)
        snapshot = BookSnapshot(
            market.slug,
            (BookLevel(.49, 100), BookLevel(.48, 100)),
            (BookLevel(.51, 100), BookLevel(.52, 100)),
            "MARKET_STATE_OPEN", now)
        frame = MarketFrame(
            market=market, now=now, book=snapshot, book_observed_at=now,
            position=venue.position,
            working_orders=tuple(venue.orders.values()), state_known=True,
            reward_eligible=frozenset({"bid", "offer"}),
            expected_net_by_side={"bid": .01, "offer": .01},
            existing_portfolio_worst_loss=0,
            position_opened_at=venue.position_opened_at,
            entry_price=venue.entry_price)
        actions = planner.plan(frame)
        for action in actions:
            venue.apply(action)
        if second == next_fill:
            if venue.inject_fill(fill_side, now):
                fill_side = "offer" if fill_side == "bid" else "bid"
                next_fill += 300
        if venue.position != 0 and venue.position_opened_at is not None:
            age = (now - venue.position_opened_at).total_seconds()
            max_inventory_age = max(max_inventory_age, age)
            if age > horizon_seconds + policy.cancel_confirm_grace_seconds:
                if not venue.critical:
                    raise AssertionError(
                        "inventory exceeded timeout and cancel grace without critical")

    if venue.critical:
        raise AssertionError(f"healthy scenario raised critical: {venue.critical}")
    if venue.position != 0:
        raise AssertionError("healthy scenario ended with unmatched inventory")
    if venue.entries == 0 or venue.entries != venue.ioc_exits:
        raise AssertionError("every injected fill must have one hard exit")
    return {
        "logical_hours": logical_hours,
        "horizon_seconds": horizon_seconds,
        "entry_fills": venue.entries,
        "hard_ioc_exits": venue.ioc_exits,
        "max_observed_inventory_age_seconds": max_inventory_age,
        "critical_failures": 0,
        "orders_sent_to_polymarket": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run accelerated no-network Phase 4 safety acceptance")
    parser.add_argument("--logical-hours", type=int, default=4)
    parser.add_argument("--horizons", default="15,30,60,120")
    args = parser.parse_args()
    horizons = tuple(int(item.strip()) for item in args.horizons.split(","))
    if args.logical_hours <= 0 or not horizons or min(horizons) <= 0:
        parser.error("logical hours and horizons must be positive")
    result = [run_scenario(
        logical_hours=args.logical_hours, horizon_seconds=horizon)
        for horizon in horizons]
    print(json.dumps({"scenarios": result}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
