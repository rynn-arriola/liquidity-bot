from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from dataclasses import replace

from liquidity_bot.discord_summary import (
    GREEN,
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
        # With no slot_capacity supplied it still ratios against the bound
        # count, which is what this caller has.
        self.assertIn("9/20", rewards)
        # And the eleven bound-but-silent slots are counted outright, because
        # "how many slots are broken" is the question a slot count is asked.
        self.assertIn("stuck, look at it", rewards)
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

    def test_broken_slots_are_counted_outright(self):
        """"Are the other slots working?" is the most-asked question about
        this bot, and a slot count alone cannot answer it.

        Three different things get called an idle slot and only two are
        faults. A slot BOUND to a market that rests no order is occupied and
        earning nothing, and cannot be given to another market until it is
        released -- that is the one that costs reward, and the one counted
        here. Capacity with nothing viable to put in it is not a fault, and
        on a quiet evening it is most of the board.
        """

        # Tonight, measured 2026-08-18T20:32Z: 3 bound, 3 quoting, and 88% of
        # rewarded markets refused for books tighter than 2c. Nothing broken.
        healthy = LiquiditySummary(
            mode="LIVE", readiness_status="SCALE_ELIGIBLE",
            observed_at=NOW, session_started_at=NOW - timedelta(hours=2),
            active_markets=3, resting_markets=3,
            slot_capacity=20, viable_markets=3, rewarded_markets=26,
            spread_filtered_markets=23, selection_failures=0)
        embed = build_liquidity_summary(healthy)["embeds"][0]
        slots = [f for f in embed["fields"] if "Slots" in f["name"]][0]["value"]
        self.assertIn(f"{'  stuck, look at it':<19}{0:>10}", slots)
        self.assertNotEqual(embed["color"], ORANGE)
        self.assertIn("17 slot(s) free", embed["description"])
        # The funnel, so "why only three" is answerable from the message.
        self.assertIn("Viable now", slots)
        self.assertIn("Refused, tight book", slots)
        self.assertIn("Lost to errors", slots)

        # Fourteen bound but only seven quoting: seven slots held and silent.
        broken = replace(healthy, active_markets=14, resting_markets=7,
                         viable_markets=7, spread_filtered_markets=12,
                         idle_market_reasons=(("book_stale", 5),))
        embed = build_liquidity_summary(broken)["embeds"][0]
        slots = [f for f in embed["fields"] if "Slots" in f["name"]][0]["value"]
        self.assertIn(f"{'  stuck, look at it':<19}{7:>10}", slots)
        self.assertEqual(embed["color"], ORANGE)
        self.assertIn("7 slot(s) STUCK", embed["description"])
        # ...and why, so it is actionable rather than just alarming.
        self.assertIn("book_stale", slots)

    def test_the_slot_numbers_partition_capacity_exactly(self):
        """quoting + broken + free must equal capacity, always.

        These three are the whole answer to "are we at full capacity or is
        something stuck", and they are computed from two different inputs --
        `resting_markets` counts bound slugs holding an order, while
        `active_markets` counts bound slugs. If they ever fail to add up, one
        of the three is being derived wrongly and the reader cannot tell
        which. Checked across the shapes a real session moves through.
        """

        for active, resting, capacity, waiting, cycling in (
                (3, 3, 20, 0, 0),    # quiet evening, nothing broken
                (14, 7, 20, 0, 0),   # seven slots bound and silent
                (20, 20, 20, 0, 0),  # genuinely full
                (0, 0, 20, 0, 0),    # nothing bound yet
                (20, 0, 20, 0, 0),   # every slot bound and dead
                (9, 6, 20, 3, 0),    # three kicked off, awaiting rotation
                (9, 6, 20, 1, 0),    # one kicked off, two really stuck
                (14, 7, 20, 0, 7),   # all seven mid-cycle, nothing stuck
                (14, 7, 20, 2, 3)):  # two kicked off, three cycling, two stuck
            summary = LiquiditySummary(
                mode="LIVE", readiness_status="SCALE_ELIGIBLE",
                observed_at=NOW, session_started_at=NOW - timedelta(hours=1),
                active_markets=active, resting_markets=resting,
                slot_capacity=capacity, viable_markets=resting,
                awaiting_rotation_markets=waiting, cycling_markets=cycling)
            slots = [f for f in build_liquidity_summary(summary)["embeds"][0]
                     ["fields"] if "Slots" in f["name"]][0]["value"]

            def value(label):
                line = [row for row in slots.splitlines()
                        if row.startswith(label)][0]
                return int(line[len(label):].split()[0])

            # Parsed from the rendered text, not from the inputs -- the
            # point is to check what the reader actually sees.
            quoting = int([row for row in slots.splitlines()
                           if row.startswith("Quoting")][0]
                          .split()[-1].split("/")[0])
            broken = value("  stuck, look at it")
            free = value("  free, no supply")
            held = value("  awaiting rotation") if waiting else 0
            spin = value("  re-quoting") if cycling else 0
            self.assertEqual(
                quoting + broken + spin + held + free, capacity,
                f"{quoting}+{broken}+{spin}+{held}+{free} != {capacity} "
                f"for active={active} resting={resting} "
                f"waiting={waiting} cycling={cycling}")

    def test_the_broken_slot_reasons_account_for_every_broken_slot(self):
        """The breakdown under the count must sum to the count.

        Truncating silently made the reasons fail to add up to
        active - resting, on the one field whose job is explaining why slots
        are stuck. The tail is folded into `other` instead.
        """

        summary = LiquiditySummary(
            mode="LIVE", readiness_status="SCALE_ELIGIBLE",
            observed_at=NOW, session_started_at=NOW - timedelta(hours=1),
            active_markets=20, resting_markets=5, slot_capacity=20,
            viable_markets=5,
            idle_market_reasons=(("target_not_met", 6), ("book_stale", 4),
                                 ("no_book", 3), ("retired", 1),
                                 ("quarantined", 1)))
        slots = [f for f in build_liquidity_summary(summary)["embeds"][0]
                 ["fields"] if "Slots" in f["name"]][0]["value"]
        # Only the reason rows, which are indented 6. The three slot states
        # are indented 2, and counting those too double-counted the total.
        counted = sum(
            int(row.split()[-1]) for row in slots.splitlines()
            if row.startswith("      ") and row.split()[-1].isdigit())
        self.assertEqual(counted, 15, "reasons must sum to active - resting")
        self.assertIn("other", slots)

    def test_a_kicked_off_market_is_not_counted_as_a_broken_slot(self):
        """A started market cannot quote again and is only waiting for the
        next rotation sweep. It is idle for a known, harmless reason.

        Measured 2026-08-18: 96 markets did this in one day, each holding a
        slot ~11 minutes, while free slots never dropped below 9 -- so none
        of it cost anything. Counting them as broken made the alarm fire on
        the ordinary case, and an alarm that cries wolf stops being read.
        """

        base = dict(
            mode="LIVE", readiness_status="SCALE_ELIGIBLE",
            observed_at=NOW, session_started_at=NOW - timedelta(hours=1),
            active_markets=9, resting_markets=6, slot_capacity=20,
            viable_markets=6, rewarded_markets=59)

        # All three idle slots are simply post-kickoff. Nothing is broken.
        quiet = LiquiditySummary(
            **base, awaiting_rotation_markets=3,
            idle_market_reasons=(("kicked_off", 3),))
        embed = build_liquidity_summary(quiet)["embeds"][0]
        slots = [f for f in embed["fields"] if "Slots" in f["name"]][0]["value"]
        self.assertIn(f"{'  stuck, look at it':<19}{0:>10}", slots)
        self.assertIn(f"{'  awaiting rotation':<19}{3:>10}", slots)
        self.assertNotIn("slot(s) STUCK", embed["description"])

        # One kicked off, two genuinely stuck: the two must still be counted.
        mixed = LiquiditySummary(
            **base, awaiting_rotation_markets=1,
            idle_market_reasons=(("kicked_off", 1), ("book_stale", 2)))
        embed = build_liquidity_summary(mixed)["embeds"][0]
        slots = [f for f in embed["fields"] if "Slots" in f["name"]][0]["value"]
        self.assertIn(f"{'  stuck, look at it':<19}{2:>10}", slots)
        self.assertEqual(embed["color"], ORANGE)
        self.assertIn("2 slot(s) STUCK", embed["description"])
        self.assertIn("book_stale", slots)

        # Never negative, and never hides a stuck slot, if a caller reports
        # more awaiting rotation than are idle at all.
        absurd = LiquiditySummary(**base, awaiting_rotation_markets=99)
        slots = [f for f in build_liquidity_summary(absurd)["embeds"][0]
                 ["fields"] if "Slots" in f["name"]][0]["value"]
        self.assertIn(f"{'  stuck, look at it':<19}{0:>10}", slots)
        self.assertIn(f"{'  awaiting rotation':<19}{3:>10}", slots)

    def test_a_market_between_a_cancel_and_its_replace_is_not_stuck(self):
        """Churn is not breakage, and counting it as breakage cried wolf.

        Measured 2026-08-19: markets cycled every 17-40 seconds -- 50 places
        against 51 cancels in 23 minutes -- so a sample taken at any instant
        catches several holding no order between a cancel and its re-place.
        Those were counted as stuck, and the operator was sent to investigate
        a healthy bot three times over it.
        """

        base = dict(
            mode="LIVE", readiness_status="SCALE_ELIGIBLE",
            observed_at=NOW, session_started_at=NOW - timedelta(hours=1),
            active_markets=14, resting_markets=7, slot_capacity=20,
            viable_markets=7, rewarded_markets=40)

        spinning = LiquiditySummary(**base, cycling_markets=7)
        embed = build_liquidity_summary(spinning)["embeds"][0]
        slots = [f for f in embed["fields"] if "Slots" in f["name"]][0]["value"]
        self.assertIn(f"{'  stuck, look at it':<19}{0:>10}", slots)
        self.assertIn(f"{'  re-quoting':<19}{7:>10}", slots)
        self.assertNotEqual(embed["color"], ORANGE)

        # A genuinely stuck slot still surfaces alongside the cycling ones.
        mixed = LiquiditySummary(
            **base, cycling_markets=5,
            idle_market_reasons=(("book_stale", 2),))
        embed = build_liquidity_summary(mixed)["embeds"][0]
        slots = [f for f in embed["fields"] if "Slots" in f["name"]][0]["value"]
        self.assertIn(f"{'  stuck, look at it':<19}{2:>10}", slots)
        self.assertEqual(embed["color"], ORANGE)
        self.assertIn("2 slot(s) STUCK", embed["description"])

        # Cycling can never exceed the idle slots and hide a stuck one.
        absurd = LiquiditySummary(**base, cycling_markets=99)
        slots = [f for f in build_liquidity_summary(absurd)["embeds"][0]
                 ["fields"] if "Slots" in f["name"]][0]["value"]
        self.assertIn(f"{'  re-quoting':<19}{7:>10}", slots)

    def _stop_summary(self, **overrides):
        base = dict(
            mode="LIVE", readiness_status="SCALE_ELIGIBLE",
            observed_at=NOW, session_started_at=NOW - timedelta(hours=2),
            active_markets=3, resting_markets=3, slot_capacity=20,
            viable_markets=3, rewarded_markets=26, spread_filtered_markets=23,
            selection_failures=0,
            session_loss_limit_usd=Decimal("40"),
            hard_stop_loss_usd=Decimal("45"))
        base.update(overrides)
        return LiquiditySummary(**base)

    def test_the_hard_meter_reads_equity_not_the_session_number(self):
        """The HARD bar drew the SESSION stop's number against the hard cap.

        They are different quantities and they diverge in the DANGEROUS
        direction. `runtime_safety.blockers` trips the session stop on
        `-realized_pnl`, which is matched-pair only and cannot see a position
        that settles worthless; `hard_stop_reached` compares marked equity
        against the day's opening equity and does see it. So the bar could sit
        near zero while the stop it is labelled for was one tick from firing.
        """
        summary = self._stop_summary(
            session_realized_pnl_usd=Decimal("0"),   # matched pairs: flat
            today_pnl_usd=Decimal("-44"))            # equity: nearly stopped
        risk = [f for f in build_liquidity_summary(summary)["embeds"][0]["fields"]
                if "stop loss" in f["name"]][0]["value"]
        self.assertIn("$44.00 / $45.00", risk)
        # ...and the session bar still reads its own, unchanged, number.
        self.assertIn("$0.00 / $40.00", risk)

    def test_the_strip_is_red_on_a_losing_day_and_green_on_a_winning_one(self):
        """The strip is the first thing read on a phone, so it answers the
        first question. Operational state keeps the title icon."""

        losing = self._stop_summary(today_pnl_usd=Decimal("-12.34"))
        embed = build_liquidity_summary(losing)["embeds"][0]
        self.assertEqual(embed["color"], RED)
        self.assertIn("TODAY  $-12.34", embed["description"])

        winning = self._stop_summary(today_pnl_usd=Decimal("8.90"))
        embed = build_liquidity_summary(winning)["embeds"][0]
        self.assertEqual(embed["color"], GREEN)
        self.assertIn("TODAY  $+8.90", embed["description"])

    def test_a_profitable_day_never_paints_over_a_stuck_slot(self):
        """P&L may only take the strip when nothing operational wants
        attention. A green strip on a profitable day with seven slots held and
        silent would hide the thing that is costing reward -- the same defect
        as a stop meter reading safe while its stop is about to fire."""

        stuck = self._stop_summary(
            active_markets=14, resting_markets=7, viable_markets=7,
            spread_filtered_markets=12, today_pnl_usd=Decimal("50"),
            idle_market_reasons=(("book_stale", 5),))
        embed = build_liquidity_summary(stuck)["embeds"][0]
        self.assertEqual(embed["color"], ORANGE)
        self.assertIn("7 slot(s) STUCK", embed["description"])

    def test_the_bot_overall_line_is_separate_from_today(self):
        """A good day inside a bad week must not read as success, so both are
        shown. They are different scopes and the message says so: today is
        account-wide marked equity, overall is this ledger's own trading."""

        summary = self._stop_summary(
            today_pnl_usd=Decimal("3.00"), overall_pnl_usd=Decimal("-91.20"))
        description = build_liquidity_summary(summary)["embeds"][0]["description"]
        self.assertIn("TODAY  $+3.00", description)
        self.assertIn("BOT OVERALL  $-91.20", description)
        self.assertIn("ACCOUNT-WIDE", description)

    def test_viable_markets_going_unquoted_is_also_a_fault(self):
        """Supply exists and is not being collected. Distinct from a bound
        slot resting nothing -- here the market is not bound at all -- and it
        must not be reported as healthy just because no slot is stuck."""

        summary = LiquiditySummary(
            mode="LIVE", readiness_status="SCALE_ELIGIBLE",
            observed_at=NOW, session_started_at=NOW - timedelta(hours=2),
            active_markets=3, resting_markets=3,
            slot_capacity=20, viable_markets=9, rewarded_markets=26,
            spread_filtered_markets=17, selection_failures=0)
        embed = build_liquidity_summary(summary)["embeds"][0]
        self.assertEqual(embed["color"], ORANGE)
        self.assertIn("6 viable market(s) not quoted", embed["description"])

    def test_unquoted_viable_count_never_exceeds_slot_capacity(self):
        summary = LiquiditySummary(
            mode="LIVE", readiness_status="SCALE_ELIGIBLE",
            observed_at=NOW, session_started_at=NOW - timedelta(hours=2),
            active_markets=0, resting_markets=0,
            slot_capacity=20, viable_markets=98, rewarded_markets=247,
        )

        description = build_liquidity_summary(summary)["embeds"][0]["description"]
        self.assertIn("20 viable market(s) not quoted", description)
        self.assertNotIn("98 viable market(s) not quoted", description)

    def test_a_summary_without_supply_data_keeps_the_old_wording(self):
        """`slot_capacity` is the sentinel. Shadow runs and older callers
        supply no funnel, and must not be told how many slots are free or
        called healthy on the strength of a default of zero viable."""

        summary = LiquiditySummary(
            mode="LIVE", readiness_status="SCALE_ELIGIBLE",
            observed_at=NOW, session_started_at=NOW - timedelta(hours=2),
            active_markets=20, resting_markets=3)
        embed = build_liquidity_summary(summary)["embeds"][0]
        self.assertIn("17 slot(s) STUCK", embed["description"])
        self.assertNotIn("slot(s) free", embed["description"])
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
            # Trading -1 plus PAID 2. The nine PENDING must stay out of it:
            # invariant 22 allows pending as context and forbids it in
            # accounted net, because an unsettled reward is not income.
            overall_pnl_usd=Decimal("1"),
            trading_realized_usd=Decimal("-1"),
            open_marked_usd=Decimal("-0.5"),
            session_loss_limit_usd=Decimal("5"),
            integrity_problems=("private stream stale",))
        payload = build_liquidity_summary(summary)
        embed = payload["embeds"][0]
        self.assertEqual(embed["color"], RED)
        self.assertIn("INTEGRITY FAILURE", embed["description"])
        self.assertIn("BOT OVERALL  $+1.00", embed["description"])
        self.assertIn("Pending rewards **$9.00**", embed["description"])
        # The number pending would produce if it were counted as income.
        self.assertNotIn("$+10.00", embed["description"])
        # And the subtext reconciles to the headline rather than drifting.
        self.assertIn("trading $-1.00 + PAID reward $+2.00", embed["description"])

        calls = []
        result = DiscordNotifier(
            self.secrets(), transport=lambda url, body: calls.append(
                (url, json.loads(body))) or True).send_payload(payload)
        self.assertTrue(result.sent)
        self.assertEqual(calls[0][1], payload)


if __name__ == "__main__":
    unittest.main()
