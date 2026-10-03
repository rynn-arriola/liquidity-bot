"""BC-2026-10-02-B: Trade Scorer entry filter (scorer mode)."""
import json
import math
import os
import random
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from liquidity_bot.trade_score import (
    TRADE_SCORE_BELOW_MINIMUM,
    TRADE_SCORE_MODEL_UNAVAILABLE,
    TradeScoreModel,
    TradeScorePolicy,
    TradeScorer,
    league_of,
    load_trade_score_policy,
)
import test_live_commissioning as live

NOW = datetime(2026, 10, 2, 17, 0, tzinfo=timezone.utc)   # 1 PM ET
HOURS = ["12am-6am", "6am-12pm", "12pm-6pm"]


def model_doc(*, intercept=0.0, price=0.0, league=0.0, offer=0.0, noon=0.0,
              trained_at=NOW - timedelta(hours=2), auc=0.83):
    return {
        "schema": 1, "trained_at": trained_at.isoformat(), "trades": 500,
        "features": {"numeric": ["price", "spread_cents"],
                     "means": {"price": 0.5, "spread_cents": 2.0},
                     "stds": {"price": 0.1, "spread_cents": 1.0},
                     "leagues": ["cs2"], "sides": ["offer"], "hours": HOURS},
        "win_coefficients": [intercept, price, 0.0, league, offer, 0.0, 0.0, noon],
        "value_coefficients": [0.0] * 8,
        "test_metrics": {"auc_win": auc},
    }


class TradeScoreModelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "latest-model.json"

    def write(self, doc):
        self.path.write_text(json.dumps(doc), encoding="utf-8")
        # mtime granularity: force a visible change between writes
        st = self.path.stat()
        os.utime(self.path, (st.st_atime, st.st_mtime + random.random() + 1))

    def scorer(self, **policy):
        return TradeScorer(TradeScorePolicy(
            mode="scorer", model_path=str(self.path), check_interval_seconds=60, **policy))

    def test_probability_matches_the_logistic_formula(self):
        model = TradeScoreModel.from_json(model_doc(
            intercept=0.2, price=0.5, league=-1.0, offer=0.3, noon=0.4))
        traits = {"quote_price": 0.6, "spread": 0.03, "side": "offer", "league": "cs2"}
        z = 0.2 + 0.5 * (0.6 - 0.5) / 0.1 + 0 * (3.0 - 2.0) - 1.0 + 0.3 + 0.4
        self.assertAlmostEqual(model.win_probability(traits, NOW), 1 / (1 + math.exp(-z)))
        # missing trait = training mean; unknown league/bid side/6pm-12am = baseline
        night = datetime(2026, 10, 2, 23, 0, tzinfo=timezone.utc)  # 7 PM ET
        self.assertAlmostEqual(
            model.win_probability({"side": "bid", "league": "zzz"}, night),
            1 / (1 + math.exp(-0.2)))

    def test_league_and_hour_band_match_the_trade_scorer(self):
        self.assertEqual(league_of("aec-setkameua-olihry-dunmyk-2026-10-02"), "setkameua")
        self.assertEqual(league_of("odd"), "other")

    def test_invalid_models_are_refused(self):
        for bad in (dict(model_doc(), schema=2),
                    dict(model_doc(), win_coefficients=[0.0] * 7),
                    dict(model_doc(), trained_at="2026-10-02T12:00:00")):
            with self.assertRaises((ValueError, KeyError)):
                TradeScoreModel.from_json(bad)
        doc = model_doc()
        doc["features"]["numeric"] = ["price", "mystery"]
        doc["features"]["means"]["mystery"] = 0.0
        doc["features"]["stds"]["mystery"] = 1.0
        with self.assertRaises(ValueError):
            TradeScoreModel.from_json(doc)

    def test_missing_model_fails_closed(self):
        scorer = self.scorer()
        self.assertIn("model_missing", scorer.refresh(NOW))
        self.assertFalse(scorer.usable)
        self.assertIsNone(scorer.win_probability({}, NOW))

    def test_weak_retrain_is_rejected_and_last_good_model_kept(self):
        scorer = self.scorer()
        self.write(model_doc(intercept=1.0))
        scorer.refresh(NOW)
        good = scorer.model
        self.assertTrue(scorer.usable)
        self.write(model_doc(intercept=-1.0, auc=0.55))
        line = scorer.refresh(NOW + timedelta(minutes=2))
        self.assertIn("rejected", line)
        self.assertIs(scorer.model, good)
        self.assertTrue(scorer.usable)

    def test_stale_model_stops_entries_until_a_fresh_one_arrives(self):
        scorer = self.scorer(max_model_age_hours=72)
        self.write(model_doc(trained_at=NOW - timedelta(hours=73)))
        self.assertIn("model_stale", scorer.refresh(NOW))
        self.assertIsNone(scorer.win_probability({}, NOW))
        self.assertIsNone(scorer.refresh(NOW + timedelta(minutes=2)))   # no repeat log
        self.write(model_doc(trained_at=NOW))
        self.assertIn("usable=true", scorer.refresh(NOW + timedelta(minutes=4)))

    def test_reload_is_throttled(self):
        scorer = self.scorer()
        self.write(model_doc(intercept=1.0))
        scorer.refresh(NOW)
        self.write(model_doc(intercept=-1.0))
        scorer.refresh(NOW + timedelta(seconds=30))
        self.assertGreater(scorer.win_probability({}, NOW), 0.5)
        scorer.refresh(NOW + timedelta(seconds=61))
        self.assertLess(scorer.win_probability({}, NOW), 0.5)

    def test_checked_in_switch_is_the_operator_decision(self):
        # Operator 2026-10-02: "switching off the normal way to farm reward and
        # enabling this one for now"; 60% recommended and accepted.
        shipped = load_trade_score_policy()
        self.assertEqual((shipped.mode, shipped.min_win_probability), ("scorer", 0.60))
        self.assertEqual(shipped.model_path, "data/trade-scorer/latest-model.json")

    def test_policy_validation(self):
        self.assertFalse(TradeScorePolicy().scorer_mode)          # default = reward farming
        self.assertFalse(load_trade_score_policy(Path(self.tmp.name) / "none.json").scorer_mode)
        for bad in ({"mode": "both"}, {"min_win_probability": 1.5},
                    {"min_model_test_auc": 0.4}, {"check_interval_seconds": True}):
            with self.assertRaises(ValueError):
                TradeScorePolicy(**bad)
        path = Path(self.tmp.name) / "policy.json"
        path.write_text(json.dumps({"mode": "scorer", "surprise": 1}), encoding="utf-8")
        with self.assertRaises(ValueError):
            load_trade_score_policy(path)


