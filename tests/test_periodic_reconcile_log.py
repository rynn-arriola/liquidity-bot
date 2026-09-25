import ast
import contextlib
import inspect
import io
import textwrap
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import TestCase

from liquidity_bot.errors import AdapterError
from liquidity_bot.live_commissioning import CommissioningRunner

NOW = datetime(2026, 9, 25, 22, 0, tzinfo=timezone.utc)


def _runner():
    runner = object.__new__(CommissioningRunner)
    runner._now = lambda: NOW
    runner.client = SimpleNamespace(key_id="key-abc123", secret_key="secret-xyz789")
    return runner


def _note(runner, exc):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        runner._note_periodic_reconcile(exc)
    return out.getvalue()


class PeriodicReconcileLogTests(TestCase):
    def test_failures_are_counted_logged_and_recovery_is_reported_once(self):
        runner = _runner()
        self.assertEqual(_note(runner, None), "")  # Healthy cadence stays quiet.
        first = _note(runner, AdapterError(
            "GET /v1/portfolio/positions failed: 401", status_code=401))
        self.assertIn("[periodic-reconcile]", first)
        self.assertIn("outcome=failed consecutive=1", first)
        self.assertIn("status=401", first)
        self.assertIn("AdapterError", first)
        self.assertIn("consecutive=2", _note(runner, RuntimeError("timeout")))
        recovered = _note(runner, None)
        self.assertIn("outcome=recovered after_failures=2", recovered)
        self.assertEqual(_note(runner, None), "")
        self.assertIn("consecutive=1", _note(runner, RuntimeError("again")))

    def test_credentials_are_redacted(self):
        line = _note(_runner(), RuntimeError(
            "rejected key-abc123 with secret-xyz789"))
        self.assertNotIn("key-abc123", line)
        self.assertNotIn("secret-xyz789", line)
        self.assertIn("<redacted>", line)

    def test_logging_failure_never_escapes(self):
        runner = _runner()

        def broken_clock():
            raise RuntimeError("clock unavailable")

        runner._now = broken_clock
        runner._note_periodic_reconcile(RuntimeError("x"))
        runner._note_periodic_reconcile(None)

    def test_run_loop_reports_both_periodic_outcomes(self):
        tree = ast.parse(textwrap.dedent(inspect.getsource(CommissioningRunner.run)))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Try):
                continue
            calls = [call for call in ast.walk(node.body[0])
                     if isinstance(call, ast.Call)
                     and getattr(call.func, "attr", None) == "_rest_reconcile"]
            if not any(kw.arg == "tag" and getattr(kw.value, "value", None) == "periodic"
                       for call in calls for kw in call.keywords):
                continue
            handler = ast.unparse(node.handlers[0])
            self.assertIn("_note_periodic_reconcile(exc)", handler)
            self.assertIn("_note_periodic_reconcile(None)",
                          "\n".join(ast.unparse(stmt) for stmt in node.orelse))
            return
        self.fail("periodic account proof try block not found")
