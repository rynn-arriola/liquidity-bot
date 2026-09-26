"""Regression tests for the September 26 reporting audit fixes."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from scripts.reward_table import (
    AccountRealizedToday, account_realized_from_live_cache,
    build_dashboard_snapshot, build_discord_payload,
    historical_account_trading_by_day, render_table, reward_posting_days,
    settled_trading_by_day, settlement_total)
from liquidity_bot.settlement_activity import (
    Resolution, ResolutionHistory, SettlementUnavailable, fetch_resolutions,
    load_cache, parse_resolution, refresh_resolutions, write_cache)

UTC = timezone.utc


def resolution_activity(slug, *, net="10", long_price="1",
                        at="2026-09-20T18:00:00.123456789Z", long_sides=1):
    sides = [{"long": True, "price": long_price}] * long_sides + [
        {"long": False, "price": "0"}]
    return {
        "type": "ACTIVITY_TYPE_POSITION_RESOLUTION",
        "positionResolution": {
            "marketSlug": slug,
            "beforePosition": {"netPosition": net},
            "afterPosition": {"netPosition": "0"},
            "updateTime": at,
            "market": {"marketSides": sides},
        },
    }


class FakeGateway:
    def __init__(self, pages, fail=False):
        self.pages = list(pages)
        self.calls = []
        self.fail = fail
        self.client = SimpleNamespace(portfolio=SimpleNamespace(
            activities=self._activities))

    def _activities(self, params):
        self.calls.append(dict(params))
        if self.fail:
            raise RuntimeError("HTTP 429")
        return self.pages.pop(0)

    async def _call(self, name, operation):
        return operation()


class LedgerCase(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(prefix="reporting-fixes-"))
        self.db = str(self.folder / "ledger.db")
        with sqlite3.connect(self.db) as connection:
            connection.execute(
                "CREATE TABLE execution_fills ("
                "execution_id TEXT PRIMARY KEY, market_slug TEXT, price TEXT, "
                "qty TEXT, fee TEXT, intent TEXT, event_at TEXT, "
                "received_at TEXT, reversed INTEGER)")
            connection.execute(
                "CREATE TABLE execution_risk_day_baselines ("
                "risk_date TEXT PRIMARY KEY, opening_equity TEXT NOT NULL, "
                "opening_realized TEXT NOT NULL, opening_cash TEXT, "
                "created_at TEXT NOT NULL, source TEXT NOT NULL)")

    def fills(self, rows):
        with sqlite3.connect(self.db) as connection:
            connection.executemany(
                "INSERT INTO execution_fills VALUES (?,?,?,?,?,?,?,?,?)",
                [(str(index), slug, price, qty, fee, intent, at, at, 0)
                 for index, (slug, price, qty, fee, intent, at)
                 in enumerate(rows)])

    def baselines(self, rows):
        with sqlite3.connect(self.db) as connection:
            connection.executemany(
                "INSERT INTO execution_risk_day_baselines VALUES (?,?,?,?,?,?)",
                [(day, equity, "0", equity, created, "fresh_et_day")
                 for day, equity, created in rows])


class SettlementReplayTests(LedgerCase):
    def test_residual_inventory_settles_at_resolution_price(self):
        self.fills([
            # Long 10 at 0.40 (fee 0.10), never closed by a fill: wins.
            ("win", "0.40", "10", "0.10", "ORDER_INTENT_BUY_LONG",
             "2026-09-20T15:00:00+00:00"),
            # Short 5 at long-price 0.70 (costs 0.30): long side wins, loses.
            ("lose", "0.70", "5", "0", "ORDER_INTENT_BUY_SHORT",
             "2026-09-20T15:00:00+00:00"),
            # Balanced pair: already closed by fills, settlement adds nothing.
            ("flat", "0.40", "10", "0", "ORDER_INTENT_BUY_LONG",
             "2026-09-20T15:00:00+00:00"),
            ("flat", "0.50", "10", "0", "ORDER_INTENT_BUY_SHORT",
             "2026-09-20T15:10:00+00:00"),
        ])
        resolved = datetime(2026, 9, 21, 3, tzinfo=UTC)  # 09-20 23:00 ET
        resolutions = (
            Resolution("win", resolved, Decimal("1"), Decimal("10")),
            Resolution("lose", resolved, Decimal("1"), Decimal("-5")),
            Resolution("flat", resolved, Decimal("1"), Decimal("10")),
            Resolution("never-traded", resolved, Decimal("1"), Decimal("3")),
        )
        days = [date(2026, 9, 20)]
        fills_only = settled_trading_by_day(self.db, days)
        with_settlements = settled_trading_by_day(
            self.db, days, resolutions=resolutions)
        # fills: -0.10 fee + flat pair 10*(1-0.40-0.50)=+1.00
        self.assertEqual(fills_only["2026-09-20"], Decimal("0.90"))
        # + win 10*(1-0.40)=+6.00, + lose 5*((1-1)-0.30)=-1.50
        self.assertEqual(with_settlements["2026-09-20"], Decimal("5.40"))
        self.assertEqual(settlement_total(self.db, resolutions), Decimal("4.50"))

    def test_settlement_is_booked_on_its_resolution_et_day(self):
        self.fills([("m", "0.40", "10", "0", "ORDER_INTENT_BUY_LONG",
                     "2026-09-20T15:00:00+00:00")])
        resolutions = (Resolution(
            "m", datetime(2026, 9, 21, 4, 30, tzinfo=UTC), Decimal("0"),
            Decimal("10")),)
        result = settled_trading_by_day(
            self.db, [date(2026, 9, 20), date(2026, 9, 21)],
            resolutions=resolutions)
        self.assertEqual(result, {"2026-09-20": Decimal("0"),
                                  "2026-09-21": Decimal("-4.00")})

    def test_resolution_before_the_fills_does_not_settle_later_inventory(self):
        self.fills([("m", "0.40", "10", "0", "ORDER_INTENT_BUY_LONG",
                     "2026-09-20T15:00:00+00:00")])
        early = (Resolution("m", datetime(2026, 9, 19, tzinfo=UTC),
                            Decimal("1"), Decimal("10")),)
        self.assertEqual(settlement_total(self.db, early), Decimal(0))

    def test_uncovered_days_are_unavailable_not_zero(self):
        self.fills([("m", "0.40", "10", "0", "ORDER_INTENT_BUY_LONG",
                     "2026-09-20T15:00:00+00:00")])
        days = [date(2026, 9, 20), date(2026, 9, 21)]
        result = settled_trading_by_day(
            self.db, days, resolutions=(),
            complete_through=datetime(2026, 9, 21, 12, tzinfo=UTC))
        self.assertEqual(result["2026-09-20"], Decimal(0))
        self.assertIsNone(result["2026-09-21"])
        payload = build_discord_payload(
            days, pnl={}, paid={}, pending={}, resting_hours={},
            elapsed_hours={}, today=days[-1],
            generated_at=datetime(2026, 9, 21, 12, tzinfo=UTC),
            unbalanced_markets=0, settled_history=result)
        self.assertIn("**P&L:** —", payload["embeds"][0]["description"])
        self.assertIn("closed fills + settlements",
                      payload["embeds"][0]["footer"]["text"])


class ResolutionParsingTests(unittest.TestCase):
    def test_parse_keeps_microseconds_and_skips_unheld(self):
        parsed = parse_resolution(resolution_activity("m", net="-5.5",
                                                      long_price="0.5"))
        self.assertEqual(parsed.resolved_at,
                         datetime(2026, 9, 20, 18, 0, 0, 123456, tzinfo=UTC))
        self.assertEqual(parsed.long_price, Decimal("0.5"))
        self.assertEqual(parsed.venue_net_before, Decimal("-5.5"))
        self.assertIsNone(parse_resolution(resolution_activity("m", net="0")))

    def test_parse_fails_closed_on_ambiguous_or_invalid_price(self):
        with self.assertRaises(SettlementUnavailable):
            parse_resolution(resolution_activity("m", long_sides=2))
        with self.assertRaises(SettlementUnavailable):
            parse_resolution(resolution_activity("m", long_price="1.5"))
        with self.assertRaises(SettlementUnavailable):
            parse_resolution({"type": "ACTIVITY_TYPE_TRADE"})


class ResolutionFetchTests(unittest.TestCase):
    def test_incremental_scan_stops_once_older_than_the_boundary(self):
        gateway = FakeGateway([
            {"activities": [resolution_activity(
                "new", at="2026-09-25T10:00:00Z"),
                resolution_activity("old", at="2026-09-20T10:00:00Z")],
             "eof": False, "nextCursor": "c1"},
        ])
        found = asyncio.run(fetch_resolutions(
            gateway, stop_before=datetime(2026, 9, 22, tzinfo=UTC),
            max_pages=3))
        self.assertEqual([item.market_slug for item in found], ["new", "old"])
        self.assertEqual(len(gateway.calls), 1)
        self.assertEqual(gateway.calls[0]["types"],
                         ["ACTIVITY_TYPE_POSITION_RESOLUTION"])

    def test_page_bound_without_boundary_or_eof_fails_closed(self):
        page = {"activities": [resolution_activity("m")],
                "eof": False, "nextCursor": "again"}
        gateway = FakeGateway([page, dict(page, nextCursor="next")])
        with self.assertRaises(SettlementUnavailable):
            asyncio.run(fetch_resolutions(gateway, stop_before=None,
                                          max_pages=2))

    def test_refresh_merges_caches_and_degrades_on_read_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            cache = Path(folder) / "settlements.json"
            first = asyncio.run(refresh_resolutions(
                FakeGateway([{"activities": [resolution_activity(
                    "a", at="2026-09-20T10:00:00Z")], "eof": True}]),
                cache, now=datetime(2026, 9, 21, tzinfo=UTC)))
            self.assertIsNone(first.error)
            self.assertEqual(len(first.resolutions), 1)
            second_gateway = FakeGateway([{"activities": [
                resolution_activity("b", at="2026-09-22T10:00:00Z"),
                resolution_activity("a", at="2026-09-20T10:00:00Z")],
                "eof": True}])
            second = asyncio.run(refresh_resolutions(
                second_gateway, cache,
                now=datetime(2026, 9, 23, tzinfo=UTC)))
            self.assertEqual([item.market_slug for item in second.resolutions],
                             ["a", "b"])
            self.assertEqual(second_gateway.calls[0]["limit"], 100)
            failed = asyncio.run(refresh_resolutions(
                FakeGateway([], fail=True), cache,
                now=datetime(2026, 9, 24, tzinfo=UTC)))
            self.assertIn("429", failed.error)
            self.assertEqual(failed.complete_through,
                             datetime(2026, 9, 23, tzinfo=UTC))
            self.assertEqual(len(failed.resolutions), 2)

    def test_missing_or_corrupt_cache_has_no_history(self):
        with tempfile.TemporaryDirectory() as folder:
            cache = Path(folder) / "settlements.json"
            self.assertIsNone(load_cache(cache).complete_through)
            cache.write_text("{not json", encoding="utf-8")
            self.assertIsNone(load_cache(cache).complete_through)
            history = ResolutionHistory(
                (Resolution("m", datetime(2026, 9, 1, tzinfo=UTC),
                            Decimal("0.5"), Decimal("2")),),
                datetime(2026, 9, 2, tzinfo=UTC))
            write_cache(cache, history)
            self.assertEqual(load_cache(cache), history)


class CalendarToleranceTests(LedgerCase):
    def history(self, closing_created):
        self.baselines([
            ("2026-09-20", "1000", "2026-09-20T04:00:05.1+00:00"),
            ("2026-09-21", "990", closing_created),
        ])
        return historical_account_trading_by_day(
            self.db, [date(2026, 9, 20)], ())

    def snapshot(self, history):
        # Two newer paid days keep 09-20 out of the still-posting window.
        return build_dashboard_snapshot(
            [date(2026, 9, 20)], pnl={},
            paid={"2026-09-20": Decimal("30"), "2026-09-21": Decimal("1"),
                  "2026-09-22": Decimal("1")},
            pending={}, resting_hours={}, today=date(2026, 9, 23),
            generated_at=datetime(2026, 9, 23, 12, tzinfo=UTC),
            unbalanced_markets=0, overall_trading=Decimal(0),
            account_history=history)

    def test_snapshots_seconds_after_midnight_finalize_the_day(self):
        history = self.history("2026-09-21T04:00:59+00:00")
        row = self.snapshot(history)["rows"][0]
        self.assertFalse(row["trading_estimate"])
        self.assertEqual(row["net_status"], "finalized")
        self.assertEqual(Decimal(row["net"]), Decimal("20"))
        rendered = render_table(
            [date(2026, 9, 20)], pnl={},
            paid={"2026-09-20": Decimal("30"), "2026-09-21": Decimal("1"),
                  "2026-09-22": Decimal("1")},
            pending={}, resting_hours={}, elapsed_hours={},
            today=date(2026, 9, 23), account_history=history)
        self.assertIn("**+20.00**", rendered)

    def test_boundary_more_than_a_minute_late_stays_an_estimate(self):
        row = self.snapshot(self.history("2026-09-21T04:01:01+00:00"))["rows"][0]
        self.assertTrue(row["trading_estimate"])
        self.assertEqual(row["net_status"], "pending")


class RewardPostingTests(unittest.TestCase):
    PAID = {"2026-09-20": Decimal("100"), "2026-09-21": Decimal("90"),
            "2026-09-22": Decimal("80"), "2026-09-23": Decimal("40")}

    def test_newest_two_closed_paid_days_are_still_posting(self):
        self.assertEqual(
            reward_posting_days(self.PAID, today=date(2026, 9, 24)),
            {"2026-09-22", "2026-09-23"})
        self.assertEqual(
            reward_posting_days({"2026-09-24": Decimal(1)},
                                today=date(2026, 9, 24)), frozenset())

    def test_posting_days_never_finalize_and_are_labelled(self):
        days = [date(2026, 9, 20 + offset) for offset in range(5)]
        history = {day.isoformat(): Decimal("-10") for day in days[:-1]}
        kwargs = dict(pnl={}, paid=self.PAID, pending={}, resting_hours={},
                      today=days[-1], account_history=history,
                      settled_history={d.isoformat(): Decimal("-9") for d in days})
        table = render_table(days, elapsed_hours={}, **kwargs)
        self.assertIn("| 2026-09-21 | $-9.00 | $-10.00 | $90.00 | $0.00 | **+80.00**", table)
        self.assertIn("| 2026-09-22 | $-9.00 | $-10.00 | $80.00 | Still posting | Pending", table)
        self.assertIn("| 2026-09-23 | $-9.00 | $-10.00 | $40.00 | Still posting | Pending", table)
        self.assertIn("Finalized net subtotal (proven closed days only): **$+170.00**", table)
        snapshot = build_dashboard_snapshot(
            days, generated_at=datetime(2026, 9, 24, 12, tzinfo=UTC),
            unbalanced_markets=0, overall_trading=Decimal(0), **kwargs)
        statuses = [(row["gain_posting"], row["net_status"])
                    for row in snapshot["rows"]]
        self.assertEqual(statuses, [
            (False, "finalized"), (False, "finalized"), (True, "pending"),
            (True, "pending"), (False, "pending")])
        payload = build_discord_payload(
            days, elapsed_hours={}, unbalanced_markets=0,
            generated_at=datetime(2026, 9, 24, 12, tzinfo=UTC), **kwargs)
        table_text = payload["embeds"][0]["fields"][0]["value"]
        self.assertIn("9/21      -9.00   90.00    0.00", table_text)
        self.assertIn("9/23      -9.00   40.00 posting", table_text)
        self.assertIn("posting: payouts still arriving",
                      payload["embeds"][0]["footer"]["text"])
        from scripts.reward_image import render_reward_png
        self.assertTrue(render_reward_png(payload).startswith(b"\x89PNG"))

    def test_real_pending_amount_is_still_shown_on_a_posting_day(self):
        days = [date(2026, 9, 23), date(2026, 9, 24)]
        table = render_table(
            days, pnl={}, paid=self.PAID, pending={"2026-09-23": Decimal("5")},
            resting_hours={}, elapsed_hours={}, today=days[-1])
        self.assertIn("| $40.00 | $5.00 | Pending", table)


class LiveMarkReuseTests(LedgerCase):
    NOW = datetime(2026, 9, 25, 18, tzinfo=UTC)

    def setUp(self):
        super().setUp()
        self.baselines([("2026-09-25", "800", "2026-09-25T04:00:01+00:00")])
        self.cache = self.folder / "tv-live-pnl.json"

    def write(self, **overrides):
        payload = {
            "schema_version": 1, "observed_at": "2026-09-25T17:58:00Z",
            "risk_date_et": "2026-09-25", "marked_pnl": "-12.5",
            "realized_pnl": "-10", "unrealized_pnl": "-2.5",
            "nontrading_cash": "3", "scope": "ACCOUNT_WIDE_TRADING"}
        payload.update(overrides)
        self.cache.write_text(json.dumps(payload), encoding="utf-8")

    def test_fresh_runner_mark_is_reused(self):
        self.write()
        result = account_realized_from_live_cache(self.db, self.cache, now=self.NOW)
        self.assertIsInstance(result, AccountRealizedToday)
        self.assertEqual(result.marked_value, Decimal("-12.5"))
        self.assertEqual(result.value, Decimal("-10"))
        self.assertEqual(result.open_unrealized, Decimal("-2.5"))
        self.assertEqual(result.opening_as_of,
                         datetime(2026, 9, 25, 4, 0, 1, tzinfo=UTC))

    def test_marked_only_runner_mark_is_reused(self):
        self.write(realized_pnl=None, unrealized_pnl=None)
        result = account_realized_from_live_cache(self.db, self.cache, now=self.NOW)
        self.assertEqual(result.marked_value, Decimal("-12.5"))
        self.assertIsNone(result.value)

    def test_unusable_marks_fall_back_to_the_report_reads(self):
        cases = {
            "stale": dict(observed_at="2026-09-25T17:54:59Z"),
            "future": dict(observed_at="2026-09-25T18:01:00Z"),
            "other day": dict(risk_date_et="2026-09-24"),
            "unreconciled": dict(unrealized_pnl="-2"),
            "half breakdown": dict(realized_pnl=None),
            "wrong scope": dict(scope="BOT"),
            "bad number": dict(marked_pnl="NaN"),
        }
        for name, overrides in cases.items():
            with self.subTest(name):
                self.write(**overrides)
                self.assertIsNone(account_realized_from_live_cache(
                    self.db, self.cache, now=self.NOW))
        self.cache.unlink()
        self.assertIsNone(account_realized_from_live_cache(
            self.db, self.cache, now=self.NOW))

    def test_missing_opening_baseline_falls_back(self):
        self.write(observed_at="2026-09-26T17:58:00Z", risk_date_et="2026-09-26")
        self.assertIsNone(account_realized_from_live_cache(
            self.db, self.cache, now=self.NOW + timedelta(days=1)))


if __name__ == "__main__":
    unittest.main()
