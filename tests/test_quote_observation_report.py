from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

from liquidity_bot.execution_ledger import ExecutionLedger
from scripts.quote_observation_report import render_report
from tests.test_execution_ledger import quote_observation


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

    def test_retained_value_shadow_reports_all_thresholds_without_refusing(self):
        path = Path(tempfile.mkdtemp(prefix="quote-shadow-report-")) / "ledger.db"
        ledger = ExecutionLedger(path)
        self.addCleanup(ledger.close)
        ledger.prepare_intent(
            "intent-shadow", "PLACE", "market-one", "client-shadow", {},
            created_at=NOW,
            quote_observation=quote_observation(
                observed_at=NOW,
                book_observed_at=NOW,
                retained_value_ratio=.65,
                stressed_exit_value=3.25,
                would_refuse_retained_60=False,
                would_refuse_retained_70=True,
                would_refuse_retained_80=True,
                replacement_market_slug="market-spare",
                replacement_projected_reward=.004,
                replacement_selection_age_seconds=30,
                replacement_reason="recent_selector_spare",
                slot_would_go_dark=False,
            ),
            quote_observation_required=True,
        )

        output = io.StringIO()
        with redirect_stdout(output):
            render_report(ledger.connection)

        report = output.getvalue()
        self.assertIn("eligible observations            : 1", report)
        self.assertIn("below 60%: flagged=    0", report)
        self.assertIn("below 70%: flagged=    1", report)
        self.assertIn(
            "selector_spare=    1 no_spare=    0 unknown=    0", report)
        self.assertIn("live orders refused              : 0", report)


if __name__ == "__main__":
    unittest.main()
