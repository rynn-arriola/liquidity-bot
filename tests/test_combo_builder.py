"""Combo Builder phase 1 (quote-only) and the shared candidates scan."""
import unittest

from scripts.combo_builder import fair_price, pick_legs
from liquidity_bot.late_favorite_trader import LateFavoritePolicy, SportRule, combo_candidates


def cand(event, mid, market=None, side="long"):
    return {"event": event, "market": market or f"aec-{event}", "side": side, "fav_mid": mid}


class ComboBuilderTests(unittest.TestCase):
    def test_pick_legs_one_per_event_strongest_first(self):
        legs = pick_legs([cand("a", .96), cand("a", .99, "aec-a2"), cand("b", .97), cand("c", .94),
                          cand("d", .98)], min_mid=.95, max_legs=3, min_legs=2)
        self.assertEqual([l["market"] for l in legs], ["aec-a2", "aec-d", "aec-b"])
        self.assertEqual(pick_legs([cand("a", .99), cand("b", .90)], min_mid=.95, min_legs=2), [])
        # Operator: "minimum of 5 legs max 8".
        many = [cand(e, .97) for e in "abcdefghij"]
        self.assertEqual(len(pick_legs(many)), 8)
        self.assertEqual(pick_legs(many[:4]), [])
        self.assertAlmostEqual(fair_price([cand("a", .97), cand("b", .96)]), .9312)

    def test_combo_candidates_reuse_trader_rules_with_lower_floor(self):
        policy = LateFavoritePolicy(enabled=True, min_price=.95, max_spread=.10,
                                    rules={"hockey": SportRule(periods=("P3",), max_clock_minutes=2)})
        event = {"slug": "nhl-x", "eventState": {"type": "hockey", "period": "P3", "elapsed": "01:30",
                                                  "live": True, "ended": False, "score": "3-1"},
                 "markets": [{"slug": "aec-nhl-x", "sportsMarketTypeV2": "SPORTS_MARKET_TYPE_MONEYLINE",
                              "status": "MARKET_STATUS_OPEN", "bestBidQuote": {"value": "0.91"},
                              "bestAskQuote": {"value": "0.93"}}]}
        out = combo_candidates([event], policy, floor=.90)
        self.assertEqual(len(out), 1)
        self.assertEqual((out[0]["market"], out[0]["side"], out[0]["fav_mid"]), ("aec-nhl-x", "long", .92))
        tied = dict(event, eventState=dict(event["eventState"], score="2-2"))
        self.assertEqual(combo_candidates([tied], policy), [])


class HalfwayRulesTests(unittest.TestCase):
    def test_combo_candidates_use_halfway_rules_trader_keeps_final_minutes(self):
        from dataclasses import replace
        from liquidity_bot.late_favorite_trader import load_policy, pick_entry
        policy = replace(load_policy(), min_price=.95)
        self.assertIn("hockey", policy.combo_rules)
        event = {"slug": "nhl-y", "eventState": {"type": "hockey", "period": "P2", "elapsed": "08:00",
                                                  "live": True, "ended": False, "score": "3-0"},
                 "markets": [{"slug": "aec-nhl-y", "sportsMarketTypeV2": "SPORTS_MARKET_TYPE_MONEYLINE",
                              "status": "MARKET_STATUS_OPEN", "bestBidQuote": {"value": "0.95"},
                              "bestAskQuote": {"value": "0.96"}}]}
        self.assertIsNone(pick_entry(event, policy))                      # trader: not final minutes
        self.assertEqual([c["market"] for c in combo_candidates([event], policy)], ["aec-nhl-y"])


if __name__ == "__main__":
    unittest.main()
