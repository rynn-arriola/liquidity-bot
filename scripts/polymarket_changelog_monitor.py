#!/usr/bin/env python3
"""Fetch official changelog RSS and alert once per new entry."""

from __future__ import annotations

import argparse
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from liquidity_bot.changelog_monitor import ChangelogEntry, check_feed  # noqa: E402
from liquidity_bot.notifications import DiscordNotifier  # noqa: E402
from liquidity_bot.runtime_secrets import load_runtime_secrets  # noqa: E402


RSS_URL = "https://docs.polymarket.us/changelog/rss.xml"
MAX_FEED_BYTES = 1_000_000


def fetch_feed() -> bytes:
    request = urllib.request.Request(
        RSS_URL, headers={"User-Agent": "LiquidityBotChangelogMonitor/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = response.read(MAX_FEED_BYTES + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"changelog RSS fetch failed: {type(exc).__name__}") from exc
    if len(payload) > MAX_FEED_BYTES:
        raise RuntimeError("changelog RSS exceeded the one-megabyte safety bound")
    return payload


def message(entries: tuple[ChangelogEntry, ...]) -> str:
    lines = ["Polymarket published a new official changelog item:"]
    for entry in entries[:8]:
        suffix = f" — {entry.published}" if entry.published else ""
        lines.append(f"• {entry.title}{suffix}\n  {entry.link}")
    if len(entries) > 8:
        lines.append(f"• plus {len(entries) - 8} more item(s); inspect the feed")
    lines.append(
        "Operator action: compare the Retail effective window with bot symptoms; "
        "do not change trading behavior from the announcement alone.")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--state", default="data/polymarket-changelog-state.json")
    parser.add_argument("--post-discord", action="store_true")
    args = parser.parse_args()
    notifier = None
    if args.post_discord:
        discord = DiscordNotifier(load_runtime_secrets())
        notifier = lambda entries: discord.send(
            message(entries), critical=False, channel="errors").sent
    new_entries = check_feed(
        fetch_feed(), ROOT / args.state, notify=notifier)
    if new_entries:
        print(message(new_entries))
        if args.post_discord and notifier is not None:
            # `check_feed` already called the notifier; this line only reports
            # the committed outcome and never posts a second time.
            print("changelog alert posted to Discord errors channel")
    else:
        print("changelog unchanged (or initial baseline established)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
