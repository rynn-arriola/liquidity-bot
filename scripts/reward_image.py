"""Deterministic, report-only PNG rendering and one-shot Discord upload.

Pillow is an optional reporting dependency, never imported by the trader.
Rendering failure falls back BEFORE delivery; ambiguous uploads never retry.
"""

from __future__ import annotations

import asyncio
import copy
import io
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from liquidity_bot.notifications import NotificationResult


CANVAS_WIDTH = 1200
CONTENT_LEFT = 32
CONTENT_RIGHT = CANVAS_WIDTH - 32
TOTAL_CARD_GAP = 13
TOTAL_CARD_WIDTH = 370
SECTION_HEADERS = {"Reward Pulse": ["Date", "Level", "vs normal", "$/h"],
                   "Reward Pools": ["Our pools", "All pools", "Pool now $", "Profitable?"]}
POOL_VERDICT_COLORS = {"YES": "#7bdfb2", "MAYBE": "#ffd166", "NO": "#ff959b"}
PULSE_COLORS = {"Hot": "#ff9f43", "Good": "#7bdfb2", "Avg": "#ffd166",
                "Low": "#ffa94d", "Dead": "#ff959b"}


def _font(size: int, bold: bool = False):
    from PIL import ImageFont
    names = (["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
              "C:/Windows/Fonts/arialbd.ttf"] if bold else
             ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
              "C:/Windows/Fonts/arial.ttf"])
    for path in names:
        if Path(path).is_file():
            return ImageFont.truetype(path, size)
    # Pillow bundles a scalable fallback: no system-font installation needed.
    return ImageFont.load_default(size=size)


def render_reward_png(payload: dict) -> bytes:
    """Render the SAME values as the text fallback; no second money math."""
    from PIL import Image, ImageDraw
    embed = payload["embeds"][0]
    sections = []
    for field in embed["fields"]:
        lines = field["value"].splitlines()
        if len(lines) < 4 or lines[0] != "```text" or lines[-1] != "```":
            raise ValueError("invalid reward table")
        rows = [line.split() for line in lines[3:-1]]
        if len(rows) > 7 or any(len(row) != 4 for row in rows):
            raise ValueError("invalid reward rows")
        headers = SECTION_HEADERS.get(field["name"]) or (
            ["Date", "P&L", "Paid", "Pend"] if not sections else ["Date", "Day-of h", "Daily h", "Avg"])
        sections.append((field["name"], headers, rows))
    if not 2 <= len(sections) <= 4:
        raise ValueError("expected two reward tables (plus optional Reward Pulse / Reward Pools)")
    # Discord constrains tall attachments by height on mobile.  A narrow
    # portrait canvas therefore renders as a small strip even when the message
    # bubble has much more horizontal room.  Keep the same vertical density,
    # but use a wider canvas whose aspect ratio fills the mobile attachment
    # area without changing any report values.
    footer = embed["footer"]["text"]
    notes = []
    if "posting: payouts still arriving" in footer:
        notes.append("posting: payouts still arriving")
    if "+ partial split" in footer:
        notes.append("+ partial split · Avg includes untyped hours")
    names = {name for name, _, _ in sections}
    if "Reward Pulse" in names:
        notes.append("Pulse: reward $/order-hour vs our 3-week normal")
    if "Reward Pools" in names:
        note = re.search(r"(pools [^·]+)", footer)
        notes.append("Pools: score 1-10 vs best in 30 days; ours 5 or under = not profitable" +
                     (f" · {note.group(1).strip()[6:]}" if note else ""))
    width = CANVAS_WIDTH
    height = (266 + sum(112 + 56*len(rows) for _, _, rows in sections) + 108
              + 32 * max(0, len(notes) - 1))
    image = Image.new("RGB", (width, height), "#101720")
    draw = ImageDraw.Draw(image)
    white, muted, accent = "#eef3fa", "#a5b5c9", "#6abaff"

    def text(value, x, y, *, size=34, color=white, bold=False, right=False,
             max_width=CANVAS_WIDTH - 80):
        font = _font(size, bold)
        while draw.textlength(value, font=font) > max_width and size > 16:
            size -= 1
            font = _font(size, bold)
        if draw.textlength(value, font=font) > max_width:
            raise ValueError("report label exceeds image bounds")
        draw.text((x, y), value, font=font, fill=color, anchor="ra" if right else "la")

    draw.rounded_rectangle((32, 32, 40, 83), radius=4, fill=accent)
    text(embed["title"], 56, 32, size=38, bold=True,
         max_width=CONTENT_RIGHT - 56)
    from datetime import datetime
    from liquidity_bot.reward_calendar import NEW_YORK
    stamp = datetime.fromisoformat(embed["timestamp"]).astimezone(NEW_YORK)
    text(stamp.strftime("%b %d, %Y · %I:%M %p ET"), 40, 90, size=24, color=muted)
    # Three readable total cards, not the old long wrapping headline.
    totals = embed["description"].replace("**", "").splitlines()
    if len(totals) != 3:
        raise ValueError("expected three report totals")
    for index, line in enumerate(totals):
        label, value = line.split(": ", 1)
        x = CONTENT_LEFT + index*(TOTAL_CARD_WIDTH + TOTAL_CARD_GAP)
        draw.rounded_rectangle(
            (x, 137, x+TOTAL_CARD_WIDTH, 243), radius=12, fill="#1a2533")
        text(label, x+16, 149, size=25, color=muted,
             max_width=TOTAL_CARD_WIDTH-32)
        color = "#ff959b" if value.startswith("-") else white
        text(value, x+16, 187, size=34, bold=True, color=color,
             max_width=TOTAL_CARD_WIDTH-32)
    y = 266
    for name, headers, rows in sections:
        text(name, 40, y, size=30, bold=True)
        y += 48
        draw.rounded_rectangle(
            (CONTENT_LEFT, y, CONTENT_RIGHT, y+52+56*len(rows)),
            radius=10, fill="#1a2533")
        positions = [50, 600, 870, CONTENT_RIGHT-20]
        for index, header in enumerate(headers):
            text(header, positions[index], y+9, size=27, color=muted,
                 right=index > 0, max_width=240)
        y += 52
        for index, row in enumerate(rows):
            if index % 2 == 0:
                draw.rectangle(
                    (CONTENT_LEFT+1, y, CONTENT_RIGHT-1, y+55),
                    fill="#202e3e")
            for col, value in enumerate(row):
                color = white
                if name.startswith("Results") and col == 1:
                    if value.startswith("-") and value != "--": color = "#ff959b"
                    elif value.startswith("+"): color = "#7bdfb2"
                if name == "Reward Pulse" and col == 1:
                    color = PULSE_COLORS.get(value, white)
                if name == "Reward Pools" and col == 3:
                    color = POOL_VERDICT_COLORS.get(value, white)
                if name == "Reward Pools" and col == 0 and value.endswith("/10"):
                    score_value = float(value[:-3])
                    color = ("#ff959b" if score_value <= 5 else "#ffd166" if score_value < 8 else "#7bdfb2")
                text(value, positions[col], y+9, size=34, color=color,
                     right=col > 0, max_width=240 if col else 180)
            y += 56
        y += 12
    text("P&L: closed fills + settlements · * today · Hours: local rest",
         40, y+8, size=23, color=muted)
    for i, line in enumerate(notes):
        text(line, 40, y + 40 + 32 * i, size=23, color=muted)
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    result = output.getvalue()
    if len(result) > 2_000_000:
        raise ValueError("reward image exceeds local upload cap")
    return result


