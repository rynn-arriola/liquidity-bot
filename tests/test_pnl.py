from __future__ import annotations

import unittest
from decimal import Decimal

from liquidity_bot.pnl import (
    MatchedInventory,
    economic_contract_cost,
    economic_sale_proceeds,
    open_inventory_cost,
    strategy_price_from_position_cost,
)


class PriceFrameTests(unittest.TestCase):
    def test_buy_short_cost_is_one_minus_stored_long_price(self):
        self.assertEqual(
            economic_contract_cost("ORDER_INTENT_BUY_SHORT", "0.85"),
            Decimal("0.15"),
        )

    def test_sell_proceeds_follow_the_outcome_being_sold(self):
        self.assertEqual(
            economic_sale_proceeds("ORDER_INTENT_SELL_LONG", "0.60"),
            Decimal("0.60"),
        )
        self.assertEqual(
            economic_sale_proceeds("ORDER_INTENT_SELL_SHORT", "0.60"),
            Decimal("0.40"),
        )

    def test_negative_venue_cost_converts_back_to_long_frame(self):
        self.assertEqual(
            strategy_price_from_position_cost("-10", "0.707"),
            Decimal("0.293"),
        )

    def test_open_short_basis_uses_one_minus_without_a_negative_value(self):
        self.assertEqual(
            open_inventory_cost("-5", "0.85"), Decimal("0.75"))


class MatchedInventoryTests(unittest.TestCase):
    def test_asymmetric_live_pair_uses_one_minus_for_short(self):
        book = MatchedInventory()
        self.assertEqual(book.apply(
            intent="ORDER_INTENT_BUY_LONG", price="0.83", quantity="5"), 0)
        self.assertEqual(book.apply(
            intent="ORDER_INTENT_BUY_SHORT", price="0.85", quantity="5"),
            Decimal("0.10"),
        )
        self.assertEqual(book.position, 0)
        self.assertIsNone(book.average_cost)

    def test_partial_reduction_and_flip_keep_the_correct_basis(self):
        book = MatchedInventory()
        book.apply(intent="ORDER_INTENT_BUY_LONG", price="0.40", quantity="10")
        first = book.apply(
            intent="ORDER_INTENT_BUY_SHORT", price="0.55", quantity="4")
        second = book.apply(
            intent="ORDER_INTENT_BUY_SHORT", price="0.50", quantity="8")

        self.assertEqual(first, Decimal("0.60"))
        self.assertEqual(second, Decimal("0.60"))
        self.assertEqual(book.position, Decimal("-2"))
        self.assertEqual(book.average_cost, Decimal("0.50"))

    def test_manual_sell_and_buy_close_share_the_same_pair_arithmetic(self):
        sold = MatchedInventory()
        sold.apply(intent="ORDER_INTENT_BUY_LONG", price="0.40", quantity="5")
        sold_pnl = sold.apply(
            intent="ORDER_INTENT_SELL_LONG", price="0.60", quantity="5")

        paired = MatchedInventory()
        paired.apply(intent="ORDER_INTENT_BUY_LONG", price="0.40", quantity="5")
        paired_pnl = paired.apply(
            intent="ORDER_INTENT_BUY_SHORT", price="0.60", quantity="5")

        self.assertEqual(sold_pnl, Decimal("1.00"))
        self.assertEqual(paired_pnl, Decimal("1.00"))

    def test_open_inventory_realizes_only_its_observed_fee(self):
        book = MatchedInventory()
        result = book.apply(
            intent="ORDER_INTENT_BUY_SHORT", price="0.85", quantity="5",
            fee="0.07",
        )
        self.assertEqual(result, Decimal("-0.07"))
        self.assertEqual(book.position, Decimal("-5"))
        self.assertEqual(book.average_cost, Decimal("0.15"))


if __name__ == "__main__":
    unittest.main()
