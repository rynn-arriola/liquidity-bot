"""Best-effort Discord alert entry point for the external process supervisor."""

from __future__ import annotations

import argparse

from .notifications import DiscordNotifier
from .runtime_secrets import load_runtime_secrets


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("message")
    parser.add_argument("--critical", action="store_true")
    args = parser.parse_args()
    secrets = load_runtime_secrets()
    result = DiscordNotifier(secrets).send(
        args.message, critical=args.critical, channel="errors")
    print(result.reason)
    return 0 if result.sent else 1


if __name__ == "__main__":
    raise SystemExit(main())
