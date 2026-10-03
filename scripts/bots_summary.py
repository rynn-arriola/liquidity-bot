#!/usr/bin/env python3
"""Post the five-minute "Trading Bots" card (Liquidity Trader + Endgame Trader).

    bots_summary.py --post             build and post to the summary channel
    bots_summary.py --preview out.png  build only, write the image

Run from the live bot directory (its data/ and config/). Read-only apart from the
Discord post: local databases, config, the process list and one balance read on the
opportunistic report lane of the shared API gate.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from liquidity_bot.bots_summary import (  # noqa: E402
    BotsModel, endgame_block, liquidity_block, processes, render_bots_card, text_payload)

CRON_MARK = "# liquidity-bot trading bots summary"


async def balance() -> tuple:
    try:
        from liquidity_bot.account_snapshots import AsyncAccountSnapshotGateway
        from liquidity_bot.adapters import (
            AsyncReadOnlyGateway, OPPORTUNISTIC_REPORT_RATE_PER_SECOND, shared_api_limiter)
        from liquidity_bot.probe import client_from_environment
        gateway = AsyncReadOnlyGateway(
            client_from_environment(),
            limiter=shared_api_limiter(OPPORTUNISTIC_REPORT_RATE_PER_SECOND,
                                       state_path=Path.cwd() / "data" / "api-rate-limiter.state"),
            default_traffic_class="report")
        account = AsyncAccountSnapshotGateway(gateway, resolve_client_id=lambda _x: None)
        b = await asyncio.wait_for(account.fetch_balance(observed_at=datetime.now(timezone.utc)), 60)
        return b.current_balance, b.buying_power
    except (Exception, SystemExit):     # missing credentials exit; the card still posts
        return None, None


def build(now: datetime, *, cash=None, buying_power=None, endgame_policy: Path | None = None) -> BotsModel:
    ps = processes()
    return BotsModel(
        observed_at=now,
        liquidity=liquidity_block(Path("data/commissioning.db"), Path("config"), now, ps),
        endgame=endgame_block(Path("data/late-favorite/trader.db"),
                              endgame_policy or ROOT / "config" / "late_favorite_policy.json", now, ps),
        cash=cash, buying_power=buying_power)


def cron_line(root: Path, code: Path = ROOT) -> str:
    return (f"*/5 * * * * cd {root} && flock -n data/bots-summary.lock timeout 240 nice -n 10 "
            f"{root}/.venv/bin/python -B {code}/scripts/bots_summary.py --post "
            f">> logs/bots-summary.log 2>&1 {CRON_MARK}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--post", action="store_true")
    ap.add_argument("--preview", type=Path)
    a = ap.parse_args(argv)
    if not Path("data/commissioning.db").exists():
        raise SystemExit("run from the live bot directory")
    now = datetime.now(timezone.utc)
    cash, power = asyncio.run(balance())
    model = build(now, cash=cash, buying_power=power)
    payload = text_payload(model)
    try:
        png = render_bots_card(model)
    except Exception as exc:
        png = None
        print(f"bots summary image unavailable: {type(exc).__name__}", file=sys.stderr)
    if a.preview:
        if png:
            a.preview.write_bytes(png)
        print(payload["embeds"][0]["description"])
        return 0
    if a.post:
        from liquidity_bot.notifications import DiscordNotifier
        from liquidity_bot.runtime_secrets import load_runtime_secrets
        from liquidity_bot.summary_image import post
        secrets = load_runtime_secrets()
        sent = False
        if png is not None and secrets.discord_webhook_url:
            sent = post(secrets.discord_webhook_url, png, {"embeds": [dict(payload["embeds"][0],
                                                                             footer={"text": ""})]})
        if not sent and png is None:
            sent = DiscordNotifier(secrets).send_payload(payload, channel="summary").sent
        print(f"[bots-summary] at={now.isoformat()} sent={sent}", flush=True)
        return 0 if sent else 1
    print(payload["embeds"][0]["description"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
