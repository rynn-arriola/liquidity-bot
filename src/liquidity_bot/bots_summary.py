"""Five-minute "Trading Bots" card: Liquidity Trader + Endgame Trader on one image.

Operator 2026-10-03: "Can we change summary section and add end game trader info, you
can edit what we show on summary remove unnecessary data and just provide useful
information there", "The one i want you to change is the bots summary, not the reward
webhook". The card is built by its own 5-minute job (scripts/bots_summary.py), so it
posts whichever bot is running. Everything here is read-only: local databases, config,
the process list and one balance read done by the caller.
"""
from __future__ import annotations

import io
import json
import sqlite3
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from .reward_calendar import NEW_YORK, reward_day

ACTIVE_ORDER_STATES = ("OPEN", "NEW", "PARTIAL", "PENDING", "SUBMITTED")


@dataclass
class LiquidityBlock:
    running: bool = False
    strategy: str | None = None
    window: str | None = None
    window_loss: Decimal | None = None
    window_limit: Decimal | None = None
    entry_cap: int | None = None
    working_orders: int | None = None
    positions: int | None = None
    settled_today: Decimal | None = None


@dataclass
class EndgameBlock:
    running: bool = False
    live: bool = False
    trades: int = 0
    won: int = 0
    lost: int = 0
    result_today: Decimal = Decimal(0)
    open_positions: int = 0
    at_risk: Decimal = Decimal(0)
    daily_stop: Decimal | None = None
    settings: str = ""
    recent: list = field(default_factory=list)   # (time, sport, match, cost, outcome)


@dataclass
class BotsModel:
    observed_at: datetime
    liquidity: LiquidityBlock
    endgame: EndgameBlock
    cash: Decimal | None = None
    buying_power: Decimal | None = None


