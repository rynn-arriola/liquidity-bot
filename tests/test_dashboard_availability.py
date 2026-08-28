from __future__ import annotations

import io
import sqlite3
import sys
import unittest
from contextlib import redirect_stdout
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from liquidity_bot.errors import AdapterError
from scripts import dashboard


class DashboardVenueAvailabilityTests(unittest.IsolatedAsyncioTestCase):
    def test_risk_caps_follow_the_restart_scoped_quantity(self):
        config = dashboard.CommissioningConfig.diagnostic_defaults(
            quote_size=Decimal("20"))

        self.assertEqual(dashboard._risk_caps(config), (160.0, 80.0, 160.0))

    async def _render_with_failure(self, fail_at: str) -> str:
        class FailingAccount:
            def __init__(self, _gateway):
                pass

            async def fetch_open_orders(self, **_kwargs):
                if fail_at == "orders":
                    raise AdapterError("orders unavailable")
                return (SimpleNamespace(market_slug="market-one"),)

            async def fetch_positions(self, **_kwargs):
                if fail_at == "positions":
                    raise AdapterError("positions unavailable")
                return ()

            async def fetch_balance(self, **_kwargs):
                if fail_at == "balance":
                    raise AdapterError("balance unavailable")
                return SimpleNamespace(current_balance=100, asset_notional=0)

        output = io.StringIO()
        argv = ["dashboard.py", "--no-color"]
        with (
            patch.object(sys, "argv", argv),
            patch.object(dashboard, "_proc", return_value=1),
            patch.object(dashboard, "_uptime", return_value="1h"),
            patch.object(dashboard, "_tail", return_value=[]),
            patch.object(dashboard.sqlite3, "connect",
                         side_effect=sqlite3.OperationalError("no ledger")),
            patch("liquidity_bot.probe.client_from_environment",
                  return_value=SimpleNamespace()),
            patch("liquidity_bot.account_snapshots.AsyncAccountSnapshotGateway",
                  FailingAccount),
            redirect_stdout(output),
        ):
            await dashboard.main()
        return output.getvalue()

    async def test_any_incomplete_account_read_renders_unknown_not_flat(self):
        """A partial account snapshot is not proof of zero exposure."""

        for fail_at in ("orders", "positions", "balance"):
            with self.subTest(fail_at=fail_at):
                rendered = await self._render_with_failure(fail_at)
                self.assertIn("venue", rendered)
                self.assertIn("unreachable", rendered)
                self.assertIn("positions  unknown", rendered)
                self.assertIn("resting  unknown", rendered)
                self.assertIn("slot counts unknown", rendered)
                self.assertNotIn("(flat)", rendered)
                self.assertNotIn("0 / 20", rendered)
                self.assertNotIn("empty                20", rendered)

    def test_discord_embed_preserves_the_same_unknown_state(self):
        payload = dashboard._embed(
            now=dashboard.datetime.now(dashboard.timezone.utc),
            failures=[], health=[("warn", "venue", "unreachable")],
            today_pnl=None, total_pnl=None, quoted=None, idle_bound=[],
            free=None, positions=None, orders=None, fills_today=0, fills_all=0,
            session_loss=0, session_cap=80, viable=None, tight=0, rewarded=0,
            reward_paid=None, reward_pending=None, uptime="1h")

        rendered = str(payload)
        self.assertIn("venue state unavailable", rendered)
        self.assertGreaterEqual(rendered.count("unknown"), 5)
        self.assertNotIn("positions          0", rendered)
        self.assertNotIn("resting orders     0", rendered)


if __name__ == "__main__":
    unittest.main()
