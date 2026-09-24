import importlib.util
from pathlib import Path
import sys
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_tests.py"
SPEC = importlib.util.spec_from_file_location("run_tests", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
run_tests = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = run_tests
SPEC.loader.exec_module(run_tests)


class ParallelTestRunnerTests(unittest.TestCase):
    def test_parse_summary_uses_final_unittest_result(self):
        output = "Ran 2 tests in 0.1s\nOK\nRan 7 tests in 1.25s\nOK\n"
        self.assertEqual(run_tests.parse_summary(output), (7, 1.25))

    def test_parse_summary_preserves_missing_result(self):
        self.assertEqual(run_tests.parse_summary("import failed"), (0, None))

    def test_discover_filters_filename_globs(self):
        selected = run_tests.discover(["test_rest_*.py"])
        self.assertTrue(selected)
        self.assertTrue(all(name.startswith("test_rest_") for name in selected))

    def test_default_worker_count_is_bounded(self):
        self.assertGreaterEqual(run_tests.default_workers(), 1)
        self.assertLessEqual(run_tests.default_workers(), 4)

    def test_zero_discovered_tests_is_not_a_pass(self):
        result = run_tests.Result("empty.py", 0, 0, 0.0, 0.1, "Ran 0 tests")
        self.assertFalse(result.ok)

    def test_success_requires_tests_and_zero_return_code(self):
        result = run_tests.Result("good.py", 0, 3, 0.1, 0.2, "OK")
        self.assertTrue(result.ok)


if __name__ == "__main__":
    unittest.main()
