"""Independent entry-pause evidence for the live Retail runner.

The Retail API can continue accepting requests while the public Polymarket US
application prevents ordinary users from trading.  That is a participation
state, not an authenticated account-state fact, so it is observed separately
and may only remove new-risk authority.  It never authorizes a mutation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import gzip
import json
import os
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen


POLYMARKET_US_HOME = "https://polymarket.us/"
UI_PROBE_TIMEOUT_SECONDS = 5.0
UI_PROBE_MAX_BYTES = 4 * 1024 * 1024
OPERATOR_ENTRY_PAUSE_PATH = Path("data/operator-entry-pause.json")


class UiTradingState(str, Enum):
    HEALTHY = "HEALTHY"
    MAINTENANCE = "MAINTENANCE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class UiTradingProbe:
    state: UiTradingState
    detail: str


def classify_polymarket_us_homepage(
    status_code: int, body: str,
) -> UiTradingProbe:
    """Classify only strong, product-scoped homepage evidence."""

    if status_code != 200:
        return UiTradingProbe(
            UiTradingState.UNKNOWN, f"http_status_{status_code}")
    lowered = body.casefold()
    maintenance_heading = "maintenance mode" in lowered
    trading_paused = "trading is paused right now" in lowered
    maintenance_body = (
        "currently performing maintenance on the platform" in lowered)
    if maintenance_heading and (trading_paused or maintenance_body):
        return UiTradingProbe(UiTradingState.MAINTENANCE, "exact_ui_banner")

    # Generic uses of the word "maintenance" in scripts or legal copy are not
    # participation evidence.  A healthy result requires the official product
    # title and the absence of either exact pause phrase.
    has_product_title = (
        "<title" in lowered and "polymarket us" in lowered)
    if (has_product_title and not maintenance_heading
            and not trading_paused and not maintenance_body):
        return UiTradingProbe(UiTradingState.HEALTHY, "official_homepage")
    return UiTradingProbe(UiTradingState.UNKNOWN, "unrecognized_page")


def probe_polymarket_us_homepage() -> UiTradingProbe:
    """Fetch a bounded public page without credentials or venue API calls."""

    request = Request(
        POLYMARKET_US_HOME,
        headers={
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Encoding": "gzip",
            "Cache-Control": "no-cache",
            "User-Agent": "liquidity-bot-ui-availability/1.0",
        },
        method="GET",
    )
    try:
        with urlopen(request, timeout=UI_PROBE_TIMEOUT_SECONDS) as response:
            final_host = (urlparse(response.geturl()).hostname or "").lower()
            if final_host not in {"polymarket.us", "www.polymarket.us"}:
                return UiTradingProbe(
                    UiTradingState.UNKNOWN, "unexpected_redirect")
            content_encoding = str(
                response.headers.get("Content-Encoding", "")).casefold()
            if content_encoding == "gzip":
                with gzip.GzipFile(fileobj=response) as compressed:
                    raw = compressed.read(UI_PROBE_MAX_BYTES + 1)
            else:
                raw = response.read(UI_PROBE_MAX_BYTES + 1)
            if len(raw) > UI_PROBE_MAX_BYTES:
                return UiTradingProbe(
                    UiTradingState.UNKNOWN, "response_too_large")
            body = raw.decode("utf-8", errors="replace")
            return classify_polymarket_us_homepage(
                int(response.status), body)
    except Exception as exc:
        return UiTradingProbe(
            UiTradingState.UNKNOWN, type(exc).__name__)


def operator_entry_pause_requested(
    path: Path = OPERATOR_ENTRY_PAUSE_PATH,
) -> bool:
    """A pause file is a fail-closed latch; its contents grant no authority."""

    try:
        return path.exists()
    except OSError:
        # An unreadable runtime-control path cannot safely mean "resume".
        return True


def set_operator_entry_pause(
    paused: bool,
    *,
    path: Path = OPERATOR_ENTRY_PAUSE_PATH,
    now: datetime | None = None,
) -> None:
    """Atomically persist or clear the machine-local operator latch."""

    if not paused:
        path.unlink(missing_ok=True)
        return
    observed = now or datetime.now(timezone.utc)
    if observed.tzinfo is None or observed.utcoffset() is None:
        raise ValueError("entry-pause timestamp must be timezone-aware")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    payload = {
        "schema_version": 1,
        "paused": True,
        "requested_at": observed.astimezone(timezone.utc).isoformat()
        .replace("+00:00", "Z"),
    }
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(0o600)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
