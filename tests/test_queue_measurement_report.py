from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from liquidity_bot.execution_ledger import ExecutionLedger
from scripts.queue_measurement_report import render_report


class QueueMeasurementReportTests(unittest.TestCase):
    def test_empty_installed_ledger_is_retired_without_venue_access(self):
        path = Path(tempfile.mkdtemp(prefix="queue-report-")) / "ledger.db"
        ledger = ExecutionLedger(path)
        try:
            self.assertEqual(render_report(ledger.connection), "RETIRED")
        finally:
            ledger.close()

    def test_missing_table_is_named_not_installed(self):
        import sqlite3
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        try:
            self.assertEqual(render_report(connection), "NOT_INSTALLED")
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
