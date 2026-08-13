from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from liquidity_bot.runtime_safety import (
    LeaseHeld,
    SessionRiskGuard,
    SingleInstanceLease,
)


NOW = datetime(2026, 8, 13, 4, 0, tzinfo=timezone.utc)


class SingleInstanceLeaseTests(unittest.TestCase):
    def test_second_local_runner_is_refused_and_release_recovers(self):
        path = Path(tempfile.mkdtemp(prefix="liquidity-lock-")) / "live.lock"
        first = SingleInstanceLease(path)
        second = SingleInstanceLease(path)
        first.acquire()
        try:
            with self.assertRaises(LeaseHeld):
                second.acquire()
        finally:
            first.release()
        second.acquire()
        second.release()


class SessionRiskGuardTests(unittest.TestCase):
    def guard(self):
        return SessionRiskGuard(
            started_at=NOW, starting_equity=Decimal("100"),
            max_session_loss=Decimal("5"), max_market_loss=Decimal("2"),
            max_duration_seconds=1800, max_equity_age_seconds=10,
            hard_stop_loss=Decimal("15"))

    def test_unknown_or_stale_marked_equity_blocks_new_risk(self):
        guard = self.guard()
        self.assertFalse(guard.can_add_new_risk(now=NOW))
        guard.observe_equity("100", observed_at=NOW)
        self.assertTrue(guard.can_add_new_risk(now=NOW + timedelta(seconds=9)))
        self.assertFalse(guard.can_add_new_risk(now=NOW + timedelta(seconds=11)))

    def test_session_market_time_and_critical_limits_trip_independently(self):
        guard = self.guard()
        guard.observe_equity("95", observed_at=NOW)
        self.assertIn("session loss limit reached", guard.blockers(now=NOW))
        guard.observe_equity("100", observed_at=NOW)
        guard.observe_market_realized({"market-one": "-2"})
        self.assertIn("market loss limit reached", guard.blockers(now=NOW))
        guard.observe_market_realized({})
        self.assertIn(
            "session duration limit reached",
            guard.blockers(now=NOW + timedelta(seconds=1800)))
        guard.trip_critical("private stream disconnected")
        self.assertTrue(any(item.startswith("critical failure")
                            for item in guard.blockers(now=NOW)))

    def test_fifteen_dollar_hard_stop_is_separate_from_soft_stop(self):
        guard = self.guard()
        guard.observe_equity("95", observed_at=NOW)
        self.assertFalse(guard.can_add_new_risk(now=NOW))
        self.assertFalse(guard.hard_stop_reached(now=NOW))
        guard.observe_equity("85", observed_at=NOW)
        self.assertTrue(guard.hard_stop_reached(now=NOW))
        self.assertFalse(guard.hard_stop_reached(
            now=NOW + timedelta(seconds=11)))


if __name__ == "__main__":
    unittest.main()
