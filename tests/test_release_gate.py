"""The Command 1 / scheduler flat gate: pure summary and refusal logic."""
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from scripts.release_gate import FLAT_KEYS, gate_result, is_flat

NOW = datetime(2026, 9, 26, 15, tzinfo=timezone.utc)


def order(market, side="ORDER_SIDE_BUY", qty="15", filled="0", status="OPEN"):
    return SimpleNamespace(market_slug=market, side=side, original_qty=Decimal(qty),
                           filled_qty=Decimal(filled), status=status)


def position(market, qty):
    return SimpleNamespace(market_slug=market, net_qty=Decimal(qty))


class FakeLedger:
    def __init__(self, *, unresolved=0, unknown=0, working=0, positions=()):
        self.unresolved, self.unknown, self.working = unresolved, unknown, working
        self.positions = positions

    def unresolved_intents(self):
        return [object()] * self.unresolved

    def account_unknown_order_count(self):
        return self.unknown

    def working_orders(self):
        return [object()] * self.working

    def query(self, sql):
        assert sql == "SELECT net_qty FROM execution_positions"
        return [(q,) for q in self.positions]


class ReleaseGateTests(unittest.TestCase):
    def test_flat_account_and_ledger_pass(self):
        result = gate_result([], [position("m", "0")], FakeLedger(positions=("0",)),
                             observed_at=NOW, include_positions=True)
        self.assertTrue(is_flat(result))
        self.assertEqual(result["position_coverage"], [])
        self.assertEqual(result["observed_at"], NOW.isoformat())

    def test_each_kind_of_exposure_refuses(self):
        cases = {
            "venue_orders": ([order("m")], [], FakeLedger()),
            "venue_positions": ([], [position("m", "-3")], FakeLedger()),
            "unresolved_intents": ([], [], FakeLedger(unresolved=1)),
            "unknown_orders": ([], [], FakeLedger(unknown=1)),
            "ledger_working": ([], [], FakeLedger(working=2)),
            "ledger_positions": ([], [], FakeLedger(positions=("0", "1.5"))),
        }
        self.assertEqual(set(cases), set(FLAT_KEYS))
        for key, (orders, positions, ledger) in cases.items():
            with self.subTest(key=key):
                result = gate_result(orders, positions, ledger,
                                     observed_at=NOW, include_positions=False)
                self.assertTrue(result[key])
                self.assertFalse(is_flat(result))

    def test_position_coverage_lists_only_that_markets_orders(self):
        orders = [order("a", side="ORDER_SIDE_SELL", qty="15", filled="5"), order("b")]
        result = gate_result(orders, [position("a", "10"), position("b", "0")],
                             FakeLedger(), observed_at=NOW, include_positions=True)
        self.assertEqual(result["venue_position_market_orders"], 1)
        self.assertEqual(result["venue_flat_market_orders"], 1)
        self.assertEqual(result["position_coverage"], [{
            "market": "a", "quantity": "10",
            "orders": [{"side": "ORDER_SIDE_SELL", "remaining": "10", "status": "OPEN"}]}])


if __name__ == "__main__":
    unittest.main()