SCORER_REPO = Path(__file__).resolve().parents[2] / "trade-scorer"


@unittest.skipUnless((SCORER_REPO / "trade_scorer" / "features.py").exists()
                     and (SCORER_REPO / "output" / "latest-model.json").exists(),
                     "trade-scorer project not checked out next to this repo")
class TradeScorerParityTests(unittest.TestCase):
    def test_same_probabilities_as_the_trade_scorer_project(self):
        sys.path.insert(0, str(SCORER_REPO))
        try:
            from trade_scorer.features import FeatureSpec
            from trade_scorer.model import predict_proba
        finally:
            sys.path.remove(str(SCORER_REPO))
        doc = json.loads((SCORER_REPO / "output" / "latest-model.json").read_text())
        spec = FeatureSpec.from_json(doc["features"])
        ours = TradeScoreModel.from_json(doc)
        rng = random.Random(7)
        leagues = list(doc["features"]["leagues"]) + ["setkameua", "unknown"]
        for _ in range(300):
            at = NOW + timedelta(minutes=rng.randrange(0, 24 * 60))
            traits = {
                "quote_price": rng.uniform(0.1, 0.9), "spread": rng.choice([0.01, 0.02, 0.05]),
                "side": rng.choice(["bid", "offer"]), "league": rng.choice(leagues),
                "seconds_to_event": rng.uniform(0, 30000),
                "observed_queue_ahead": rng.uniform(0, 3000), "depth_at_quote": rng.uniform(0, 3000),
                "depth_better_than_quote": rng.choice([0.0, rng.uniform(0, 50)]),
                "bid_depth": rng.uniform(0, 5000), "offer_depth": rng.uniform(0, 5000),
                "retained_value_ratio": rng.choice([None, rng.uniform(0.6, 1.0)]),
                "fill_probability": rng.uniform(0, 0.1),
                "touch_changes_30s": rng.randrange(0, 6), "touch_changes_120s": rng.randrange(0, 20),
                "seconds_since_touch_change": rng.choice([None, rng.uniform(0, 3000)]),
                "ticks_from_best": rng.choice([0, 1, None]), "quantity": 10.0,
                "stressed_exit_pnl": rng.uniform(-2, 0.2),
            }
            theirs = predict_proba(doc["win_coefficients"],
                                   spec.row(dict(traits, observed_at=at.isoformat())))
            self.assertAlmostEqual(ours.win_probability(traits, at), theirs, places=12)


