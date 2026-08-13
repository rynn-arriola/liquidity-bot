"""Pure risk sizing and conservative opportunity economics."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OpportunityEconomics:
    """Conservative value estimate for one proposed resting order.

    Reward, rebate, and spread inputs must already be conservative expected
    dollar values attributable to this order. Only the unwind loss is
    conditional here, so it is multiplied by observed fill probability.
    """

    conservative_reward: float
    maker_rebate: float
    spread_capture: float
    fill_probability: float
    unwind_loss_if_filled: float
    operational_buffer: float = 0.0

    def __post_init__(self) -> None:
        amounts = (
            self.conservative_reward,
            self.maker_rebate,
            self.spread_capture,
            self.unwind_loss_if_filled,
            self.operational_buffer,
        )
        if any(value < 0 for value in amounts):
            raise ValueError("economic inputs cannot be negative")
        if not 0 <= self.fill_probability <= 1:
            raise ValueError("fill_probability must be in [0, 1]")

    @property
    def expected_net_value(self) -> float:
        return (
            self.conservative_reward
            + self.maker_rebate
            + self.spread_capture
            - self.fill_probability * self.unwind_loss_if_filled
            - self.operational_buffer
        )

    @property
    def should_quote(self) -> bool:
        return self.expected_net_value > 0


def maximum_safe_order_size(
    side: str,
    price: float,
    *,
    loss_budget: float,
    size_cap: float,
) -> float:
    """Cap size by the collateral that can be lost if one side settles at zero."""

    side = str(side).strip().lower()
    price = float(price)
    loss_budget = float(loss_budget)
    size_cap = float(size_cap)
    if side not in {"bid", "offer"}:
        raise ValueError("side must be 'bid' or 'offer'")
    if not 0 < price < 1:
        raise ValueError("price must be between 0 and 1")
    if loss_budget < 0 or size_cap < 0:
        raise ValueError("budgets and caps cannot be negative")
    unit_loss = price if side == "bid" else 1.0 - price
    return min(size_cap, loss_budget / unit_loss)
