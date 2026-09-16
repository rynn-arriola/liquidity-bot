"""Run the real supervisor script against inert executables, never a trader."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


@unittest.skipUnless(os.name == "posix" and shutil.which("bash"), "bash runtime required")
class SupervisorAttentionTests(unittest.TestCase):
    def run_supervisor(self, code, *, mode=None, relaunch_proven=False,
                       recovery=False):
        with tempfile.TemporaryDirectory(prefix="supervisor-test-") as directory:
            root = Path(directory)
            for subdir in ("scripts", ".venv/bin", "logs", "bin"):
                (root / subdir).mkdir(parents=True)
            script = root / "scripts/supervise_commissioning.sh"
            script.write_bytes((Path(__file__).resolve().parents[1] /
                                "scripts/supervise_commissioning.sh").read_bytes())
            fake_python = root / ".venv/bin/python"
            fake_python.write_text("""#!/usr/bin/env bash
case "$*" in
  *'import uuid;'*) echo 4e7a4a2a-81ce-4545-9ef1-87db7564c5b4; exit 0;;
  *liquidity_bot.account_source*) exit "$TEST_RELAUNCH_CODE";;
  *liquidity_bot.quote_size*) echo 5; exit 0;;
  *liquidity_bot.slot_capacity*) echo 20; exit 0;;
  *liquidity_bot.supervisor_alert*) exit 0;;
  *liquidity_bot.host_recovery*) echo "$*" >> host-calls; exit 0;;
  *liquidity_bot.live_commissioning*) echo "$*" >> calls; exit "$TEST_RUNNER_CODE";;
esac
exit 99
""")
            fake_python.chmod(0o755)
            sleeper = root / "bin/sleep"
            sleeper.write_text("""#!/usr/bin/env bash
# Stop only this isolated supervisor once it proves attention-loop entry.
if [ "$1" = 60 ]; then kill -TERM "$PPID"; fi
exit 0
""")
            sleeper.chmod(0o755)
            env = dict(os.environ, TEST_RUNNER_CODE=str(code),
                       TEST_RELAUNCH_CODE="0" if relaunch_proven else "1",
                       PATH=str(root / "bin") + os.pathsep + os.environ["PATH"])
            args = ["bash", str(script)]
            if mode:
                args += ["auto", "10", "32", mode]
            if recovery:
                args += ["recovery-only", "1" * 32]
            result = subprocess.run(args, cwd=root, env=env,
                                    text=True, capture_output=True, timeout=10)
            calls = (root / "calls").read_text().splitlines()
            return result, calls

    def test_host_recovery_is_single_pass_even_for_restartable_exit(self):
        for code in (0, 2, 3, 5):
            with self.subTest(code=code):
                result, calls = self.run_supervisor(code, mode="rest", recovery=True)
                self.assertEqual(result.returncode, code)
                self.assertEqual(len(calls), 1)
                self.assertIn("--recovery-only", calls[0])

    def test_manual_clean_stop_ends_supervisor(self):
        result, calls = self.run_supervisor(0)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(len(calls), 1)
        self.assertNotIn("ATTENTION_REQUIRED", result.stdout)

    def test_refused_nonflat_and_integrity_failure_never_relaunch(self):
        for code in (2, 3, 4):
            with self.subTest(code=code):
                result, calls = self.run_supervisor(code)
                self.assertEqual(len(calls), 1)
                self.assertEqual(result.returncode, -15)
                self.assertIn("state=ATTENTION_REQUIRED", result.stdout)
                self.assertIn("trading=stopped", result.stdout)

    def test_three_fast_crashes_enter_attention_not_a_fourth_run(self):
        result, calls = self.run_supervisor(5)
        self.assertEqual(len(calls), 3)
        self.assertEqual(result.returncode, -15)
        self.assertIn("state=ATTENTION_REQUIRED reason=3 fast process deaths", result.stdout)

    def test_only_verified_run_may_replace_explicit_launch_choice(self):
        for proven in (False, True):
            with self.subTest(proven=proven):
                _, calls = self.run_supervisor(5, mode="rest", relaunch_proven=proven)
                self.assertIn("--account-state-mode rest", calls[0])
                self.assertEqual("--account-state-mode" in calls[1], not proven)
                self.assertEqual("--account-state-mode" in calls[2], not proven)
