"""Immutable read results; collection has no ledger or mutation authority."""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable
from .api_diagnostics import api_workflow


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


class DeferredIdentityResolver:
    """Capture immutable identity inputs; only the owner may resolve/adopt.

    Normalizing history in a read worker must not call a ledger resolver whose
    no-ID-intent adoption path can write. Preserve each exact input for ordered
    replay after every worker has completed.
    """

    def __init__(self):
        self.inputs = []

    def __call__(self, exchange_id, raw_order):
        self.inputs.append((exchange_id, json.dumps(raw_order)))
        return None

    def project(self, resolver):
        identities = {}
        for exchange_id, raw in self.inputs:
            identities[exchange_id] = resolver(exchange_id, json.loads(raw))
        return identities


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


class PositionWatch:
    """One immutable, read-only observation in flight; never trading proof.

    Used while the serial owner is scanning candidates. Completion only asks
    that owner to resume strict reconciliation; no worker may project a fill,
    adopt an order, free a slot or authorize a mutation.
    """

    def __init__(self, fetch, clock):
        self.fetch = fetch
        self.clock = clock
        self.task = None
        self.next_at = None

    def start_if_due(self, *, source, generation, scope, interval):
        if self.task is not None:
            return
        started = self.clock()
        if self.next_at is not None and started < self.next_at:
            return

        @api_workflow("catalog_position_watch")
        async def collect():
            try:
                rows = await self.fetch(observed_at=started)
                return AccountObservation(source, generation, tuple(scope), started,
                                          self.clock(), (), tuple(rows), None)
            finally:
                self.next_at = self.clock() + timedelta(seconds=interval)

        self.task = asyncio.create_task(collect())

    def take_ready(self):
        if self.task is None or not self.task.done():
            return None
        task, self.task = self.task, None
        return task.result()

    async def close(self):
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