def image_request(url: str, payload: dict, png: bytes) -> urllib.request.Request:
    if not png.startswith(b"\x89PNG\r\n\x1a\n") or len(png) > 2_000_000:
        raise ValueError("invalid reward image")
    embed = payload["embeds"][0]
    description = (embed["title"] + "\n" + embed["description"].replace("**", "")
                   + "\n" + "\n".join(f["name"] + "\n" + f["value"].replace("```text\n", "").replace("```", "")
                                      for f in embed["fields"]))
    body_payload = {
        "allowed_mentions": {"parse": []},
        "embeds": [{"image": {"url": "attachment://rewards.png"}, "color": embed["color"]}],
        "attachments": [{"id": 0, "filename": "rewards.png", "description": description[:1024]}],
    }
    boundary = "reward-" + uuid.uuid4().hex
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="payload_json"\r\n'
            'Content-Type: application/json\r\n\r\n').encode()
    body += json.dumps(body_payload, ensure_ascii=False).encode("utf-8")
    body += (f'\r\n--{boundary}\r\nContent-Disposition: form-data; name="files[0]"; filename="rewards.png"\r\n'
             'Content-Type: image/png\r\n\r\n').encode() + png
    body += f"\r\n--{boundary}--\r\n".encode()
    parts = urllib.parse.urlsplit(url)
    query = dict(urllib.parse.parse_qsl(parts.query))
    query["wait"] = "true"
    target = urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query)))
    return urllib.request.Request(target, data=body, method="POST", headers={
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "User-Agent": "LiquidityBotDiscordWebhook/1.0",
    })


def post_reward_image(url: str, payload: dict, png: bytes) -> NotificationResult:
    if not url:
        return NotificationResult(False, "webhook_not_configured")
    request = image_request(url, payload, png)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            sent = 200 <= response.status < 300
    except (urllib.error.URLError, TimeoutError, OSError):
        # The message may already exist: no retry/text duplicate after upload.
        return NotificationResult(False, "image_delivery_failed")
    return NotificationResult(sent, "sent" if sent else "image_delivery_failed")


async def send_reward_report(payload: dict, secrets, notifier) -> NotificationResult:
    snapshot = copy.deepcopy(payload)
    try:
        png = await asyncio.to_thread(render_reward_png, snapshot)
    except Exception:
        # No transport has run yet. Log no exception text/URLs/credentials.
        print("Reward image unavailable; using compact text report.", file=sys.stderr)
        return await notifier.send_payload_async(snapshot, channel="rewards")
    return await asyncio.to_thread(
        post_reward_image, secrets.discord_rewards_webhook_url, snapshot, png)
