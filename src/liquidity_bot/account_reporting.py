"""Immutable account-report evidence; never a trading freshness clock."""
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from .account_snapshots import AccountEquitySnapshot


@dataclass(frozen=True)
class AccountReportSnapshot:
    equity: AccountEquitySnapshot
    nontrading_cash: Decimal
    day: date
    opening_equity: Decimal
    opening_cash: Decimal | None
    cash_boundary: datetime

    def matches(self, *, day, opening_equity, opening_cash, cash_boundary):
        return (self.day == day and self.opening_equity == opening_equity
                and self.opening_cash == opening_cash
                and self.cash_boundary == cash_boundary)
