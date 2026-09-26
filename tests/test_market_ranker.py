"""Rewarded-market ranker: blockers, league history, paid history, ranking."""
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from scripts import market_ranker as ranker

NOW = datetime(2026, 9, 26, 19, tzinfo=timezone.utc)


def market(slug="aec-cfb-a-b-2026-09-26", *, moneyline=True, bid=0.47, offer=0.475, qty=1):
    return SimpleNamespace(slug=slug, is_supported_moneyline=moneyline, minimum_trade_qty=qty,
                           catalog_best_bid=bid, catalog_best_offer=offer,
                           game_start=NOW + timedelta(hours=2), sports_market_type_v2="X")


POLICY = SimpleNamespace(excluded_leagues=frozenset({"utr"}), quote_size=15,
                         minimum_entry_spread=0.02, projection_horizon_seconds=30)


def side(reward, *, eligible=True, blocker=None, viable=False):
    return SimpleNamespace(projection=SimpleNamespace(eligible=eligible, projected_reward=reward),
                           blocker=blocker, viable=viable)


def opportunity(*sides, bid=0.47, offer=0.475):
    book = SimpleNamespace(best_bid=SimpleNamespace(price=bid), best_offer=SimpleNamespace(price=offer))
    return SimpleNamespace(sides=sides, book=book,
                           viable_sides=tuple(i for i, s in enumerate(sides) if s.viable))


HISTORY = ranker.league_history(
    [dict(cell="global", orders=1000, observed_per_30s=0.01),
     dict(cell="league:setkameua", orders=500, observed_per_30s=0.02),
     dict(cell="league:rare", orders=10, observed_per_30s=0.5)],
    [dict(key="setkameua", closed=100, closed_pnl=-5, pnl_per_closed=-0.05),
     dict(key="wtt", closed=50, closed_pnl=-20, pnl_per_closed=-0.4)])


