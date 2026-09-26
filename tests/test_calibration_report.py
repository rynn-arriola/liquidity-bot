"""Offline calibration report: exposure censoring, episodes, markouts, coverage."""
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from scripts import calibration_report as report

T0 = datetime(2026, 9, 26, 12, tzinfo=timezone.utc)
iso = lambda seconds: (T0 + timedelta(seconds=seconds)).isoformat()


def observation(cid, *, slug="aec-setkameua-a-b-2026-09-26", side="bid", at=0,
                probability=0.05, retained=0.95, seconds_to_event=3000.0):
    return dict(client_order_id=cid, market_slug=slug, side=side, observed_at=iso(at),
                quote_price="0.45", fill_probability=probability, fill_risk_driver="global",
                model_queue_ahead=10.0, retained_value_ratio=retained,
                would_refuse_retained_70=0, projected_reward=0.01, best_bid=0.45,
                best_offer=0.47, seconds_to_event=seconds_to_event)


def fill(eid, cid, *, slug="aec-setkameua-a-b-2026-09-26", intent, price, qty="10",
         at, liquidity="MAKER", fee="0"):
    return dict(execution_id=eid, client_order_id=cid, market_slug=slug, side="",
                price=price, qty=qty, fee=fee, liquidity=liquidity, intent=intent,
                event_at=iso(at))


def dataset():
    return dict(
        observations=[observation("e1"), observation("e2", side="offer", retained=0.75)],
        orders=[dict(client_order_id="e1", exchange_order_id="x1", market_slug="m",
                     side="ORDER_SIDE_BUY", status="FILLED", event_at=iso(60)),
                dict(client_order_id="e2", exchange_order_id="x2", market_slug="m",
                     side="ORDER_SIDE_SELL", status="EXPIRED", event_at=iso(300))],
        intents=[dict(client_order_id="e1", created_at=iso(0), quote_observation_required=1),
                 dict(client_order_id="e2", created_at=iso(0), quote_observation_required=1),
                 dict(client_order_id="r1", created_at=iso(61), quote_observation_required=0)],
        fills=[fill("f1", "e1", intent="ORDER_INTENT_BUY_LONG", price="0.40", at=60),
               fill("f2", "r1", intent="ORDER_INTENT_SELL_LONG", price="0.45", at=120)],
    )


