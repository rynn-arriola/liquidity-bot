from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from liquidity_bot.discord_summary import (
    RED,
    LiquiditySummary,
    build_liquidity_summary,
)
from liquidity_bot.notifications import DiscordNotifier
from liquidity_bot.runtime_secrets import (
    RuntimeSecretError,
    RuntimeSecrets,
    load_runtime_secrets,
)


NOW = datetime(2026, 8, 13, 4, 0, tzinfo=timezone.utc)


class RuntimeSecretTests(unittest.TestCase):
    def secret_file(self, body: str) -> Path:
        path = Path(tempfile.mkdtemp(prefix="liquidity-secrets-")) / "credentials.py"
        path.write_text(body, encoding="utf-8")
        return path

    def test_literal_shared_file_loads_without_secret_repr(self):
        path = self.secret_file(
            'KEY_ID = "key-one"\n'
            'SECRET_KEY = "secret-one"\n'
            'DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/1/token"\n'
            'DISCORD_ERRORS_WEBHOOK_URL = "https://discord.com/api/webhooks/2/token"\n')
        secrets = load_runtime_secrets(
            credentials_file=path, environ={}, require_polymarket=True)
        self.assertTrue(secrets.polymarket_configured)
        self.assertTrue(secrets.discord_errors_configured)
        rendered = repr(secrets)
        self.assertNotIn("key-one", rendered)
        self.assertNotIn("secret-one", rendered)
        self.assertNotIn("/1/token", rendered)

    def test_environment_overrides_file_without_requiring_copy(self):
        path = self.secret_file('KEY_ID="old"\nSECRET_KEY="old-secret"\n')
        secrets = load_runtime_secrets(
            credentials_file=path,
            environ={
                "POLYMARKET_US_KEY_ID": "new",
                "POLYMARKET_US_SECRET_KEY": "new-secret",
            }, require_polymarket=True)
        self.assertEqual(secrets.key_id, "new")
        self.assertEqual(secrets.source, "environment")

    def test_file_is_parsed_as_data_and_executable_value_is_rejected(self):
        path = self.secret_file(
            'KEY_ID = __import__("os").getenv("KEY")\nSECRET_KEY="x"\n')
        with self.assertRaisesRegex(RuntimeSecretError, "literal string"):
            load_runtime_secrets(credentials_file=path, environ={})

    def test_non_discord_webhook_host_is_rejected(self):
        path = self.secret_file(
            'KEY_ID="key"\nSECRET_KEY="secret"\n'
            'DISCORD_WEBHOOK_URL="https://example.com/api/webhooks/1/token"\n')
        with self.assertRaisesRegex(RuntimeSecretError, "approved Discord"):
            load_runtime_secrets(credentials_file=path, environ={})


class DiscordTests(unittest.TestCase):
    def secrets(self) -> RuntimeSecrets:
        return RuntimeSecrets(
            discord_webhook_url="https://discord.com/api/webhooks/1/status",
            discord_errors_webhook_url="https://discord.com/api/webhooks/2/error")

    def test_status_and_critical_use_separate_hooks_without_network(self):
        calls = []

        def transport(url, body):
            calls.append((url, json.loads(body)))
            return True

        notifier = DiscordNotifier(self.secrets(), transport=transport)
        self.assertTrue(notifier.send("healthy").sent)
        self.assertTrue(notifier.send("money risk", critical=True).sent)
        self.assertIn("/1/status", calls[0][0])
        self.assertIn("/2/error", calls[1][0])
        self.assertEqual(calls[1][1]["content"], "money risk")

    def test_channel_routes_independently_of_severity(self):
        """Destination and severity are separate choices.

        They used to be one knob -- critical went to the errors webhook and
        everything else to the status one -- so downgrading routine recoveries
        out of critical silently moved them into the reporting channel.
        """
        calls = []

        def transport(url, body):
            calls.append(url)
            return True

        notifier = DiscordNotifier(self.secrets(), transport=transport)
        notifier.send("retrying", critical=False, channel="errors")
        notifier.send("session summary", critical=False)

        self.assertIn("/2/error", calls[0])
        self.assertIn("/1/status", calls[1])

    def test_an_unknown_channel_is_refused(self):
        notifier = DiscordNotifier(self.secrets(), transport=lambda u, b: True)
        with self.assertRaises(ValueError):
            notifier.send("x", channel="somewhere")

    def test_liquidity_summary_excludes_pending_reward_and_leads_with_risk(self):
        summary = LiquiditySummary(
            mode="LIVE", readiness_status="DO_NOT_START_TRIAL",
            observed_at=NOW, session_started_at=NOW - timedelta(minutes=30),
            active_markets=1, reward_eligible_sides=2, score_seconds=100,
            attributable_paid_rewards_usd=Decimal("2"),
            pending_rewards_usd=Decimal("9"),
            trading_realized_usd=Decimal("-1"),
            open_marked_usd=Decimal("-0.5"),
            session_loss_limit_usd=Decimal("5"),
            integrity_problems=("private stream stale",))
        payload = build_liquidity_summary(summary)
        embed = payload["embeds"][0]
        self.assertEqual(embed["color"], RED)
        self.assertIn("INTEGRITY FAILURE", embed["description"])
        self.assertIn("ACCOUNTED NET  $+0.50", embed["description"])
        self.assertIn("Pending rewards **$9.00**", embed["description"])
        self.assertNotIn("$+9.50", embed["description"])

        calls = []
        result = DiscordNotifier(
            self.secrets(), transport=lambda url, body: calls.append(
                (url, json.loads(body))) or True).send_payload(payload)
        self.assertTrue(result.sent)
        self.assertEqual(calls[0][1], payload)


if __name__ == "__main__":
    unittest.main()
