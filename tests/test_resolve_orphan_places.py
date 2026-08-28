import sqlite3
import unittest

from scripts.resolve_orphan_places import candidate_blockers


class ResolveOrphanPlacesTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            "CREATE TABLE execution_orders (client_order_id TEXT, status TEXT)")
        self.row = {
            "client_order_id": "local-only-client",
            "market_slug": "market-one",
        }

    def tearDown(self):
        self.conn.close()

    def blockers(self, *, clients=(), markets=(), held=()):
        return candidate_blockers(
            self.conn, self.row,
            live_clients=set(clients),
            live_markets=set(markets),
            held=set(held),
        )

    def test_clean_account_has_no_blocker(self):
        self.assertEqual(self.blockers(), [])

    def test_different_client_in_same_market_blocks_resolution(self):
        self.assertIn(
            "the venue reports a live order in this market",
            self.blockers(
                clients={"venue:exchange-id"}, markets={"market-one"}),
        )

    def test_position_in_same_market_blocks_resolution(self):
        self.assertIn(
            "the venue holds a position in this market",
            self.blockers(held={"market-one"}),
        )

    def test_existing_local_order_row_blocks_resolution(self):
        self.conn.execute(
            "INSERT INTO execution_orders VALUES (?, ?)",
            ("local-only-client", "UNKNOWN_ABSENT"),
        )
        self.assertIn("an order row exists", "; ".join(self.blockers()))

    def test_other_market_does_not_block_resolution(self):
        self.assertEqual(
            self.blockers(markets={"market-two"}, held={"market-two"}), [])


if __name__ == "__main__":
    unittest.main()
