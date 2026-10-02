import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from liquidity_bot.reward_pool_gate import (  # noqa: E402
    DEFAULT_REWARD_POOL_GATE_POLICY_PATH,
    RewardPoolGate,
    RewardPoolGatePolicy,
    load_reward_pool_gate_policy,
    read_reward_pool_score,
)

NOW = datetime(2026, 10, 2, 16, 0, tzinfo=timezone.utc)
ON = RewardPoolGatePolicy(enabled=True)


class RewardPoolGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "latest-score.json"

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, payload):
        self.path.write_text(json.dumps(payload), encoding="utf-8")

    def read(self, policy=ON, now=NOW):
        return read_reward_pool_score(self.path, policy, now)

    def test_five_and_under_pauses_above_five_trades(self):
        for score, paused in ((3.0, True), (5.0, True), (5.1, False), (10, False)):
            self.write({"at": NOW.isoformat(), "ours": {"score": score}})
            self.assertEqual(self.read().paused, paused, score)

    def test_missing_unreadable_stale_or_future_score_falls_back_to_trading(self):
        self.assertEqual(self.read().detail, "score_missing")
        self.path.write_text("{not json", encoding="utf-8")
        self.assertEqual(self.read().detail, "score_unreadable")
        self.write({"at": "2026-10-02T12:00:00", "ours": {"score": 2}})  # naive
        self.assertEqual(self.read().detail, "score_unreadable")
        self.write({"at": NOW.isoformat(), "ours": {"score": True}})
        self.assertEqual(self.read().detail, "score_unreadable")
        self.write({"at": (NOW - timedelta(hours=3, minutes=1)).isoformat(),
                    "ours": {"score": 2}})
        reading = self.read()
        self.assertFalse(reading.paused)
        self.assertTrue(reading.detail.startswith("score_stale"))
        self.write({"at": (NOW + timedelta(hours=1)).isoformat(), "ours": {"score": 2}})
        self.assertEqual(self.read().detail, "score_from_future")
        for reading in (self.read(),):
            self.assertFalse(reading.paused)

    def test_disabled_policy_never_reads_or_pauses(self):
        self.write({"at": NOW.isoformat(), "ours": {"score": 1}})
        reading = self.read(RewardPoolGatePolicy(enabled=False))
        self.assertEqual((reading.paused, reading.detail), (False, "disabled"))

    def test_gate_rereads_on_interval_and_reports_only_changes(self):
        self.write({"at": NOW.isoformat(), "ours": {"score": 3}})
        gate = RewardPoolGate(RewardPoolGatePolicy(
            enabled=True, score_path=str(self.path), check_interval_seconds=60))
        self.assertTrue(gate.refresh(NOW).paused)
        self.assertIsNone(gate.refresh(NOW + timedelta(minutes=2)))  # unchanged
        self.write({"at": NOW.isoformat(), "ours": {"score": 6}})
        self.assertIsNone(gate.refresh(NOW + timedelta(minutes=2, seconds=30)))
        self.assertTrue(gate.paused)  # not due yet
        self.assertFalse(gate.refresh(NOW + timedelta(minutes=3, seconds=1)).paused)

    def test_stale_age_growth_is_not_a_change(self):
        self.write({"at": (NOW - timedelta(hours=4)).isoformat(), "ours": {"score": 3}})
        gate = RewardPoolGate(RewardPoolGatePolicy(
            enabled=True, score_path=str(self.path), check_interval_seconds=60))
        self.assertIsNotNone(gate.refresh(NOW))
        self.assertIsNone(gate.refresh(NOW + timedelta(minutes=5)))
        self.assertFalse(gate.paused)

    def test_relative_score_path_uses_root(self):
        gate = RewardPoolGate(RewardPoolGatePolicy(enabled=True), root=Path("/x"))
        self.assertEqual(gate.path, Path("/x/data/reward-pool-tracker/latest-score.json"))

    def test_checked_in_policy_is_the_operator_decision(self):
        policy = load_reward_pool_gate_policy(DEFAULT_REWARD_POOL_GATE_POLICY_PATH)
        self.assertTrue(policy.enabled)
        self.assertEqual(policy.pause_at_or_below, 5.0)
        self.assertEqual(policy.max_score_age_seconds, 10800)
        self.assertEqual(policy.score_path, "data/reward-pool-tracker/latest-score.json")

    def test_policy_rejects_bad_values(self):
        for bad in ({"enabled": "yes"}, {"pause_at_or_below": 0},
                    {"max_score_age_seconds": 60}, {"check_interval_seconds": True}):
            with self.assertRaises(ValueError):
                RewardPoolGatePolicy(**bad)
        path = Path(self.tmp.name) / "policy.json"
        path.write_text(json.dumps({"enabled": True, "surprise": 1}), encoding="utf-8")
        with self.assertRaises(ValueError):
            load_reward_pool_gate_policy(path)


if __name__ == "__main__":
    unittest.main()