class MarketRankerTests(unittest.TestCase):
    def test_pre_book_blockers_name_each_live_rule(self):
        self.assertEqual(ranker.pre_book_blockers(market(), None, now=NOW, policy=POLICY),
                         ["spread_below_2c"])
        self.assertEqual(ranker.pre_book_blockers(
            market("aec-utr-a-b", moneyline=False, qty=100, offer=0.50), None, now=NOW, policy=POLICY),
            ["not_moneyline", "excluded_league", "minimum_quantity"])
        self.assertEqual(ranker.pre_book_blockers(market(offer=0.49), None, now=NOW, policy=POLICY), [])

    def test_league_history_needs_enough_samples_and_falls_back_to_global(self):
        self.assertEqual(HISTORY["rates"], {"setkameua": 0.02})
        self.assertAlmostEqual(HISTORY["global_result"], -25 / 150)
        value, basis = ranker.expected_trading_per_hour("setkameua", 2, HISTORY)
        self.assertEqual(basis, "league_history")
        self.assertAlmostEqual(value, 0.02 * 120 * 2 * -0.05)
        value, basis = ranker.expected_trading_per_hour("cfb", 1, HISTORY)
        self.assertEqual(basis, "global_average")
        self.assertAlmostEqual(value, 0.01 * 120 * -25 / 150)

    def test_paid_leagues_use_newest_snapshot_and_ignore_zero_rows(self):
        snaps = [dict(at="2026-09-25", rows=[dict(market_slug="aec-utr-a-b", status="PAID", reward="1")]),
                 dict(at="2026-09-26", rows=[dict(market_slug="aec-wtt-a-b", status="PAID", reward="0.5"),
                                             dict(market_slug="aec-utr-a-b", status="PAID", reward="0"),
                                             dict(market_slug="aec-cs2-a-b", status="PENDING", reward="2")])]
        self.assertEqual(ranker.paid_leagues(snaps), {"wtt"})
        self.assertIsNone(ranker.paid_leagues([]))
        # Rested-but-unpaid is evidence; never quoted is only unproven.
        self.assertEqual(ranker.unpaid_leagues({"wtt"}, {"wtt", "utr"}), {"utr"})
        self.assertIsNone(ranker.unpaid_leagues(None, {"utr"}))

    def test_score_counts_reward_per_hour_and_zeroes_never_paid_leagues(self):
        period = SimpleNamespace(period="day_of", reward_pool=200)
        relaxed = opportunity(side(0.001), side(0.002), side(0.5, eligible=False))
        live = opportunity(side(0.001, blocker="spread_too_tight"), side(0.002))
        row = ranker.score_row(market(), period, relaxed, live, now=NOW, policy=POLICY,
                               history=HISTORY, unpaid=set(), traded=False)
        self.assertAlmostEqual(row["reward_per_hour"], 0.003 * 120)
        self.assertFalse(row["never_paid"])
        self.assertEqual(row["reward_evidence"], "no_history")
        self.assertAlmostEqual(row["net_per_hour"], 0.36 + 0.01 * 120 * 2 * -25 / 150, places=4)
        self.assertEqual(row["blocked_by"], ["spread_too_tight"])
        unpaid = ranker.score_row(market(), period, relaxed, live, now=NOW, policy=POLICY,
                                  history=HISTORY, unpaid={"cfb"}, traded=False, rested={"cfb"})
        self.assertTrue(unpaid["never_paid"])
        self.assertAlmostEqual(unpaid["net_per_hour"], unpaid["trading_per_hour"])
        viable = ranker.score_row(market(), period, relaxed, opportunity(side(0.1, viable=True)),
                                  now=NOW, policy=POLICY, history=HISTORY, unpaid=None, traded=True,
                                  rested={"cfb"})
        self.assertEqual(viable["blocked_by"], [])
        self.assertTrue(viable["live_viable"] and viable["traded_now"])
        self.assertEqual(viable["reward_evidence"], "paid_before")

    def test_book_priority_reads_traded_then_moneylines_and_skips_never_paid(self):
        def item(slug, pool, moneyline=True):
            return (market(slug, moneyline=moneyline), None, SimpleNamespace(reward_pool=pool))
        items = [item("aec-ncaams-a-b-draw", 9000, moneyline=False), item("aec-cfb-a-b", 200),
                 item("aec-wtt-c-d", 15), item("aec-wtt-e-f-over", 500, moneyline=False),
                 item("aec-setkameua-g-h", 15)]
        ordered, never_paid = ranker.book_priority(
            items, traded={"aec-setkameua-g-h"}, unpaid={"ncaams"})
        self.assertEqual(never_paid, 1)
        # Never-paid markets are still measured, but only after everything else.
        self.assertEqual([i[0].slug for i in ordered],
                         ["aec-setkameua-g-h", "aec-cfb-a-b", "aec-wtt-c-d", "aec-wtt-e-f-over",
                          "aec-ncaams-a-b-draw"])
        ordered, never_paid = ranker.book_priority(items, traded=set(), unpaid=None)
        self.assertEqual((len(ordered), never_paid), (5, 0))

    def test_cron_lines_do_not_overlap_and_keep_other_jobs(self):
        from pathlib import Path
        from scripts.rotate_logs import cron_line as rotate_line
        frequent, daily = ranker.cron_lines(Path("/bot"))
        self.assertTrue(frequent.startswith("5,35 * * * * cd /bot && flock -n data/market-ranker.lock"))
        self.assertTrue(frequent.endswith(ranker.CRON_MARK))
        self.assertIn("--include-spreads-totals", daily)
        self.assertTrue(daily.endswith(ranker.DAILY_CRON_MARK))
        self.assertNotIn("--include-spreads-totals", frequent)
        self.assertFalse(frequent.endswith(ranker.DAILY_CRON_MARK))
        self.assertIn(rotate_line(Path("/bot")).split(" # ")[0][:20], rotate_line(Path("/bot")))

    def test_rank_orders_by_net_value(self):
        rows = ranker.rank([dict(market="b", net_per_hour=0.1), dict(market="a", net_per_hour=0.5),
                            dict(market="c", net_per_hour=0.1)])
        self.assertEqual([(r["rank"], r["market"]) for r in rows], [(1, "a"), (2, "b"), (3, "c")])


if __name__ == "__main__":
    unittest.main()