class TradeScoreRunnerTests(unittest.IsolatedAsyncioTestCase):
    """The live economics pass applies the score to new entries only."""

    setUp = live.LiveCommissioningTests.setUp
    build_runner = live.LiveCommissioningTests.build_runner

    async def prepared(self, intercept):
        runner, client, _ = self.build_runner()
        await runner.preflight()
        self.addAsyncCleanup(runner.private_stream.close)
        self.addAsyncCleanup(runner.market_stream.close)
        path = runner.ledger.path.parent / "latest-model.json"
        path.write_text(json.dumps(model_doc(
            intercept=intercept, trained_at=live.NOW - timedelta(hours=1))), encoding="utf-8")
        runner.trade_scorer = TradeScorer(TradeScorePolicy(
            mode="scorer", model_path=str(path), min_win_probability=0.60))
        runner.trade_scorer.refresh(live.NOW)
        return runner

    async def test_low_score_blocks_new_entries_and_is_replaceable(self):
        runner = await self.prepared(intercept=-3.0)        # p about 5%
        self.assertIn(TRADE_SCORE_BELOW_MINIMUM, runner.REPLACEABLE_ECONOMIC_BLOCKERS)
        self.assertNotIn(TRADE_SCORE_MODEL_UNAVAILABLE, runner.REPLACEABLE_ECONOMIC_BLOCKERS)
        runner._evaluate_rewards(live.NOW, "market-one")
        state = runner.state_for("market-one")
        blocked = [s for s in ("bid", "offer")
                   if state.economic_blocker_by_side.get(s) == TRADE_SCORE_BELOW_MINIMUM]
        self.assertTrue(blocked)
        for side in blocked:
            self.assertEqual(state.expected_net_by_side[side], 0.0)
            self.assertLess(state.trade_score_by_side[side], 0.10)

    async def test_high_score_keeps_the_normal_decision(self):
        runner = await self.prepared(intercept=3.0)         # p about 95%
        runner._evaluate_rewards(live.NOW, "market-one")
        state = runner.state_for("market-one")
        self.assertFalse(any(str(v).startswith("trade_score")
                             for v in state.economic_blocker_by_side.values()))
        self.assertTrue(any(net > 0 for net in state.expected_net_by_side.values()))
        self.assertTrue(all(p > 0.9 for p in state.trade_score_by_side.values()))
        self.assertTrue(state.trade_score_by_side)

    async def test_without_a_usable_model_no_new_entries(self):
        runner = await self.prepared(intercept=3.0)
        runner.trade_scorer.model = None
        runner._evaluate_rewards(live.NOW, "market-one")
        state = runner.state_for("market-one")
        self.assertIn(TRADE_SCORE_MODEL_UNAVAILABLE, state.economic_blocker_by_side.values())
        self.assertFalse(any(net > 0 for net in state.expected_net_by_side.values()))

    async def test_reward_mode_has_no_scorer_and_is_unchanged(self):
        runner, _, _ = self.build_runner()
        await runner.preflight()
        self.addAsyncCleanup(runner.private_stream.close)
        self.addAsyncCleanup(runner.market_stream.close)
        self.assertIsNone(runner.trade_scorer)
        runner._evaluate_rewards(live.NOW, "market-one")
        state = runner.state_for("market-one")
        self.assertEqual(state.trade_score_by_side, {})
        self.assertTrue(any(net > 0 for net in state.expected_net_by_side.values()))


if __name__ == "__main__":
    unittest.main()
