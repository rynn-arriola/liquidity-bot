"""One-pass execution proof receipts, never account or mutation authority."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class ExecutionProofReceipt:
    version: tuple
    # None means a completed full proof; a set means exact scoped coverage.
    markets: frozenset[str] | None
    started_at: datetime
    completed_at: datetime


class ExecutionProofCycle:
    """Single serial owner; consume once, never refresh the original clocks.

    The runner supplies the ledger/source revision and checks account readiness,
    pending observations and ownership. This object has no IO or ledger access.
    A new decision sweep gets a new instance; no receipt survives its boundary.
    """

    def __init__(self):
        self.receipt: ExecutionProofReceipt | None = None

    def invalidate(self):
        self.receipt = None

    def take(self, market: str, *, version: tuple, now: datetime):
        receipt, self.receipt = self.receipt, None
        if receipt is None:
            return None, "missing"
        if receipt.version != version:
            return None, "revision"
        if receipt.markets is not None and market not in receipt.markets:
            return None, "scope"
        if not (receipt.started_at <= receipt.completed_at
                and 0 <= (now - receipt.started_at).total_seconds() <= 30
                and 0 <= (now - receipt.completed_at).total_seconds() < 1):
            return None, "age"
        return receipt, "reused"
