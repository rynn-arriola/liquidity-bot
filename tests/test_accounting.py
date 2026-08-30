from __future__ import annotations

import sqlite3
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from scripts.accounting import _from_ledger


class AccountingPriceFrameTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp(prefix="accounting-frame-")) / "fills.db"
        connection = sqlite3.connect(self.path)
        connection.execute(
            "CREATE TABLE execution_fills ("
            "market_slug TEXT,intent TEXT,price TEXT,qty TEXT,fee TEXT,"
            "outcome TEXT,reversed INTEGER DEFAULT 0)"
        )
        connection.commit()
        connection.close()

    def insert(self, intent: str, price: str, qty: str, fee: str = "0"):
        connection = sqlite3.connect(self.path)
        connection.execute(
            "INSERT INTO execution_fills VALUES (?,?,?,?,?,?,0)",
            ("market-one", intent, price, qty, fee, intent),
        )
        connection.commit()
        connection.close()

    def test_buy_short_spend_uses_one_minus(self):
        self.insert("ORDER_INTENT_BUY_LONG", "0.83", "5", "0.04")
        self.insert("ORDER_INTENT_BUY_SHORT", "0.85", "5", "0.06")

        markets, spent, fees, unknown = _from_ledger(str(self.path))

        self.assertEqual(markets["market-one"]["L_c"], Decimal("4.15"))
        self.assertEqual(markets["market-one"]["S_c"], Decimal("0.75"))
        self.assertEqual(spent, Decimal("4.90"))
        self.assertEqual(fees, Decimal("0.10"))
        self.assertEqual(unknown, 0)

    def test_sell_short_proceeds_use_the_short_outcome_price(self):
        self.insert("ORDER_INTENT_BUY_SHORT", "0.70", "5")
        self.insert("ORDER_INTENT_SELL_SHORT", "0.60", "5")

        markets, spent, _fees, _unknown = _from_ledger(str(self.path))

        self.assertEqual(markets["market-one"]["S_q"], Decimal("0"))
        self.assertEqual(markets["market-one"]["S_c"], Decimal("-0.50"))
        self.assertEqual(spent, Decimal("-0.50"))


if __name__ == "__main__":
    unittest.main()
