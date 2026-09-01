"""Single authority for restart-scoped liquidity slot canaries."""

from __future__ import annotations

import sys
from decimal import Decimal, InvalidOperation
from typing import Sequence


DEFAULT_SLOT_CAPACITY = 20
MIN_SLOT_CAPACITY = 20
MAX_SLOT_CAPACITY = 32


def parse_slot_capacity(value: object) -> int:
    """Return an authorized whole-market capacity or raise ``ValueError``."""

    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(
            f"slot capacity must be a whole number from {MIN_SLOT_CAPACITY} "
            f"to {MAX_SLOT_CAPACITY}") from exc
    if (not parsed.is_finite()
            or parsed != parsed.to_integral_value()
            or not MIN_SLOT_CAPACITY <= parsed <= MAX_SLOT_CAPACITY):
        raise ValueError(
            f"slot capacity must be a whole number from {MIN_SLOT_CAPACITY} "
            f"to {MAX_SLOT_CAPACITY}")
    return int(parsed)


def main(argv: Sequence[str] | None = None) -> int:
    """Validate and normalize one capacity for the shell launchers."""

    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 1:
        print("usage: python -m liquidity_bot.slot_capacity SLOT_CAPACITY",
              file=sys.stderr)
        return 2
    try:
        capacity = parse_slot_capacity(arguments[0])
    except ValueError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    print(capacity)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
