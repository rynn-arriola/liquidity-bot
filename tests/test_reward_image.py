from __future__ import annotations

import importlib.util
import io
import json
import unittest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from scripts.reward_table import build_discord_payload
from scripts.reward_image import (
    CANVAS_WIDTH, image_request, post_reward_image, render_reward_png, send_reward_report,
)
from liquidity_bot.notifications import NotificationResult


PIL_AVAILABLE = importlib.util.find_spec("PIL") is not None


@unittest.skipUnless(PIL_AVAILABLE, "Pillow report extra is not installed")
class RewardImageTests(unittest.TestCase):
    def _payload(self):
        days = [date(2026, 9, 13) + timedelta(days=offset)
                for offset in range(7)]
        period_hours = {
            day.isoformat(): {
                "day_of": 410.8 + offset,
                "daily_event": 12.4 if offset == 4 else 0.0,
                "unallocated": 0.0,
            }
            for offset, day in enumerate(days)
        }
        return build_discord_payload(
            days,
            pnl={},
            paid={day.isoformat(): Decimal("82.14") for day in days[:4]},
            pending={day.isoformat(): Decimal("86.71") for day in days[4:]},
            resting_hours={
                key: sum(values.values())
                for key, values in period_hours.items()
            },
            elapsed_hours={day.isoformat(): 24.0 for day in days},
            today=days[-1],
            generated_at=datetime(2026, 9, 19, 11, 28,
                                  tzinfo=timezone.utc),
            unbalanced_markets=0,
            settled_history={
                day.isoformat(): Decimal("-12.31") for day in days
            },
            period_hours=period_hours,
        )

    def test_seven_day_report_uses_mobile_width_aspect_ratio(self):
        from PIL import Image

        png = render_reward_png(self._payload())
        with Image.open(io.BytesIO(png)) as image:
            self.assertEqual(image.width, CANVAS_WIDTH)
            self.assertEqual(image.height, 1382)
            # Discord height-constrains tall attachments on mobile.  This
            # ratio makes the image use nearly the whole message width; the
            # former 800px canvas produced a narrow 0.58 ratio.
            self.assertGreaterEqual(image.width / image.height, 0.85)
            self.assertEqual(image.format, "PNG")
        self.assertLess(len(png), 2_000_000)

    def test_multipart_upload_is_single_attachment_with_wait(self):
        payload = self._payload()
        png = render_reward_png(payload)
        webhook = "https://discord.com/api/webhooks/1/fake-secret?thread_id=4"

        request = image_request(webhook, payload, png)

        self.assertEqual(request.method, "POST")
        self.assertIn("thread_id=4", request.full_url)
        self.assertIn("wait=true", request.full_url)
        self.assertIn(b'attachment://rewards.png', request.data)
        self.assertIn(png, request.data)
        self.assertNotIn(webhook.encode(), request.data)
        self.assertTrue(request.headers["Content-type"].startswith(
            "multipart/form-data; boundary=reward-"))

    def test_image_draws_exact_payload_values_and_partial_marker(self):
        from PIL import ImageDraw
        payload = self._payload()
        payload["embeds"][0]["footer"]["text"] += " · + partial split"
        original = ImageDraw.ImageDraw.text
        drawn = []

        def record(draw, xy, text, *args, **kwargs):
            drawn.append(text)
            return original(draw, xy, text, *args, **kwargs)

        with patch.object(ImageDraw.ImageDraw, "text", record):
            render_reward_png(payload)
        for field in payload["embeds"][0]["fields"]:
            for line in field["value"].splitlines()[3:-1]:
                for value in line.split():
                    self.assertIn(value, drawn)
        self.assertIn("Day-of h", drawn)
        self.assertIn("Daily h", drawn)
        self.assertIn("Avg", drawn)
        self.assertTrue(any("partial split" in text for text in drawn))

    def test_empty_and_one_day_images_and_font_fallback(self):
        from PIL import Image
        for count in (0, 1):
            day = date(2026, 9, 19)
            payload = build_discord_payload([day]*count, pnl={}, paid={}, pending={},
                resting_hours={}, elapsed_hours={}, today=day,
                generated_at=datetime(2026, 9, 19, 12, tzinfo=timezone.utc), unbalanced_markets=0)
            with patch("scripts.reward_image.Path.is_file", return_value=False):
                png = render_reward_png(payload)
            with Image.open(io.BytesIO(png)) as img:
                self.assertEqual(img.width, 1200)
                self.assertGreater(img.height, 500)

    def test_invalid_image_and_malformed_rows_refused(self):
        payload = self._payload()
        with self.assertRaises(ValueError):
            image_request("https://discord.com/api/webhooks/1/test", payload, b"not png")
        payload["embeds"][0]["fields"][0]["value"] = "```text\nhead\n----\nbad row\n```"
        with self.assertRaises(ValueError):
            render_reward_png(payload)

    def test_transport_is_single_bounded_attempt_and_redacts_error(self):
        payload = self._payload()
        png = render_reward_png(payload)
        with patch("scripts.reward_image.urllib.request.urlopen", side_effect=TimeoutError("secret")) as transport:
            result = post_reward_image("https://discord.com/api/webhooks/1/test", payload, png)
        self.assertFalse(result.sent)
        self.assertNotIn("secret", result.reason)
        transport.assert_called_once()
        self.assertEqual(transport.call_args.kwargs["timeout"], 15)
        with patch("scripts.reward_image.urllib.request.urlopen") as transport:
            self.assertFalse(post_reward_image("", payload, png).sent)
            transport.assert_not_called()


class RewardImageDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_render_failure_falls_back_before_any_image_transport(self):
        notifier = SimpleNamespace(send_payload_async=AsyncMock(
            return_value=NotificationResult(True, "sent")))
        payload = {"embeds": [{"title": "safe fallback"}]}
        secrets = SimpleNamespace(discord_rewards_webhook_url="fake")

        with patch("scripts.reward_image.render_reward_png",
                   side_effect=ValueError("bad image")), patch(
                       "scripts.reward_image.post_reward_image") as post:
            result = await send_reward_report(payload, secrets, notifier)

        self.assertTrue(result.sent)
        post.assert_not_called()
        notifier.send_payload_async.assert_awaited_once_with(
            payload, channel="rewards")

    async def test_failed_image_delivery_is_not_duplicated_as_text(self):
        notifier = SimpleNamespace(send_payload_async=AsyncMock())
        payload = {"embeds": [{"title": "one attempt"}]}
        secrets = SimpleNamespace(discord_rewards_webhook_url="fake")

        with patch("scripts.reward_image.render_reward_png",
                   return_value=b"png"), patch(
                       "scripts.reward_image.post_reward_image",
                       return_value=NotificationResult(
                           False, "image_delivery_failed")):
            result = await send_reward_report(payload, secrets, notifier)

        self.assertFalse(result.sent)
        notifier.send_payload_async.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
