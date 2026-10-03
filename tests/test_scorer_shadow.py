"""Trade Scorer shadow: strict paper fills and the bot's exit rules."""
import unittest
from datetime import datetime, timedelta, timezone

from liquidity_bot.models import BookLevel, BookSnapshot, TradePrint
from liquidity_bot.scorer_shadow import (
    PaperTrade, RestingQuote, auc, depth_at_and_better, summarize, ticks_from_best)
from liquidity_bot.shadow_math import cross_exit

T0 = datetime(2026, 10, 3, 14, 0, tzinfo=timezone.utc)
START = T0 + timedelta(hours=3)


def book(bids, offers, slug="m"):
    return BookSnapshot(slug, tuple(BookLevel(p, s) for p, s in bids),
                        tuple(BookLevel(p, s) for p, s in offers))


def trade(price, qty, maker_buy, at=T0, slug="m"):
    return TradePrint(slug, price, qty, "ORDER_SIDE_BUY" if maker_buy else "ORDER_SIDE_SELL", at)


def paper(side="bid", price=0.50, qty=10.0, queue=0.0, game_start=START):
    return PaperTrade(slug="m", league="x", side=side, price=price, qty=qty, win_probability=0.7,
                      model="t", traits={}, placed_at=T0, game_start=game_start, tick=0.01,
                      entry=RestingQuote(side, price, qty, queue))


class RestingQuoteTests(unittest.TestCase):
    def test_queue_ahead_trades_first_then_us_then_trade_through_fills_the_rest(self):
        q = RestingQuote("bid", 0.50, 10, queue_ahead=20)
        self.assertEqual(q.on_trade(trade(0.50, 15, maker_buy=True)), 0)      # queue 20 -> 5
        self.assertEqual(q.on_trade(trade(0.50, 8, maker_buy=False)), 0)      # other side ignored
        self.assertEqual(q.on_trade(trade(0.51, 50, maker_buy=True)), 0)      # better bid traded
        self.assertEqual(q.on_trade(trade(0.50, 9, maker_buy=True)), 4)       # 5 ahead, then 4 us
        self.assertEqual(q.on_trade(trade(0.49, 1, maker_buy=True)), 6)       # through our price
        self.assertEqual(q.remaining, 0)

    def test_offer_side_and_book_crossing(self):
        q = RestingQuote("offer", 0.52, 10, queue_ahead=0)
        self.assertEqual(q.on_trade(trade(0.52, 4, maker_buy=False)), 4)
        self.assertEqual(q.on_book(book([(0.51, 5)], [(0.53, 5)])), 0)
        self.assertEqual(q.on_book(book([(0.52, 5)], [(0.53, 5)])), 6)      # a bid at our offer

    def test_book_helpers(self):
        b = book([(0.50, 10), (0.49, 7), (0.48, 3)], [(0.52, 4), (0.53, 6)])
        self.assertEqual(depth_at_and_better(b, "bid", 0.49), (10, 7))
        self.assertEqual(depth_at_and_better(b, "offer", 0.53), (4, 6))
        self.assertEqual(ticks_from_best(b, "bid", 0.48, 0.01), 2)
        self.assertEqual(ticks_from_best(b, "bid", 0.51, 0.01), 0)
        self.assertEqual(ticks_from_best(b, "offer", 0.53, 0.01), 1)


