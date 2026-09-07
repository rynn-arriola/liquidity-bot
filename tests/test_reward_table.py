from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from scripts.reward_table import (
    AccountMetricUnavailable, AccountRealizedToday,
    _historical_nontrading_cash_changes, _nontrading_cash_today,
    _opening_cash_for_day, account_realized_today, build_discord_payload,
    build_dashboard_snapshot, historical_account_trading_by_day, render_table,
    resting_side_hours_by_day, write_dashboard_snapshot)
from liquidity_bot.cash_activity import NontradingCashChange


class _August25Datetime(datetime):
    """Keep rollover-sensitive account snapshots deterministic."""

    @classmethod
    def now(cls, tz=None):
        moment = datetime(2026, 8, 25, 17, tzinfo=timezone.utc)
        return moment if tz is None else moment.astimezone(tz)


class RewardTableTests(unittest.TestCase):
    def setUp(self):
        folder = Path(tempfile.mkdtemp(prefix="liquidity-reward-table-"))
        self.database = folder / "ledger.db"
        connection = sqlite3.connect(self.database)
        connection.execute(
            "CREATE TABLE execution_intents ("
            "client_order_id TEXT, operation TEXT, created_at TEXT)")
        connection.execute(
            "CREATE TABLE execution_orders ("
            "client_order_id TEXT, market_slug TEXT, side TEXT, "
            "status TEXT, event_at TEXT)")
        connection.execute(
            "CREATE TABLE execution_risk_day_baselines ("
            "risk_date TEXT PRIMARY KEY, opening_equity TEXT NOT NULL, "
            "opening_realized TEXT NOT NULL, opening_cash TEXT, "
            "created_at TEXT NOT NULL, source TEXT NOT NULL)")
        connection.executemany(
            "INSERT INTO execution_intents VALUES (?,?,?)",
            [
                ("one", "PLACE", "2026-08-24T03:00:00+00:00"),
                ("two", "PLACE", "2026-08-24T04:30:00+00:00"),
                ("three", "PLACE", "2026-08-25T04:00:00+00:00"),
            ])
        connection.executemany(
            "INSERT INTO execution_orders VALUES (?,?,?,?,?)",
            [
                ("one", "market-one", "ORDER_SIDE_BUY", "CANCELED",
                 "2026-08-24T05:00:00+00:00"),
                # Same market/side overlaps `one` by 30 minutes. The report
                # must merge the coverage instead of counting 2.5 hours.
                ("two", "market-one", "ORDER_SIDE_BUY", "CANCELED",
                 "2026-08-24T06:00:00+00:00"),
                ("three", "market-two", "ORDER_SIDE_SELL", "OPEN",
                 "2026-08-25T04:00:00+00:00"),
            ])
        connection.commit()
        connection.close()

    def test_resting_hours_split_on_et_days_and_merge_overlap(self):
        days = [date(2026, 8, 24), date(2026, 8, 25)]
        hours, elapsed = resting_side_hours_by_day(
            str(self.database), days,
            now=datetime(2026, 8, 25, 6, 0, tzinfo=timezone.utc))

        self.assertAlmostEqual(hours["2026-08-24"], 2.0)
        self.assertAlmostEqual(hours["2026-08-25"], 2.0)
        self.assertAlmostEqual(elapsed["2026-08-24"], 24.0)
        self.assertAlmostEqual(elapsed["2026-08-25"], 2.0)

    def test_table_never_counts_pending_or_unposted_reward_as_net(self):
        days = [date(2026, 8, 23), date(2026, 8, 24), date(2026, 8, 25)]
        output = render_table(
            days,
            pnl={day.isoformat(): Decimal("-10") for day in days},
            paid={
                "2026-08-23": Decimal("15"),
                "2026-08-24": Decimal("5"),
            },
            pending={"2026-08-24": Decimal("20")},
            resting_hours={day.isoformat(): 24.0 for day in days},
            elapsed_hours={day.isoformat(): 24.0 for day in days},
            today=date(2026, 8, 25),
            account_history={"2026-08-23": Decimal("-10"),
                             "2026-08-24": Decimal("-10")},
            account_realized=AccountRealizedToday(
                Decimal("-30.9844"), "ok"),
        )

        self.assertIn("| 2026-08-23 | $-10.00 | $15.00 | $0.00 | **+5.00**", output)
        self.assertIn("| 2026-08-24 | $-10.00 | $5.00 | $20.00 | Pending", output)
        self.assertIn(
            "| 2026-08-25* | $-30.98 | Not posted | Not posted | Pending",
            output)
        self.assertIn("Finalized net subtotal (proven closed days only): **$+5.00**", output)
        self.assertIn("Account marked trading today (ET): **$-30.98**", output)
        self.assertIn("Balanced-pair estimate (7d): **$-30.00**", output)

    def test_discord_embed_contains_two_aligned_visual_tables(self):
        days = [date(2026, 8, 19 + offset) for offset in range(7)]
        payload = build_discord_payload(
            days,
            pnl={day.isoformat(): Decimal("-10") for day in days},
            paid={day.isoformat(): Decimal("15") for day in days},
            pending={},
            resting_hours={day.isoformat(): 24.0 for day in days},
            elapsed_hours={day.isoformat(): 24.0 for day in days},
            today=date(2026, 8, 25),
            generated_at=datetime(2026, 8, 25, 17, tzinfo=timezone.utc),
            unbalanced_markets=30,
            account_history={day.isoformat(): Decimal("-10") for day in days[:-1]},
            account_realized=AccountRealizedToday(
                Decimal("-30.9844"), "ok"))

        embed = payload["embeds"][0]
        self.assertEqual(embed["title"], "Liquidity Bot · Last 7 ET Days")
        self.assertEqual(len(embed["fields"]), 2)
        pnl_table = embed["fields"][0]["value"]
        resting_table = embed["fields"][1]["value"]
        self.assertIn("Date ET        Trading", pnl_table)
        self.assertIn("2026-08-19     $-10.00", pnl_table)
        self.assertIn("2026-08-25*    $-30.98", pnl_table)
        self.assertIn("$+5.00", pnl_table)
        self.assertIn("Side-hours  Avg sides", resting_table)
        self.assertIn("2026-08-25*       24.00       1.00", resting_table)
        self.assertNotIn("|", pnl_table)
        pnl_lines = pnl_table.splitlines()[1:-1]
        resting_lines = resting_table.splitlines()[1:-1]
        self.assertEqual({len(line) for line in pnl_lines}, {53})
        self.assertEqual({len(line) for line in resting_lines}, {34})
        self.assertIn("**Finalized net subtotal:** $+30.00", embed["description"])
        self.assertIn(
            "**Trading total:** $-90.98",
            embed["description"])
        self.assertNotIn("Balanced-pair", embed["description"])
        self.assertEqual(embed["timestamp"], "2026-08-25T17:00:00+00:00")
        self.assertLess(len(embed["footer"]["text"]), 80)
        self.assertNotIn("unbalanced", embed["footer"]["text"])

    def test_current_day_cannot_be_finalized_even_if_rewards_are_paid(self):
        today = date(2026, 8, 25)
        output = render_table(
            [today], pnl={today.isoformat(): Decimal("-26.41")},
            paid={today.isoformat(): Decimal("172.90")}, pending={},
            resting_hours={today.isoformat(): 1.0},
            elapsed_hours={today.isoformat(): 1.0}, today=today,
            account_realized=AccountRealizedToday(
                Decimal("-44.52"), "ok"))

        self.assertIn(
            "| 2026-08-25* | $-44.52 | $172.90 | $0.00 | Pending",
            output)
        self.assertIn(
            "Finalized net subtotal (proven closed days only): **Unavailable (no finalized days)**",
            output)
        self.assertIn(
            "Balanced-pair estimate (7d): **$-26.41**", output)

    def test_discord_post_refuses_more_days_than_the_visual_layout_allows(self):
        days = [date(2026, 8, 18) + timedelta(days=offset)
                for offset in range(8)]
        with self.assertRaisesRegex(ValueError, "at most 7"):
            build_discord_payload(
                days, pnl={}, paid={}, pending={}, resting_hours={},
                elapsed_hours={}, today=date(2026, 8, 25),
                generated_at=datetime(2026, 8, 25, tzinfo=timezone.utc),
                unbalanced_markets=0)

    def test_dashboard_snapshot_has_today_overall_and_pending_safe_rows(self):
        days = [date(2026, 8, 24), date(2026, 8, 25)]
        payload = build_dashboard_snapshot(
            days,
            pnl={day.isoformat(): Decimal("-10") for day in days},
            paid={"2026-08-24": Decimal("15")},
            pending={"2026-08-25": Decimal("20")},
            resting_hours={"2026-08-24": 24.5, "2026-08-25": 31.25},
            today=date(2026, 8, 25),
            generated_at=datetime(2026, 8, 25, 17, tzinfo=timezone.utc),
            unbalanced_markets=4,
            overall_trading=Decimal("-130.65"),
            account_history={"2026-08-24": Decimal("-10")},
            account_realized=AccountRealizedToday(Decimal("-44.52"), "ok"),
        )

        self.assertEqual(payload["headline"], {
            "today_pnl": "-44.52",
            "today_realized": None,
            "today_unrealized": None,
            "today_as_of": None,
            "overall_estimate": True,
            "overall_pnl": "-115.65",
            "overall_trading": "-130.65",
            "overall_gain": "15",
            "overall_since_et": "2026-08-14",
        })
        self.assertEqual(payload["rows"][0]["net"], "5")
        self.assertEqual(payload["rows"][0]["net_status"], "finalized")
        self.assertIsNone(payload["rows"][1]["net"])
        self.assertEqual(payload["rows"][1]["net_status"], "pending")
        self.assertEqual(payload["rows"][1]["gain"], "0")
        self.assertEqual(payload["rows"][1]["pending"], "20")
        self.assertEqual(payload["rows"][1]["side_hours"], 31.25)

        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "dashboard" / "latest.json"
            write_dashboard_snapshot(cache, payload)
            self.assertEqual(
                json.loads(cache.read_text(encoding="utf-8")), payload)
            self.assertEqual(list(cache.parent.glob(".*.tmp")), [])

    def test_historical_account_day_replaces_incomplete_pair_estimate(self):
        connection = sqlite3.connect(self.database)
        connection.executemany(
            "INSERT INTO execution_risk_day_baselines VALUES (?,?,?,?,?,?)",
            [
                ("2026-08-26", "772.7878", "0", "772.7878",
                 "2026-08-26T04:00:14.133861+00:00", "fresh_et_day"),
                ("2026-08-27", "708.3008", "0", "708.3008",
                 "2026-08-27T04:04:10.544588+00:00", "fresh_et_day"),
            ],
        )
        connection.commit()
        connection.close()

        result = historical_account_trading_by_day(
            str(self.database), [date(2026, 8, 26)], ())

        self.assertEqual(result["2026-08-26"], Decimal("-64.4870"))

    def test_historical_account_day_subtracts_nontrading_cash(self):
        connection = sqlite3.connect(self.database)
        connection.executemany(
            "INSERT INTO execution_risk_day_baselines VALUES (?,?,?,?,?,?)",
            [
                ("2026-08-23", "500", "0", "500",
                 "2026-08-23T04:00:01+00:00", "fresh_et_day"),
                ("2026-08-24", "520", "0", "520",
                 "2026-08-24T04:00:01+00:00", "fresh_et_day"),
            ],
        )
        connection.commit()
        connection.close()
        changes = (NontradingCashChange(
            datetime(2026, 8, 23, 12, tzinfo=timezone.utc), Decimal("30")),)

        result = historical_account_trading_by_day(
            str(self.database), [date(2026, 8, 23)], changes)

        self.assertEqual(result["2026-08-23"], Decimal("-10"))

    def test_midnight_cash_is_matched_to_actual_snapshot_interval(self):
        # No trading: a deposit between midnight and the delayed opening
        # belongs to the preceding snapshot interval, never a $100 loss.
        with sqlite3.connect(self.database) as connection:
            connection.executemany(
                "INSERT INTO execution_risk_day_baselines VALUES (?,?,?,?,?,?)",
                [("2026-09-05", "1000", "0", "1000",
                  "2026-09-05T04:00:00+00:00", "fresh_et_day"),
                 ("2026-09-06", "1100", "0", "1100",
                  "2026-09-06T04:01:00+00:00", "fresh_et_day"),
                 ("2026-09-07", "1100", "0", "1100",
                  "2026-09-07T04:00:00+00:00", "fresh_et_day")])
        days = [date(2026, 9, 5), date(2026, 9, 6)]
        for second in (30, 60):
            with self.subTest(second=second):
                change = NontradingCashChange(
                    datetime(2026, 9, 6, 4, tzinfo=timezone.utc)
                    + timedelta(seconds=second), Decimal(100))
                history = historical_account_trading_by_day(
                    str(self.database), days, (change,))
                self.assertEqual(history, {"2026-09-05": Decimal(0),
                                           "2026-09-06": Decimal(0)})
                covered = historical_account_trading_by_day(
                    str(self.database), days, (change,),
                    through=datetime(2026, 9, 7, 3, 59, tzinfo=timezone.utc))
                self.assertEqual(covered, {"2026-09-05": Decimal(0)})
                payload = build_dashboard_snapshot(
                    days, pnl={}, paid={"2026-09-05": Decimal(5)}, pending={},
                    resting_hours={}, today=date(2026, 9, 7),
                    generated_at=datetime(2026, 9, 7, 5, tzinfo=timezone.utc),
                    unbalanced_markets=0, overall_trading=Decimal(0),
                    account_history=history)
                row = payload["rows"][0]
                self.assertTrue(row["trading_estimate"])
                self.assertIsNone(row["net"])
                self.assertEqual(row["trading_interval"][1],
                                 "2026-09-06T04:01:00+00:00")
                rendered = render_table(
                    days, pnl={}, paid={}, pending={}, resting_hours={},
                    elapsed_hours={}, today=date(2026, 9, 7), account_history=history)
                self.assertIn("~$+0.00", rendered)
                self.assertIn("not an exact calendar day", rendered)

    def test_september_six_restart_snapshot_is_labeled_without_double_counting(self):
        with sqlite3.connect(self.database) as connection:
            connection.executemany(
                "INSERT INTO execution_risk_day_baselines VALUES (?,?,?,?,?,?)",
                [("2026-09-06", "1090.8613", "0", "1090.8613",
                  "2026-09-06T04:01:46.645731+00:00", "fresh_et_day"),
                 ("2026-09-07", "1086.6591", "0", "1086.6591",
                  "2026-09-07T04:07:54.512370+00:00", "fresh_et_day"),
                 ("2026-09-08", "1081.6591", "0", "1081.6591",
                  "2026-09-08T04:00:00+00:00", "fresh_et_day")])
        days = [date(2026, 9, 6), date(2026, 9, 7)]
        # Both a normal credit and a credit inside the restart gap use the
        # same saved boundary. An opposite withdrawal makes net cash $100.
        changes = (
            NontradingCashChange(datetime(2026, 9, 6, 12, tzinfo=timezone.utc), Decimal(100)),
            NontradingCashChange(datetime(2026, 9, 7, 4, 5, tzinfo=timezone.utc), Decimal(20)),
            NontradingCashChange(datetime(2026, 9, 7, 4, 6, tzinfo=timezone.utc), Decimal(-20)))
        history = historical_account_trading_by_day(str(self.database), days, changes)
        self.assertEqual(history, {"2026-09-06": Decimal("-104.2022"),
                                   "2026-09-07": Decimal("-5")})
        self.assertEqual(history.intervals["2026-09-06"][1],
                         history.intervals["2026-09-07"][0])
        kwargs = dict(pnl={}, paid={"2026-09-06": Decimal(50)}, pending={},
                      resting_hours={}, today=date(2026, 9, 8), account_history=history)
        payload = build_discord_payload(
            days, elapsed_hours={}, generated_at=datetime(2026, 9, 8, 5, tzinfo=timezone.utc),
            unbalanced_markets=0, **kwargs)
        self.assertIn("~$-104.20", payload["embeds"][0]["fields"][0]["value"])
        embed = payload["embeds"][0]
        self.assertEqual(len(embed["fields"]), 2)
        self.assertIn("~ estimated interval", embed["footer"]["text"])
        self.assertIn("Trading total (estimated)", embed["description"])
        snapshot = build_dashboard_snapshot(
            days, generated_at=datetime(2026, 9, 8, 5, tzinfo=timezone.utc),
            unbalanced_markets=0, overall_trading=Decimal(0), **kwargs)
        self.assertTrue(snapshot["rows"][0]["trading_estimate"])
        self.assertIsNone(snapshot["rows"][0]["net"])

    def test_near_midnight_window_is_bounded_and_dst_aware(self):
        # Fall-back day lasts 25 hours; boundaries are local midnight, not
        # UTC midnight or a fixed 24-hour increment.
        day = date(2026, 11, 1)
        for delay, accepted in ((0, True), (900, True), (900.000001, False), (-1, False)):
            with self.subTest(delay=delay):
                close = datetime(2026, 11, 2, 5, tzinfo=timezone.utc) + timedelta(seconds=delay)
                with sqlite3.connect(self.database) as connection:
                    connection.execute("DELETE FROM execution_risk_day_baselines")
                    connection.executemany(
                        "INSERT INTO execution_risk_day_baselines VALUES (?,?,?,?,?,?)",
                        [("2026-11-01", "100", "0", "100", "2026-11-01T04:00:00+00:00", "fresh_et_day"),
                         ("2026-11-02", "90", "0", "90", close.isoformat(), "fresh_et_day")])
                history = historical_account_trading_by_day(str(self.database), [day], ())
                self.assertEqual(history, {day.isoformat(): Decimal(-10)} if accepted else {})

    def test_boundary_beyond_fifteen_minutes_cannot_create_account_history(self):
        connection = sqlite3.connect(self.database)
        connection.executemany(
            "INSERT INTO execution_risk_day_baselines VALUES (?,?,?,?,?,?)",
            [
                ("2026-08-23", "500", "0", "500",
                 "2026-08-23T04:00:01+00:00", "fresh_et_day"),
                ("2026-08-24", "490", "0", "490",
                 "2026-08-24T04:15:00.000001+00:00", "fresh_et_day"),
            ],
        )
        connection.commit()
        connection.close()

        result = historical_account_trading_by_day(
            str(self.database), [date(2026, 8, 23)], ())

        self.assertEqual(result, {})

    def test_carried_position_is_included_in_marked_account_history(self):
        connection = sqlite3.connect(self.database)
        connection.executemany(
            "INSERT INTO execution_risk_day_baselines VALUES (?,?,?,?,?,?)",
            [
                ("2026-08-23", "495", "0", "500",
                 "2026-08-23T04:00:01+00:00", "fresh_et_day"),
                ("2026-08-24", "490", "0", "490",
                 "2026-08-24T04:00:01+00:00", "fresh_et_day"),
            ],
        )
        connection.commit()
        connection.close()

        result = historical_account_trading_by_day(
            str(self.database), [date(2026, 8, 23)], ())

        self.assertEqual(result, {"2026-08-23": Decimal("-5")})

    def test_september_three_full_loss_and_exact_delayed_boundary(self):
        with sqlite3.connect(self.database) as connection:
            connection.executemany(
                "INSERT INTO execution_risk_day_baselines VALUES (?,?,?,?,?,?)",
                [("2026-09-03", "913.1138", "0", "896.8571",
                  "2026-09-03T04:00:08.760412+00:00", "fresh_et_day"),
                 ("2026-09-04", "792.7224", "0", "792.7224",
                  "2026-09-04T04:07:34.628633+00:00", "fresh_et_day")])
        days = [date(2026, 9, 3)]
        self.assertEqual(historical_account_trading_by_day(
            str(self.database), days, ()), {"2026-09-03": Decimal("-120.3914")})
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "UPDATE execution_risk_day_baselines SET opening_equity='793' "
                "WHERE risk_date='2026-09-04'")
        self.assertEqual(historical_account_trading_by_day(
            str(self.database), days, ()), {})

    def test_deposits_withdrawals_and_rewards_never_change_trading_loss(self):
        day = date(2026, 8, 23)
        for cash_flow in (Decimal("100"), Decimal("-400"), Decimal("25")):
            with self.subTest(cash_flow=cash_flow):
                with sqlite3.connect(self.database) as connection:
                    connection.execute("DELETE FROM execution_risk_day_baselines")
                    connection.executemany(
                        "INSERT INTO execution_risk_day_baselines VALUES (?,?,?,?,?,?)",
                        [("2026-08-23", "1000", "0", "990",
                          "2026-08-23T04:00:01+00:00", "fresh_et_day"),
                         ("2026-08-24", str(Decimal("880") + cash_flow), "0",
                          str(Decimal("880") + cash_flow),
                          "2026-08-24T04:00:01+00:00", "fresh_et_day")])
                changes = (NontradingCashChange(
                    datetime(2026, 8, 23, 12, tzinfo=timezone.utc), cash_flow),)
                self.assertEqual(historical_account_trading_by_day(
                    str(self.database), [day], changes),
                    {day.isoformat(): Decimal("-120")})

    def test_missing_history_never_uses_pair_loss_or_finalizes_net(self):
        days = [date(2026, 9, 3), date(2026, 9, 4)]
        kwargs = dict(
            pnl={"2026-09-03": Decimal("-66.9033")},
            paid={"2026-09-03": Decimal("85.76")}, pending={},
            resting_hours={}, today=days[1], account_history={},
            account_realized=AccountRealizedToday(Decimal("-10"), "ok"))
        output = render_table(days, elapsed_hours={}, **kwargs)
        self.assertIn("| 2026-09-03 | Unavailable", output)
        self.assertIn("Unavailable (1/2 days proven)", output)
        payload = build_discord_payload(
            days, elapsed_hours={}, generated_at=datetime(2026, 9, 4, 12,
            tzinfo=timezone.utc), unbalanced_markets=136, **kwargs)
        self.assertIn("unavail", payload["embeds"][0]["fields"][0]["value"])
        self.assertNotIn("$-66.90", payload["embeds"][0]["fields"][0]["value"])
        snapshot = build_dashboard_snapshot(
            days, generated_at=datetime(2026, 9, 4, 12, tzinfo=timezone.utc),
            unbalanced_markets=136, overall_trading=Decimal("-100"), **kwargs)
        self.assertIsNone(snapshot["rows"][0]["trading"])
        self.assertIsNone(snapshot["rows"][0]["net"])
        self.assertNotEqual(snapshot["rows"][0]["net_status"], "finalized")

    def test_hourly_wrapper_is_bounded_locked_and_lower_priority(self):
        report = (
            Path(__file__).resolve().parents[1]
            / "scripts" / "reward_table.py"
        ).read_text(encoding="utf-8")
        wrapper = (
            Path(__file__).resolve().parents[1]
            / "scripts" / "post_hourly_reward_table.sh"
        ).read_text(encoding="utf-8")

        self.assertIn("flock -n", wrapper)
        self.assertIn("timeout 45m", wrapper)
        self.assertIn("nice -n 10", wrapper)
        self.assertIn("--days 7 --post-discord --quiet", wrapper)
        self.assertIn("--dashboard-cache data/tv-gain-snapshot.json", wrapper)
        self.assertNotIn("discord.com/api/webhooks", wrapper)
        self.assertIn("require_rewards_webhook=True", report)
        self.assertIn(
            "ACCOUNT_REPORT_RATE_PER_SECOND = "
            "OPPORTUNISTIC_REPORT_RATE_PER_SECOND",
            report,
        )
        self.assertIn('default_traffic_class="report"', report)

    def test_nontrading_reports_use_the_opportunistic_lane(self):
        root = Path(__file__).resolve().parents[1]
        report_paths = (
            "scripts/accounting.py",
            "scripts/backfill_executions.py",
            "scripts/daily_pnl.py",
            "scripts/dashboard.py",
            "scripts/period_mix.py",
            "scripts/resting_by_kickoff.py",
            "scripts/reward_table.py",
            "scripts/rewards_report.py",
            "scripts/usable_markets.py",
            "src/liquidity_bot/reward_census.py",
            "src/liquidity_bot/scalp_run.py",
            "src/liquidity_bot/shadow_run.py",
        )
        for relative in report_paths:
            with self.subTest(path=relative):
                text = (root / relative).read_text(encoding="utf-8")
                self.assertIn('default_traffic_class="report"', text)


