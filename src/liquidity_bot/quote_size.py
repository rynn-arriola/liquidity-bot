"""Single authority for restart-scoped liquidity quote quantities."""

from __future__ import annotations

import sys
from decimal import Decimal, InvalidOperation
from typing import Sequence


DEFAULT_QUOTE_SIZE = Decimal("5")
MIN_QUOTE_SIZE = Decimal("5")
MAX_QUOTE_SIZE = Decimal("30")


def parse_quote_size(value: object) -> Decimal:
    """Return an authorized whole-contract quantity or raise ``ValueError``."""

    try:
        size = Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(
            f"quote size must be a whole number from {MIN_QUOTE_SIZE} "
            f"to {MAX_QUOTE_SIZE}") from exc
    if (not size.is_finite()
            or size != size.to_integral_value()
            or not MIN_QUOTE_SIZE <= size <= MAX_QUOTE_SIZE):
        raise ValueError(
            f"quote size must be a whole number from {MIN_QUOTE_SIZE} "
            f"to {MAX_QUOTE_SIZE}")
    return size


def main(argv: Sequence[str] | None = None) -> int:
    """Validate and normalize one quantity for the shell launchers."""

    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 1:
        print("usage: python -m liquidity_bot.quote_size QUOTE_SIZE",
              file=sys.stderr)
        return 2
    try:
        size = parse_quote_size(arguments[0])
    except ValueError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    print(int(size))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
