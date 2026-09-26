"""Log rotation: config, idempotent cron line, and a real copytruncate run."""
import os
from pathlib import Path
import shutil
import tempfile
import unittest

from scripts.rotate_logs import CRON_MARK, config_text, cron_line, rotate, with_cron_line


class RotateLogsTests(unittest.TestCase):
    def test_config_keeps_evidence_and_is_safe_for_an_appending_writer(self):
        text = config_text(Path("/bot"))
        self.assertTrue(text.startswith("/bot/logs/commissioning.log {"))
        for directive in ("weekly", "rotate 520", "compress", "delaycompress",
                          "copytruncate", "dateext", "missingok", "notifempty"):
            self.assertIn(f"\n    {directive}\n", text)
        self.assertNotIn("create", text)

    def test_cron_line_is_replaced_not_duplicated(self):
        line = cron_line(Path("/bot"))
        self.assertIn("cd /bot && ", line)
        self.assertTrue(line.endswith(CRON_MARK))
        once = with_cron_line("MAILTO=\"\"\n0 1 * * * other-job\n", line)
        twice = with_cron_line(once, line)
        self.assertEqual(once, twice)
        self.assertEqual(once.count(CRON_MARK), 1)
        self.assertIn("0 1 * * * other-job", twice)

    @unittest.skipUnless(os.name == "posix" and (shutil.which("logrotate")
                                                 or Path("/usr/sbin/logrotate").exists()),
                         "logrotate required")
    def test_forced_rotation_truncates_under_a_live_appending_writer(self):
        logrotate = shutil.which("logrotate") or "/usr/sbin/logrotate"
        with tempfile.TemporaryDirectory(prefix="rotate-test-") as directory:
            root = Path(directory)
            (root / "logs").mkdir()
            log = root / "logs/commissioning.log"
            with open(log, "a") as writer:  # the supervisor's >> redirection
                writer.write("old line\n" * 1000)
                writer.flush()
                self.assertEqual(rotate(root, force=True, logrotate=logrotate), 0)
                writer.write("new line\n")
                writer.flush()
            self.assertEqual(log.read_bytes(), b"new line\n")
            rotated = [p for p in (root / "logs").iterdir() if p.name != "commissioning.log"]
            self.assertEqual(len(rotated), 1)
            self.assertTrue(rotated[0].name.startswith("commissioning.log-"))
            self.assertEqual(rotated[0].read_text(), "old line\n" * 1000)
            self.assertTrue((root / "data/logrotate.state").exists())
            self.assertEqual([p.name for p in (root / "data").iterdir()], ["logrotate.state"])


if __name__ == "__main__":
    unittest.main()
