from __future__ import annotations

import json
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

from liquidity_bot.discord_summary import (
    GRAY, RED, LiquiditySummary, build_liquidity_summary, build_runtime_alert,
)
from liquidity_bot.notifications import DiscordNotifier
from liquidity_bot.runtime_secrets import RuntimeSecrets
from scripts.reward_table import build_discord_payload


NOW = datetime(2026, 9, 7, 13, tzinfo=timezone.utc)


class DiscordReportLayoutTests(unittest.TestCase):
    def test_window_meter_never_falls_back_to_daily_pnl_when_unknown(self):
        summary = LiquiditySummary(
            mode="LIVE", readiness_status="SCALE_ELIGIBLE", observed_at=NOW,
            session_started_at=NOW, today_pnl_usd=Decimal("-104"),
            hard_stop_loss_usd=Decimal("25"), hard_stop_window="06:00–12:00 ET",
            pending_entry_cap=25, pending_entry_count=24,
            hard_stop_state="Window budget available")
        payload = json.dumps(build_liquidity_summary(summary))
        self.assertIn("HARD     UNAVAILABLE", payload)
        confirmed = json.dumps(build_liquidity_summary(replace(
            summary, hard_stop_pnl_usd=Decimal("-5"))))
        self.assertIn("$5.00 / $25.00", confirmed)
        self.assertNotIn("$104.00 / $25.00", confirmed)
        self.assertIn("$20.00 (80%)", confirmed)
        self.assertIn("Pending entries", confirmed)
        self.assertIn("24 / 25", confirmed)
        self.assertIn("Window budget available", confirmed)

    def test_summary_is_one_column_report_without_repeated_notes(self):
        summary = LiquiditySummary(
            mode="LIVE", readiness_status="SCALE_ELIGIBLE",
            observed_at=NOW, session_started_at=NOW - timedelta(hours=1),
            today_pnl_usd=Decimal("-21"),
            today_realized_pnl_usd=Decimal("-20"),
            today_unrealized_pnl_usd=Decimal("-1"), today_pnl_as_of=NOW,
            hard_stop_loss_usd=Decimal("90"),
            attributable_paid_rewards_usd=Decimal("15"),
            pending_rewards_usd=Decimal("12"),
        )
        embed = build_liquidity_summary(summary)["embeds"][0]
        self.assertEqual(embed["title"], "Liquidity Bot · LIVE")
        self.assertLess(len(embed["description"]), 170)
        self.assertTrue(all(not f["inline"] for f in embed["fields"]))
        self.assertEqual(embed["fields"][0]["name"], "Position and stop loss")
        self.assertIn("Rewards · lifetime", [f["name"] for f in embed["fields"]])
        rendered = json.dumps(embed)
        for noise in ("-#", "BOT OVERALL", "incomplete ledger", "are shown but"):
            self.assertNotIn(noise, rendered)
        self.assertEqual(rendered.count("12.00"), 1)
        self.assertIn("$21.00 / $90.00", rendered)
        self.assertIn("Mark age 0.0s", embed["footer"]["text"])
        for mode in ("SHADOW", "DRY_RUN"):
            other = build_liquidity_summary(replace(summary, mode=mode))["embeds"][0]
            self.assertIn(mode.replace("_", " "), other["title"])
            if mode == "SHADOW":
                self.assertNotIn("TRADING TODAY", other["description"])

    def test_errors_keep_severity_and_context_without_boilerplate(self):
        for critical in (False, True):
            embed = build_runtime_alert(
                component="private_stream", error="TimeoutError: read timed out",
                attempt=4, critical=critical, market="market-a", host="host-a",
                commit="abcdef", now=NOW)["embeds"][0]
            self.assertEqual(embed["description"], "TimeoutError: read timed out")
            self.assertEqual(embed["color"], RED if critical else GRAY)
            self.assertIn("Action required" if critical else "Retrying", embed["title"])
            self.assertEqual([f["name"] for f in embed["fields"]],
                             ["Component", "Attempt", "Market"])
            self.assertNotIn("footer", embed)

    def test_withdrawal_value_is_numeric_only_with_proof_and_blocker_is_short(self):
        summary = LiquiditySummary(
            mode="LIVE", readiness_status="SCALE_ELIGIBLE", observed_at=NOW,
            session_started_at=NOW, quote_size=Decimal("10"), slot_capacity=32,
            portfolio_exposure_cap_usd=Decimal("420"),
            weekly_reserve_usd=Decimal("630"),
            settled_cash_usd=Decimal("1100"), buying_power_usd=Decimal("45"),
            capital_state_proven=True,
        )

        def capital(s):
            return next(f["value"] for f in build_liquidity_summary(s)["embeds"][0]["fields"]
                        if f["name"] == "Capital guidance")

        self.assertIn(f"{'Free to withdraw':<19}{'$45.00':>10}", capital(summary))
        blocked = capital(replace(
            summary, capital_state_proven=False,
            capital_unavailable_reason="order proof pending (5 intents, 2 unknown)"))
        self.assertIn(f"{'Free to withdraw':<19}{'UNAVAILABLE':>10}", blocked)
        self.assertIn("Order confirmation pending", blocked)
        self.assertNotIn("intents", blocked)
        self.assertNotIn("Slot footprint", blocked)

    def test_reward_report_keeps_unknown_rows_without_diagnostic_notes(self):
        today = NOW.date()
        payload = build_discord_payload(
            [today], pnl={}, paid={}, pending={}, resting_hours={},
            elapsed_hours={}, today=today, generated_at=NOW, unbalanced_markets=999)
        embed = payload["embeds"][0]
        self.assertEqual(len(embed["fields"]), 2)
        self.assertEqual(len(embed["description"].splitlines()), 4)
        self.assertIn("unavail", embed["fields"][0]["value"])
        self.assertIn("Pending", embed["fields"][0]["value"])
        self.assertIn("Unavailable (0/1 days proven)", embed["description"])
        self.assertIn("**Reported paid:** Unavailable", embed["description"])
        self.assertIn("**Reported pending:** Unavailable", embed["description"])
        for noise in ("Balanced-pair", "unbalanced", "Snapshot intervals",
                      "Includes carried", "Missing boundaries", "Today starts"):
            self.assertNotIn(noise, json.dumps(payload))


class DiscordNoiseTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.notifier = DiscordNotifier(RuntimeSecrets(
            discord_errors_webhook_url="https://discord.com/api/webhooks/1/errors",
            discord_webhook_url="https://discord.com/api/webhooks/2/summary",
            discord_rewards_webhook_url="https://discord.com/api/webhooks/3/rewards"),
            transport=lambda url, body: self.calls.append((url, json.loads(body))) or True)

    def test_repeat_success_is_suppressed_until_exact_five_minute_boundary(self):
        with patch("liquidity_bot.notifications.time.monotonic", return_value=0) as clock:
            self.assertTrue(self.notifier.send("Stream restored").sent)
            clock.return_value = 299.999
            self.assertEqual(self.notifier.send("Stream restored").reason,
                             "duplicate_suppressed")
            clock.return_value = 300
            self.assertTrue(self.notifier.send("Stream restored").sent)
        self.assertEqual(len(self.calls), 2)

    def test_critical_reminders_and_reports_are_never_suppressed(self):
        for _ in range(2):
            self.assertTrue(self.notifier.send("Shutdown not flat", critical=True).sent)
            self.assertTrue(self.notifier.send("Report", channel="summary").sent)
            self.assertTrue(self.notifier.send("Report", channel="rewards").sent)
        self.assertEqual(len(self.calls), 6)

    def test_failed_delivery_is_retryable_and_changed_errors_are_distinct(self):
        with patch.object(self.notifier, "_transport", return_value=False):
            self.assertEqual(self.notifier.send("Timeout").reason, "delivery_failed")
        self.assertTrue(self.notifier.send("Timeout").sent)
        self.assertTrue(self.notifier.send("Ownership conflict", critical=True).sent)
        self.assertTrue(self.notifier.send("Timeout on a different market").sent)
        self.assertEqual(len(self.calls), 3)

    def test_embed_timestamp_only_change_does_not_repeat_same_event(self):
        args = dict(component="stream", error="timeout", attempt=4, critical=False)
        self.assertTrue(self.notifier.send_payload(build_runtime_alert(**args, now=NOW)).sent)
        self.assertEqual(self.notifier.send_payload(build_runtime_alert(
            **args, now=NOW + timedelta(seconds=1))).reason, "duplicate_suppressed")
        args["attempt"] = 8
        self.assertTrue(self.notifier.send_payload(build_runtime_alert(**args, now=NOW)).sent)

    def test_concurrent_duplicates_send_once_and_cache_is_bounded(self):
        barrier = threading.Barrier(2)

        def send():
            barrier.wait(timeout=2)
            return self.notifier.send("Same recovery")

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: send(), range(2)))
        self.assertEqual(sum(r.sent for r in results), 1)
        for i in range(140):
            self.notifier.send(f"Recovery {i}")
        self.assertEqual(len(self.notifier._recent_operations), 128)

    def test_new_notifier_does_not_inherit_suppression(self):
        self.notifier.send("Startup")
        other = DiscordNotifier(RuntimeSecrets(
            discord_errors_webhook_url="https://discord.com/api/webhooks/1/errors"),
            transport=lambda url, body: True)
        self.assertTrue(other.send("Startup").sent)


if __name__ == "__main__":
    unittest.main()
