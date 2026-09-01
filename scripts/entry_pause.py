#!/usr/bin/env python3
"""Persist, clear or inspect the live runner's new-entry pause latch."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from liquidity_bot.entry_pause import (  # noqa: E402
    OPERATOR_ENTRY_PAUSE_PATH,
    operator_entry_pause_requested,
    set_operator_entry_pause,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Control the persistent new-entry pause; exits stay live.")
    parser.add_argument("action", choices=("pause", "resume", "status"))
    parser.add_argument(
        "--path", type=Path, default=OPERATOR_ENTRY_PAUSE_PATH,
        help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.action == "pause":
        set_operator_entry_pause(True, path=args.path)
    elif args.action == "resume":
        set_operator_entry_pause(False, path=args.path)
    paused = operator_entry_pause_requested(args.path)
    print("ENTRY_PAUSED" if paused else "ENTRY_ENABLED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
