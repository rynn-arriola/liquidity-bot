"""Small Discord transport using the shared runtime webhook configuration."""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .runtime_secrets import RuntimeSecrets


@dataclass(frozen=True)
class NotificationResult:
    sent: bool
    reason: str


Transport = Callable[[str, bytes], bool]


def _discord_post(url: str, body: bytes) -> bool:
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            # Discord's edge rejects urllib's default user agent.
            "User-Agent": "LiquidityBotDiscordWebhook/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return 200 <= response.status < 300
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


class DiscordNotifier:
    """Send status or critical messages without ever exposing webhook URLs."""

    def __init__(self, secrets: RuntimeSecrets,
                 transport: Transport = _discord_post):
        self._status_url = secrets.discord_webhook_url
        self._error_url = (secrets.discord_errors_webhook_url
                           or secrets.discord_webhook_url)
        self._transport = transport

    @staticmethod
    def _body(message: str) -> bytes:
        clean = str(message).strip()
        if not clean:
            raise ValueError("Discord message cannot be empty")
        # Discord content is limited to 2,000 characters. Preserve a visible
        # truncation marker rather than letting an important alert be rejected.
        if len(clean) > 2000:
            clean = clean[:1985] + "… [truncated]"
        return json.dumps(
            {"content": clean}, ensure_ascii=False).encode("utf-8")

    def send(self, message: str, *, critical: bool = False) -> NotificationResult:
        return self.send_payload(
            {"content": json.loads(self._body(message))["content"]},
            critical=critical)

    def send_payload(
        self, payload: Mapping[str, Any], *, critical: bool = False,
    ) -> NotificationResult:
        url = self._error_url if critical else self._status_url
        if not url:
            return NotificationResult(False, "webhook_not_configured")
        if not isinstance(payload, Mapping) or not payload:
            raise ValueError("Discord payload cannot be empty")
        body = json.dumps(
            dict(payload), ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        sent = self._transport(url, body)
        return NotificationResult(
            sent, "sent" if sent else "delivery_failed")

    async def send_async(
        self, message: str, *, critical: bool = False,
    ) -> NotificationResult:
        # Never block the market/private WebSocket event loop on Discord HTTP.
        return await asyncio.to_thread(self.send, message, critical=critical)

    async def send_payload_async(
        self, payload: Mapping[str, Any], *, critical: bool = False,
    ) -> NotificationResult:
        return await asyncio.to_thread(
            self.send_payload, payload, critical=critical)
