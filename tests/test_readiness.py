from __future__ import annotations

import unittest

from liquidity_bot.readiness import (
    ControlledTrialConfig,
    ReadinessEvidence,
    evaluate_readiness,
)


def config() -> ControlledTrialConfig:
    return ControlledTrialConfig(
        max_markets=1, quote_size=1, session_minutes=30,
        max_inventory_age_seconds=30, hard_stop_loss_usd=5,
        rest_requests_per_second=4,
        pregame_only=True)


def evidence(**changes) -> ReadinessEvidence:
    values = dict(
        shadow_complete_days=3,
        selected_horizon_exit_samples=100,
        selected_horizon_completion_rate=.99,
        selected_horizon_budget_pass_rate=.99,
        phase3_fault_tests_passed=True,
        phase4_logical_hours=4,
        phase4_critical_failures=0,
        phase4_max_age_seconds=30,
        private_stream_verified=True,
        account_flat=True,
        account_open_orders=0,
        unresolved_intents=0,
        unknown_orders=0,
        isolated_account_confirmed=True,
        trial_account_fingerprint="trial-account",
        maker_account_fingerprint="maker-account",
        api_key_exclusive_confirmed=True,
        maker_bot_stopped_confirmed=True,
        manual_supervision_confirmed=True,
        kill_switch_tested=True,
        attributable_paid_reward_days=3,
        independent_paid_events=20,
        controlled_trial_sessions=5,
        conservative_expected_net_usd=.01,
        pnl_reconciliation_gap_usd=.01,
        controlled_live_critical_incidents=0,
    )
    values.update(changes)
    return ReadinessEvidence(**values)


class ReadinessTests(unittest.TestCase):
    def test_current_missing_evidence_refuses_trial(self):
        verdict = evaluate_readiness(config(), evidence(
            shadow_complete_days=0,
            selected_horizon_exit_samples=0,
            selected_horizon_completion_rate=0,
            selected_horizon_budget_pass_rate=0,
            isolated_account_confirmed=False,
            trial_account_fingerprint="maker-account"))
        self.assertEqual(verdict.status, "DO_NOT_START_TRIAL")
        self.assertFalse(verdict.trial_ready)

    def test_trial_can_collect_reward_calibration_but_cannot_scale_without_it(self):
        verdict = evaluate_readiness(config(), evidence(
            attributable_paid_reward_days=0,
            independent_paid_events=0,
            controlled_trial_sessions=0,
            conservative_expected_net_usd=None,
            pnl_reconciliation_gap_usd=None))
        self.assertEqual(verdict.status, "TRIAL_ELIGIBLE_NOT_SCALE")
        self.assertTrue(verdict.trial_ready)
        self.assertFalse(verdict.scale_ready)

    def test_scale_requires_all_paid_and_reconciled_evidence(self):
        verdict = evaluate_readiness(config(), evidence())
        self.assertEqual(verdict.status, "SCALE_ELIGIBLE")
        self.assertTrue(verdict.scale_ready)

    def test_api_budget_above_four_blocks_initial_trial(self):
        unsafe = ControlledTrialConfig(**{
            **config().__dict__, "rest_requests_per_second": 5})
        verdict = evaluate_readiness(unsafe, evidence())
        self.assertIn("initial trial REST budget must be in (0, 4] requests/second",
                      verdict.trial_blockers)

    def test_same_account_is_allowed_only_with_exclusive_stopped_handoff(self):
        same = evidence(
            isolated_account_confirmed=False,
            trial_account_fingerprint="maker-account",
            maker_account_fingerprint="maker-account")
        self.assertTrue(evaluate_readiness(config(), same).trial_ready)
        not_exclusive = evaluate_readiness(
            config(), evidence(
                isolated_account_confirmed=False,
                trial_account_fingerprint="maker-account",
                maker_account_fingerprint="maker-account",
                api_key_exclusive_confirmed=False))
        self.assertFalse(not_exclusive.trial_ready)
        self.assertIn(
            "exclusive API-key ownership is not confirmed",
            not_exclusive.trial_blockers)


if __name__ == "__main__":
    unittest.main()
