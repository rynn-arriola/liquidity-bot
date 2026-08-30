"""Read-only local TV dashboard for the commissioning runner.

This service deliberately has no venue client.  It observes the local execution
ledger, commissioning log and OS process table so it cannot spend API capacity
or participate in trading decisions.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from functools import partial
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import math
import mimetypes
from pathlib import Path
import re
import sqlite3
import subprocess
from typing import Any, Iterable
from urllib.parse import urlsplit

from .commissioning import RUNTIME_SLOT_CAPACITY_PATH, configured_slot_capacity


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ASSETS = PROJECT_ROOT / "web" / "tv-dashboard"
DEFAULT_GAIN_CACHE = PROJECT_ROOT / "data" / "tv-gain-snapshot.json"
ACTIVE_ORDER_STATES = ("OPEN", "NEW", "PARTIAL", "PENDING", "SUBMITTED")
UNRESOLVED_INTENT_STATES = ("PREPARED", "SUBMITTING", "ACKNOWLEDGED", "UNCERTAIN")
MAX_ACTIVITY_LINES = 12
MAX_REJECTED_REQUEST_BODY_BYTES = 64 * 1024

_MARKET_RE = re.compile(r"\bmarket=([^\s]+)")
_AT_RE = re.compile(r"\bat=([^\s]+)")
_SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization\s*[:=]\s*(?:bearer\s+)?)[^\s,;]+"),
    re.compile(r"(?i)((?:api[_-]?key|key[_-]?id|secret|signature|token)\s*[:=]\s*)[^\s,;]+"),
    re.compile(r"https://discord(?:app)?\.com/api/webhooks/[^\s]+", re.I),
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _age_seconds(value: datetime | None, now: datetime) -> float | None:
    if value is None:
        return None
    return max(0.0, (now - value).total_seconds())


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _open_read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _gain_unavailable(error: str) -> dict[str, Any]:
    return {
        "available": False,
        "error": error,
        "generated_at": None,
        "age_seconds": None,
        "headline": None,
        "rows": [],
    }


def _money_value(value: Any, field: str) -> str | None:
    if value is None:
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{field} is invalid") from exc
    if not parsed.is_finite():
        raise ValueError(f"{field} is not finite")
    return format(parsed, "f")


def _read_gain_snapshot(path: Path | None, now: datetime) -> dict[str, Any]:
    """Read and validate the isolated hourly snapshot; never query the venue."""

    if path is None:
        return _gain_unavailable("gain snapshot is not configured")
    try:
        if path.stat().st_size > 128_000:
            raise ValueError("gain snapshot is unexpectedly large")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            raise ValueError("gain snapshot schema is unsupported")
        generated_at = _parse_time(payload.get("generated_at"))
        if generated_at is None:
            raise ValueError("gain snapshot time is invalid")
        source_rows = payload.get("rows")
        if not isinstance(source_rows, list) or len(source_rows) > 7:
            raise ValueError("gain snapshot rows are invalid")
        rows: list[dict[str, Any]] = []
        dates: list[str] = []
        current_days = 0
        for index, source in enumerate(source_rows):
            if not isinstance(source, dict):
                raise ValueError(f"gain row {index} is invalid")
            date_et = str(source.get("date_et") or "")
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_et):
                raise ValueError(f"gain row {index} date is invalid")
            datetime.fromisoformat(date_et)
            current_day = source.get("current_day") is True
            current_days += int(current_day)
            side_hours = float(source.get("side_hours"))
            if not math.isfinite(side_hours) or side_hours < 0:
                raise ValueError(f"gain row {index} side hours are invalid")
            net_status = str(source.get("net_status") or "")
            if net_status not in {"finalized", "pending"}:
                raise ValueError(f"gain row {index} net status is invalid")
            net = _money_value(source.get("net"), f"gain row {index} net")
            if net_status == "finalized" and net is None:
                raise ValueError(f"gain row {index} finalized net is missing")
            rows.append({
                "date_et": date_et,
                "current_day": current_day,
                "trading": _money_value(
                    source.get("trading"), f"gain row {index} trading"),
                "trading_estimate": source.get("trading_estimate") is True,
                "gain": _money_value(
                    source.get("gain"), f"gain row {index} gain"),
                "pending": _money_value(
                    source.get("pending"), f"gain row {index} pending"),
                "side_hours": side_hours,
                "net": net,
                "net_status": net_status,
            })
            dates.append(date_et)
        if len(set(dates)) != len(dates) or dates != sorted(dates):
            raise ValueError("gain snapshot dates are not unique and ordered")
        if current_days > 1:
            raise ValueError("gain snapshot has multiple current days")
        source_headline = payload.get("headline")
        if not isinstance(source_headline, dict):
            raise ValueError("gain snapshot headline is invalid")
        since = str(source_headline.get("overall_since_et") or "")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", since):
            raise ValueError("gain snapshot overall boundary is invalid")
        headline = {
            "today_pnl": _money_value(
                source_headline.get("today_pnl"), "today pnl"),
            "overall_pnl": _money_value(
                source_headline.get("overall_pnl"), "overall pnl"),
            "overall_trading": _money_value(
                source_headline.get("overall_trading"), "overall trading"),
            "overall_gain": _money_value(
                source_headline.get("overall_gain"), "overall gain"),
            "overall_since_et": since,
        }
        return {
            "available": True,
            "error": None,
            "generated_at": _iso(generated_at),
            "age_seconds": _age_seconds(generated_at, now),
            "headline": headline,
            "rows": rows,
        }
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return _gain_unavailable(str(exc))


def _process_snapshot() -> list[dict[str, Any]]:
    try:
        result = subprocess.run(
            ["ps", "-eo", "pid=,etimes=,args="],
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return []
    rows: list[dict[str, Any]] = []
    for line in result.stdout.splitlines():
        parts = line.strip().split(maxsplit=2)
        if len(parts) != 3 or not parts[0].isdigit() or not parts[1].isdigit():
            continue
        rows.append({"pid": int(parts[0]), "age_seconds": int(parts[1]), "args": parts[2]})
    return rows


def _process_state(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    runner = [row for row in rows if "liquidity_bot.live_commissioning" in str(row.get("args", ""))]
    supervisor = [row for row in rows if "supervise_commissioning.sh" in str(row.get("args", ""))]
    return {
        "runner": bool(runner),
        "runner_pids": [row["pid"] for row in runner],
        "runner_uptime_seconds": max((int(row["age_seconds"]) for row in runner), default=None),
        "supervisor": bool(supervisor),
        "supervisor_pids": [row["pid"] for row in supervisor],
    }


def _sanitize_log_line(line: str) -> str:
    clean = line.strip().replace("\x00", "")
    for pattern in _SECRET_PATTERNS:
        clean = pattern.sub(lambda match: f"{match.group(1)}[redacted]" if match.lastindex else "[webhook redacted]", clean)
    clean = re.sub(r"\s+", " ", clean)
    return clean[:360]


def _activity_kind(line: str) -> str | None:
    lowered = line.lower()
    labels = (
        ("[action]", "order"),
        ("[top-up]", "slots"),
        ("[selection]", "slots"),
        ("[rotation", "slots"),
        ("[runtime-recovery]", "recovery"),
        ("[maintenance", "maintenance"),
        ("[market-retired]", "warning"),
        ("unresolved_mutation_timeout", "warning"),
        ("[supervisor]", "system"),
        ("started", "system"),
        ("stopped", "system"),
    )
    return next((kind for marker, kind in labels if marker in lowered), None)


def _read_activity(path: Path) -> tuple[list[dict[str, Any]], datetime | None, str | None]:
    try:
        stat = path.stat()
        with path.open("rb") as handle:
            handle.seek(max(0, stat.st_size - 128_000))
            raw = handle.read().decode("utf-8", errors="replace")
    except OSError as exc:
        return [], None, str(exc)

    activity: list[dict[str, Any]] = []
    for source in reversed(raw.splitlines()):
        line = _sanitize_log_line(source)
        kind = _activity_kind(line)
        if not kind:
            continue
        market_match = _MARKET_RE.search(line)
        at_match = _AT_RE.search(line)
        activity.append(
            {
                "kind": kind,
                "market": market_match.group(1) if market_match else None,
                "at": at_match.group(1) if at_match else None,
                "message": line,
            }
        )
        if len(activity) == MAX_ACTIVITY_LINES:
            break
    return activity, datetime.fromtimestamp(stat.st_mtime, timezone.utc), None


def _latest_scope(
    connection: sqlite3.Connection, capacity: int,
) -> dict[str, Any] | None:
    if not _table_exists(connection, "execution_queue_stream_sessions"):
        return None
    row = connection.execute(
        "SELECT id, started_at, ended_at, scope_json, scope_size, status, "
        "trades_received, dropped_trades, decode_failures, last_trade_at, "
        "last_updated_at, error FROM execution_queue_stream_sessions "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    try:
        markets = json.loads(row["scope_json"])
    except (TypeError, json.JSONDecodeError):
        markets = []
    if not isinstance(markets, list):
        markets = []
    return {**dict(row), "markets": [str(value) for value in markets[:capacity]]}


def _grouped_markets(
    connection: sqlite3.Connection, table: str, where: str, params: tuple[Any, ...]
) -> set[str]:
    if not _table_exists(connection, table):
        return set()
    return {
        str(row[0])
        for row in connection.execute(
            f"SELECT DISTINCT market_slug FROM {table} WHERE {where}", params
        )
        if row[0]
    }


def _ledger_state(path: Path, now: datetime, capacity: int) -> dict[str, Any]:
    unavailable = {
        "available": False,
        "error": "ledger unavailable",
        "updated_at": None,
        "age_seconds": None,
        "working_orders": None,
        "working_markets": None,
        "positions": None,
        "unresolved_intents": None,
        "unknown_orders": None,
        "scope": None,
        "slots": [],
    }
    try:
        stat = path.stat()
        connection = _open_read_only(path)
        try:
            scope = _latest_scope(connection, capacity)
            working = _grouped_markets(
                connection,
                "execution_orders",
                f"status IN ({','.join('?' for _ in ACTIVE_ORDER_STATES)})",
                ACTIVE_ORDER_STATES,
            )
            positions = _grouped_markets(
                connection, "execution_positions", "ABS(net_qty) > ?", (1e-9,)
            )
            unresolved = _grouped_markets(
                connection,
                "execution_intents",
                f"state IN ({','.join('?' for _ in UNRESOLVED_INTENT_STATES)})",
                UNRESOLVED_INTENT_STATES,
            )
            unknown = _grouped_markets(
                connection, "execution_orders", "status=?", ("UNKNOWN_ABSENT",)
            )

            def count(table: str, where: str, params: tuple[Any, ...]) -> int | None:
                if not _table_exists(connection, table):
                    return None
                return int(connection.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}", params).fetchone()[0])

            scope_markets = list(scope["markets"]) if scope else []
            fallback = sorted(working | positions | unresolved | unknown)
            markets = (scope_markets + [market for market in fallback if market not in scope_markets])[:capacity]
            slots = []
            for index in range(capacity):
                market = markets[index] if index < len(markets) else None
                status = "empty"
                if market:
                    status = "observed"
                    if market in working:
                        status = "quoting"
                    if market in unresolved or market in unknown:
                        status = "attention"
                    if market in positions:
                        status = "position"
                slots.append({"number": index + 1, "market": market, "status": status})

            scope_time = _parse_time(scope.get("last_updated_at")) if scope else None
            result = {
                "available": True,
                "error": None,
                "updated_at": _iso(datetime.fromtimestamp(stat.st_mtime, timezone.utc)),
                "age_seconds": _age_seconds(datetime.fromtimestamp(stat.st_mtime, timezone.utc), now),
                "working_orders": count(
                    "execution_orders",
                    f"status IN ({','.join('?' for _ in ACTIVE_ORDER_STATES)})",
                    ACTIVE_ORDER_STATES,
                ),
                "working_markets": len(working),
                "positions": count("execution_positions", "ABS(net_qty) > ?", (1e-9,)),
                "unresolved_intents": count(
                    "execution_intents",
                    f"state IN ({','.join('?' for _ in UNRESOLVED_INTENT_STATES)})",
                    UNRESOLVED_INTENT_STATES,
                ),
                "unknown_orders": count("execution_orders", "status=?", ("UNKNOWN_ABSENT",)),
                "scope": {
                    "size": len(scope_markets),
                    "status": scope.get("status"),
                    "updated_at": _iso(scope_time),
                    "age_seconds": _age_seconds(scope_time, now),
                    "trades_received": scope.get("trades_received"),
                    "dropped_trades": scope.get("dropped_trades"),
                    "decode_failures": scope.get("decode_failures"),
                    "error": scope.get("error"),
                } if scope else None,
                "slots": slots,
            }
        finally:
            connection.close()
        return result
    except (OSError, sqlite3.Error) as exc:
        return {**unavailable, "error": str(exc)}


def collect_state(
    database: Path,
    log: Path,
    *,
    now: datetime | None = None,
    processes: Iterable[dict[str, Any]] | None = None,
    gain_cache: Path | None = None,
    slot_capacity_path: Path = RUNTIME_SLOT_CAPACITY_PATH,
) -> dict[str, Any]:
    """Build one local-only snapshot. Unknown data remains unknown, never zero."""

    observed_at = now or _utc_now()
    capacity = configured_slot_capacity(slot_capacity_path)
    process = _process_state(processes if processes is not None else _process_snapshot())
    ledger = _ledger_state(database, observed_at, capacity)
    activity, log_updated, log_error = _read_activity(log)
    log_age = _age_seconds(log_updated, observed_at)

    runner_live = process["runner"]
    if not runner_live:
        mode = "offline"
    elif not ledger["available"] or log_error is not None:
        mode = "degraded"
    elif (
        log_age is not None
        and ledger["age_seconds"] is not None
        and log_age > 600
        and ledger["age_seconds"] > 600
    ):
        mode = "stale"
    else:
        mode = "live"

    return {
        "schema_version": 1,
        "observed_at": _iso(observed_at),
        "mode": mode,
        "capacity": capacity,
        "source": "LOCAL READ-ONLY · ZERO VENUE API CALLS",
        "process": process,
        "ledger": ledger,
        "log": {
            "available": log_error is None,
            "updated_at": _iso(log_updated),
            "age_seconds": log_age,
            "error": log_error,
        },
        "gain_report": _read_gain_snapshot(gain_cache, observed_at),
        "activity": activity,
    }


def _client_is_local(address: str) -> bool:
    try:
        client = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    return client.is_loopback or client.is_private or client.is_link_local


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "LiquidityTV/1"

    def __init__(self, *args: Any, database: Path, log: Path, assets: Path,
                 gain_cache: Path | None = None,
                 slot_capacity_path: Path = RUNTIME_SLOT_CAPACITY_PATH,
                 **kwargs: Any) -> None:
        self.database = database
        self.log = log
        self.assets = assets.resolve()
        self.gain_cache = gain_cache
        self.slot_capacity_path = slot_capacity_path
        super().__init__(*args, **kwargs)

    def _headers(self, content_type: str, length: int, *, cache: str = "no-store") -> None:
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", cache)
        self.send_header("Content-Security-Policy", "default-src 'self'; connect-src 'self'; img-src 'self'; style-src 'self'; script-src 'self'")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()

    def _write(self, status: HTTPStatus, content: bytes, content_type: str, *, head: bool = False) -> None:
        self.send_response(status)
        self._headers(content_type, len(content))
        if not head:
            self.wfile.write(content)

    def _authorized(self) -> bool:
        if _client_is_local(self.client_address[0]):
            return True
        self._write(HTTPStatus.FORBIDDEN, b"local network only\n", "text/plain; charset=utf-8")
        return False

    def _serve(self, *, head: bool = False) -> None:
        if not self._authorized():
            return
        path = urlsplit(self.path).path
        if path == "/api/state":
            body = json.dumps(
                collect_state(
                    self.database, self.log, gain_cache=self.gain_cache,
                    slot_capacity_path=self.slot_capacity_path),
                separators=(",", ":")).encode()
            self._write(HTTPStatus.OK, body, "application/json; charset=utf-8", head=head)
            return
        asset_name = {"/": "index.html", "/index.html": "index.html", "/styles.css": "styles.css", "/app.js": "app.js"}.get(path)
        if not asset_name:
            self._write(HTTPStatus.NOT_FOUND, b"not found\n", "text/plain; charset=utf-8", head=head)
            return
        candidate = (self.assets / asset_name).resolve()
        if candidate.parent != self.assets:
            self._write(HTTPStatus.FORBIDDEN, b"forbidden\n", "text/plain; charset=utf-8", head=head)
            return
        try:
            body = candidate.read_bytes()
        except OSError:
            self._write(HTTPStatus.NOT_FOUND, b"asset unavailable\n", "text/plain; charset=utf-8", head=head)
            return
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in {"application/javascript", "application/json"}:
            content_type += "; charset=utf-8"
        self._write(HTTPStatus.OK, body, content_type, head=head)

    def do_GET(self) -> None:  # noqa: N802
        self._serve()

    def do_HEAD(self) -> None:  # noqa: N802
        self._serve(head=True)

    def _discard_rejected_body(self) -> None:
        """Drain a small rejected body before replying on Windows sockets."""

        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            return
        try:
            length = int(raw_length)
        except ValueError:
            self.close_connection = True
            return
        if length < 0 or length > MAX_REJECTED_REQUEST_BODY_BYTES:
            self.close_connection = True
            return
        if length:
            self.rfile.read(length)

    def do_POST(self) -> None:  # noqa: N802
        # Responding while request bytes remain unread can make Windows close
        # the connection with RST after the 405 headers are sent. The client
        # then sees a transport abort instead of the deliberate read-only
        # response. Drain only a bounded body; this endpoint still performs no
        # mutation and large/malformed requests force the connection closed.
        self._discard_rejected_body()
        self._write(HTTPStatus.METHOD_NOT_ALLOWED, b"read only\n", "text/plain; charset=utf-8")

    do_PUT = do_POST
    do_PATCH = do_POST
    do_DELETE = do_POST

    def log_message(self, format: str, *args: Any) -> None:
        # Keep the service log compact; failures still surface through stderr.
        if args and str(args[1]).startswith(("4", "5")):
            super().log_message(format, *args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Serve the local read-only liquidity TV dashboard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--database", type=Path, default=PROJECT_ROOT / "data" / "commissioning.db")
    parser.add_argument("--log", type=Path, default=PROJECT_ROOT / "logs" / "commissioning.log")
    parser.add_argument("--assets", type=Path, default=DEFAULT_ASSETS)
    parser.add_argument("--gain-cache", type=Path, default=DEFAULT_GAIN_CACHE)
    parser.add_argument(
        "--slot-capacity-file", type=Path,
        default=PROJECT_ROOT / RUNTIME_SLOT_CAPACITY_PATH)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.assets.is_dir():
        raise SystemExit(f"dashboard assets unavailable: {args.assets}")
    handler = partial(
        DashboardHandler, database=args.database, log=args.log,
        assets=args.assets, gain_cache=args.gain_cache,
        slot_capacity_path=args.slot_capacity_file)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"Liquidity TV dashboard: http://{args.host}:{args.port}", flush=True)
    print("Source: local read-only ledger/log; zero venue API calls", flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
