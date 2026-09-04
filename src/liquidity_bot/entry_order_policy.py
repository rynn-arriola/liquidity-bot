"""Validated, restart-scoped pending-entry-order policy."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .slot_capacity import MAX_SLOT_CAPACITY


NEW_YORK = "America/New_York"
DEFAULT_ENTRY_ORDER_POLICY_PATH = (
    Path(__file__).resolve().parents[2] / "config" / "entry_order_policy.json"
)
MAX_PENDING_ENTRY_ORDERS = MAX_SLOT_CAPACITY * 2


class EntryOrderPolicyError(ValueError):
    """A policy file cannot safely authorize pending entry orders."""


def _whole_order_cap(value: object, name: str) -> int:
    if isinstance(value, bool):
        raise EntryOrderPolicyError(f"{name} must be a whole order count")
    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as exc:
        raise EntryOrderPolicyError(
            f"{name} must be a whole order count") from exc
    if (not parsed.is_finite()
            or parsed != parsed.to_integral_value()
            or not 0 <= parsed <= MAX_PENDING_ENTRY_ORDERS):
        raise EntryOrderPolicyError(
            f"{name} must be from 0 to {MAX_PENDING_ENTRY_ORDERS}")
    return int(parsed)


def _clock(value: object, name: str) -> time:
    raw = str(value).strip()
    if re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", raw) is None:
        raise EntryOrderPolicyError(
            f"{name} must use HH:MM local time")
    try:
        parsed = time.fromisoformat(raw)
    except ValueError as exc:
        raise EntryOrderPolicyError(
            f"{name} must use HH:MM local time") from exc
    if parsed.tzinfo is not None:  # pragma: no cover - excluded by regex
        raise EntryOrderPolicyError(f"{name} must use HH:MM local time")
    return parsed


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise EntryOrderPolicyError(f"{name} must be an object")
    return value


def _exact_keys(
    document: Mapping[str, Any], expected: set[str], name: str,
) -> None:
    keys = set(document)
    if keys != expected:
        missing = ",".join(sorted(expected - keys)) or "none"
        extra = ",".join(sorted(keys - expected)) or "none"
        raise EntryOrderPolicyError(
            f"{name} keys are invalid (missing={missing}; extra={extra})")


@dataclass(frozen=True)
class EntryOrderPolicy:
    """One coherent time schedule plus monotonically tighter loss tiers."""

    timezone: str
    overnight_start: time
    overnight_end: time
    overnight_max_orders: int
    daytime_max_orders: int
    loss_tiers: tuple[tuple[Decimal, int], ...]

    def __post_init__(self) -> None:
        if self.timezone != NEW_YORK:
            raise EntryOrderPolicyError(
                f"entry-order policy timezone must remain {NEW_YORK}")
        if self.overnight_start == self.overnight_end:
            raise EntryOrderPolicyError(
                "overnight start and end cannot be identical")
        overnight = _whole_order_cap(
            self.overnight_max_orders, "overnight max_pending_entry_orders")
        daytime = _whole_order_cap(
            self.daytime_max_orders, "daytime max_pending_entry_orders")
        if overnight > daytime:
            raise EntryOrderPolicyError(
                "overnight order cap cannot exceed the daytime order cap")
        if not self.loss_tiers:
            raise EntryOrderPolicyError("at least one loss tier is required")
        previous_fraction = Decimal("0")
        previous_cap = daytime
        for fraction, cap in self.loss_tiers:
            try:
                normalized = Decimal(str(fraction))
            except (InvalidOperation, ValueError) as exc:
                raise EntryOrderPolicyError(
                    "loss-tier hard_fraction is invalid") from exc
            tier_cap = _whole_order_cap(
                cap, "loss-tier max_pending_entry_orders")
            if (not normalized.is_finite()
                    or not previous_fraction < normalized < Decimal("1")):
                raise EntryOrderPolicyError(
                    "loss-tier hard_fraction values must increase within 0 and 1")
            if tier_cap > previous_cap:
                raise EntryOrderPolicyError(
                    "loss-tier order caps must stay level or decrease")
            previous_fraction = normalized
            previous_cap = tier_cap

    def is_overnight(self, observed_at: datetime) -> bool:
        if (observed_at.tzinfo is None
                or observed_at.utcoffset() is None):
            raise ValueError("observed_at must be timezone-aware")
        try:
            zone = ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise EntryOrderPolicyError(
                f"{self.timezone} timezone data is unavailable") from exc
        local = observed_at.astimezone(zone).time().replace(tzinfo=None)
        if self.overnight_start < self.overnight_end:
            return self.overnight_start <= local < self.overnight_end
        return local >= self.overnight_start or local < self.overnight_end

    def time_cap(self, observed_at: datetime, *, full_cap: int) -> int:
        selected = (
            self.overnight_max_orders
            if self.is_overnight(observed_at)
            else self.daytime_max_orders
        )
        return min(full_cap, selected)

    def loss_cap(self, loss_fraction: Decimal, *, full_cap: int) -> int:
        fraction = max(Decimal("0"), Decimal(loss_fraction))
        selected = full_cap
        for threshold, tier_cap in self.loss_tiers:
            if fraction >= threshold:
                selected = min(selected, tier_cap)
        return selected


def load_entry_order_policy(
    path: Path = DEFAULT_ENTRY_ORDER_POLICY_PATH,
) -> EntryOrderPolicy:
    """Load a strict policy before credentials, database, or network access."""

    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EntryOrderPolicyError(
            f"entry-order policy could not be read from {path}") from exc
    document = _mapping(raw, "entry-order policy")
    _exact_keys(
        document, {"timezone", "overnight", "daytime", "loss_tiers"},
        "entry-order policy")
    overnight = _mapping(document["overnight"], "overnight")
    daytime = _mapping(document["daytime"], "daytime")
    _exact_keys(
        overnight, {"start", "end", "max_pending_entry_orders"},
        "overnight")
    _exact_keys(daytime, {"max_pending_entry_orders"}, "daytime")
    raw_tiers = document["loss_tiers"]
    if not isinstance(raw_tiers, list):
        raise EntryOrderPolicyError("loss_tiers must be an array")
    tiers: list[tuple[Decimal, int]] = []
    for index, raw_tier in enumerate(raw_tiers):
        tier = _mapping(raw_tier, f"loss_tiers[{index}]")
        _exact_keys(
            tier, {"hard_fraction", "max_pending_entry_orders"},
            f"loss_tiers[{index}]")
        try:
            fraction = Decimal(str(tier["hard_fraction"]).strip())
        except (InvalidOperation, ValueError) as exc:
            raise EntryOrderPolicyError(
                f"loss_tiers[{index}].hard_fraction is invalid") from exc
        tiers.append((fraction, _whole_order_cap(
            tier["max_pending_entry_orders"],
            f"loss_tiers[{index}].max_pending_entry_orders")))
    return EntryOrderPolicy(
        timezone=str(document["timezone"]).strip(),
        overnight_start=_clock(overnight["start"], "overnight.start"),
        overnight_end=_clock(overnight["end"], "overnight.end"),
        overnight_max_orders=_whole_order_cap(
            overnight["max_pending_entry_orders"],
            "overnight.max_pending_entry_orders"),
        daytime_max_orders=_whole_order_cap(
            daytime["max_pending_entry_orders"],
            "daytime.max_pending_entry_orders"),
        loss_tiers=tuple(tiers),
    )
