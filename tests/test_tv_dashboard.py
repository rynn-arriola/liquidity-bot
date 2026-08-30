from __future__ import annotations

from datetime import datetime, timedelta, timezone
from functools import partial
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import threading
import unittest
from decimal import Decimal
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from liquidity_bot.tv_dashboard import (
    DashboardHandler,
    _client_is_local,
    _open_read_only,
    _read_active_scope_snapshot,
    _read_gain_snapshot,
    _read_live_pnl_snapshot,
    collect_state,
)
from liquidity_bot.active_scope_cache import write_active_scope_snapshot
from liquidity_bot.live_pnl_cache import write_live_pnl_snapshot


NOW = datetime(2026, 8, 28, 23, 0, tzinfo=timezone.utc)


class TvDashboardTests(unittest.TestCase):
    def _active_scope_cache(
        self, root: Path, markets: list[str], *,
        observed_at: datetime = NOW, capacity: int = 20,
        status: str = "RUNNING", publisher_pid: int = 100,
    ) -> Path:
        path = root / "tv-active-scope.json"
        write_active_scope_snapshot(
            path,
            observed_at=observed_at,
            markets=markets,
            capacity=capacity,
            status=status,
            publisher_pid=publisher_pid,
        )
        return path

    def _live_pnl_cache(
        self, root: Path, *, risk_date: str = "2026-08-28",
        realized: str | None = "-21.0368",
        unrealized: str | None = "-0.1000",
        marked: str = "-21.1368",
    ) -> Path:
        path = root / "tv-live-pnl.json"
        path.write_text(json.dumps({
            "schema_version": 1,
            "observed_at": "2026-08-28T22:59:53Z",
            "risk_date_et": risk_date,
            "marked_pnl": marked,
            "realized_pnl": realized,
            "unrealized_pnl": unrealized,
            "nontrading_cash": "0",
            "scope": "ACCOUNT_WIDE_TRADING",
        }), encoding="utf-8")
        return path

    def _gain_cache(self, root: Path) -> Path:
        path = root / "tv-gain-snapshot.json"
        path.write_text(json.dumps({
            "schema_version": 1,
            "generated_at": "2026-08-28T22:59:30Z",
            "time_zone": "America/New_York",
            "unbalanced_markets": 4,
            "headline": {
                "today_pnl": "-44.52",
                "overall_pnl": "10.70",
                "overall_trading": "-130.65",
                "overall_gain": "141.35",
                "overall_since_et": "2026-08-14",
            },
            "rows": [{
                "date_et": "2026-08-28",
                "current_day": True,
                "trading": "-44.52",
                "trading_estimate": False,
                "gain": "14.32",
                "pending": "0",
                "side_hours": 416.65,
                "net": "-30.20",
                "net_status": "finalized",
            }],
        }), encoding="utf-8")
        return path

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
            root = Path(directory)
            database, log = self._fixture(root)
            capacity = root / "slot_capacity"
            capacity.write_text("20\n", encoding="utf-8")
            state = collect_state(
                database,
                log,
                now=NOW,
                slot_capacity_path=capacity,
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

    def test_stopped_legacy_scope_falls_back_to_current_ledger_evidence(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            database, log = self._fixture(root)
            connection = sqlite3.connect(database)
            connection.execute(
                "UPDATE execution_queue_stream_sessions SET "
                "status='STOPPED', ended_at=?",
                ("2026-08-28T22:59:59+00:00",),
            )
            connection.commit()
            connection.close()

            state = collect_state(database, log, now=NOW, processes=[])

            scope = state["ledger"]["scope"]
            self.assertEqual(scope["status"], "LEDGER_FALLBACK")
            self.assertEqual(scope["source"], "CURRENT_LEDGER_EVIDENCE")
            statuses = [row["status"] for row in state["ledger"]["slots"]]
            self.assertNotIn("observed", statuses)
            self.assertEqual(statuses[:4], [
                "quoting", "position", "attention", "attention"])

    def test_fresh_runner_scope_replaces_retired_queue_scope(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            database, log = self._fixture(root)
            current = ["current-one", "current-two", "current-three"]
            connection = sqlite3.connect(database)
            connection.execute(
                "UPDATE execution_queue_stream_sessions SET "
                "status='STOPPED', ended_at=?",
                ("2026-08-28T22:59:59+00:00",),
            )
            connection.execute(
                "INSERT INTO execution_orders VALUES (?, 'OPEN')",
                (current[0],),
            )
            connection.commit()
            connection.close()
            cache = self._active_scope_cache(root, current)

            state = collect_state(
                database, log, now=NOW, processes=[{
                    "pid": 100,
                    "age_seconds": 60,
                    "args": "python -m liquidity_bot.live_commissioning",
                }],
                active_scope_cache=cache)

            scope = state["ledger"]["scope"]
            self.assertEqual(scope["status"], "RUNNING")
            self.assertEqual(scope["source"], "RUNNER_ACTIVE_SCOPE")
            self.assertEqual(scope["size"], 3)
            self.assertEqual(
                [row["market"] for row in state["ledger"]["slots"][:3]],
                current,
            )
            self.assertEqual(
                [row["status"] for row in state["ledger"]["slots"][:3]],
                ["quoting", "observed", "observed"],
            )

    def test_dead_or_stopped_runner_scope_fails_to_ledger_evidence(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            database, log = self._fixture(root)
            stale = datetime(2026, 8, 28, 22, 57, tzinfo=timezone.utc)
            cache = self._active_scope_cache(
                root, ["stale-market"], observed_at=stale)
            report = _read_active_scope_snapshot(cache, NOW, 20, set())
            self.assertTrue(report["available"])
            self.assertFalse(report["usable"])

            state = collect_state(
                database, log, now=NOW, processes=[],
                active_scope_cache=cache)
            self.assertEqual(
                state["ledger"]["scope"]["source"],
                "CURRENT_LEDGER_EVIDENCE",
            )
            self.assertNotIn(
                "stale-market",
                [row["market"] for row in state["ledger"]["slots"]],
            )

            stopped = self._active_scope_cache(
                root, ["stopped-market"], status="STOPPED")
            report = _read_active_scope_snapshot(stopped, NOW, 20, {100})
            self.assertTrue(report["available"])
            self.assertFalse(report["usable"])

    def test_active_scope_writer_rejects_ambiguous_payloads(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "scope.json"
            with self.assertRaisesRegex(ValueError, "unique"):
                write_active_scope_snapshot(
                    path,
                    observed_at=NOW,
                    markets=["same", "same"],
                    capacity=20,
                )
            with self.assertRaisesRegex(ValueError, "exceeds"):
                write_active_scope_snapshot(
                    path,
                    observed_at=NOW,
                    markets=["one", "two"],
                    capacity=1,
                )

    def test_future_or_wrong_capacity_active_scope_is_not_usable(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            future = self._active_scope_cache(
                root,
                ["future-market"],
                observed_at=NOW + timedelta(minutes=1),
            )
            report = _read_active_scope_snapshot(future, NOW, 20, {100})
            self.assertFalse(report["available"])
            self.assertIn("future", report["error"])

            mismatched = self._active_scope_cache(
                root, ["old-capacity-market"], capacity=21)
            report = _read_active_scope_snapshot(mismatched, NOW, 20, {100})
            self.assertTrue(report["available"])
            self.assertFalse(report["usable"])

    def test_slot_map_uses_the_validated_restart_capacity(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            database, log = self._fixture(root)
            capacity = root / "slot_capacity"
            capacity.write_text("22\n", encoding="utf-8")

            state = collect_state(
                database, log, now=NOW, processes=[],
                slot_capacity_path=capacity)

            self.assertEqual(state["capacity"], 22)
            self.assertEqual(len(state["ledger"]["slots"]), 22)
            self.assertEqual(state["ledger"]["slots"][-1]["number"], 22)

    def test_gain_snapshot_is_local_validated_and_exposed(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            database, log = self._fixture(root)
            cache = self._gain_cache(root)
            before = cache.stat()

            state = collect_state(
                database, log, now=NOW, processes=[], gain_cache=cache)

            report = state["gain_report"]
            self.assertTrue(report["available"])
            self.assertEqual(report["age_seconds"], 30)
            self.assertEqual(report["headline"]["today_pnl"], "-44.52")
            self.assertEqual(report["headline"]["overall_pnl"], "10.70")
            self.assertEqual(report["rows"][0]["side_hours"], 416.65)
            self.assertEqual(cache.stat().st_mtime_ns, before.st_mtime_ns)

    def test_corrupt_gain_snapshot_fails_closed(self):
        with TemporaryDirectory() as directory:
            cache = Path(directory) / "cache.json"
            cache.write_text('{"schema_version":1,"rows":"wrong"}', encoding="utf-8")

            report = _read_gain_snapshot(cache, NOW)

            self.assertFalse(report["available"])
            self.assertEqual(report["rows"], [])
            self.assertIsNone(report["headline"])

    def test_live_runner_mark_is_validated_and_exposed(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            database, log = self._fixture(root)
            cache = self._live_pnl_cache(root)

            state = collect_state(
                database, log, now=NOW, processes=[], live_pnl_cache=cache)

            mark = state["live_pnl"]
            self.assertTrue(mark["available"])
            self.assertEqual(mark["age_seconds"], 7)
            self.assertEqual(mark["marked_pnl"], "-21.1368")
            self.assertEqual(mark["realized_pnl"], "-21.0368")
            self.assertEqual(mark["unrealized_pnl"], "-0.1000")

    def test_live_mark_with_inconsistent_breakdown_fails_closed(self):
        with TemporaryDirectory() as directory:
            cache = self._live_pnl_cache(
                Path(directory), unrealized="-0.09")

            report = _read_live_pnl_snapshot(cache, NOW)

            self.assertFalse(report["available"])
            self.assertIn("does not reconcile", report["error"])

    def test_previous_et_day_live_mark_fails_closed_after_rollover(self):
        with TemporaryDirectory() as directory:
            cache = self._live_pnl_cache(Path(directory))
            after_midnight_et = datetime(
                2026, 8, 29, 4, 0, 1, tzinfo=timezone.utc)

            report = _read_live_pnl_snapshot(cache, after_midnight_et)

            self.assertFalse(report["available"])
            self.assertIn("previous ET day", report["error"])

    def test_marked_total_remains_available_without_carried_basis_breakdown(self):
        with TemporaryDirectory() as directory:
            cache = self._live_pnl_cache(
                Path(directory), realized=None, unrealized=None,
                marked="-3.25")

            report = _read_live_pnl_snapshot(cache, NOW)

            self.assertTrue(report["available"])
            self.assertEqual(report["marked_pnl"], "-3.25")
            self.assertIsNone(report["realized_pnl"])
            self.assertIsNone(report["unrealized_pnl"])

    def test_runner_cache_writer_is_atomic_and_reconciled(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / "nested" / "tv-live-pnl.json"

            write_live_pnl_snapshot(
                cache, observed_at=NOW, risk_date=NOW.date(),
                marked_pnl=Decimal("-21.1368"),
                realized_pnl=Decimal("-21.0368"),
                unrealized_pnl=Decimal("-0.1000"),
                nontrading_cash=Decimal("0"))

            payload = json.loads(cache.read_text(encoding="utf-8"))
            self.assertEqual(payload["marked_pnl"], "-21.1368")
            self.assertEqual(list(cache.parent.glob(".*.tmp")), [])
            with self.assertRaisesRegex(ValueError, "does not reconcile"):
                write_live_pnl_snapshot(
                    cache, observed_at=NOW, risk_date=NOW.date(),
                    marked_pnl=Decimal("-21.13"),
                    realized_pnl=Decimal("-21.03"),
                    unrealized_pnl=Decimal("-0.09"),
                    nontrading_cash=Decimal("0"))

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
                # Repeat the rejected body path: on Windows an unread request
                # body intermittently reset the socket after the server had
                # logged 405, hiding the intended read-only response.
                for _ in range(10):
                    with self.assertRaises(HTTPError) as caught:
                        urlopen(Request(
                            f"{url}/api/state", data=b"{}", method="POST"),
                            timeout=2)
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

    def test_frontend_uses_the_runtime_slot_capacity(self):
        root = Path(__file__).resolve().parents[1] / "web" / "tv-dashboard"
        html = (root / "index.html").read_text(encoding="utf-8")
        javascript = (root / "app.js").read_text(encoding="utf-8")
        styles = (root / "styles.css").read_text(encoding="utf-8")
        self.assertIn('id="slot-capacity"', html)
        self.assertIn('id="scope-capacity"', html)
        self.assertIn("TODAY P&amp;L", html)
        self.assertIn("OVERALL P&amp;L", html)
        headers = ["Date ET", "Trading", "Gain", "Pending", "Side Hours", "Net"]
        self.assertEqual([html.index(f">{header}<") for header in headers], sorted(
            html.index(f">{header}<") for header in headers))
        self.assertIn("This screen never contacts Polymarket", html)
        self.assertIn('fetch("/api/state"', javascript)
        self.assertIn("index < safeCapacity", javascript)
        self.assertIn("renderSlots(ledger.slots, capacity)", javascript)
        self.assertNotIn("polymarket.us", javascript.lower())
        self.assertNotIn("reward", html.lower())
        self.assertNotIn("reward", javascript.lower())
        # The TV view is a single-screen instrument panel. All major sections
        # must participate in a viewport-height grid instead of growing the
        # document and requiring an operator to scroll past live information.
        self.assertIn("\n  height: 100dvh;", styles)
        self.assertIn("grid-template-rows: auto auto auto auto minmax(0, 1fr) auto auto", styles)
        self.assertIn("grid-template-columns: repeat(10, minmax(0, 1fr))", styles)
        self.assertIn("safeCapacity > 20 ? 11 : 10", javascript)
        self.assertIn("@media (max-height: 850px)", styles)
        self.assertIn("list.scrollHeight > list.clientHeight + 1", javascript)


if __name__ == "__main__":
    unittest.main()
