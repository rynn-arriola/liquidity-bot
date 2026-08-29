import importlib.util
from pathlib import Path
import unittest


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


if __name__ == "__main__":
    unittest.main()
