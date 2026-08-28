from __future__ import annotations

import sqlite3
import unittest

from liquidity_bot.sqlite_compat import active_fills_predicate


class SqliteCompatibilityTests(unittest.TestCase):
    def test_old_ledger_schema_keeps_reports_valid_during_deployment(self):
        connection = sqlite3.connect(":memory:")
        connection.execute("CREATE TABLE execution_fills(execution_id TEXT)")

        self.assertEqual(active_fills_predicate(connection), "1=1")
        self.assertEqual(
            connection.execute(
                "SELECT COUNT(*) FROM execution_fills WHERE "
                + active_fills_predicate(connection)).fetchone()[0], 0)

    def test_migrated_schema_excludes_reversed_fills_with_optional_alias(self):
        connection = sqlite3.connect(":memory:")
        connection.execute(
            "CREATE TABLE execution_fills(execution_id TEXT,reversed INTEGER)")
        connection.executemany(
            "INSERT INTO execution_fills VALUES (?,?)",
            (("active", 0), ("busted", 1)))

        self.assertEqual(active_fills_predicate(connection), "reversed=0")
        self.assertEqual(
            active_fills_predicate(connection, alias="f"), "f.reversed=0")
        self.assertEqual(
            connection.execute(
                "SELECT COUNT(*) FROM execution_fills WHERE "
                + active_fills_predicate(connection)).fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
