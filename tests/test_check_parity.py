import importlib.util
import base64
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_parity.py"
SPEC = importlib.util.spec_from_file_location("check_parity", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
check_parity = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check_parity)


class CheckParityTests(unittest.TestCase):
    def test_normalized_digest_ignores_checkout_line_endings(self):
        self.assertEqual(
            check_parity.normalized_digest(b"one\r\ntwo\r\n"),
            check_parity.normalized_digest(b"one\ntwo\n"),
        )

    def test_parse_remote_snapshot(self):
        marker, manifest = check_parity.parse_remote_snapshot(
            "DEPLOYED_COMMIT dc26c96\n"
            + "a" * 64
            + "  src/liquidity_bot/live_commissioning.py\n"
        )

        self.assertEqual(marker, "dc26c96")
        self.assertEqual(
            manifest,
            {"src/liquidity_bot/live_commissioning.py": "a" * 64},
        )

    def test_parse_remote_snapshot_fails_without_marker(self):
        with self.assertRaises(check_parity.ParityUnavailable):
            check_parity.parse_remote_snapshot("a" * 64 + "  tests/test_one.py\n")

    def test_manifest_differences_reports_missing_extra_and_changed(self):
        differences = check_parity.manifest_differences(
            {"same.py": "a", "changed.py": "b", "missing.py": "c"},
            {"same.py": "a", "changed.py": "d", "extra.py": "e"},
        )

        self.assertEqual(
            differences,
            [
                "missing on server: missing.py",
                "extra on server: extra.py",
                "content differs: changed.py",
            ],
        )

    def test_clean_matching_workstation_has_no_problem(self):
        self.assertEqual(check_parity.workstation_problems("", True), [])

    def test_workstation_problems_report_dirty_and_mismatched_state(self):
        self.assertEqual(
            check_parity.workstation_problems(" M STATUS.md\n?? local.txt", False),
            [
                "workstation has local changes",
                "   M STATUS.md",
                "  ?? local.txt",
                "workstation HEAD does not equal the GitHub source of truth",
            ],
        )

    def test_peer_config_is_optional_and_requires_host_repo_pair(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missing.json"
            self.assertEqual(check_parity.load_peer_config(path), ("", ""))

            path.write_text(json.dumps({"host": "poly-pc"}), encoding="utf-8")
            with self.assertRaises(check_parity.ParityUnavailable):
                check_parity.load_peer_config(path)

    def test_peer_config_reads_machine_local_coordinates(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "peer.json"
            path.write_text(
                json.dumps(
                    {
                        "host": "poly-laptop",
                        "repo": r"C:\Users\operator\liquidity-bot",
                    }
                ),
                encoding="utf-8",
            )

            self.assertEqual(
                check_parity.load_peer_config(path),
                ("poly-laptop", r"C:\Users\operator\liquidity-bot"),
            )

    def test_peer_command_is_encoded_safely_and_prevents_recursion(self):
        command = check_parity.peer_powershell_command(
            r"C:\Users\O'Brien\liquidity-bot",
            sync=True,
        )
        encoded = base64.b64encode(command.encode("utf-16le")).decode("ascii")
        decoded = base64.b64decode(encoded).decode("utf-16le")

        self.assertIn("O''Brien", decoded)
        self.assertIn("--sync --no-peer", decoded)
        self.assertIn("--no-fetch", decoded)
        self.assertIn("Remove-Item Env:PARITY_PEER_HOST", decoded)

    def test_peer_command_imports_bundle_before_safe_sync(self):
        command = check_parity.peer_powershell_command(
            r"C:\repo",
            sync=True,
            bundle_name=".liquidity-parity-test.bundle",
        )

        self.assertIn("git fetch $parityBundle", command)
        self.assertIn("refs/remotes/origin/main:refs/remotes/origin/main", command)
        self.assertIn("Remove-Item -LiteralPath $parityBundle", command)
        self.assertLess(
            command.index("git fetch $parityBundle"),
            command.index("scripts/check_parity.py"),
        )

    def test_offline_peer_is_returned_as_pending_evidence(self):
        completed = subprocess.CompletedProcess(
            args=["ssh"],
            returncode=255,
            stdout="",
            stderr="connection timed out",
        )
        with mock.patch.object(check_parity, "_run", return_value=completed):
            code, output = check_parity.peer_parity(
                "poly-laptop",
                r"C:\repo",
                sync=True,
                github_head="a" * 40,
            )

        self.assertEqual(code, 255)
        self.assertEqual(output, "connection timed out")

    def test_matching_peer_skips_bundle_copy(self):
        completed = subprocess.CompletedProcess(
            args=["ssh"], returncode=0, stdout="peer ok", stderr=""
        )
        with (
            mock.patch.object(
                check_parity, "_peer_head", return_value=(0, "a" * 40)
            ),
            mock.patch.object(
                check_parity, "_run", return_value=completed
            ) as run,
        ):
            code, output = check_parity.peer_parity(
                "poly-laptop",
                r"C:\repo",
                sync=True,
                github_head="a" * 40,
            )

        self.assertEqual((code, output), (0, "peer ok"))
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0][0], "ssh")

    def test_behind_peer_receives_verified_origin_bundle(self):
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        with (
            mock.patch.object(
                check_parity, "_peer_head", return_value=(0, "b" * 40)
            ),
            mock.patch.object(
                check_parity, "_run", return_value=completed
            ) as run,
        ):
            code, _ = check_parity.peer_parity(
                "poly-laptop",
                r"C:\repo",
                sync=True,
                github_head="a" * 40,
            )

        self.assertEqual(code, 0)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(commands[0][:3], ("git", "bundle", "create"))
        self.assertEqual(commands[1][0], "scp")
        self.assertEqual(commands[2][0], "ssh")

    def test_incomplete_peer_arguments_fail_closed_without_git_work(self):
        with (
            mock.patch.object(
                check_parity, "load_peer_config", return_value=("", "")
            ),
            mock.patch.object(check_parity, "_git") as git,
        ):
            code = check_parity.main(
                ["--no-fetch", "--peer-host", "poly-laptop"]
            )

        self.assertEqual(code, 2)
        git.assert_not_called()


if __name__ == "__main__":
    unittest.main()
