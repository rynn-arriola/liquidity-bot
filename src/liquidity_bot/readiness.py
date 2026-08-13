"""Phase 5 controlled-trial and scale gates.

This module cannot arm or execute trading. It only evaluates explicit evidence
and returns a reproducible verdict with every blocker.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ControlledTrialConfig:
    max_markets: int
    quote_size: float
    session_minutes: int
    max_inventory_age_seconds: int
    max_market_loss_usd: float
    max_session_loss_usd: float
    rest_requests_per_second: float = 4.0
    pregame_only: bool = True


@dataclass(frozen=True)
class ReadinessEvidence:
    shadow_complete_days: int
    selected_horizon_exit_samples: int
    selected_horizon_completion_rate: float
    selected_horizon_budget_pass_rate: float
    phase3_fault_tests_passed: bool
    phase4_logical_hours: float
    phase4_critical_failures: int
    phase4_max_age_seconds: float
    private_stream_verified: bool
    account_flat: bool
    account_open_orders: int
    unresolved_intents: int
    unknown_orders: int
    isolated_account_confirmed: bool
    trial_account_fingerprint: str
    maker_account_fingerprint: str
    api_key_exclusive_confirmed: bool
    maker_bot_stopped_confirmed: bool
    manual_supervision_confirmed: bool
    kill_switch_tested: bool
    attributable_paid_reward_days: int = 0
    independent_paid_events: int = 0
    controlled_trial_sessions: int = 0
    conservative_expected_net_usd: float | None = None
    pnl_reconciliation_gap_usd: float | None = None
    controlled_live_critical_incidents: int = 0


@dataclass(frozen=True)
class ReadinessVerdict:
    status: str
    trial_ready: bool
    scale_ready: bool
    trial_blockers: tuple[str, ...]
    scale_blockers: tuple[str, ...]


def evaluate_readiness(
    config: ControlledTrialConfig, evidence: ReadinessEvidence,
) -> ReadinessVerdict:
    trial: list[str] = []
    if not 1 <= config.max_markets <= 3:
        trial.append("controlled trial must use one to three markets")
    if not 0 < config.quote_size <= 2:
        trial.append("controlled trial quote size must be in (0, 2]")
    if not 0 < config.session_minutes <= 60:
        trial.append("controlled trial session must be at most 60 minutes")
    if config.max_inventory_age_seconds <= 0:
        trial.append("a measured positive inventory timeout is required")
    if min(config.max_market_loss_usd, config.max_session_loss_usd) <= 0:
        trial.append("positive market and session loss limits are required")
    if config.max_session_loss_usd < config.max_market_loss_usd:
        trial.append("session loss limit must cover one market")
    if not 0 < config.rest_requests_per_second <= 4:
        trial.append("initial trial REST budget must be in (0, 4] requests/second")
    if not config.pregame_only:
        trial.append("controlled trial must be pregame only")
    if evidence.shadow_complete_days < 3:
        trial.append("at least three complete shadow collection days are required")
    if evidence.selected_horizon_exit_samples < 100:
        trial.append("selected timeout needs at least 100 shadow exit samples")
    if evidence.selected_horizon_completion_rate < .99:
        trial.append("selected timeout stressed completion rate must be at least 99%")
    if evidence.selected_horizon_budget_pass_rate < .99:
        trial.append("selected timeout loss-budget pass rate must be at least 99%")
    if not evidence.phase3_fault_tests_passed:
        trial.append("Phase 3 fault suite has not passed")
    if evidence.phase4_logical_hours < 4:
        trial.append("Phase 4 requires at least four logical hours")
    if evidence.phase4_critical_failures:
        trial.append("Phase 4 healthy path has critical failures")
    if evidence.phase4_max_age_seconds > config.max_inventory_age_seconds:
        trial.append("Phase 4 inventory exceeded the configured timeout")
    if not evidence.private_stream_verified:
        trial.append("private order/position stream is not verified")
    if not evidence.account_flat or evidence.account_open_orders != 0:
        trial.append("trial account must be flat with zero open orders")
    if evidence.unresolved_intents or evidence.unknown_orders:
        trial.append("execution ledger has unresolved or unknown state")
    if not evidence.trial_account_fingerprint:
        trial.append("trial account fingerprint is missing")
    if not evidence.api_key_exclusive_confirmed:
        trial.append("exclusive API-key ownership is not confirmed")
    if not evidence.maker_bot_stopped_confirmed:
        trial.append("maker-lab must remain stopped during the trial")
    if not evidence.manual_supervision_confirmed:
        trial.append("manual supervision is not confirmed")
    if not evidence.kill_switch_tested:
        trial.append("bounded market-scoped kill switch is not tested")

    scale: list[str] = list(trial)
    if evidence.attributable_paid_reward_days < 3:
        scale.append("three attributable PAID reward days are required to scale")
    if evidence.independent_paid_events < 20:
        scale.append("at least 20 independent paid events are required to scale")
    if evidence.controlled_trial_sessions < 5:
        scale.append("at least five controlled trial sessions are required to scale")
    if (evidence.conservative_expected_net_usd is None
            or evidence.conservative_expected_net_usd <= 0):
        scale.append("conservative paid-reward plus trading net must be positive")
    if (evidence.pnl_reconciliation_gap_usd is None
            or abs(evidence.pnl_reconciliation_gap_usd) > .01):
        scale.append("exchange and ledger P&L must reconcile within $0.01")
    if evidence.controlled_live_critical_incidents:
        scale.append("controlled live critical incidents must be zero")

    trial_ready = not trial
    scale_ready = trial_ready and not scale
    status = ("SCALE_ELIGIBLE" if scale_ready else
              "TRIAL_ELIGIBLE_NOT_SCALE" if trial_ready else
              "DO_NOT_START_TRIAL")
    return ReadinessVerdict(
        status, trial_ready, scale_ready, tuple(trial), tuple(scale))


def _load(path: Path) -> tuple[ControlledTrialConfig, ReadinessEvidence]:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return (ControlledTrialConfig(**data["config"]),
            ReadinessEvidence(**data["evidence"]))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate fail-closed controlled-trial and scale gates")
    parser.add_argument("evidence", type=Path)
    args = parser.parse_args()
    config, evidence = _load(args.evidence)
    verdict = evaluate_readiness(config, evidence)
    print(json.dumps({
        "config": asdict(config),
        "verdict": asdict(verdict),
        "orders_sent_to_polymarket": 0,
    }, indent=2, sort_keys=True))
    return 0 if verdict.scale_ready else 2


if __name__ == "__main__":
    raise SystemExit(main())
