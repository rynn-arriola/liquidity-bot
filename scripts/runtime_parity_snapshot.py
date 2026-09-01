#!/usr/bin/env python3
"""Create and verify a private, live-safe execution-ledger parity snapshot.

This intentionally handles only the authoritative commissioning ledger.  Git
parity handles tracked files, while the larger measurement databases remain in
the encrypted weekly recovery workflow.  A snapshot is always produced with
SQLite's online backup API; live database/WAL files are never copied directly.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from typing import Sequence
import uuid


DATABASE_NAME = "commissioning-latest.db"
MANIFEST_NAME = "commissioning-latest.json"
FORMAT_VERSION = 1


class RuntimeSnapshotError(RuntimeError):
    """The private runtime snapshot could not be positively verified."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _git_value(repo: Path, *arguments: str) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(repo), *arguments],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if result.returncode:
        return None
    return result.stdout.strip() or None


def _deployed_commit(repo: Path) -> str | None:
    marker = repo / "DEPLOYED_COMMIT"
    if not marker.is_file():
        return None
    value = marker.read_text(encoding="utf-8").strip()
    return value or None


def inspect_database(path: Path) -> dict[str, object]:
    if not path.is_file():
        raise RuntimeSnapshotError(f"SQLite snapshot is missing: {path}")
    connection = sqlite3.connect(
        path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True
    )
    try:
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        tables = [
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
        ]
    finally:
        connection.close()
    if integrity != "ok":
        raise RuntimeSnapshotError(
            f"SQLite integrity check failed for {path}: {integrity}"
        )
    return {
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
        "integrity": integrity,
        "tables": tables,
    }


