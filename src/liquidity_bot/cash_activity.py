"""Bounded, read-only accounting for cash that is not trading P&L."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

from .adapters import AsyncReadOnlyGateway
from .errors import AdapterError


CASH_ACTIVITY_TYPES = (
    "ACTIVITY_TYPE_ACCOUNT_DEPOSIT",
    "ACTIVITY_TYPE_ACCOUNT_ADVANCED_DEPOSIT",
    "ACTIVITY_TYPE_ACCOUNT_WITHDRAWAL",
    "ACTIVITY_TYPE_REFERRAL_BONUS",
    "ACTIVITY_TYPE_TRANSFER",
)

# The first ET-keyed row copied the old UTC baseline. This one boundary was
# positively reconstructed while the account was flat at 04:00Z and a bounded
# activity walk proved no transfer crossed it. Never apply the correction to a
# database whose stored legacy value differs from the value that was audited.
LEGACY_ET_OPENING_CASH = {
    "2026-08-25": (Decimal("478.6998"), Decimal("466.0899")),
}


class CashActivityUnavailable(AdapterError):
    """A bounded cash-activity walk could not prove the requested window."""


@dataclass(frozen=True)
class NontradingCashChange:
    """One positively classified completed non-trading cash movement."""

    completed_at: datetime
    amount: Decimal


def corrected_opening_cash(
    risk_date: str, stored: Decimal, source: str,
) -> Decimal:
    """Return the audited opening cash for one durable ET risk baseline."""

    if source == "fresh_et_day":
        return stored
    if source != "legacy_utc_cutover":
        raise CashActivityUnavailable(f"unknown ET baseline source: {source}")
    correction = LEGACY_ET_OPENING_CASH.get(str(risk_date))
    if correction is None or stored != correction[0]:
        raise CashActivityUnavailable(
            "legacy UTC baseline has no proven ET correction")
    return correction[1]


def _decimal(value: Any, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise CashActivityUnavailable(f"{field} is invalid") from exc
    if not result.is_finite():
        raise CashActivityUnavailable(f"{field} is not finite")
    return result


def _timestamp(value: Any, field: str) -> datetime:
    text = str(value or "").strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        result = datetime.fromisoformat(text)
    except ValueError as exc:
        raise CashActivityUnavailable(
            f"{field} timestamp is invalid") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise CashActivityUnavailable(
            f"{field} timestamp is timezone-naive")
    return result.astimezone(timezone.utc)


def _first_timestamp(*sources: Any) -> datetime:
    keys = (
        "updateTime", "createTime", "updatedAt", "createdAt",
        "update_time", "create_time", "updated_at", "created_at",
        "timestamp", "time", "date", "settledAt",
    )
    for source in sources:
        if not isinstance(source, dict):
            continue
        for key in keys:
            if source.get(key) not in (None, ""):
                return _timestamp(source[key], "activity")
    raise CashActivityUnavailable("cash activity has no timestamp")


def _amount(raw: Any) -> Decimal:
    if isinstance(raw, dict):
        currency = str(raw.get("currency") or "USD").upper()
        if currency != "USD":
            raise CashActivityUnavailable(
                f"cash activity currency is {currency}, not USD")
        raw = raw.get("value")
    return _decimal(raw, "cash activity amount")


def _completed(status: Any) -> bool:
    clean = str(status or "").upper()
    return ((clean == "COMPLETED" or clean.endswith("_COMPLETED"))
            and not clean.endswith("NOT_COMPLETED"))


def _cash_change_sign(
    activity_type: str, description: str, amount: Decimal,
) -> Decimal:
    if amount < 0:
        return amount
    if activity_type == "ACTIVITY_TYPE_ACCOUNT_WITHDRAWAL":
        return -amount
    if activity_type in {
            "ACTIVITY_TYPE_ACCOUNT_DEPOSIT",
            "ACTIVITY_TYPE_ACCOUNT_ADVANCED_DEPOSIT",
            "ACTIVITY_TYPE_REFERRAL_BONUS"}:
        return amount
    text = description.lower()
    if "withdraw" in text:
        return -amount
    if any(token in text for token in (
            "reward", "deposit", "apple pay", "credit", "bonus", "referral")):
        return amount
    try:
        structured = json.loads(description)
    except (json.JSONDecodeError, TypeError):
        structured = None
    if (isinstance(structured, dict)
            and structured.get("v") == 1
            and str(structured.get("kind") or "").lower() == "remediation"
            and str(structured.get("action") or "").lower() == "goodwill"
            and str(structured.get("scope") or "").lower() == "isv"):
        return amount
    raise CashActivityUnavailable(
        f"unclassified completed cash transfer: {description or '(blank)'}")


def _cash_rows(activity: dict[str, Any]) -> Iterable[tuple[dict, dict]]:
    change = activity.get("accountBalanceChange")
    if not isinstance(change, dict):
        raise CashActivityUnavailable(
            "cash activity has no accountBalanceChange")
    # The balance change is the economic event. Its transaction list contains
    # payment attempts and may grow; summing both levels can count one deposit
    # more than once. Use transactions only as a compatibility fallback.
    if change.get("amount") is not None and change.get("status") is not None:
        yield change, change
        return
    transactions = change.get("transactions")
    if isinstance(transactions, list) and transactions:
        for transaction in transactions:
            if not isinstance(transaction, dict):
                raise CashActivityUnavailable(
                    "cash activity transaction is not an object")
            yield change, transaction
    else:
        yield change, change


async def fetch_nontrading_cash_changes(
    gateway: AsyncReadOnlyGateway,
    *,
    boundary: datetime,
    through: datetime,
    max_pages: int = 4,
    fixed_historical_window: bool = False,
) -> tuple[NontradingCashChange, ...]:
    """Completed deposits, withdrawals, rewards and transfers in a window.

    The venue sorts activity by creation time while cash moves at completion
    time. Therefore an explicit EOF is required; crossing the time boundary in
    one page is not proof that an older-created row did not complete later.

    ``fixed_historical_window`` is only for an immutable past interval. A row
    completed after that interval is irrelevant but does not end the scan. A
    live balance snapshot keeps the default strict behavior because a later
    completion means the bracketed account value may have moved.
    """

    if boundary.tzinfo is None or boundary.utcoffset() is None:
        raise CashActivityUnavailable("cash boundary is timezone-naive")
    if through.tzinfo is None or through.utcoffset() is None:
        raise CashActivityUnavailable("cash snapshot time is timezone-naive")
    if through < boundary:
        raise CashActivityUnavailable("cash snapshot precedes its boundary")
    if max_pages <= 0:
        raise CashActivityUnavailable("cash activity page bound is invalid")

    cursor: str | None = None
    seen_cursors: set[str] = set()
    changes: list[NontradingCashChange] = []
    for _ in range(max_pages):
        params: dict[str, Any] = {
            "limit": 100,
            "types": list(CASH_ACTIVITY_TYPES),
            "sortOrder": "SORT_ORDER_DESCENDING",
        }
        if cursor:
            params["cursor"] = cursor
        response = await gateway._call(
            "GET /v1/portfolio/activities (cash only)",
            lambda params=params: gateway.client.portfolio.activities(params))
        if not isinstance(response, dict) or not isinstance(
                response.get("activities"), list):
            raise CashActivityUnavailable(
                "cash-activity response has no activities list")
        for activity in response["activities"]:
            if not isinstance(activity, dict):
                raise CashActivityUnavailable("cash activity is not an object")
            activity_type = str(activity.get("type") or "").upper()
            if activity_type not in CASH_ACTIVITY_TYPES:
                raise CashActivityUnavailable(
                    f"unexpected cash activity type: {activity_type or '(blank)'}")
            for parent, row in _cash_rows(activity):
                status = row.get("status", parent.get("status"))
                if status in (None, ""):
                    raise CashActivityUnavailable(
                        "cash activity status is missing")
                if not _completed(status):
                    continue
                row_at = _first_timestamp(row, parent, activity)
                if row_at > through:
                    if fixed_historical_window:
                        continue
                    raise CashActivityUnavailable(
                        "completed cash activity changed after the account snapshot")
                # The opening balance already contains every cash movement
                # completed through its receipt timestamp. Treat the lower
                # boundary as exclusive so a same-timestamp activity is not
                # subtracted twice. This also keeps a just-created baseline
                # valid when the following snapshot has the same clock tick.
                if row_at <= boundary:
                    continue
                amount = _amount(row.get("amount", parent.get("amount")))
                description = str(
                    row.get("description", parent.get("description", "")))
                changes.append(NontradingCashChange(
                    completed_at=row_at,
                    amount=_cash_change_sign(
                        activity_type, description, amount),
                ))
        eof = response.get("eof")
        if eof is True:
            return tuple(changes)
        if eof is not False:
            raise CashActivityUnavailable(
                "cash-activity pagination has no explicit eof")
        next_cursor = response.get("nextCursor")
        if not isinstance(next_cursor, str) or not next_cursor.strip():
            raise CashActivityUnavailable(
                "cash-activity pagination ended without eof")
        if next_cursor in seen_cursors:
            raise CashActivityUnavailable(
                "cash-activity pagination cursor repeated")
        seen_cursors.add(next_cursor)
        cursor = next_cursor
    raise CashActivityUnavailable(
        "cash-activity scan did not reach the ET boundary")


async def fetch_nontrading_cash(
    gateway: AsyncReadOnlyGateway,
    *,
    boundary: datetime,
    through: datetime,
    max_pages: int = 4,
) -> Decimal:
    """Net completed non-trading cash in a bounded, proven window."""

    changes = await fetch_nontrading_cash_changes(
        gateway,
        boundary=boundary,
        through=through,
        max_pages=max_pages,
    )
    return sum((change.amount for change in changes), Decimal(0))
