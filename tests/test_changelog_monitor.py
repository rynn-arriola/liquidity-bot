from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from liquidity_bot.changelog_monitor import check_feed, load_seen


def feed(*items):
    body = "".join(
        f"<item><guid>{guid}</guid><title>{title}</title>"
        f"<link>https://docs.polymarket.us/changelog/{guid}</link>"
        f"<pubDate>Thu, 27 Aug 2026 12:00:00 GMT</pubDate></item>"
        for guid, title in items)
    return f"<rss><channel>{body}</channel></rss>".encode()


class ChangelogMonitorTests(unittest.TestCase):
    def setUp(self):
        self.state = Path(tempfile.mkdtemp()) / "state.json"

    def test_first_run_baselines_without_historical_alert(self):
        notified = []
        result = check_feed(
            feed(("old", "Old release")), self.state,
            notify=lambda rows: notified.append(rows) or True)
        self.assertEqual(result, ())
        self.assertEqual(notified, [])
        self.assertEqual(load_seen(self.state), ("old",))

    def test_new_item_alerts_once_and_commits_after_success(self):
        check_feed(feed(("old", "Old release")), self.state)
        notified = []
        payload = feed(("new", "New release"), ("old", "Old release"))

        first = check_feed(
            payload, self.state,
            notify=lambda rows: notified.append(rows) or True)
        second = check_feed(
            payload, self.state,
            notify=lambda rows: notified.append(rows) or True)

        self.assertEqual([row.entry_id for row in first], ["new"])
        self.assertEqual(second, ())
        self.assertEqual(len(notified), 1)

    def test_failed_delivery_is_retried_without_losing_item(self):
        check_feed(feed(("old", "Old release")), self.state)
        payload = feed(("new", "New release"), ("old", "Old release"))

        pending = check_feed(payload, self.state, notify=lambda _rows: False)
        retried = check_feed(payload, self.state, notify=lambda _rows: True)

        self.assertEqual([row.entry_id for row in pending], ["new"])
        self.assertEqual([row.entry_id for row in retried], ["new"])
        self.assertIn("new", load_seen(self.state))

    def test_invalid_or_empty_feed_fails_closed(self):
        with self.assertRaises(ValueError):
            check_feed(b"<not-rss>", self.state)
        with self.assertRaisesRegex(ValueError, "no usable items"):
            check_feed(b"<rss><channel/></rss>", self.state)


if __name__ == "__main__":
    unittest.main()