class CalibrationReportTests(unittest.TestCase):
    def test_fill_model_censors_exposure_at_first_fill_and_terminal_state(self):
        result = report.fill_model_calibration(dataset(), since=T0, now=T0 + timedelta(hours=1))
        cells = {row["cell"]: row for row in result["cells"]}
        # e1: 60 s to its fill (2 intervals); e2: 300 s to expiry (10 intervals).
        self.assertEqual(cells["global"]["orders"], 2)
        self.assertEqual(cells["global"]["fills"], 1)
        self.assertAlmostEqual(cells["global"]["observed_per_30s"], 1 / 12)
        self.assertAlmostEqual(cells["global"]["predicted_per_30s"], 0.05)
        self.assertAlmostEqual(cells["side:bid"]["observed_per_30s"], 1 / 2)
        self.assertAlmostEqual(cells["side:offer"]["observed_per_30s"], 0.0)
        self.assertIn("league:setkameua", cells)
        self.assertIn("time_to_event:30_60m", cells)

    def test_episodes_split_flat_to_flat_and_classify_exit(self):
        data = dataset()
        data["fills"] += [
            fill("f3", "e3", intent="ORDER_INTENT_BUY_LONG", price="0.50", at=400),
            fill("f4", "r3", intent="ORDER_INTENT_SELL_LONG", price="0.44", at=500,
                 liquidity="TAKER"),
            fill("f5", "e4", intent="ORDER_INTENT_BUY_LONG", price="0.30", at=600),
        ]
        episodes = report.position_episodes(data)
        self.assertEqual([e["exit"] for e in episodes],
                         ["passive_reducer", "taker_exit", "open_or_settled"])
        self.assertEqual(episodes[0]["pnl"], Decimal("0.50"))
        self.assertEqual(episodes[1]["pnl"], Decimal("-0.60"))
        self.assertEqual(episodes[0]["entry_client_order_id"], "e1")
        self.assertEqual(episodes[0]["entry_qty"], Decimal("10"))

    def test_episode_summary_attributes_only_owned_entries_to_decision_features(self):
        summary = report.episode_summary(dataset(), since=T0)
        self.assertEqual(summary["counts"], {"owned_entry": 1, "other_entry": 0})
        (row,) = summary["retained_value"]
        self.assertEqual(row["key"], ">=90%")
        self.assertEqual(row["closed_pnl"], Decimal("0.50"))
        self.assertEqual(row["win_rate"], 1)
        self.assertEqual(summary["kickoff_at_entry"][0]["key"], "30_60m")

    def test_markout_sign_is_favourable_positive_for_both_sides(self):
        records = [
            dict(kind="fill_markout", fill_at=iso(0), market="aec-cs2-a-b", side="ORDER_SIDE_BUY",
                 price="0.40", mid=0.42, role="entry", horizon_seconds=30),
            dict(kind="fill_markout", fill_at=iso(0), market="aec-cs2-a-b", side="ORDER_SIDE_SELL",
                 price="0.40", mid=0.42, role="entry", horizon_seconds=30),
            dict(kind="fill_markout", fill_at=iso(0), market="aec-cs2-a-b", side="ORDER_SIDE_BUY",
                 price="0.40", mid=None, role="entry", horizon_seconds=30),
        ]
        result = report.markout_summary(records, since=T0)
        overall = next(r for r in result["rows"] if r["group"] == "all")
        self.assertEqual(overall["samples"], 2)
        self.assertAlmostEqual(overall["avg_markout_cents"], 0.0)
        self.assertEqual(overall["adverse_share"], 0.5)
        self.assertEqual(result["samples_without_book"], 1)
        buy_only = report.markout_summary(records[:1], since=T0)["rows"][0]
        self.assertAlmostEqual(buy_only["avg_markout_cents"], 2.0)
        self.assertEqual(buy_only["adverse_share"], 0)
        sell_only = report.markout_summary(records[1:2], since=T0)["rows"][0]
        self.assertAlmostEqual(sell_only["avg_markout_cents"], -2.0)

    def test_suppressed_and_coverage(self):
        records = [dict(kind="entry_suppressed", at=iso(i), market="aec-wtt-a-b", side="bid",
                        reason="pending_entry_cap", seconds_to_event=900.0) for i in range(3)]
        suppressed = report.suppressed_summary(records, since=T0)
        self.assertEqual(suppressed["total"], 3)
        self.assertEqual(suppressed["distinct_market_sides"], 1)
        caps = [(T0, 2)]
        coverage = report.coverage_summary(dataset(), caps, since=T0,
                                           now=T0 + timedelta(seconds=120), step_seconds=30)
        (day,) = coverage["days"]
        # Two entries rest during [0, 60); only e2 during [60, 120): at cap half the time.
        self.assertEqual(day["samples"], 4)
        self.assertEqual(day["at_cap_share"], 0.5)
        self.assertEqual(day["mean_shortfall"], 0.5)

    def test_main_reads_a_real_ledger_file_and_writes_both_outputs(self):
        with tempfile.TemporaryDirectory(prefix="calibration-") as directory:
            root = Path(directory)
            db = root / "ledger.db"
            conn = sqlite3.connect(db)
            conn.executescript("""
                CREATE TABLE execution_quote_observations(client_order_id, market_slug, side,
                  observed_at, quote_price, fill_probability, fill_risk_driver, model_queue_ahead,
                  retained_value_ratio, would_refuse_retained_70, projected_reward, observation_json);
                CREATE TABLE execution_orders(client_order_id, exchange_order_id, market_slug,
                  side, status, event_at);
                CREATE TABLE execution_intents(client_order_id, operation, created_at,
                  quote_observation_required);
                CREATE TABLE execution_fills(execution_id, client_order_id, market_slug, side,
                  price, qty, fee, liquidity, intent, event_at, received_at, reversed);
            """)
            now = datetime.now(timezone.utc)
            at = (now - timedelta(hours=1)).isoformat()
            conn.execute("INSERT INTO execution_quote_observations VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                         ("e1", "aec-wtt-a-b", "bid", at, "0.45", 0.03, "global", 5.0, 0.95, 0, 0.01,
                          json.dumps({"best_bid": 0.45, "best_offer": 0.46, "seconds_to_event": 5000})))
            conn.execute("INSERT INTO execution_orders VALUES (?,?,?,?,?,?)",
                         ("e1", "x1", "aec-wtt-a-b", "ORDER_SIDE_BUY", "EXPIRED", now.isoformat()))
            conn.execute("INSERT INTO execution_intents VALUES (?,?,?,?)", ("e1", "PLACE", at, 1))
            conn.commit()
            conn.close()
            log = root / "commissioning.log"
            log.write_text(f"[window-risk] at={at} start=x loss=0 hard=30 cap=30 held=False\n")
            out = root / "out"
            self.assertEqual(report.main(["--db", str(db), "--days", "1", "--log", str(log),
                                          "--output-root", str(out)]), 0)
            (folder,) = out.iterdir()
            data = json.loads((folder / "report.json").read_text())
            self.assertEqual(data["api_requests"], 0)
            self.assertEqual(data["fill_model"]["cells"][0]["orders"], 1)
            self.assertIn("## Fill model", (folder / "report.md").read_text())


    def test_cron_line_is_daily_marked_and_coexists_with_log_rotation(self):
        from scripts.rotate_logs import CRON_MARK as ROTATE_MARK, cron_line as rotate_line, with_cron_line
        line = report.cron_line(Path("/bot"))
        self.assertTrue(line.startswith("40 4 * * * cd /bot && "))
        self.assertIn("--archive data/market-quality/*.sqlite3", line)
        self.assertTrue(line.endswith(report.CRON_MARK))
        both = with_cron_line(with_cron_line("", rotate_line(Path("/bot"))), line,
                              mark=report.CRON_MARK)
        again = with_cron_line(both, line, mark=report.CRON_MARK)
        self.assertEqual(both, again)
        self.assertEqual(again.count(ROTATE_MARK), 1)
        self.assertEqual(again.count(report.CRON_MARK), 1)
        from unittest.mock import patch
        with patch.object(report, "install_cron", return_value=0) as install:
            self.assertEqual(report.main(["--install-cron"]), 0)
        install.assert_called_once()
        with self.assertRaises(SystemExit):
            report.main(["--days", "1"])

    def test_archive_reader_verifies_checksums_and_skips_other_kinds(self):
        import hashlib
        with tempfile.TemporaryDirectory(prefix="calibration-archive-") as directory:
            path = Path(directory) / "run.sqlite3"
            conn = sqlite3.connect(path)
            conn.execute("CREATE TABLE records(seq, kind, received_at, body, sha256, dropped)")
            good = json.dumps({"market": "m", "at": iso(0)})
            conn.execute("INSERT INTO records VALUES (1,'entry_suppressed','x',?,?,0)",
                         (good, hashlib.sha256(good.encode()).hexdigest()))
            conn.execute("INSERT INTO records VALUES (2,'discovery','x',?,?,0)", (b"packed", "bad"))
            conn.commit()
            (record,) = report.read_archive_records([path], {"entry_suppressed", "fill_markout"})
            self.assertEqual(record["kind"], "entry_suppressed")
            import zlib
            from liquidity_bot.market_quality_store import PACKED
            packed = json.dumps({"rows": [{"market_slug": "aec-wtt-a-b"}]})
            conn.execute("INSERT INTO records VALUES (3,'rewards','x',?,?,0)",
                         (PACKED + zlib.compress(packed.encode()), hashlib.sha256(packed.encode()).hexdigest()))
            conn.commit()
            (rewards,) = report.read_archive_records([path], {"rewards"})
            self.assertEqual(rewards["rows"][0]["market_slug"], "aec-wtt-a-b")
            conn.execute("UPDATE records SET body='{}' WHERE seq=1")
            conn.commit()
            conn.close()
            with self.assertRaises(ValueError):
                report.read_archive_records([path], {"entry_suppressed"})


if __name__ == "__main__":
    unittest.main()
