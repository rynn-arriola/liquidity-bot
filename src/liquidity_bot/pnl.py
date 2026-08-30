"""Canonical binary-contract price-frame and realized-P&L arithmetic.

The strategy stores every fill price in the LONG/YES frame. A positive
position therefore costs ``price`` per contract, while a negative position
costs ``1 - price``. Opposite positions form a pair worth exactly one dollar.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


POSITIVE_INTENTS = {
    "ORDER_INTENT_BUY_LONG",
    "ORDER_INTENT_SELL_SHORT",
}
NEGATIVE_INTENTS = {
    "ORDER_INTENT_BUY_SHORT",
    "ORDER_INTENT_SELL_LONG",
}


def _decimal(value: Decimal | str | int | float, name: str) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{name} is not a decimal") from exc
    if not result.is_finite():
        raise ValueError(f"{name} must be finite")
    return result


def intent_direction(intent: str) -> int:
    """Return +1 for LONG exposure and -1 for SHORT exposure."""

    normalized = str(intent or "").strip().upper()
    if normalized in POSITIVE_INTENTS:
        return 1
    if normalized in NEGATIVE_INTENTS:
        return -1
    raise ValueError("execution intent is required for economic direction")


def economic_contract_cost(
    intent: str, price: Decimal | str | int | float,
) -> Decimal:
    """Convert a stored LONG-frame price to the acquired outcome's cost."""

    value = _decimal(price, "price")
    if not Decimal("0") < value < Decimal("1"):
        raise ValueError("price must be strictly between zero and one")
    return value if intent_direction(intent) > 0 else Decimal("1") - value


def economic_sale_proceeds(
    intent: str, price: Decimal | str | int | float,
) -> Decimal:
    """Return proceeds for a SELL intent in the stored LONG price frame."""

    normalized = str(intent or "").strip().upper()
    if normalized not in {"ORDER_INTENT_SELL_LONG", "ORDER_INTENT_SELL_SHORT"}:
        raise ValueError("a SELL intent is required")
    value = _decimal(price, "price")
    if not Decimal("0") < value < Decimal("1"):
        raise ValueError("price must be strictly between zero and one")
    return value if normalized.endswith("SELL_LONG") else Decimal("1") - value


def strategy_price_from_position_cost(
    position: Decimal | str | int | float,
    cost_per_contract: Decimal | str | int | float,
) -> Decimal:
    """Convert venue-reported economic basis back to strategy price frame."""

    qty = _decimal(position, "position")
    cost = _decimal(cost_per_contract, "cost_per_contract")
    if qty == 0 or not Decimal("0") < cost < Decimal("1"):
        raise ValueError("nonzero position and unit cost within (0, 1) required")
    return cost if qty > 0 else Decimal("1") - cost


def open_inventory_cost(
    position: Decimal | str | int | float,
    strategy_price: Decimal | str | int | float,
) -> Decimal:
    """Value open inventory at economic basis without marking it to market."""

    qty = _decimal(position, "position")
    price = _decimal(strategy_price, "strategy_price")
    if qty == 0:
        return Decimal("0")
    if not Decimal("0") < price < Decimal("1"):
        raise ValueError("strategy_price must be strictly between zero and one")
    unit_cost = price if qty > 0 else Decimal("1") - price
    return abs(qty) * unit_cost


@dataclass
class MatchedInventory:
    """Incrementally realize P&L while carrying unmatched inventory at cost."""

    position: Decimal = Decimal("0")
    average_cost: Decimal | None = None

    def apply(
        self,
        *,
        intent: str,
        price: Decimal | str | int | float,
        quantity: Decimal | str | int | float,
        fee: Decimal | str | int | float = Decimal("0"),
    ) -> Decimal:
        """Apply one fill and return the realized P&L created by that fill.

        Fees are incurred immediately, including on a fill that leaves an open
        position. Gross P&L is realized only when opposite inventory pairs.
        """

        qty = _decimal(quantity, "quantity")
        charge = _decimal(fee, "fee")
        if qty <= 0:
            raise ValueError("quantity must be positive")
        direction = intent_direction(intent)
        incoming = qty * direction
        incoming_cost = economic_contract_cost(intent, price)
        realized = -charge

        if self.position == 0:
            self.position = incoming
            self.average_cost = incoming_cost
            return realized

        if (self.position > 0) == (incoming > 0):
            combined = abs(self.position) + qty
            if self.average_cost is None:
                raise ValueError("nonzero inventory has no cost basis")
            self.average_cost = (
                self.average_cost * abs(self.position) + incoming_cost * qty
            ) / combined
            self.position += incoming
            return realized

        if self.average_cost is None:
            raise ValueError("nonzero inventory has no cost basis")
        matched = min(abs(self.position), qty)
        realized += matched * (
            Decimal("1") - self.average_cost - incoming_cost)
        prior_sign = 1 if self.position > 0 else -1
        self.position += incoming
        if self.position == 0:
            self.average_cost = None
        elif (1 if self.position > 0 else -1) != prior_sign:
            self.average_cost = incoming_cost
        return realized
