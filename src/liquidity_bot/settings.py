"""Safety-first strategy settings independent of secrets and deployment."""

from __future__ import annotations

from dataclasses import dataclass


class UnsafeConfiguration(ValueError):
    """The requested settings violate one or more live safety invariants."""


@dataclass(frozen=True)
class StrategySettings:
    live: bool = False
    max_markets: int = 5
    quote_size_cap: float = 2.0
    max_unpaired_loss_per_market: float = 5.0
    max_total_unpaired_loss: float = 15.0
    # Deliberately has no guessed default. Phase 2 shadow measurements must
    # select the maximum inventory age and bounded exit policy before live can
    # be armed.
    max_unpaired_seconds: float | None = None
    allow_in_play_entries: bool = False
    # Retained for configuration compatibility, but a second account is not
    # required. A shared account is permitted only with an exclusive session
    # handoff from maker-lab and a verified flat exchange snapshot.
    isolated_account_confirmed: bool = False
    exclusive_api_key_confirmed: bool = False
    maker_bot_stopped_confirmed: bool = False
    manual_supervision_confirmed: bool = False
    kill_switch_tested: bool = False

    def problems(self) -> list[str]:
        problems: list[str] = []
        if self.max_markets <= 0:
            problems.append("max_markets must be positive")
        if self.quote_size_cap <= 0:
            problems.append("quote_size_cap must be positive")
        if self.max_unpaired_loss_per_market <= 0:
            problems.append("max_unpaired_loss_per_market must be positive")
        if self.max_total_unpaired_loss <= 0:
            problems.append("max_total_unpaired_loss must be positive")
        if self.max_total_unpaired_loss < self.max_unpaired_loss_per_market:
            problems.append(
                "max_total_unpaired_loss must cover at least one market budget")
        if self.max_unpaired_seconds is not None and self.max_unpaired_seconds <= 0:
            problems.append("max_unpaired_seconds must be positive when set")

        if self.live:
            if self.max_unpaired_seconds is None:
                problems.append("live execution requires a measured maximum inventory age")
            if self.allow_in_play_entries:
                problems.append("initial live execution cannot enter in-play")
            if self.max_markets > 3:
                problems.append("initial live execution is capped at three markets")
            if self.quote_size_cap > 2:
                problems.append("initial live quote size is capped at two contracts")
            if not self.exclusive_api_key_confirmed:
                problems.append("live execution requires exclusive API-key ownership")
            if not self.maker_bot_stopped_confirmed:
                problems.append("maker-lab must be stopped for the live session")
            if not self.manual_supervision_confirmed:
                problems.append("initial live execution requires manual supervision")
            if not self.kill_switch_tested:
                problems.append("the market-scoped kill switch must be tested")
        return problems

    def assert_safe(self) -> None:
        problems = self.problems()
        if problems:
            raise UnsafeConfiguration("; ".join(problems))
