from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from dataclasses import replace

from liquidity_bot.discord_summary import (
    ORANGE,
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

    def test_summary_separates_markets_managed_from_markets_earning(self):
        """Managed is not earning: reward is paid for resting, not selecting."""

        summary = LiquiditySummary(
            mode="LIVE", readiness_status="SCALE_ELIGIBLE",
            observed_at=NOW, session_started_at=NOW - timedelta(minutes=30),
            active_markets=20, resting_markets=9,
            idle_market_reasons=(("target_not_met", 8), ("no_book", 3)))
        # Matched on "Slots", not on "reward": the slots field is *named*
        # "Slots and reward eligibility", so a substring match on "reward"
        # also matches the separate rewards field and passed here only by
        # ordering luck.
        rewards = [field for field in build_liquidity_summary(summary)["embeds"][0]
                   ["fields"] if "Slots" in field["name"]][0]["value"]

        # Shown as a ratio, because 9 alone does not say what it is 9 of.
        # Measured 2026-08-16: 20 managed against 9 actually holding an order.
        self.assertIn("9/20", rewards)
        # And the eleven that are not resting are broken down, because
        # "priced out" and "broken" need very different responses.
        self.assertIn("target_not_met", rewards)
        self.assertIn("no_book", rewards)

    def test_summary_reports_ledger_size_so_a_full_disk_is_visible(self):
        """The ledger is never rebuilt, so nothing else warns before it fills."""

        summary = LiquiditySummary(
            mode="LIVE", readiness_status="SCALE_ELIGIBLE",
            observed_at=NOW, session_started_at=NOW - timedelta(minutes=30),
            ledger_bytes=22_396_928 + 4_194_304)
        health = [field for field in build_liquidity_summary(summary)["embeds"][0]
                  ["fields"] if "Health" in field["name"]][0]["value"]

        # Rendered in units a human can act on, not raw bytes.
        self.assertIn("Ledger on disk", health)
        self.assertIn("25.4MB", health)

        # Negative size is a bug in the caller, not something to render.
        with self.assertRaises(ValueError):
            LiquiditySummary(
                mode="LIVE", readiness_status="SCALE_ELIGIBLE",
                observed_at=NOW, session_started_at=NOW, ledger_bytes=-1)

    def test_idle_slots_read_as_supply_not_as_a_fault(self):
        """"Only 3 of 20 quoting" is the most-asked question about this bot,
        and the summary could not answer it. A ratio against capacity makes
        every quiet evening look broken.

        Measured 2026-08-18T20:32Z: 3 of 20 slots filled, and all three of
        the three viable markets were quoted -- 88% of rewarded markets had
        books tighter than 2c and were refused before a book was read. That
        is the selector working, not failing, and it must not be orange.
        """

        quiet = LiquiditySummary(
            mode="LIVE", readiness_status="SCALE_ELIGIBLE",
            observed_at=NOW, session_started_at=NOW - timedelta(hours=2),
            active_markets=20, resting_markets=3,
            slot_capacity=20, viable_markets=3, rewarded_markets=26,
            spread_filtered_markets=23, selection_failures=0)
        embed = build_liquidity_summary(quiet)["embeds"][0]
        self.assertIn("supply-limited", embed["description"])
        self.assertNotEqual(embed["color"], ORANGE)
        slots = [f for f in embed["fields"] if "Slots" in f["name"]][0]["value"]
        # The funnel, so "why only three" is answerable from the message.
        self.assertIn("Viable now", slots)
        self.assertIn("Refused, tight book", slots)
        self.assertIn("Lost to errors", slots)

        # Same 3 of 20, but now six viable markets are going unquoted. That
        # is the real fault this wording exists to separate out.
        broken = replace(quiet, viable_markets=9, selection_failures=4)
        embed = build_liquidity_summary(broken)["embeds"][0]
        self.assertEqual(embed["color"], ORANGE)
        self.assertIn("6 viable market(s) not quoted", embed["description"])
        self.assertNotIn("supply-limited", embed["description"])

    def test_a_summary_without_supply_data_keeps_the_old_wording(self):
        """`slot_capacity` is the sentinel. Shadow runs and older callers
        supply no funnel, and must not be described as supply-limited on the
        strength of a default of zero -- that would report "0 viable" as
        healthy on a bot that is genuinely idle."""

        summary = LiquiditySummary(
            mode="LIVE", readiness_status="SCALE_ELIGIBLE",
            observed_at=NOW, session_started_at=NOW - timedelta(hours=2),
            active_markets=20, resting_markets=3)
        embed = build_liquidity_summary(summary)["embeds"][0]
        self.assertIn("17 slot(s) not quoting", embed["description"])
        self.assertNotIn("supply-limited", embed["description"])
        slots = [f for f in embed["fields"] if "Slots" in f["name"]][0]["value"]
        self.assertNotIn("Viable now", slots)

    def test_stop_meter_draws_from_the_stop_input_not_marked_equity(self):
        """The bar has to measure the thing that actually fires.

        `blockers()` trips on realised P&L, never on marked equity -- marked
        equity swings with every unrealised tick (320 open contracts move $38
        on twelve cents), so a bar drawn from it shows the bot walking into a
        limit nothing is walking into. These two are deliberately opposite
        here: equity is up $30, realised is down $20, and the meter must
        follow the realised number.
        """

        summary = LiquiditySummary(
            mode="LIVE", readiness_status="SCALE_ELIGIBLE",
            observed_at=NOW, session_started_at=NOW - timedelta(minutes=30),
            active_markets=2, resting_markets=2,
            trading_realized_usd=Decimal("30"),
            session_realized_pnl_usd=Decimal("-20"),
            worst_market_realized_pnl_usd=Decimal("-8"),
            session_loss_limit_usd=Decimal("40"),
            hard_stop_loss_usd=Decimal("45"))
        risk = [field for field in build_liquidity_summary(summary)["embeds"][0]
                ["fields"] if "stop loss" in field["name"]][0]["value"]

        self.assertIn("$20.00 / $40.00", risk)
        self.assertIn("$8.00", risk)
        # Half the cap, so half the bar. Catches an off-by-one or an inverted
        # meter, which a "does it contain a block character" check would not.
        self.assertIn("██████░░░░░░", risk)
        self.assertNotIn("$30.00", risk)

    def test_a_profitable_session_shows_an_empty_stop_meter(self):
        """Profit is not negative loss. Clamped, or the bar renders inverted."""

        summary = LiquiditySummary(
            mode="LIVE", readiness_status="SCALE_ELIGIBLE",
            observed_at=NOW, session_started_at=NOW - timedelta(minutes=30),
            active_markets=2, resting_markets=2,
            session_realized_pnl_usd=Decimal("12.50"),
            session_loss_limit_usd=Decimal("40"))
        risk = [field for field in build_liquidity_summary(summary)["embeds"][0]
                ["fields"] if "stop loss" in field["name"]][0]["value"]

        self.assertIn("$0.00 / $40.00", risk)
        self.assertIn("░░░░░░░░░░░░", risk)

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
