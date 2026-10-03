"""Phone push alerts through ntfy (operator 2026-10-03: "alert me using ntfy, when bot
start and when bot crash or stop").

The topic is private: it lives only on the bot machine (env NTFY_TOPIC, or the
git-ignored file data/ntfy-topic), never in the repository. Sending never raises.
"""
from __future__ import annotations

import os
import urllib.request
from pathlib import Path

DEFAULT_SERVER = "https://ntfy.sh"
TOPIC_FILE = Path("data/ntfy-topic")


def topic(path: Path = TOPIC_FILE) -> str | None:
    value = os.environ.get("NTFY_TOPIC", "").strip()
    if not value:
        try:
            value = path.read_text(encoding="utf-8").strip()
        except OSError:
            return None
    return value or None


def send(title: str, message: str, *, priority: int = 3, tags: str = "",
         topic_name: str | None = None, server: str | None = None, timeout: float = 10.0) -> bool:
    name = topic_name or topic()
    if not name:
        return False
    url = f"{(server or os.environ.get('NTFY_SERVER') or DEFAULT_SERVER).rstrip('/')}/{name}"
    headers = {"Title": title.encode("ascii", "ignore").decode() or "Bot alert",
               "Priority": str(max(1, min(5, priority)))}
    if tags:
        headers["Tags"] = tags
    request = urllib.request.Request(url, data=message.encode("utf-8"), method="POST", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return 200 <= response.status < 300
    except Exception:
        return False
