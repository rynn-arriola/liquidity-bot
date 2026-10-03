"""BC-2026-10-03-B late-favourite trader rules."""
import unittest
from dataclasses import replace
from decimal import Decimal

from liquidity_bot.late_favorite_trader import (
    LateFavoritePolicy, SportRule, almost_over, clock_minutes, contracts_for, daily_stop_hit,
    load_policy, match_minute, pick_entry, realized_result)

RULES = {"soccer": SportRule(min_minute=85),
         "hockey": SportRule(periods=("P3",), max_clock_minutes=5),
         "basketball": SportRule(periods=("Q4",), max_clock_minutes=3)}
POLICY = LateFavoritePolicy(enabled=True, rules=RULES)


def event(sport="hockey", period="P3", elapsed="04:00", bid=.95, ask=.96, live=True,
          mtype="SPORTS_MARKET_TYPE_MONEYLINE", status="MARKET_STATUS_OPEN"):
    return {"slug": "ev", "eventState": {"type": sport, "period": period, "elapsed": elapsed,
                                         "live": live, "ended": False},
            "markets": [{"slug": "m1", "sportsMarketTypeV2": mtype, "status": status, "feeCoefficient": 0.0695,
                         "bestBidQuote": {"value": str(bid)}, "bestAskQuote": {"value": str(ask)}}]}


class RuleTests(unittest.TestCase):
    def test_clock_and_minute_parsing(self):
        self.assertEqual(match_minute("87'"), 87)
        self.assertEqual(match_minute("2H", "90+3'"), 90)
        self.assertIsNone(match_minute("2H", None))
        self.assertAlmostEqual(clock_minutes("04:30"), 4.5)
        self.assertAlmostEqual(clock_minutes("5:09"), 5.15)
        self.assertIsNone(clock_minutes("End P2"))

    def test_almost_over_per_sport(self):
        self.assertTrue(almost_over("hockey", "P3", "05:00", RULES))
        self.assertFalse(almost_over("hockey", "P3", "05:01", RULES))
        self.assertFalse(almost_over("hockey", "P2", "01:00", RULES))
        self.assertTrue(almost_over("soccer", "2H", "86'", RULES))
        self.assertFalse(almost_over("soccer", "2H", "80'", RULES))
        self.assertFalse(almost_over("tennis", "S3", None, RULES))      # no rule = no trade
        self.assertTrue(almost_over("basketball", "Q4", "02:59", RULES))

    def test_pick_entry_long_short_band_and_profit(self):
        self.assertEqual(pick_entry(event(), POLICY).side, "long")
        short = pick_entry(event(bid=.04, ask=.05), POLICY)
        self.assertEqual((short.side, short.price, short.cost), ("short", .04, .96))
        self.assertIsNone(pick_entry(event(bid=.80, ask=.85), POLICY))          # below 90c
        self.assertIsNone(pick_entry(event(elapsed="09:00"), POLICY))           # not late
        self.assertIsNone(pick_entry(event(live=False), POLICY))
        self.assertIsNone(pick_entry(event(status="MARKET_STATUS_HALTED"), POLICY))
        self.assertIsNone(pick_entry(event(mtype="SPORTS_MARKET_TYPE_TOTAL"), POLICY))
        self.assertIsNotNone(pick_entry(event(bid=.985, ask=.99), POLICY))      # 99c still profits

    def test_sizing_daily_stop_and_results(self):
        self.assertEqual(contracts_for(10, 0.92, 0.01), Decimal("10.86"))
        self.assertLessEqual(float(contracts_for(10, 0.99, 0.01)) * 0.99, 10)
        self.assertAlmostEqual(realized_result("long", .9, 10, 1.0, .0695), 1.0 - .0695 * .09 * 10)
        self.assertAlmostEqual(realized_result("short", .9, 10, 1.0, .0695), -9.0 - .0695 * .09 * 10)
        self.assertTrue(daily_stop_hit(-50.0, POLICY))
        self.assertFalse(daily_stop_hit(-49.9, POLICY))

    def test_checked_in_policy_is_the_operator_decision(self):
        p = load_policy()
        self.assertEqual((p.risk_per_trade_usd, p.max_open_positions, p.daily_loss_stop_usd),
                         (10, 50, 50))
        self.assertEqual((p.min_price, p.max_price), (0.90, 0.99))
        self.assertEqual(sorted(p.rules), ["basketball", "football", "hockey", "soccer"])

    def test_policy_validation(self):
        for bad in (dict(min_price=0.95, max_price=0.9), dict(max_open_positions=0),
                    dict(enabled="yes"), dict(max_price=1.0)):
            with self.assertRaises(ValueError):
                LateFavoritePolicy(**bad)


if __name__ == "__main__":
    unittest.main()
