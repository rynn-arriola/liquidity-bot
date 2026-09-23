"""Optional bounded diagnostic archive. No API, execution or trading authority."""
from __future__ import annotations

from datetime import datetime, timezone
from dataclasses import dataclass
from collections import Counter
import hashlib
import json
from pathlib import Path
from queue import Queue, Empty
import shutil
import sqlite3
from threading import Thread, Event, BoundedSemaphore
import uuid
import zlib

PACKED = b'zlib-json-v1\0'
MAX_DISCOVERY_BYTES = 16_000_000


@dataclass(frozen=True)
class _Discovery:
    args: tuple
    kwargs: dict


class QualityStore:
    """One daemon writer; enqueue never waits for disk or a queue slot.

    Gaps are explicit in sequence numbers and cumulative counters. An unclosed
    run is incomplete. Quota exhaustion disables capture rather than deleting
    evidence or threatening the execution ledger's disk reserve.
    """
    def __init__(self, directory: Path, *, queue_size=32, max_record_bytes=2_000_000,
                 max_bytes=512_000_000, reserve_bytes=512_000_000):
        if any(type(v) is not int or v <= 0 for v in
               (queue_size, max_record_bytes, max_bytes)) or type(reserve_bytes) is not int or reserve_bytes < 0:
            raise ValueError("invalid measurement storage bounds")
        self.run_id = uuid.uuid4().hex
        self.path = Path(directory) / (self.run_id + ".sqlite3")
        self.queue = Queue(maxsize=queue_size)
        self.max_record_bytes = max_record_bytes
        self.max_bytes, self.reserve_bytes = max_bytes, reserve_bytes
        self.sequence = self.dropped = self.errors = self.written = 0
        self.disabled = False
        self.source_hashes = {}
        self.stopping = Event()
        self.discovery_slot = BoundedSemaphore(1)
        self.thread = Thread(target=self._write, name="market-quality", daemon=True)
        self.thread.start()

    def emit(self, kind: str, payload: dict) -> bool:
        self.sequence += 1
        if self.disabled or self.stopping.is_set():
            self.dropped += 1
            return False
        try:
            # Snapshot caller-owned mutable data now; strict JSON also refuses
            # nonfinite values. Producers bound their collections before here.
            body = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                              allow_nan=False)
            if len(body.encode("utf8")) > self.max_record_bytes:
                raise ValueError("record too large")
            row = (self.sequence, kind, datetime.now(timezone.utc).isoformat(),
                   body, hashlib.sha256(body.encode()).hexdigest(), self.dropped)
            self.queue.put_nowait(row)
            return True
        except Exception:
            self.dropped += 1
            return False

    def health(self) -> dict:
        return dict(run_id=self.run_id, attempted=self.sequence, written=self.written,
                    dropped=self.dropped, errors=self.errors, disabled=self.disabled,
                    source_sha256=self.source_hashes)

    def defer_discovery(self, catalog, executable, incentives, candidates, detailed,
                        deferred, selection, at, *, reasons=None, policy=None):
        """Copy collections of frozen domain values; serialize in the writer.

        At most one catalog is queued/in progress. No IO, deep serialization or
        waiting on the trading loop. Skipped scans remain explicit gaps.
        """
        self.sequence += 1
        if self.disabled or self.stopping.is_set() or not self.discovery_slot.acquire(blocking=False):
            self.dropped += 1
            return False
        try:
            if len(catalog) > 20000:
                raise ValueError('measurement catalog bound')
            job = _Discovery((tuple(catalog), dict(executable), dict(incentives),
                tuple(candidates), tuple(detailed), tuple(deferred), selection, at),
                dict(reasons=dict(reasons or {}), policy=policy))
            self.queue.put_nowait((self.sequence, 'discovery',
                datetime.now(timezone.utc).isoformat(), job, None, self.dropped))
            return True
        except Exception:
            self.discovery_slot.release()
            self.dropped += 1
            return False

    def close(self, timeout: float = .05) -> None:
        self.stopping.set()
        self.thread.join(timeout=max(0, min(timeout, .05)))

    def _write(self):
        conn = None
        try:
            for name in ('market_quality_store.py', 'market_quality_measurement.py',
                         'live_commissioning.py', 'market_selector.py', 'rewards.py',
                         'selection_measurement.py', 'execution_ledger.py'):
                self.source_hashes[name] = hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if self.path.parent.is_symlink():
                raise ValueError("symlink archive directory")
            self.path.touch(mode=0o600, exist_ok=False)
            conn = sqlite3.connect(self.path, timeout=.1)
            conn.execute("CREATE TABLE records(seq INTEGER PRIMARY KEY,kind TEXT,"
                         "received_at TEXT,body TEXT,sha256 TEXT,dropped INTEGER)")
            conn.execute("CREATE TABLE health(body TEXT)")
            conn.execute("INSERT INTO health VALUES (?)", (json.dumps(self.health()),))
            conn.commit()
            while not self.stopping.is_set() or not self.queue.empty():
                try:
                    row = self.queue.get(timeout=.1)
                except Empty:
                    continue
                if isinstance(row[3], _Discovery):
                    try:
                        from .market_quality_measurement import discovery_snapshot
                        job = row[3]
                        body = json.dumps(discovery_snapshot(*job.args, **job.kwargs),
                            sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
                        if len(body) > MAX_DISCOVERY_BYTES:
                            raise ValueError('discovery record too large')
                        row = (*row[:3], body.decode(), hashlib.sha256(body).hexdigest(), row[5])
                    except Exception:
                        self.dropped += 1
                        self.errors += 1
                        continue
                    finally:
                        self.discovery_slot.release()
                # Compression is also off the execution loop. Old TEXT archives
                # remain readable; hashes certify the original uncompressed JSON.
                packed = PACKED + zlib.compress(row[3].encode(), level=1)
                row = (*row[:3], packed, *row[4:])
                # All IO, including quotas, stays in this writer thread.
                used = sum(p.stat().st_size for p in self.path.parent.glob("*.sqlite3"))
                # Allow for SQLite pages and the transient journal, not only
                # the compressed payload, before spending the disk reserve.
                allocation = 2 * len(packed) + 65536
                if (used + allocation > self.max_bytes or
                        shutil.disk_usage(self.path.parent).free < self.reserve_bytes + allocation):
                    raise OSError("measurement storage budget")
                with conn:
                    conn.execute("INSERT INTO records VALUES (?,?,?,?,?,?)", row)
                    self.written += 1
                    conn.execute("UPDATE health SET body=?", (json.dumps(self.health()),))
            with conn:
                final = {**self.health(), "closed": True}
                conn.execute("UPDATE health SET body=?", (json.dumps(final),))
        except Exception:
            self.errors += 1
            self.disabled = True
            if conn is not None:
                try:
                    with conn:
                        conn.execute("UPDATE health SET body=?", (json.dumps(self.health()),))
                except Exception:
                    pass
        finally:
            if conn is not None:
                conn.close()


def read_archive(path: Path, *, summarize_discovery=False) -> tuple[list[dict], dict]:
    """One short readonly snapshot. Verify integrity before accepting evidence."""
    conn = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        health = json.loads(conn.execute("SELECT body FROM health").fetchone()[0])
        rows = conn.execute("SELECT seq,kind,received_at,body,sha256,dropped FROM records ORDER BY seq").fetchall()
    finally:
        conn.close()
    records = []
    for seq, kind, received, body, digest, dropped in rows:
        if isinstance(body, bytes):
            if not body.startswith(PACKED):
                raise ValueError('unknown archive encoding')
            unpacker = zlib.decompressobj()
            raw = unpacker.decompress(body[len(PACKED):], MAX_DISCOVERY_BYTES + 1)
            if len(raw) > MAX_DISCOVERY_BYTES or not unpacker.eof or unpacker.unused_data:
                raise ValueError('invalid compressed archive record')
            body = raw.decode('utf8')
        if hashlib.sha256(body.encode()).hexdigest() != digest:
            raise ValueError("quality archive checksum mismatch")
        payload = json.loads(body)
        if summarize_discovery and kind == 'discovery':
            payload['candidate_observations_by_stage'] = dict(Counter(
                row['stage'] for row in payload.pop('candidates')))
            payload['candidates'] = []
        records.append(dict(seq=seq, kind=kind, received_at=received,
                            payload=payload, sha256=digest, dropped=dropped,
                            run_id=health["run_id"]))
    health["sequence_complete"] = [r["seq"] for r in records] == list(range(1, len(records)+1))
    if health['written'] != len(records) or health['attempted'] < len(records):
        raise ValueError("quality archive count mismatch")
    return records, health
