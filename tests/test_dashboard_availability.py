from __future__ import annotations

import io
import sqlite3
import sys
import unittest
from contextlib import redirect_stdout
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from liquidity_bot.errors import AdapterError
from scripts import dashboard


class DashboardVenueAvailabilityTests(unittest.IsolatedAsyncioTestCase):
    def test_window_hard_stop_cap_is_fixed_despite_quantity_and_breadth(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            quote_size = root / "quote_size"
            slot_capacity = root / "slot_capacity"
            quote_size.write_text("10\n", encoding="utf-8")
            slot_capacity.write_text("24\n", encoding="utf-8")
            config = dashboard.CommissioningConfig.diagnostic_defaults(
                runtime_quote_size_path=quote_size,
                runtime_slot_capacity_path=slot_capacity)

        self.assertEqual(config.quote_size, Decimal("10"))
        self.assertEqual(config.max_markets, 24)
        self.assertEqual(dashboard._hard_stop_cap(config), 40)  # shipped window stop

    def test_window_meter_reads_only_fresh_matching_checkpoint(self):
        from liquidity_bot.execution_ledger import ExecutionLedger
        from liquidity_bot.risk_windows import WindowRiskMeter
        config = dashboard.CommissioningConfig.diagnostic_defaults()
        now = dashboard.datetime(2026, 9, 15, 12, tzinfo=dashboard.timezone.utc)
        with TemporaryDirectory() as directory:
            path = Path(directory) / "test.db"
            ledger = ExecutionLedger(path)
            try:
                meter = WindowRiskMeter(ledger.connection, config.risk_window_policy)
                for pnl in (Decimal(0), Decimal(-5)):
                    meter.observe(settled_pnl=pnl, observed_at=now, now=now,
                                  full_cap=64)
                self.assertEqual(dashboard._window_hard_stop_pnl(now, database=path), Decimal(-5))
                self.assertIsNone(dashboard._window_hard_stop_pnl(
                    now + dashboard.timedelta(seconds=16), database=path))
                self.assertIsNone(dashboard._window_hard_stop_pnl(
                    now + dashboard.timedelta(hours=6), database=path))
            finally:
                ledger.close()

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
            hard_stop_pnl=Decimal("0"), hard_stop_cap=80,
            viable=None, tight=0, rewarded=0,
            reward_paid=None, reward_pending=None, uptime="1h")

        rendered = str(payload)
        self.assertIn("venue state unavailable", rendered)
        self.assertGreaterEqual(rendered.count("unknown"), 5)
        self.assertIn("HARD", rendered)
        self.assertNotIn("session loss", rendered)
        self.assertNotIn("worst market", rendered)
        self.assertNotIn("positions          0", rendered)
        self.assertNotIn("resting orders     0", rendered)


if __name__ == "__main__":
    unittest.main()
