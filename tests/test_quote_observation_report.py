from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

from liquidity_bot.execution_ledger import ExecutionLedger
from scripts.quote_observation_report import render_report


NOW = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)


class QuoteObservationReportTests(unittest.TestCase):
    def test_missing_required_observation_is_visible_without_venue_calls(self):
        path = Path(tempfile.mkdtemp(prefix="quote-report-")) / "ledger.db"
        ledger = ExecutionLedger(path)
        self.addCleanup(ledger.close)
        ledger.prepare_intent(
            "intent-one", "PLACE", "market-one", "client-one", {},
            created_at=NOW, quote_observation_required=True)

        output = io.StringIO()
        with redirect_stdout(output):
            verdict = render_report(ledger.connection)

        self.assertEqual(verdict, "COLLECTING")
        self.assertIn("required building PLACE intents : 1", output.getvalue())
        self.assertIn("missing observations            : 1", output.getvalue())


if __name__ == "__main__":
    unittest.main()