def processes() -> str:
    try:
        return subprocess.run(["ps", "-eo", "args="], capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return ""


def liquidity_block(db: Path, config_dir: Path, now: datetime, ps: str) -> LiquidityBlock:
    block = LiquidityBlock(running="liquidity_bot.live_commissioning" in ps)
    try:
        from .strategy import load_strategy_policy
        block.strategy = load_strategy_policy(config_dir / "strategy_policy.json").label
    except Exception:
        pass
    try:
        from .risk_windows import load_risk_window_policy
        policy = load_risk_window_policy(config_dir / "risk_window_policy.json")
        start, end = policy.bounds(now)
        block.window = (f"{start.astimezone(NEW_YORK):%H:%M}–{end.astimezone(NEW_YORK):%H:%M} ET")
        block.window_limit = Decimal(str(policy.stop_loss_usd))
    except Exception:
        start = None
    if not db.exists():
        return block
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
        try:
            if start is not None:
                row = conn.execute("SELECT settled_pnl, entry_cap FROM execution_settled_risk_windows "
                                   "WHERE window_start=?", (start.isoformat(),)).fetchone()
                if row is not None:
                    block.window_loss = max(Decimal(0), -Decimal(str(row[0])))
                    block.entry_cap = int(row[1]) if row[1] is not None else None
                else:
                    block.window_loss = Decimal(0)
            marks = ",".join("?" for _ in ACTIVE_ORDER_STATES)
            block.working_orders = conn.execute(
                f"SELECT COUNT(*) FROM execution_orders WHERE status IN ({marks})",
                ACTIVE_ORDER_STATES).fetchone()[0]
            block.positions = conn.execute(
                "SELECT COUNT(*) FROM execution_positions WHERE CAST(net_qty AS REAL)!=0").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error:
        pass
    try:
        from .summary_report import settled_totals
        block.settled_today = settled_totals(db, now)["today"]["net"]
    except Exception:
        pass
    return block


def _short_match(slug: str) -> str:
    parts = slug.split("-")
    core = [p for p in parts[1:] if not p.isdigit() and len(p) > 1][:3]
    return "-".join(core)[:22] or slug[:22]


def endgame_block(trader_db: Path, policy_path: Path, now: datetime, ps: str) -> EndgameBlock:
    block = EndgameBlock(running="late_favorite_trader.py run" in ps)
    try:
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        block.live = bool(policy.get("enabled")) and bool(policy.get("live_orders"))
        block.daily_stop = Decimal(str(policy.get("daily_loss_stop_usd")))
        block.settings = (f"{round(policy['min_price'] * 100)}–{round(policy['max_price'] * 100)}¢ · "
                          f"spread ≤{round(policy['max_spread'] * 100)}¢ · ${policy['risk_per_trade_usd']}/trade · "
                          f"max {policy['max_open_positions']} open")
    except Exception:
        pass
    if not trader_db.exists():
        return block
    today = reward_day(now)
    try:
        conn = sqlite3.connect(f"file:{trader_db}?mode=ro", uri=True, timeout=2)
        try:
            rows = conn.execute(
                "SELECT created_at, market_slug, cost, avg_price, qty, filled_qty, settlement, result, "
                "settled_at, game_state FROM trades WHERE mode='live' AND status='FILLED' ORDER BY id").fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return block
    for created, slug, cost, avg, qty, filled, settlement, result, settled_at, game in rows:
        paid = Decimal(str(avg if avg is not None else cost))
        if settlement is None:
            block.open_positions += 1
            block.at_risk += paid * Decimal(str(filled or qty))
        if reward_day(datetime.fromisoformat(created.replace("Z", "+00:00"))) == today:
            block.trades += 1
        if result is not None and settled_at and reward_day(
                datetime.fromisoformat(settled_at.replace("Z", "+00:00"))) == today:
            block.won += int(result > 0)
            block.lost += int(result <= 0)
            block.result_today += Decimal(str(result))
    for created, slug, cost, avg, qty, filled, settlement, result, settled_at, game in rows[-5:][::-1]:
        at = datetime.fromisoformat(created.replace("Z", "+00:00")).astimezone(NEW_YORK)
        try:
            sport = (json.loads(game or "{}").get("type") or "")[:12]
        except ValueError:
            sport = ""
        outcome = "open" if result is None else f"{'+' if result > 0 else '-'}${abs(result):.2f}"
        block.recent.append((at.strftime("%I:%M %p").lstrip("0"), sport, _short_match(slug),
                             f"{round(float(avg if avg is not None else cost) * 100)}¢", outcome))
    return block


def money(value: Decimal | None, signed: bool = True) -> str:
    if value is None:
        return "--"
    sign = ("+" if value > 0 else "-" if value < 0 else "") if signed else ("-" if value < 0 else "")
    return f"{sign}${abs(value):,.2f}"


def text_payload(model: BotsModel) -> dict:
    """Plain Discord fallback (and image alt text) with the same values."""
    liq, end = model.liquidity, model.endgame
    lines = [
        f"Liquidity Trader: {'ON' if liq.running else 'OFF'} · today {money(liq.settled_today)} · "
        f"window loss {money(liq.window_loss, False)} / {money(liq.window_limit, False)}",
        f"Endgame Trader: {'LIVE' if end.running and end.live else 'ON (practice)' if end.running else 'OFF'} · "
        f"today {end.won}-{end.lost} {money(end.result_today)} · open {end.open_positions} "
        f"({money(end.at_risk, False)} at risk)",
        f"Cash {money(model.cash, False)} · buying power {money(model.buying_power, False)}",
    ]
    return {"embeds": [{"title": "TRADING BOTS", "description": "\n".join(lines),
                        "color": 0x2ECC71, "footer": {"text": end.settings},
                        "timestamp": model.observed_at.isoformat()}]}


# --- image -----------------------------------------------------------------------

W = 1200
BG, PANEL, HEAD, WHITE, MUTED = "#101720", "#1a2533", "#5eb6ff", "#eef3fa", "#a5b5c9"
GREEN, RED, AMBER, TRACK = "#7bdfb2", "#ff959b", "#ffd166", "#3a4658"


def _font(size: int, bold: bool = False):
    from PIL import ImageFont
    paths = (["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "C:/Windows/Fonts/arialbd.ttf"] if bold
             else ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "C:/Windows/Fonts/arial.ttf"])
    for path in paths:
        if Path(path).is_file():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)


def _color(value: str) -> str:
    return RED if value.startswith("-") else GREEN if value.startswith("+") else WHITE


