"""Ranker slot-priority file: only fresh, well-formed evidence orders work."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from liquidity_bot.ranker_priority import (
    MAX_PRIORITY_AGE_SECONDS, MAX_PRIORITY_MARKETS, RankerPriority)

NOW = datetime(2026, 9, 26, 23, tzinfo=timezone.utc)


def payload(*slugs, generated=NOW - timedelta(minutes=20), schema=1):
    return dict(schema=schema, generated_at=generated.isoformat(),
                markets=[dict(slug=s, rank=i, net_per_hour=1.0) for i, s in enumerate(slugs, 1)])


class RankerPriorityTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "priority.json"
        self.lines = []
        self.loader = RankerPriority(self.path, log=self.lines.append)

    def tearDown(self):
        self.dir.cleanup()

    def write(self, data):
        self.path.write_text(json.dumps(data) if isinstance(data, dict) else data, encoding="utf-8")

    def test_fresh_file_gives_ranks_in_file_order_without_duplicates(self):
        self.write(payload("b", "a", "b", "c"))
        self.assertEqual(self.loader.ranks(NOW), {"b": 1, "a": 2, "c": 3})
        self.assertIn("status=loaded markets=3", self.lines[-1])

    def test_missing_stale_future_invalid_and_unknown_schema_give_no_priority(self):
        self.assertEqual(self.loader.ranks(NOW), {})
        self.assertIn("status=missing", self.lines[-1])
        stale = NOW - timedelta(seconds=MAX_PRIORITY_AGE_SECONDS + 1)
        for data, status in [(payload("a", generated=stale), "stale"),
                             (payload("a", generated=NOW + timedelta(hours=1)), "stale"),
                             ("{not json", "invalid"),
                             (payload("a", schema=2), "invalid"),
                             (dict(schema=1, markets=[]), "invalid")]:
            self.write(data)
            self.assertEqual(self.loader.ranks(NOW), {}, data)
            self.assertIn(f"status={status}", self.lines[-1])

    def test_caps_the_list_and_logs_only_on_change(self):
        self.write(payload(*[f"m{i}" for i in range(MAX_PRIORITY_MARKETS + 10)]))
        self.assertEqual(len(self.loader.ranks(NOW)), MAX_PRIORITY_MARKETS)
        self.loader.ranks(NOW + timedelta(seconds=30))
        self.assertEqual(len(self.lines), 1)
        # The file ages out without being rewritten: the bot falls back.
        later = NOW + timedelta(seconds=MAX_PRIORITY_AGE_SECONDS)
        self.assertEqual(self.loader.ranks(later), {})
        self.assertIn("status=stale", self.lines[-1])

    def test_loader_never_raises(self):
        self.path.mkdir()  # a directory where the file should be
        self.assertEqual(self.loader.ranks(NOW), {})
        self.assertTrue(self.lines[-1].startswith("[ranker-priority]"))


if __name__ == "__main__":
    unittest.main()
