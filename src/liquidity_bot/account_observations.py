"""Immutable read results; collection has no ledger or mutation authority."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Awaitable, Callable


@dataclass(frozen=True)
class AccountObservation:
    source: str
    generation: int
    scope: tuple[str, ...]
    started_at: datetime
    completed_at: datetime
    orders: tuple
    positions: tuple
    history: Any


class AccountSnapshotReader:
    """Single-flight collection, bounded to three independent read chains.

    Each callback must return complete normalized data or raise. No failed or
    cancelled batch can be published as an empty/partial account observation.
    The caller owns projection, order proof and generation/freshness checks.
    """

    def __init__(self):
        self.task = None

    async def collect(self, *, source, generation, scope, clock,
                      orders: Callable[[], Awaitable],
                      positions: Callable[[], Awaitable],
                      history: Callable[[], Awaitable]):
        if self.task is not None:
            raise RuntimeError("account snapshot collection already in flight")
        started = clock()

        async def collect_all():
            tasks = [asyncio.create_task(call()) for call in (orders, positions, history)]
            try:
                results = await asyncio.gather(*tasks)
            except BaseException:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise
            return AccountObservation(source, generation, tuple(scope), started,
                                      clock(), tuple(results[0]), tuple(results[1]),
                                      results[2])

        self.task = asyncio.create_task(collect_all())
        try:
            return await self.task
        finally:
            self.task = None

    async def close(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