def render_bots_card(model: BotsModel) -> bytes:
    from PIL import Image, ImageDraw
    liq, end = model.liquidity, model.endgame
    rows_recent = max(1, len(end.recent))
    height = 1010 + 52 * rows_recent
    image = Image.new("RGB", (W, height), BG)
    d = ImageDraw.Draw(image)

    def text(value, x, y, size=30, color=WHITE, bold=False, right=False, max_width=W - 80):
        f = _font(size, bold)
        while d.textlength(value, font=f) > max_width and size > 14:
            size -= 1
            f = _font(size, bold)
        d.text((x, y), value, font=f, fill=color, anchor="ra" if right else "la")

    def meter(x, y, w, used, limit):
        d.rounded_rectangle((x, y, x + w, y + 20), radius=10, fill=TRACK)
        if used is None or not limit:
            return
        share = max(0.0, min(1.0, float(used / limit)))
        if share > 0:
            fill = GREEN if share < 0.5 else AMBER if share < 0.8 else RED
            d.rounded_rectangle((x, y, x + max(20, int(w * share)), y + 20), radius=10, fill=fill)

    stamp = model.observed_at.astimezone(NEW_YORK)
    text("TRADING BOTS", 36, 28, size=42, bold=True)
    text(stamp.strftime("%m/%d · %I:%M %p ET"), 36, 84, size=24, color=MUTED)
    status = (f"Liquidity {'ON' if liq.running else 'OFF'}  ·  Endgame "
              f"{'LIVE' if end.running and end.live else 'PRACTICE' if end.running else 'OFF'}")
    text(status, 36, 122, size=28, color=HEAD)

    # Two panels.
    top, ph, pw = 172, 470, 564
    panels = [
        ("LIQUIDITY TRADER", [
            ("State", ("ON · " + (liq.strategy or "")) if liq.running else "OFF"),
            ("Settled today", money(liq.settled_today)),
            ("Window", liq.window or "--"),
            ("Window loss", f"{money(liq.window_loss, False)} / {money(liq.window_limit, False)}"),
            ("Entry cap", "--" if liq.entry_cap is None else str(liq.entry_cap)),
            ("Positions · orders", f"{liq.positions if liq.positions is not None else '--'} · "
                                   f"{liq.working_orders if liq.working_orders is not None else '--'}"),
        ], ("Window stop", liq.window_loss, liq.window_limit)),
        ("ENDGAME TRADER", [
            ("State", "LIVE" if end.running and end.live else "PRACTICE" if end.running else "OFF"),
            ("Result today", money(end.result_today)),
            ("Won · lost", f"{end.won} · {end.lost}"),
            ("Trades today", str(end.trades)),
            ("Open now", f"{end.open_positions} · {money(end.at_risk, False)} at risk"),
            ("Daily stop", f"{money(max(Decimal(0), -end.result_today), False)} / "
                           f"{money(end.daily_stop, False)}"),
        ], ("Daily stop", max(Decimal(0), -end.result_today), end.daily_stop)),
    ]
    for index, (title, rows, (meter_label, used, limit)) in enumerate(panels):
        x = 36 + index * (pw + 36)
        d.rounded_rectangle((x, top, x + pw, top + ph), radius=16, fill=PANEL)
        text(title, x + 22, top + 18, size=30, bold=True, color=HEAD)
        y = top + 70
        for label, value in rows:
            text(label, x + 22, y, size=27, color=MUTED, max_width=260)
            color = _color(value) if label in ("Settled today", "Result today") else WHITE
            text(value, x + pw - 22, y, size=29, bold=True, color=color, right=True, max_width=300)
            y += 52
        share = "--" if used is None or not limit else f"{float(used / limit) * 100:.0f}% used"
        text(f"{meter_label} · {share}", x + 22, top + ph - 52, size=22, color=MUTED)
        meter(x + 300, top + ph - 50, pw - 322, used, limit)

    # Today strip.
    y = top + ph + 30
    total = (liq.settled_today or Decimal(0)) + end.result_today
    cards = [("Liquidity today", money(liq.settled_today)), ("Endgame today", money(end.result_today)),
             ("Combined today", money(total))]
    cw = (W - 72 - 2 * 18) // 3
    for i, (label, value) in enumerate(cards):
        x = 36 + i * (cw + 18)
        d.rounded_rectangle((x, y, x + cw, y + 100), radius=12, fill=PANEL)
        text(label, x + 18, y + 12, size=23, color=MUTED, max_width=cw - 36)
        text(value, x + 18, y + 46, size=34, bold=True, color=_color(value), max_width=cw - 36)

    # Recent Endgame trades.
    y += 130
    text("ENDGAME · LAST TRADES", 36, y, size=28, bold=True, color=HEAD)
    y += 46
    d.rounded_rectangle((36, y, W - 36, y + 48 + 52 * rows_recent), radius=12, fill=PANEL)
    cols = [(58, "Time", False), (240, "Sport", False), (460, "Match", False), (930, "Paid", True),
            (W - 58, "Result", True)]
    for x, label, right in cols:
        text(label, x, y + 10, size=23, color=MUTED, right=right)
    y += 48
    if not end.recent:
        text("No trades yet", 58, y + 10, size=26, color=MUTED)
    for i, row in enumerate(end.recent):
        if i % 2 == 0:
            d.rectangle((37, y, W - 37, y + 51), fill="#202e3e")
        for (x, _, right), value in zip(cols, row):
            color = _color(value) if value.startswith(("+", "-")) else AMBER if value == "open" else WHITE
            text(value, x, y + 10, size=26, color=color, right=right, max_width=440 if x == 460 else 200)
        y += 52

    y += 26
    text(f"Cash {money(model.cash, False)} · Buying power {money(model.buying_power, False)}", 36, y,
         size=24, color=MUTED)
    text(f"Endgame: {end.settings}", 36, y + 34, size=22, color=MUTED)
    out = io.BytesIO()
    image.save(out, format="PNG", optimize=True)
    return out.getvalue()
