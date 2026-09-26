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

    def test_reward_calibration_compares_model_to_actual_payouts_by_league(self):
        start, end = NOW - timedelta(days=14), NOW
        at = lambda h: (start + timedelta(hours=h)).isoformat()
        data = dict(
            observations=[dict(client_order_id="a", projected_reward=0.01),
                          dict(client_order_id="b", projected_reward=0.02),
                          dict(client_order_id="c", projected_reward=0.05)],
            intents=[dict(client_order_id=c, created_at=at(1), quote_observation_required=1)
                     for c in "abc"],
            orders=[  # a: 100 h of setkameua rest; b: 60 h; c: 10 h of wtt (too few hours)
                dict(client_order_id="a", market_slug="aec-setkameua-x-y", status="EXPIRED", event_at=at(101)),
                dict(client_order_id="b", market_slug="aec-setkameua-z-w", status="EXPIRED", event_at=at(61)),
                dict(client_order_id="c", market_slug="aec-wtt-p-q", status="CANCELED", event_at=at(11))])
        payouts = [dict(market_slug="aec-setkameua-x-y", status="PAID", reward="300"),
                   dict(market_slug="aec-setkameua-z-w", status="PAID", reward="36"),
                   dict(market_slug="aec-wtt-p-q", status="PENDING", reward="9"),
                   dict(market_slug="aec-wtt-p-q", status="PAID", reward="12")]
        cal = ranker.reward_calibration(data, payouts, start=start, end=end)
        # setkameua predicted: 0.01*100*120 + 0.02*60*120 = 120 + 144 = 264; paid 336
        self.assertAlmostEqual(cal["leagues"]["setkameua"]["predicted"], 264.0)
        self.assertAlmostEqual(cal["leagues"]["setkameua"]["ratio"], round(336 / 264, 4))
        self.assertAlmostEqual(cal["leagues"]["wtt"]["ratio"], round(12 / 60, 4))
        self.assertAlmostEqual(cal["overall_ratio"], round(348 / 324, 4))
        self.assertEqual(ranker.reward_factor("setkameua", cal), (round(336 / 264, 4), "league"))
        self.assertEqual(ranker.reward_factor("wtt", cal), (round(348 / 324, 4), "overall"))
        self.assertEqual(ranker.reward_factor("x", None), (1.0, "uncalibrated"))
        huge = dict(leagues={"cfb": dict(ratio=9.0, resting_hours=500)}, overall_ratio=1.0)
        self.assertEqual(ranker.reward_factor("cfb", huge), (ranker.MAX_REWARD_FACTOR, "league"))

    def test_weighted_calibration_weighs_the_newest_week_half(self):
        windows = ranker.week_windows(NOW)
        self.assertEqual([w for *_, w in windows], [0.5, 0.25, 0.25])
        self.assertEqual((windows[0][1], windows[2][0]), (NOW, NOW - timedelta(days=21)))
        at = lambda days_ago, h=0: (NOW - timedelta(days=days_ago) + timedelta(hours=h)).isoformat()
        data = dict(
            observations=[dict(client_order_id=c, projected_reward=0.01) for c in "xyz"],
            intents=[dict(client_order_id="x", created_at=at(3), quote_observation_required=1),
                     dict(client_order_id="y", created_at=at(18), quote_observation_required=1),
                     dict(client_order_id="z", created_at=at(7, -5), quote_observation_required=1)],
            orders=[  # x: 10 h in week 1; y: 10 h in week 3; z: 5 h week 2 + 5 h week 1
                dict(client_order_id="x", market_slug="aec-cfb-x", status="EXPIRED", event_at=at(3, 10)),
                dict(client_order_id="y", market_slug="aec-cfb-y", status="EXPIRED", event_at=at(18, 10)),
                dict(client_order_id="z", market_slug="aec-nfl-z", status="EXPIRED", event_at=at(7, 5))])
        payouts = [dict(market_slug="aec-cfb-x", status="PAID", reward="36"),
                   dict(market_slug="aec-nfl-z", status="PAID", reward="24")]
        cal = ranker.weighted_reward_calibration(data, payouts, windows)
        # cfb: predicted 12 per market; weighted 0.5*12 + 0.25*12 = 9; paid 0.5*36 = 18
        self.assertAlmostEqual(cal["leagues"]["cfb"]["ratio"], 2.0)
        self.assertAlmostEqual(cal["leagues"]["cfb"]["resting_hours"], 20.0)
        # nfl: 6 predicted per week, payout split 12/12; ratio unchanged by weights
        self.assertAlmostEqual(cal["leagues"]["nfl"]["predicted"], 0.5 * 6 + 0.25 * 6)
        self.assertAlmostEqual(cal["leagues"]["nfl"]["ratio"], 2.0)
        self.assertAlmostEqual(cal["overall_ratio"], round((18 + 9) / (9 + 4.5), 4))

    def test_weighted_history_weighs_fill_rates_and_results_by_week(self):
        from unittest import mock
        windows = ranker.week_windows(NOW)
        cells = {windows[0][0]: [dict(cell="global", orders=40, fills=4, exposure_hours=1.0),
                                 dict(cell="league:cfb", orders=40, fills=4, exposure_hours=1.0)],
                 windows[1][0]: [dict(cell="global", orders=40, fills=0, exposure_hours=1.0),
                                 dict(cell="league:cfb", orders=40, fills=0, exposure_hours=1.0)],
                 windows[2][0]: []}
        episodes = [dict(market="aec-cfb-a", opened_at=NOW - timedelta(days=d), closed_at=NOW,
                         entry_client_order_id="e", pnl=p)
                    for d, p in [(1, 1.0)] * 3 + [(10, -1.0)] * 3]
        data = dict(observations=[dict(client_order_id="e", observed_at=NOW.isoformat())],
                    intents=[dict(client_order_id="e", quote_observation_required=1)])
        with mock.patch("scripts.calibration_report.fill_model_calibration",
                        lambda d, *, since, now: dict(cells=cells[since])),                 mock.patch("scripts.calibration_report.position_episodes", lambda d: episodes):
            history = ranker.weighted_league_history(data, windows)
        # fills 0.5*4 over intervals 0.75*120 = 90
        self.assertAlmostEqual(history["rates"]["cfb"], 2 / 90)
        # results: (0.5*3 - 0.25*3) / (0.5*3 + 0.25*3)
        self.assertAlmostEqual(history["results"]["cfb"], 0.75 / 2.25)
        self.assertAlmostEqual(history["global_result"], 0.75 / 2.25)

    def test_score_applies_the_reward_factor(self):
        period = SimpleNamespace(period="day_of", reward_pool=200)
        relaxed = opportunity(side(0.001), side(0.002))
        cal = dict(leagues={"cfb": dict(ratio=0.5, resting_hours=100)}, overall_ratio=1.0)
        row = ranker.score_row(market(), period, relaxed, None, now=NOW, policy=POLICY,
                               history=HISTORY, unpaid=set(), traded=False, calibration=cal)
        self.assertAlmostEqual(row["reward_model_per_hour"], 0.36)
        self.assertAlmostEqual(row["reward_per_hour"], 0.18)
        self.assertEqual((row["reward_factor"], row["reward_factor_basis"]), (0.5, "league"))

    def test_priority_file_keeps_positive_paid_markets_in_rank_order(self):
        import json, tempfile
        from pathlib import Path
        from liquidity_bot.ranker_priority import RankerPriority
        rows = ranker.rank([dict(market="a", net_per_hour=0.5, never_paid=False),
                            dict(market="b", net_per_hour=0.9, never_paid=True),
                            dict(market="c", net_per_hour=0.2, never_paid=False),
                            dict(market="d", net_per_hour=-0.1, never_paid=False),
                            dict(market="e", net_per_hour=0.7, never_paid=False,
                                 blocked_by=["excluded_league"]),
                            dict(market="f", net_per_hour=0.1, never_paid=False,
                                 blocked_by=["spread_below_2c"])])
        report = dict(generated_at=NOW.isoformat(), rows=rows)
        with tempfile.TemporaryDirectory() as tmp:
            path = ranker.write_priority(report, Path(tmp))
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual([m["slug"] for m in data["markets"]], ["a", "c", "f"])
            self.assertEqual(sorted(p.name for p in Path(tmp).iterdir()), ["priority.json"])
            loaded = RankerPriority(path, log=lambda line: None).ranks(NOW + timedelta(minutes=10))
            self.assertEqual(loaded, {"a": 1, "c": 2, "f": 3})

    def test_rank_orders_by_net_value(self):
        rows = ranker.rank([dict(market="b", net_per_hour=0.1), dict(market="a", net_per_hour=0.5),
                            dict(market="c", net_per_hour=0.1)])
        self.assertEqual([(r["rank"], r["market"]) for r in rows], [(1, "a"), (2, "b"), (3, "c")])


if __name__ == "__main__":
    unittest.main()