def _read_manifest(directory: Path) -> dict[str, object]:
    manifest_path = directory / MANIFEST_NAME
    if not manifest_path.is_file():
        raise RuntimeSnapshotError(f"snapshot manifest is missing: {manifest_path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeSnapshotError(f"snapshot manifest is invalid: {exc}") from exc
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT_VERSION:
        raise RuntimeSnapshotError("snapshot manifest format is unsupported")
    if manifest.get("capture_method") != "sqlite-online-backup":
        raise RuntimeSnapshotError("snapshot was not made with SQLite online backup")
    if not isinstance(manifest.get("database"), dict):
        raise RuntimeSnapshotError("snapshot database evidence is missing")
    return manifest


def _verify_copied_snapshot(
    directory: Path, expected_manifest: dict[str, object],
) -> dict[str, object]:
    """Prove an already-inspected snapshot survived a byte-for-byte copy.

    The online backup is integrity-checked on native storage before copying.
    Re-running SQLite's random-read integrity walk through WSL's ``/mnt`` bridge
    adds no evidence once the destination size and SHA-256 match those inspected
    bytes.  The destination hash is still read after the copy completes.
    """

    directory = directory.resolve()
    manifest = _read_manifest(directory)
    if manifest != expected_manifest:
        raise RuntimeSnapshotError("copied snapshot manifest does not match source")
    expected = manifest["database"]
    assert isinstance(expected, dict)
    database = directory / DATABASE_NAME
    if not database.is_file():
        raise RuntimeSnapshotError(f"SQLite snapshot is missing: {database}")
    actual = {
        "bytes": database.stat().st_size,
        "sha256": _sha256(database),
    }
    for field in ("bytes", "sha256"):
        if actual[field] != expected.get(field):
            raise RuntimeSnapshotError(
                f"copied snapshot {field} does not match inspected source"
            )
    return manifest


def _remove_snapshot_sidecars(directory: Path) -> None:
    for suffix in ("-wal", "-shm", "-journal"):
        (directory / f"{DATABASE_NAME}{suffix}").unlink(missing_ok=True)


def create_snapshot(
    repo: Path,
    output_dir: Path,
    *,
    source: str = "data/commissioning.db",
    source_role: str = "ACTIVE-LAPTOP",
) -> dict[str, object]:
    repo = repo.resolve()
    source_path = (repo / source).resolve()
    try:
        source_path.relative_to(repo)
    except ValueError as exc:
        raise RuntimeSnapshotError(f"ledger source escapes repository: {source}") from exc
    if not source_path.is_file():
        raise RuntimeSnapshotError(f"live ledger is missing: {source_path}")

    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ledger-parity-") as raw:
        temporary = Path(raw)
        database = temporary / DATABASE_NAME
        source_uri = source_path.as_uri() + "?mode=ro"
        reader = sqlite3.connect(source_uri, uri=True, timeout=30)
        writer = sqlite3.connect(database, timeout=30)
        try:
            reader.backup(writer, pages=256, sleep=0.01)
        finally:
            writer.close()
            reader.close()

        database_evidence = inspect_database(database)
        created_at = datetime.now(timezone.utc).isoformat()
        manifest: dict[str, object] = {
            "format": FORMAT_VERSION,
            "created_at": created_at,
            "source_role": source_role,
            "source_host": os.environ.get("COMPUTERNAME") or platform.node(),
            "source": source,
            "capture_method": "sqlite-online-backup",
            "git_head": _git_value(repo, "rev-parse", "HEAD") or "unknown",
            "git_dirty_tracked": bool(
                _git_value(repo, "status", "--porcelain", "--untracked-files=no")
            ),
            "deployed_commit": _deployed_commit(repo),
            "database": database_evidence,
            "classification": "private-recovery-copy-not-writable-authority",
            "exclusions": [
                "credentials and environment secrets",
                "SSH and recovery private keys",
                "raw live SQLite WAL/SHM files",
                "logs and caches",
                "measurement databases handled by weekly recovery",
            ],
        }
        manifest_path = temporary / MANIFEST_NAME
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        token = uuid.uuid4().hex
        staged_database = output_dir / f".{DATABASE_NAME}.{token}.tmp"
        staged_manifest = output_dir / f".{MANIFEST_NAME}.{token}.tmp"
        try:
            shutil.copy2(database, staged_database)
            shutil.copy2(manifest_path, staged_manifest)
            os.replace(staged_database, output_dir / DATABASE_NAME)
            os.replace(staged_manifest, output_dir / MANIFEST_NAME)
            _remove_snapshot_sidecars(output_dir)
        finally:
            staged_database.unlink(missing_ok=True)
            staged_manifest.unlink(missing_ok=True)
    return _verify_copied_snapshot(output_dir, manifest)


def verify_snapshot(directory: Path) -> dict[str, object]:
    directory = directory.resolve()
    database = directory / DATABASE_NAME
    manifest = _read_manifest(directory)
    expected = manifest.get("database")
    assert isinstance(expected, dict)
    actual = inspect_database(database)
    for field in ("bytes", "sha256", "integrity", "tables"):
        if actual[field] != expected.get(field):
            raise RuntimeSnapshotError(f"snapshot {field} does not match its manifest")
    return manifest


def install_snapshot(source_dir: Path, output_dir: Path) -> dict[str, object]:
    manifest = verify_snapshot(source_dir)
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ledger-install-", dir=output_dir) as raw:
        temporary = Path(raw)
        shutil.copy2(source_dir / DATABASE_NAME, temporary / DATABASE_NAME)
        shutil.copy2(source_dir / MANIFEST_NAME, temporary / MANIFEST_NAME)
        _verify_copied_snapshot(temporary, manifest)
        os.replace(temporary / DATABASE_NAME, output_dir / DATABASE_NAME)
        os.replace(temporary / MANIFEST_NAME, output_dir / MANIFEST_NAME)
        _remove_snapshot_sidecars(output_dir)
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Create, verify or install a private online ledger snapshot"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    creator = subparsers.add_parser("create")
    creator.add_argument("--repo", type=Path, required=True)
    creator.add_argument("--output-dir", type=Path, required=True)
    creator.add_argument("--source", default="data/commissioning.db")
    creator.add_argument("--source-role", default="ACTIVE-LAPTOP")
    verifier = subparsers.add_parser("verify")
    verifier.add_argument("--directory", type=Path, required=True)
    installer = subparsers.add_parser("install")
    installer.add_argument("--source-dir", type=Path, required=True)
    installer.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "create":
            result = create_snapshot(
                args.repo,
                args.output_dir,
                source=args.source,
                source_role=args.source_role,
            )
        elif args.command == "verify":
            result = verify_snapshot(args.directory)
        else:
            result = install_snapshot(args.source_dir, args.output_dir)
    except (OSError, sqlite3.Error, RuntimeSnapshotError) as exc:
        print(f"runtime snapshot failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
