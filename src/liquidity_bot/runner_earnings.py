"""Venue reward-earnings refresh for the commissioning runner.

Moved verbatim from ``live_commissioning.CommissioningRunner`` (pure
relocation, no behavior change). Methods run on the runner instance via
inheritance, so every ``self`` attribute and override resolves as before.
Earnings are persisted for reporting and are never treated as P&L.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from .api_diagnostics import api_workflow
from .bot_era import bot_era_rewards_by_status


class EarningsReportingMixin:

    async def _refresh_due_earnings(self, now: datetime, due: datetime) -> datetime:
        """Reporting must not start an HTTP wait ahead of an imminent exit.

        Keep the overdue report due, rather than postponing it a full interval.
        No account/risk check, quote or position deadline is changed.
        """
        if (now < due or self._stop_requested
                or self._position_near_its_flatten_deadline(now)):
            return due
        try:
            await self._refresh_earnings(now)
            self._runtime_error_counts.pop("reward_earnings_refresh", None)
        except Exception as exc:
            self._earnings_observed_at = None
            self._reward_refresh_failed = True
            await self._report_runtime_error("reward_earnings_refresh", exc)
        return now + timedelta(seconds=self.config.earnings_refresh_seconds)

    @api_workflow("reward_refresh")
    async def _refresh_earnings(self, now: datetime) -> None:
        """Persist authenticated venue earnings without treating them as P&L."""

        # ONE account-wide read, not one per bound market. The per-slug loop
        # cost up to twenty calls through the same 4 req/s governor the quoting
        # loop uses, and returned strictly less: the venue answers a slug
        # filter correctly (verified 2026-08-20 -- a probe slug returned its
        # exact $2.88), but asking only about markets bound RIGHT NOW can never
        # see this bot's lifetime, because reward posts days after the market
        # it was earned in has closed.
        fetched = await self.gateway.fetch_earnings(
            program_type="liquidityProgram")
        rows_all = tuple(
            row for row in fetched
            if row.program_type == "liquidityProgram")
        from .market_quality_measurement import emit, reward_snapshot
        emit(self.quality_sink, "rewards", lambda: reward_snapshot(rows_all, self._now()))

        # Scoped view, unchanged semantics: only markets in the authorized set
        # may be persisted or reported as THIS SESSION'S earnings. A venue
        # deployment that silently ignored a query filter must not be able to
        # import a shared-account reward as ours -- pinned by
        # `test_ignored_earnings_query_cannot_import_other_market_rewards`.
        authorized = set(self.active_slugs)
        scoped = tuple(row for row in rows_all if row.market_slug in authorized)
        self.ledger.record_reward_earnings(scoped, observed_at=now)
        totals: dict[str, Decimal] = {}
        for row in scoped:
            status = str(row.status).upper()
            totals[status] = totals.get(status, Decimal("0")) + Decimal(
                str(row.reward))
        self._earnings_by_status = totals

        # Lifetime view, for the bot's own bottom line. Account-wide by
        # necessity -- the markets it earned in are long closed -- and scoped
        # by DATE to this bot's era, because the account carries $86.51 of
        # maker-lab reward predating it. Counting that here overstates the bot
        # by about 2x, which has happened twice. See `bot_era.py`.
        self._bot_era_rewards = bot_era_rewards_by_status(rows_all)
        self._earnings_observed_at = now
        self._report_rewards_observed_at = now
        self._reward_refresh_failed = False
