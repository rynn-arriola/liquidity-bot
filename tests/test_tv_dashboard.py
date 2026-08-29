from __future__ import annotations

from datetime import datetime, timezone
from functools import partial
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from liquidity_bot.tv_dashboard import (
    DashboardHandler,
    _client_is_local,
    _open_read_only,
    collect_state,
)


NOW = datetime(2026, 8, 28, 23, 0, tzinfo=timezone.utc)


class TvDashboardTests(unittest.TestCase):
    def _fixture(self, root: Path) -> tuple[Path, Path]:
        database = root / "commissioning.db"
        log = root / "commissioning.log"
        connection = sqlite3.connect(database)
        connection.executescript(
            """
            CREATE TABLE execution_orders (
                market_slug TEXT, status TEXT
            );
            CREATE TABLE execution_positions (
                market_slug TEXT, net_qty REAL
            );
            CREATE TABLE execution_intents (
                market_slug TEXT, state TEXT
            );
            CREATE TABLE execution_queue_stream_sessions (
                id INTEGER PRIMARY KEY, started_at TEXT, ended_at TEXT,
                scope_json TEXT, scope_size INTEGER, status TEXT,
                trades_received INTEGER, dropped_trades INTEGER,
                decode_failures INTEGER, last_trade_at TEXT,
                last_updated_at TEXT, error TEXT
            );
            """
        )
        markets = [f"aec-example-market-{index:02d}-2026-08-29" for index in range(1, 21)]
        connection.executemany(
            "INSERT INTO execution_orders VALUES (?, ?)",
            ((markets[0], "OPEN"), (markets[0], "PARTIAL"), (markets[3], "UNKNOWN_ABSENT")),
        )
        connection.execute("INSERT INTO execution_positions VALUES (?, ?)", (markets[1], 10))
        connection.execute("INSERT INTO execution_intents VALUES (?, ?)", (markets[2], "ACKNOWLEDGED"))
        connection.execute(
            "INSERT INTO execution_queue_stream_sessions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                1, "2026-08-28T22:55:00+00:00", None, json.dumps(markets), 20,
                "COLLECTING", 81, 0, 0, "2026-08-28T22:59:58+00:00",
                "2026-08-28T22:59:59+00:00", None,
            ),
        )
        connection.commit()
        connection.close()
        log.write_text(
            "[action] at=2026-08-28T22:59:59+00:00 market=aec-example-market-01 kind=PLACE_POST_ONLY token=do-not-show\n"
            "[supervisor] at=2026-08-28T22:59:58+00:00 url=https://discord.com/api/webhooks/123/secret-value\n",
            encoding="utf-8",
        )
        return database, log

    def test_snapshot_uses_local_ledger_and_maps_all_twenty_slots(self):
        with TemporaryDirectory() as directory:
            database, log = self._fixture(Path(directory))
            state = collect_state(
                database,
                log,
                now=NOW,
                processes=[
                    {"pid": 100, "age_seconds": 120, "args": "python -m liquidity_bot.live_commissioning"},
                    {"pid": 99, "age_seconds": 125, "args": "bash ./scripts/supervise_commissioning.sh"},
                ],
            )

            self.assertEqual(state["mode"], "live")
            self.assertEqual(state["source"], "LOCAL READ-ONLY · ZERO VENUE API CALLS")
            self.assertEqual(state["ledger"]["working_orders"], 2)
            self.assertEqual(state["ledger"]["working_markets"], 1)
            self.assertEqual(state["ledger"]["positions"], 1)
            self.assertEqual(state["ledger"]["unresolved_intents"], 1)
            self.assertEqual(state["ledger"]["unknown_orders"], 1)
            self.assertEqual(state["ledger"]["scope"]["size"], 20)
            self.assertEqual(state["ledger"]["scope"]["age_seconds"], 1)
            self.assertEqual(len(state["ledger"]["slots"]), 20)
            self.assertEqual(state["ledger"]["slots"][0]["status"], "quoting")
            self.assertEqual(state["ledger"]["slots"][1]["status"], "position")
            self.assertEqual(state["ledger"]["slots"][2]["status"], "attention")
            self.assertEqual(state["ledger"]["slots"][3]["status"], "attention")

    def test_observation_does_not_modify_the_ledger(self):
        with TemporaryDirectory() as directory:
            database, log = self._fixture(Path(directory))
            before = database.stat()
            collect_state(database, log, now=NOW, processes=[])
            after = database.stat()
            self.assertEqual((after.st_size, after.st_mtime_ns), (before.st_size, before.st_mtime_ns))

            connection = _open_read_only(database)
            try:
                with self.assertRaisesRegex(sqlite3.OperationalError, "readonly"):
                    connection.execute("INSERT INTO execution_orders VALUES ('x', 'OPEN')")
            finally:
                connection.close()

    def test_missing_schema_stays_unknown_instead_of_claiming_flat(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "empty.db"
            sqlite3.connect(database).close()
            state = collect_state(database, root / "missing.log", now=NOW, processes=[])

            self.assertTrue(state["ledger"]["available"])
            self.assertIsNone(state["ledger"]["working_orders"])
            self.assertIsNone(state["ledger"]["positions"])
            self.assertIsNone(state["ledger"]["unresolved_intents"])
            self.assertIsNone(state["ledger"]["unknown_orders"])
            self.assertNotEqual(state["mode"], "live")

    def test_missing_ledger_stays_unavailable_instead_of_claiming_flat(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            state = collect_state(root / "missing.db", root / "missing.log", now=NOW, processes=[])
            self.assertFalse(state["ledger"]["available"])
            self.assertIsNone(state["ledger"]["working_orders"])
            self.assertIsNone(state["ledger"]["positions"])

    def test_activity_redacts_credentials_and_webhooks(self):
        with TemporaryDirectory() as directory:
            database, log = self._fixture(Path(directory))
            state = collect_state(database, log, now=NOW, processes=[])
            rendered = json.dumps(state)
            self.assertNotIn("do-not-show", rendered)
            self.assertNotIn("secret-value", rendered)
            self.assertIn("[redacted]", rendered)

    def test_listener_accepts_only_loopback_or_private_clients(self):
        for address in ("127.0.0.1", "::1", "192.168.1.50", "10.0.0.2", "172.16.4.8", "fe80::1"):
            with self.subTest(address=address):
                self.assertTrue(_client_is_local(address))
        for address in ("8.8.8.8", "1.1.1.1", "2606:4700:4700::1111", "not-an-ip"):
            with self.subTest(address=address):
                self.assertFalse(_client_is_local(address))

    def test_http_surface_is_read_only_and_sends_security_headers(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            database, log = self._fixture(root)
            assets = Path(__file__).resolve().parents[1] / "web" / "tv-dashboard"
            handler = partial(DashboardHandler, database=database, log=log, assets=assets)
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            url = f"http://127.0.0.1:{server.server_address[1]}"
            try:
                with urlopen(f"{url}/api/state", timeout=2) as response:
                    payload = json.load(response)
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.headers["Cache-Control"], "no-store")
                    self.assertEqual(response.headers["X-Frame-Options"], "DENY")
                    self.assertEqual(payload["schema_version"], 1)
                with self.assertRaises(HTTPError) as caught:
                    urlopen(Request(f"{url}/api/state", data=b"{}", method="POST"), timeout=2)
                self.assertEqual(caught.exception.code, 405)
                caught.exception.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_startup_hook_is_after_runner_proof_and_failure_is_isolated(self):
        root = Path(__file__).resolve().parents[1]
        launcher = (root / "scripts" / "start_commissioning.sh").read_text(encoding="utf-8")
        proof = 'if [ -z "$(runner_pids)" ]; then'
        hook = "if ! ./scripts/start_tv_dashboard.sh --open; then"
        self.assertIn(hook, launcher)
        self.assertGreater(launcher.index(hook), launcher.index(proof))
        self.assertIn("WARNING: bot is running", launcher)

    def test_frontend_is_a_twenty_slot_local_view(self):
        root = Path(__file__).resolve().parents[1] / "web" / "tv-dashboard"
        html = (root / "index.html").read_text(encoding="utf-8")
        javascript = (root / "app.js").read_text(encoding="utf-8")
        self.assertIn("20 Market Slots", html)
        self.assertIn("This screen never contacts Polymarket", html)
        self.assertIn('fetch("/api/state"', javascript)
        self.assertIn("index < 20", javascript)
        self.assertNotIn("polymarket.us", javascript.lower())


if __name__ == "__main__":
    unittest.main()
