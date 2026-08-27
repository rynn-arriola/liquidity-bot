"""Process exclusivity and session-level live risk controls."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import BinaryIO, Mapping


class LeaseHeld(RuntimeError):
    """Another local process already owns the execution lease."""


class SingleInstanceLease:
    """Host-local advisory lock released automatically when the process dies."""

    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()
        self._file: BinaryIO | None = None

    def acquire(self) -> None:
        if self._file is not None:
            raise RuntimeError("lease is already acquired by this object")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as exc:
            handle.close()
            raise LeaseHeld(f"execution lease is already held: {self.path}") from exc
        self._file = handle

    def release(self) -> None:
        handle, self._file = self._file, None
        if handle is None:
            return
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    def __enter__(self) -> "SingleInstanceLease":
        self.acquire()
        return self

    def __exit__(self, *_: object) -> None:
        self.release()


def _decimal(value: Decimal | str | int | float, field_name: str) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field_name} is not a decimal") from exc
    if not result.is_finite():
        raise ValueError(f"{field_name} must be finite")
    return result


@dataclass
class SessionRiskGuard:
    """Block new exposure after time, loss, stale equity, or critical state.

    ``equity`` must include marked open positions; raw cash or buying power is
    not a valid substitute. Exits and cancels remain allowed after the guard
    trips.
    """

    started_at: datetime
    starting_equity: Decimal
    max_session_loss: Decimal
    max_market_loss: Decimal
    max_duration_seconds: float
    hard_stop_loss: Decimal | None = None
    max_equity_age_seconds: float = 10.0
    current_equity: Decimal | None = None
    equity_observed_at: datetime | None = None
    realized_by_market: dict[str, Decimal] = field(default_factory=dict)
    realized_pnl: Decimal = Decimal("0")
    critical_reason: str | None = None

    def __post_init__(self) -> None:
        if self.started_at.tzinfo is None or self.started_at.utcoffset() is None:
            raise ValueError("started_at must be timezone-aware")
        self.starting_equity = _decimal(self.starting_equity, "starting_equity")
        self.max_session_loss = _decimal(self.max_session_loss, "max_session_loss")
        self.max_market_loss = _decimal(self.max_market_loss, "max_market_loss")
        if self.hard_stop_loss is not None:
            self.hard_stop_loss = _decimal(
                self.hard_stop_loss, "hard_stop_loss")
        if min(self.max_session_loss, self.max_market_loss) <= 0:
            raise ValueError("loss limits must be positive")
        if (self.hard_stop_loss is not None
                and self.hard_stop_loss < self.max_session_loss):
            raise ValueError("hard stop cannot be below the session loss limit")
        if self.max_duration_seconds <= 0 or self.max_equity_age_seconds <= 0:
            raise ValueError("duration and equity freshness must be positive")

    def adopt_day_baseline(
        self, equity: Decimal | str | int | float,
    ) -> None:
        """Re-point the hard stop at the ET risk day's opening equity.

        `starting_equity` is stamped at preflight, which makes the emergency
        stop measure THIS PROCESS rather than the day. That is the ratchet
        recorded in FINDINGS: lose $30, crash, restart, and the baseline
        re-stamps at the lower equity so the $30 is forgiven and it takes
        another full $45 from the new floor to fire. The account can walk down
        indefinitely with every step inside the limit.

        The caller supplies the day's opening equity from the ledger, which
        survives a restart. Validated here rather than assigned directly so a
        non-finite value cannot silently disable the stop.
        """

        self.starting_equity = _decimal(equity, "starting_equity")

    def observe_equity(self, equity: Decimal | str | int | float, *,
                       observed_at: datetime) -> None:
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ValueError("equity observation must be timezone-aware")
        self.current_equity = _decimal(equity, "equity")
        self.equity_observed_at = observed_at

    def observe_realized(self, value: Decimal | str | int | float) -> None:
        """Record realised session P&L, which is what the session stop reads."""

        self.realized_pnl = _decimal(value, "realized_pnl")

    def observe_market_realized(
        self, values: Mapping[str, Decimal | str | int | float],
    ) -> None:
        self.realized_by_market = {
            str(slug): _decimal(value, f"realized P&L for {slug}")
            for slug, value in values.items()
        }

    def trip_critical(self, reason: str) -> None:
        self.critical_reason = str(reason or "unspecified critical failure")

    def blockers(self, *, now: datetime) -> tuple[str, ...]:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        result: list[str] = []
        elapsed = (now - self.started_at).total_seconds()
        if elapsed < 0:
            result.append("session clock moved backwards")
        elif elapsed >= self.max_duration_seconds:
            result.append("session duration limit reached")
        if self.critical_reason:
            result.append(f"critical failure: {self.critical_reason}")
        if self.current_equity is None or self.equity_observed_at is None:
            result.append("marked account equity is unknown")
        else:
            age = (now - self.equity_observed_at).total_seconds()
            if age < 0 or age > self.max_equity_age_seconds:
                result.append("marked account equity is stale")
        # The session stop fires on REALISED trading loss, not on marked
        # equity. Marked equity moves with every unrealised tick: 320 open
        # contracts swing $38 on twelve cents, so an equity-based stop trips on
        # paper movement with nothing traded. Marked equity still backs the
        # emergency hard stop below, which is what it is for.
        if -self.realized_pnl >= self.max_session_loss:
            result.append("session loss limit reached")
        if any(value <= -self.max_market_loss
               for value in self.realized_by_market.values()):
            result.append("market loss limit reached")
        return tuple(result)

    def can_add_new_risk(self, *, now: datetime) -> bool:
        # A marked-equity breach is provisional while its open exposure is
        # being closed, but it must still block every new entry immediately.
        # The runner owns the later cohort-close confirmation.
        return (not self.blockers(now=now)
                and not self.hard_stop_reached(now=now))

    def hard_stop_reached(self, *, now: datetime) -> bool:
        """True only for the emergency equity ceiling, never for stale data."""

        if self.hard_stop_loss is None:
            return False
        if self.current_equity is None or self.equity_observed_at is None:
            return False
        age = (now - self.equity_observed_at).total_seconds()
        if age < 0 or age > self.max_equity_age_seconds:
            return False
        return self.starting_equity - self.current_equity >= self.hard_stop_loss
