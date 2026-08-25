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

    def test_session_and_hard_sensors_may_share_one_dollar_limit(self):
        guard = SessionRiskGuard(
            started_at=NOW, starting_equity=Decimal("100"),
            max_session_loss=Decimal("80"), max_market_loss=Decimal("40"),
            max_duration_seconds=1800, max_equity_age_seconds=10,
            hard_stop_loss=Decimal("80"))
        guard.observe_equity("20", observed_at=NOW)
        guard.observe_realized(Decimal("-80"))

        self.assertIn("session loss limit reached", guard.blockers(now=NOW))
        self.assertTrue(guard.hard_stop_reached(now=NOW))

    def test_session_market_time_and_critical_limits_trip_independently(self):
        guard = self.guard()
        # The session stop reads REALISED P&L now. Marked equity moving is
        # not a loss until a trade closes; at 320 open contracts a twelve-cent
        # mark is $38 and would have tripped this on paper movement alone.
        guard.observe_equity("95", observed_at=NOW)
        self.assertNotIn("session loss limit reached", guard.blockers(now=NOW))
        guard.observe_realized(Decimal("-5"))
        self.assertIn("session loss limit reached", guard.blockers(now=NOW))
        guard.observe_realized(Decimal("0"))
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

    def test_hard_stop_is_separate_from_the_realised_session_stop(self):
        guard = self.guard()
        # Equity alone no longer blocks new risk: the session stop reads
        # realised P&L. The hard stop is the one thing that still watches
        # marked equity, because it exists to catch what the ledger cannot
        # explain.
        guard.observe_equity("95", observed_at=NOW)
        self.assertTrue(guard.can_add_new_risk(now=NOW))
        guard.observe_realized(Decimal("-5"))
        self.assertFalse(guard.can_add_new_risk(now=NOW))
        guard.observe_realized(Decimal("0"))
        self.assertFalse(guard.hard_stop_reached(now=NOW))
        guard.observe_equity("85", observed_at=NOW)
        self.assertTrue(guard.hard_stop_reached(now=NOW))
        self.assertFalse(guard.hard_stop_reached(
            now=NOW + timedelta(seconds=11)))


if __name__ == "__main__":
    unittest.main()