class PaperTradeTests(unittest.TestCase):
    def test_long_closes_passively_one_tick_inside_the_touch(self):
        t = paper()
        t.on_trade(trade(0.49, 1, maker_buy=True), T0 + timedelta(seconds=30))
        self.assertEqual((t.status, t.position), ("holding", 10))
        t.on_book(book([(0.50, 30)], [(0.53, 30)]), T0 + timedelta(seconds=40))
        self.assertEqual(t.reducer.price, 0.52)                                # 0.53 - tick, above entry
        t.on_trade(trade(0.52, 10, maker_buy=False), T0 + timedelta(seconds=60))
        self.assertEqual((t.status, t.close_kind), ("closed", "passive"))
        self.assertAlmostEqual(t.pnl, 0.20)

    def test_losing_long_joins_the_touch_and_is_forced_after_fifteen_minutes(self):
        t = paper()
        fill_at = T0 + timedelta(seconds=30)
        t.on_book(book([(0.49, 30)], [(0.50, 30)]), fill_at)                  # offer at our bid
        self.assertEqual(t.status, "holding")
        b = book([(0.48, 100)], [(0.49, 100)])
        t.on_book(b, fill_at + timedelta(seconds=5))
        self.assertEqual(t.reducer.price, 0.49)                                # joins, never below entry-step rule
        t.step(b, fill_at + timedelta(seconds=899), max_early_exit_loss=8.0)
        self.assertEqual(t.status, "holding")
        t.step(b, fill_at + timedelta(seconds=900), max_early_exit_loss=8.0)
        expected = cross_exit(b, entry_side="bid", entry_price=0.50, qty=10, depth_haircut=.5)
        self.assertEqual((t.status, t.close_kind), ("closed", "forced"))
        self.assertAlmostEqual(t.pnl, expected.pnl)
        self.assertLess(t.pnl, 0)

    def test_expensive_early_cross_is_deferred_to_five_minutes_before_start(self):
        t = paper(game_start=T0 + timedelta(hours=1))
        t.on_trade(trade(0.49, 1, maker_buy=True), T0)
        thin = book([(0.30, 100)], [(0.31, 100)])
        t.step(thin, T0 + timedelta(seconds=900), max_early_exit_loss=0.5)
        self.assertEqual(t.status, "holding")
        self.assertTrue(t.deferred)
        self.assertEqual(t.forced_at, T0 + timedelta(minutes=55))
        t.step(thin, T0 + timedelta(minutes=55), max_early_exit_loss=0.5)
        self.assertEqual((t.status, t.close_kind), ("closed", "forced_deferred"))
        self.assertAlmostEqual(t.pnl, (0.30 - 0.50) * 10 - 0.06 * 10 * 0.30 * 0.70)

    def test_short_entry_is_mirrored(self):
        t = paper(side="offer", price=0.52)
        t.on_trade(trade(0.53, 2, maker_buy=False), T0)                       # through our offer
        self.assertEqual((t.status, t.position), ("holding", 10))
        t.on_book(book([(0.48, 30)], [(0.52, 30)]), T0 + timedelta(seconds=10))
        self.assertEqual((t.reducer.side, t.reducer.price), ("bid", 0.49))
        t.on_trade(trade(0.49, 10, maker_buy=True), T0 + timedelta(seconds=20))
        self.assertAlmostEqual(t.pnl, 0.30)

    def test_unfilled_entry_expires_and_a_partial_fill_is_kept(self):
        t = paper(queue=0)
        t.step(None, T0 + timedelta(seconds=599), max_early_exit_loss=8)
        self.assertEqual(t.status, "resting")
        t.step(None, T0 + timedelta(seconds=600), max_early_exit_loss=8)
        self.assertEqual((t.status, t.close_kind, t.pnl), ("expired", "not_filled", None))
        u = paper(queue=0)
        u.on_trade(trade(0.50, 4, maker_buy=True), T0)
        self.assertEqual((u.status, u.position), ("resting", 4))
        u.step(None, T0 + timedelta(seconds=600), max_early_exit_loss=8)
        self.assertEqual((u.status, u.position), ("holding", 4))

    def test_entry_expires_five_minutes_before_the_start(self):
        t = paper(game_start=T0 + timedelta(minutes=8))
        self.assertEqual(t.expires_at, T0 + timedelta(minutes=3))


class SummaryTests(unittest.TestCase):
    def test_groups_split_at_the_threshold_and_auc(self):
        rows = [
            {"status": "closed", "win_probability": .9, "pnl": .3, "league": "a", "close_kind": "passive"},
            {"status": "closed", "win_probability": .7, "pnl": -.1, "league": "b", "close_kind": "forced"},
            {"status": "expired", "win_probability": .65, "pnl": None, "league": "a", "close_kind": "not_filled"},
            {"status": "closed", "win_probability": .3, "pnl": -.5, "league": "a", "close_kind": "forced"},
            {"status": "abandoned", "win_probability": .9, "pnl": None, "league": "a", "close_kind": None},
        ]
        s = summarize(rows, threshold=0.6, model_leagues={"a"})
        self.assertEqual((s["allowed"]["placed"], s["allowed"]["filled"], s["allowed"]["pnl"]), (3, 2, 0.2))
        self.assertEqual((s["skipped"]["placed"], s["skipped"]["pnl"]), (1, -0.5))
        self.assertEqual(s["allowed_new_leagues"]["placed"], 1)
        self.assertAlmostEqual(s["auc_closed"], 1.0)
        self.assertEqual(auc([.2, .8, .5], [0, 1, 1]), 1.0)
        self.assertIsNone(auc([.2], [0]))


class ShadowCronTests(unittest.TestCase):
    def test_cron_restarts_the_shadow_and_shares_the_bot_directory(self):
        from pathlib import Path
        from scripts import scorer_shadow as ss
        run, rep = ss.cron_lines(Path("/bot"), Path("/code"))
        self.assertTrue(run.startswith("*/10 * * * * cd /bot && flock -n data/scorer-shadow/cron.lock "))
        self.assertIn("/bot/.venv/bin/python -B /code/scripts/scorer_shadow.py run", run)
        self.assertIn("/code/scripts/scorer_shadow.py report", rep)
        self.assertTrue(all(line.rstrip().endswith((ss.CRON_MARK, ss.CRON_MARK + " report"))
                            for line in (run, rep)))


if __name__ == "__main__":
    unittest.main()
