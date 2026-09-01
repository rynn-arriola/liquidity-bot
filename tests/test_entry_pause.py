from __future__ import annotations

from datetime import datetime, timezone
import io
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from liquidity_bot.entry_pause import (
    UI_PROBE_MAX_BYTES,
    UiTradingState,
    classify_polymarket_us_homepage,
    operator_entry_pause_requested,
    probe_polymarket_us_homepage,
    set_operator_entry_pause,
)


class _Response:
    status = 200
    headers: dict[str, str] = {}

    def __init__(self, body: bytes, url: str = "https://polymarket.us/"):
        self.body = body
        self.url = url
        self._stream = io.BytesIO(body)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def geturl(self):
        return self.url

    def read(self, count: int):
        return self._stream.read(count)


class EntryPauseTests(unittest.TestCase):
    def test_exact_maintenance_banner_pauses(self):
        result = classify_polymarket_us_homepage(
            200,
            "<title>Polymarket US</title><h1>Maintenance Mode</h1>"
            "<p>Trading is paused right now</p>",
        )
        self.assertEqual(result.state, UiTradingState.MAINTENANCE)

    def test_generic_maintenance_word_does_not_pause(self):
        result = classify_polymarket_us_homepage(
            200,
            "<title>Polymarket US | Trade</title>maintenance schedule",
        )
        self.assertEqual(result.state, UiTradingState.HEALTHY)

    def test_unrecognized_maintenance_heading_cannot_claim_healthy(self):
        result = classify_polymarket_us_homepage(
            200, "<title>Polymarket US</title><h1>Maintenance Mode</h1>")
        self.assertEqual(result.state, UiTradingState.UNKNOWN)

    def test_unknown_status_or_page_cannot_claim_healthy(self):
        self.assertEqual(
            classify_polymarket_us_homepage(503, "Maintenance Mode").state,
            UiTradingState.UNKNOWN,
        )
        self.assertEqual(
            classify_polymarket_us_homepage(200, "login proxy").state,
            UiTradingState.UNKNOWN,
        )

    def test_probe_rejects_cross_site_redirect_and_oversized_body(self):
        with patch(
            "liquidity_bot.entry_pause.urlopen",
            return_value=_Response(
                b"<title>Polymarket US</title>", "https://example.com/"),
        ):
            self.assertEqual(
                probe_polymarket_us_homepage().state,
                UiTradingState.UNKNOWN,
            )
        with patch(
            "liquidity_bot.entry_pause.urlopen",
            return_value=_Response(b"x" * (UI_PROBE_MAX_BYTES + 1)),
        ):
            self.assertEqual(
                probe_polymarket_us_homepage().detail,
                "response_too_large",
            )

    def test_probe_accepts_bounded_gzip(self):
        import gzip

        response = _Response(gzip.compress(
            b"<title>Polymarket US | Trade</title>"))
        response.headers = {"Content-Encoding": "gzip"}
        with patch(
            "liquidity_bot.entry_pause.urlopen", return_value=response,
        ):
            self.assertEqual(
                probe_polymarket_us_homepage().state,
                UiTradingState.HEALTHY,
            )

    def test_operator_pause_is_atomic_persistent_and_reversible(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "pause.json"
            self.assertFalse(operator_entry_pause_requested(path))
            set_operator_entry_pause(
                True,
                path=path,
                now=datetime(2026, 9, 1, tzinfo=timezone.utc),
            )
            self.assertTrue(operator_entry_pause_requested(path))
            self.assertNotIn("secret", path.read_text(encoding="utf-8"))
            set_operator_entry_pause(False, path=path)
            self.assertFalse(operator_entry_pause_requested(path))

    def test_probe_exception_fails_unknown(self):
        with patch(
            "liquidity_bot.entry_pause.urlopen",
            Mock(side_effect=TimeoutError("slow")),
        ):
            result = probe_polymarket_us_homepage()
        self.assertEqual(result.state, UiTradingState.UNKNOWN)
        self.assertEqual(result.detail, "TimeoutError")