class _Account:
    def __init__(self, balances):
        self.balances_left = list(balances)

    async def balances(self):
        value = self.balances_left.pop(0)
        return {"balances": [{
            "currency": "USD", "currentBalance": str(value),
            "assetNotional": "0", "buyingPower": str(value),
        }]}


class _Portfolio:
    def __init__(self, positions, activity_pages):
        self.position_rows = positions
        self.activity_pages = list(activity_pages)
        self.activity_params = []

    async def positions(self, _params):
        return {"positions": self.position_rows, "eof": True}

    async def activities(self, params):
        self.activity_params.append(params)
        return self.activity_pages.pop(0)


class _Client:
    def __init__(self, balances, positions, activity_pages):
        self.account = _Account(balances)
        self.portfolio = _Portfolio(positions, activity_pages)


class _Gateway:
    def __init__(self, balances, positions, activity_pages):
        self.max_pages = 4
        self.client = _Client(balances, positions, activity_pages)

    async def _call(self, _operation, call):
        return await call()


class AccountRealizedTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        folder = Path(tempfile.mkdtemp(prefix="liquidity-account-realized-"))
        self.database = folder / "ledger.db"
        connection = sqlite3.connect(self.database)
        connection.execute(
            "CREATE TABLE execution_risk_day_baselines ("
            "risk_date TEXT PRIMARY KEY, opening_equity TEXT NOT NULL, "
            "opening_realized TEXT NOT NULL, opening_cash TEXT, "
            "created_at TEXT NOT NULL, source TEXT NOT NULL)")
        connection.execute(
            "INSERT INTO execution_risk_day_baselines VALUES (?,?,?,?,?,?)",
            ("2026-08-25", "500", "0", "500",
             "2026-08-25T04:00:02+00:00", "fresh_et_day"))
        connection.commit()
        connection.close()

    async def test_realized_subtracts_reward_cash_and_adds_open_basis(self):
        gateway = _Gateway(
            [Decimal("480"), Decimal("480")],
            {"market-one": {
                "netPositionDecimal": "10",
                "cost": {"value": "5", "currency": "USD"},
                "cashValue": {"value": "4", "currency": "USD"},
                "updateTime": "2026-08-25T16:00:00Z",
            }},
            [{"activities": [{
                "type": "ACTIVITY_TYPE_TRANSFER",
                "accountBalanceChange": {
                    "status": "COMPLETED",
                    "amount": {"value": "10", "currency": "USD"},
                    "description": "liquidity reward payout",
                    "updateTime": "2026-08-25T12:00:00Z",
                    # This is a payment-attempt detail for the same balance
                    # change, not a second $10 economic event.
                    "transactions": [{
                        "status": "COMPLETED",
                        "amount": {"value": "10", "currency": "USD"},
                        "description": "liquidity reward payout",
                        "updateTime": "2026-08-25T12:00:00Z",
                    }],
                },
            }], "eof": True}])

        with patch("scripts.reward_table.datetime", _August25Datetime):
            result = await account_realized_today(
                str(self.database), gateway,
                now=datetime(2026, 8, 25, 17, tzinfo=timezone.utc))

        self.assertEqual(result.value, Decimal("-25"))
        self.assertEqual(result.open_unrealized, Decimal("-1"))
        self.assertEqual(result.marked_value, Decimal("-26"))
        self.assertEqual(result.position_basis, Decimal("5"))
        self.assertEqual(result.nontrading_cash, Decimal("10"))
        params = gateway.client.portfolio.activity_params[0]
        self.assertEqual(params["sortOrder"], "SORT_ORDER_DESCENDING")
        self.assertEqual(len(params["types"]), 5)

    async def test_today_does_not_subtract_cash_already_in_delayed_opening(self):
        gateway = _Gateway([Decimal(500), Decimal(500)], {}, [{
            "activities": [{"type": "ACTIVITY_TYPE_TRANSFER",
                "accountBalanceChange": {"status": "COMPLETED",
                    "amount": {"value": "100", "currency": "USD"},
                    "description": "liquidity reward payout",
                    "updateTime": "2026-08-25T04:00:01Z"}}], "eof": True}])
        with patch("scripts.reward_table.datetime", _August25Datetime):
            result = await account_realized_today(
                str(self.database), gateway,
                now=datetime(2026, 8, 25, 17, tzinfo=timezone.utc))
        self.assertEqual(result.marked_value, Decimal(0))
        self.assertEqual(result.nontrading_cash, Decimal(0))
        self.assertEqual(result.opening_as_of,
                         datetime(2026, 8, 25, 4, 0, 2, tzinfo=timezone.utc))

    async def test_today_uses_restart_opening_without_subtracting_gap_credit(self):
        with sqlite3.connect(self.database) as connection:
            connection.execute("UPDATE execution_risk_day_baselines SET created_at=?",
                               ("2026-08-25T04:07:54+00:00",))
        gateway = _Gateway([Decimal(500), Decimal(500)], {}, [{
            "activities": [{"type": "ACTIVITY_TYPE_TRANSFER",
                "accountBalanceChange": {"status": "COMPLETED",
                    "amount": {"value": "100", "currency": "USD"},
                    "description": "liquidity reward payout",
                    "updateTime": "2026-08-25T04:05:00Z"}}], "eof": True}])
        with patch("scripts.reward_table.datetime", _August25Datetime):
            result = await account_realized_today(
                str(self.database), gateway,
                now=datetime(2026, 8, 25, 17, tzinfo=timezone.utc))
        self.assertEqual(result.marked_value, Decimal(0))
        self.assertEqual(result.nontrading_cash, Decimal(0))
        self.assertEqual(result.opening_as_of,
                         datetime(2026, 8, 25, 4, 7, 54, tzinfo=timezone.utc))

    async def test_audited_legacy_cash_uses_its_et_correction(self):
        connection = sqlite3.connect(self.database)
        connection.execute(
            "UPDATE execution_risk_day_baselines SET opening_cash=?, "
            "created_at=?, source=? WHERE risk_date=?",
            ("478.6998", "2026-08-25T00:00:01+00:00",
             "legacy_utc_cutover", "2026-08-25"))
        connection.commit()
        connection.close()

        value = _opening_cash_for_day(
            str(self.database), date(2026, 8, 25),
            datetime(2026, 8, 25, 4, tzinfo=timezone.utc))

        self.assertEqual(value, Decimal("466.0899"))

    async def test_audited_delayed_flat_baseline_is_accepted_for_today(self):
        connection = sqlite3.connect(self.database)
        connection.execute(
            "UPDATE execution_risk_day_baselines SET risk_date=?, "
            "opening_equity=?, opening_cash=?, created_at=?, source=?",
            ("2026-08-27", "708.3008", "708.3008",
             "2026-08-27T04:04:10.544588+00:00", "fresh_et_day"),
        )
        connection.commit()
        connection.close()

        value = _opening_cash_for_day(
            str(self.database), date(2026, 8, 27),
            datetime(2026, 8, 27, 4, tzinfo=timezone.utc))

        self.assertEqual(value, Decimal("708.3008"))

    async def test_realized_fails_closed_if_cash_moves_during_snapshot(self):
        gateway = _Gateway(
            [Decimal("480"), Decimal("479")], {},
            [{"activities": [], "eof": True}])

        with patch("scripts.reward_table.datetime", _August25Datetime):
            result = await account_realized_today(
                str(self.database), gateway,
                now=datetime(2026, 8, 25, 17, tzinfo=timezone.utc))

        self.assertIsNone(result.value)
        self.assertIn("cash changed", result.reason)
        self.assertEqual(len(gateway.client.portfolio.activity_params), 1)

    async def test_cash_scan_refuses_a_window_larger_than_its_page_bound(self):
        fresh = {
            "type": "ACTIVITY_TYPE_TRANSFER",
            "accountBalanceChange": {
                "status": "PENDING",
                "amount": {"value": "1", "currency": "USD"},
                "description": "liquidity reward payout",
                "updateTime": "2026-08-25T12:00:00Z",
            },
        }
        pages = [
            {"activities": [fresh], "eof": False, "nextCursor": f"page-{n}"}
            for n in range(4)
        ]
        gateway = _Gateway([], {}, pages)

        with self.assertRaisesRegex(
                AccountMetricUnavailable, "did not reach the ET boundary"):
            await _nontrading_cash_today(
                gateway,
                boundary=datetime(2026, 8, 25, 4, tzinfo=timezone.utc),
                through=datetime(2026, 8, 25, 17, tzinfo=timezone.utc))

    async def test_unknown_completed_transfer_is_not_silently_counted(self):
        gateway = _Gateway([], {}, [{"activities": [{
            "type": "ACTIVITY_TYPE_TRANSFER",
            "accountBalanceChange": {
                "status": "COMPLETED",
                "amount": {"value": "3", "currency": "USD"},
                "description": "mystery adjustment",
                "updateTime": "2026-08-25T12:00:00Z",
            },
        }], "eof": True}])

        with self.assertRaisesRegex(
                AccountMetricUnavailable, "unclassified completed"):
            await _nontrading_cash_today(
                gateway,
                boundary=datetime(2026, 8, 25, 4, tzinfo=timezone.utc),
                through=datetime(2026, 8, 25, 17, tzinfo=timezone.utc))

    async def test_completed_transfer_after_balance_makes_snapshot_unavailable(self):
        gateway = _Gateway([], {}, [{"activities": [{
            "type": "ACTIVITY_TYPE_TRANSFER",
            "accountBalanceChange": {
                "status": "COMPLETED",
                "amount": {"value": "4", "currency": "USD"},
                "description": "liquidity reward payout",
                "updateTime": "2026-08-25T17:00:01Z",
            },
        }], "eof": True}])

        with self.assertRaisesRegex(
                AccountMetricUnavailable, "changed after the account snapshot"):
            await _nontrading_cash_today(
                gateway,
                boundary=datetime(2026, 8, 25, 4, tzinfo=timezone.utc),
                through=datetime(2026, 8, 25, 17, tzinfo=timezone.utc))

    async def test_historical_cash_ignores_later_completion_and_reaches_eof(self):
        def transfer(amount, updated):
            return {
                "type": "ACTIVITY_TYPE_TRANSFER",
                "accountBalanceChange": {
                    "status": "COMPLETED",
                    "amount": {"value": str(amount), "currency": "USD"},
                    "description": "liquidity reward payout",
                    "updateTime": updated,
                },
            }

        gateway = _Gateway([], {}, [
            {"activities": [
                transfer("9", "2026-08-28T12:00:00Z"),
                transfer("4", "2026-08-25T12:00:00Z"),
            ], "eof": False, "nextCursor": "older"},
            {"activities": [], "eof": True},
        ])

        result = await _historical_nontrading_cash_changes(
            gateway,
            boundary=datetime(2026, 8, 22, 4, tzinfo=timezone.utc),
            through=datetime(2026, 8, 28, 4, tzinfo=timezone.utc),
        )

        self.assertEqual(
            result,
            (NontradingCashChange(
                datetime(2026, 8, 25, 12, tzinfo=timezone.utc),
                Decimal("4")),),
        )
        self.assertEqual(
            gateway.client.portfolio.activity_params[1]["cursor"], "older")

        connection = sqlite3.connect(self.database)
        connection.executemany(
            "INSERT INTO execution_risk_day_baselines VALUES (?,?,?,?,?,?)",
            [
                ("2026-08-26", "772.7878", "0", "772.7878",
                 "2026-08-26T04:00:14.133861+00:00", "fresh_et_day"),
                ("2026-08-27", "708.3008", "0", "708.3008",
                 "2026-08-27T04:04:10.544588+00:00", "fresh_et_day"),
            ],
        )
        connection.commit()
        connection.close()
        history = historical_account_trading_by_day(
            str(self.database), [date(2026, 8, 26)], result)
        self.assertEqual(history["2026-08-26"], Decimal("-64.4870"))

    async def test_cash_scan_uses_completion_time_not_feed_order(self):
        def transfer(amount, created, updated):
            return {
                "type": "ACTIVITY_TYPE_TRANSFER",
                "accountBalanceChange": {
                    "status": "COMPLETED",
                    "amount": {"value": str(amount), "currency": "USD"},
                    "description": "liquidity reward payout",
                    "createTime": created,
                    "updateTime": updated,
                },
            }

        gateway = _Gateway([], {}, [{"activities": [
            transfer("2", "2026-08-25T10:00:00Z", "2026-08-25T11:00:00Z"),
            # Created earlier but completed later. The live endpoint orders on
            # createTime, so updateTime is intentionally increasing here.
            transfer("3", "2026-08-25T09:00:00Z", "2026-08-25T12:00:00Z"),
            transfer("7", "2026-08-24T09:00:00Z", "2026-08-24T12:00:00Z"),
        ], "eof": True}])

        result = await _nontrading_cash_today(
            gateway,
            boundary=datetime(2026, 8, 25, 4, tzinfo=timezone.utc),
            through=datetime(2026, 8, 25, 17, tzinfo=timezone.utc))

        self.assertEqual(result, Decimal("5"))

    async def test_cash_scan_excludes_activity_at_opening_balance_boundary(self):
        """Opening cash already includes activity on the same clock tick."""

        boundary = datetime(2026, 8, 25, 4, tzinfo=timezone.utc)
        gateway = _Gateway([], {}, [{"activities": [{
            "type": "ACTIVITY_TYPE_TRANSFER",
            "accountBalanceChange": {
                "status": "COMPLETED",
                "amount": {"value": "10", "currency": "USD"},
                "description": "liquidity reward payout",
                "updateTime": boundary.isoformat(),
            },
        }], "eof": True}])

        result = await _nontrading_cash_today(
            gateway, boundary=boundary, through=boundary)

        self.assertEqual(result, Decimal("0"))


if __name__ == "__main__":
    unittest.main()
