#!/usr/bin/env python3
"""Build and verify client-encrypted runtime recovery archives.

SQLite inputs are copied through the online backup API, never as raw database
or WAL files. The compressed tar is encrypted in bounded chunks with
AES-256-GCM; only the encrypted result survives a successful build.
"""

from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import struct
import subprocess
import tarfile
import tempfile
from typing import BinaryIO, Iterable

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


MAGIC = b"LBREC1\n"
TAG_BYTES = 16
CHUNK_BYTES = 1024 * 1024
DEFAULT_SQLITE = (
    ("execution/commissioning.db", "data/commissioning.db"),
    ("measurement/scalp.db", "data/scalp.db"),
)
DEFAULT_FILES = (
    ("runtime/DEPLOYED_COMMIT", "DEPLOYED_COMMIT"),
    ("runtime/commissioning_quote_size", "data/commissioning_quote_size"),
    ("runtime/accounting_snapshots.json", "data/accounting_snapshots.json"),
    ("runtime/funding_cache.json", "data/funding_cache.json"),
    ("runtime/polymarket-changelog-state.json",
     "data/polymarket-changelog-state.json"),
    ("runtime/resolutions.json", "data/resolutions.json"),
    ("runtime/CURRENT_READINESS.json", "docs/CURRENT_READINESS.json"),
)
DEFAULT_LOGS = (
    "logs/commissioning.log",
    "logs/supervisor.log",
    "logs/reward-table-hourly.log",
    "logs/tv-dashboard.log",
    "logs/backup.log",
)
FORBIDDEN_PARTS = {
    ".ssh", ".env", "credentials.py", "id_rsa", "id_ed25519",
}


class RecoveryArchiveError(RuntimeError):
    """The archive cannot be built or positively verified."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(CHUNK_BYTES):
            digest.update(block)
    return digest.hexdigest()


def _json_bytes(payload: dict) -> bytes:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")


def _restrict_key(path: Path) -> None:
    try:
        path.chmod(0o600)
    except OSError as exc:
        raise RecoveryArchiveError(
            f"cannot restrict recovery key permissions: {exc}") from exc
    if os.name != "nt":
        return
    identity = subprocess.run(
        ["whoami"], capture_output=True, text=True, check=True,
        timeout=10,
    ).stdout.strip()
    if not identity:
        raise RecoveryArchiveError("cannot determine Windows key owner")
    result = subprocess.run(
        ["icacls", str(path), "/inheritance:r", "/grant:r", f"{identity}:(F)"],
        capture_output=True, text=True, check=False, timeout=30,
    )
    if result.returncode:
        raise RecoveryArchiveError(
            "cannot restrict recovery key Windows ACL: "
            + (result.stderr.strip() or result.stdout.strip()))


def init_key(path: Path) -> dict:
    """Create one machine-local 256-bit key without replacing an old key."""

    if path.exists():
        raise RecoveryArchiveError(f"recovery key already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    key = os.urandom(32)
    key_id = hashlib.sha256(key).hexdigest()[:16]
    payload = {
        "format": 1,
        "key_id": key_id,
        "key_b64": base64.b64encode(key).decode("ascii"),
    }
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True)
            handle.write("\n")
    except Exception:
        path.unlink(missing_ok=True)
        raise
    _restrict_key(path)
    return {"key_file": str(path), "key_id": key_id}


def _load_key(path: Path) -> tuple[bytes, str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        key = base64.b64decode(payload["key_b64"], validate=True)
        key_id = str(payload["key_id"])
    except Exception as exc:
        raise RecoveryArchiveError(
            f"recovery key is invalid: {path}") from exc
    if len(key) != 32:
        raise RecoveryArchiveError("recovery key must contain exactly 32 bytes")
    expected = hashlib.sha256(key).hexdigest()[:16]
    if key_id != expected:
        raise RecoveryArchiveError("recovery key id does not match its key")
    return key, key_id


def _safe_source(repo: Path, relative: str) -> Path:
    candidate = (repo / relative).resolve()
    try:
        candidate.relative_to(repo.resolve())
    except ValueError as exc:
        raise RecoveryArchiveError(
            f"recovery source escapes repository: {relative}") from exc
    lowered = {part.lower() for part in candidate.parts}
    if lowered & FORBIDDEN_PARTS or "credential" in candidate.name.lower():
        raise RecoveryArchiveError(
            f"secret-bearing recovery source is forbidden: {relative}")
    return candidate


def _sqlite_snapshot(source: Path, target: Path) -> dict:
    target.parent.mkdir(parents=True, exist_ok=True)
    source_uri = source.resolve().as_uri() + "?mode=ro"
    reader = sqlite3.connect(source_uri, uri=True)
    writer = sqlite3.connect(target)
    try:
        reader.backup(writer)
    finally:
        writer.close()
        reader.close()

    check = sqlite3.connect(target.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        integrity = str(check.execute("PRAGMA integrity_check").fetchone()[0])
        tables = [
            str(row[0])
            for row in check.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' ORDER BY name")
        ]
    finally:
        check.close()
    if integrity != "ok":
        raise RecoveryArchiveError(
            f"SQLite integrity check failed for {source}: {integrity}")
    return {
        "kind": "sqlite-online-backup",
        "bytes": target.stat().st_size,
        "sha256": _sha256(target),
        "integrity": integrity,
        "tables": tables,
    }


def _copy_public_file(source: Path, target: Path) -> dict:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return {
        "kind": "file",
        "bytes": target.stat().st_size,
        "sha256": _sha256(target),
    }


def _copy_log_tail(source: Path, target: Path, limit: int) -> dict:
    target.parent.mkdir(parents=True, exist_ok=True)
    size = source.stat().st_size
    with source.open("rb") as reader, target.open("wb") as writer:
        if size > limit:
            reader.seek(size - limit)
            reader.readline()
        shutil.copyfileobj(reader, writer, length=CHUNK_BYTES)
    return {
        "kind": "bounded-log-tail",
        "source_bytes": size,
        "bytes": target.stat().st_size,
        "sha256": _sha256(target),
    }


def _git_value(repo: Path, *arguments: str) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(repo), *arguments], capture_output=True,
        text=True, check=False, timeout=30,
    )
    if result.returncode:
        return None
    return result.stdout.strip() or None


def _write_plain_archive(staging: Path, target: Path) -> None:
    with tarfile.open(target, "w:gz", compresslevel=6) as archive:
        for path in sorted(staging.rglob("*")):
            if path.is_file():
                archive.add(path, arcname=path.relative_to(staging))


def _encrypt(plain: Path, encrypted: Path, key: bytes, key_id: str,
             created_at: str) -> dict:
    nonce = os.urandom(12)
    header = {
        "cipher": "AES-256-GCM",
        "created_at": created_at,
        "key_id": key_id,
        "nonce_b64": base64.b64encode(nonce).decode("ascii"),
        "plain_bytes": plain.stat().st_size,
        "plain_sha256": _sha256(plain),
    }
    header_bytes = _json_bytes(header)
    prefix = MAGIC + struct.pack(">I", len(header_bytes)) + header_bytes
    encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    encryptor.authenticate_additional_data(prefix)
    with plain.open("rb") as source, encrypted.open("xb") as destination:
        destination.write(prefix)
        while block := source.read(CHUNK_BYTES):
            destination.write(encryptor.update(block))
        destination.write(encryptor.finalize())
        destination.write(encryptor.tag)
    return header


def _read_header(handle: BinaryIO) -> tuple[bytes, dict, int]:
    magic = handle.read(len(MAGIC))
    if magic != MAGIC:
        raise RecoveryArchiveError("recovery archive magic is invalid")
    raw_size = handle.read(4)
    if len(raw_size) != 4:
        raise RecoveryArchiveError("recovery archive header is truncated")
    header_size = struct.unpack(">I", raw_size)[0]
    if header_size <= 0 or header_size > 64 * 1024:
        raise RecoveryArchiveError("recovery archive header size is invalid")
    header_bytes = handle.read(header_size)
    if len(header_bytes) != header_size:
        raise RecoveryArchiveError("recovery archive header is truncated")
    try:
        header = json.loads(header_bytes)
    except Exception as exc:
        raise RecoveryArchiveError("recovery archive header is invalid") from exc
    return magic + raw_size + header_bytes, header, len(MAGIC) + 4 + header_size


def _decrypt(encrypted: Path, plain: Path, key: bytes,
             expected_key_id: str) -> dict:
    total = encrypted.stat().st_size
    with encrypted.open("rb") as source:
        prefix, header, prefix_size = _read_header(source)
        if header.get("key_id") != expected_key_id:
            raise RecoveryArchiveError("archive was encrypted with another key")
        try:
            nonce = base64.b64decode(header["nonce_b64"], validate=True)
        except Exception as exc:
            raise RecoveryArchiveError("archive nonce is invalid") from exc
        cipher_bytes = total - prefix_size - TAG_BYTES
        if cipher_bytes < 0:
            raise RecoveryArchiveError("recovery archive ciphertext is truncated")
        source.seek(total - TAG_BYTES)
        tag = source.read(TAG_BYTES)
        source.seek(prefix_size)
        decryptor = Cipher(
            algorithms.AES(key), modes.GCM(nonce, tag),
        ).decryptor()
        decryptor.authenticate_additional_data(prefix)
        remaining = cipher_bytes
        try:
            with plain.open("xb") as destination:
                while remaining:
                    block = source.read(min(CHUNK_BYTES, remaining))
                    if not block:
                        raise RecoveryArchiveError(
                            "recovery archive ciphertext is truncated")
                    destination.write(decryptor.update(block))
                    remaining -= len(block)
                destination.write(decryptor.finalize())
        except InvalidTag as exc:
            plain.unlink(missing_ok=True)
            raise RecoveryArchiveError(
                "recovery archive authentication failed") from exc
    if plain.stat().st_size != int(header["plain_bytes"]):
        raise RecoveryArchiveError("decrypted archive size does not match header")
    if _sha256(plain) != str(header["plain_sha256"]):
        raise RecoveryArchiveError("decrypted archive hash does not match header")
    return header


def _verify_tar(plain: Path, scratch: Path) -> dict:
    with tarfile.open(plain, "r:gz") as archive:
        names = set(archive.getnames())
        if "manifest.json" not in names:
            raise RecoveryArchiveError("recovery manifest is missing")
        manifest_file = archive.extractfile("manifest.json")
        if manifest_file is None:
            raise RecoveryArchiveError("recovery manifest is unreadable")
        manifest = json.load(manifest_file)
        expected = manifest.get("artifacts", {})
        if set(expected) - names:
            raise RecoveryArchiveError("recovery archive is missing artifacts")
        for name, metadata in expected.items():
            member = archive.extractfile(name)
            if member is None:
                raise RecoveryArchiveError(f"artifact is unreadable: {name}")
            digest = hashlib.sha256()
            size = 0
            with member:
                while block := member.read(CHUNK_BYTES):
                    digest.update(block)
                    size += len(block)
            if size != int(metadata["bytes"]):
                raise RecoveryArchiveError(f"artifact size mismatch: {name}")
            if digest.hexdigest() != metadata["sha256"]:
                raise RecoveryArchiveError(f"artifact hash mismatch: {name}")

        for name, metadata in expected.items():
            if metadata.get("kind") != "sqlite-online-backup":
                continue
            member = archive.getmember(name)
            archive.extract(member, scratch, filter="data")
            database = scratch / name
            check = sqlite3.connect(
                database.resolve().as_uri() + "?mode=ro", uri=True)
            try:
                integrity = str(
                    check.execute("PRAGMA integrity_check").fetchone()[0])
            finally:
                check.close()
            if integrity != "ok":
                raise RecoveryArchiveError(
                    f"restored SQLite integrity failed: {name}: {integrity}")
    return manifest


def build(repo: Path, output_dir: Path, key_file: Path, source_role: str,
          sqlite_sources: Iterable[tuple[str, str]] = DEFAULT_SQLITE,
          file_sources: Iterable[tuple[str, str]] = DEFAULT_FILES,
          log_sources: Iterable[str] = DEFAULT_LOGS,
          log_tail_bytes: int = 2 * 1024 * 1024) -> dict:
    repo = repo.resolve()
    if not (repo / ".git").exists():
        raise RecoveryArchiveError(f"repository is not a git checkout: {repo}")
    key, key_id = _load_key(key_file)
    created = datetime.now(timezone.utc)
    created_at = created.isoformat()
    commit = _git_value(repo, "rev-parse", "HEAD") or "unknown"
    dirty = bool(_git_value(repo, "status", "--porcelain"))
    stamp = created.strftime("%Y%m%dT%H%M%SZ")
    safe_role = "".join(
        character for character in source_role.lower()
        if character.isalnum() or character in "-_")
    if not safe_role:
        raise RecoveryArchiveError("source role is empty after normalization")
    output_dir.mkdir(parents=True, exist_ok=True)
    encrypted = output_dir / (
        f"liquidity-recovery-{safe_role}-{stamp}-{commit[:8]}.tar.gz.enc")
    if encrypted.exists():
        raise RecoveryArchiveError(f"archive already exists: {encrypted}")

    with tempfile.TemporaryDirectory(
            prefix="liquidity-recovery-", dir=output_dir) as temporary:
        root = Path(temporary)
        staging = root / "staging"
        staging.mkdir()
        artifacts: dict[str, dict] = {}
        for archive_name, relative in sqlite_sources:
            source = _safe_source(repo, relative)
            if not source.is_file():
                continue
            target = staging / archive_name
            metadata = _sqlite_snapshot(source, target)
            metadata["source"] = relative
            artifacts[archive_name] = metadata
        if "execution/commissioning.db" not in artifacts:
            raise RecoveryArchiveError("required commissioning database is missing")

        for archive_name, relative in file_sources:
            source = _safe_source(repo, relative)
            if not source.is_file():
                continue
            target = staging / archive_name
            metadata = _copy_public_file(source, target)
            metadata["source"] = relative
            artifacts[archive_name] = metadata
        for relative in log_sources:
            source = _safe_source(repo, relative)
            if not source.is_file():
                continue
            archive_name = f"diagnostics/{source.name}.tail"
            target = staging / archive_name
            metadata = _copy_log_tail(source, target, log_tail_bytes)
            metadata["source"] = relative
            artifacts[archive_name] = metadata

        deployed = staging / "runtime" / "DEPLOYED_COMMIT"
        deployed_commit = (
            deployed.read_text(encoding="utf-8").strip()
            if deployed.exists() else None)
        manifest = {
            "format": 1,
            "created_at": created_at,
            "source_role": source_role,
            "source_host": os.environ.get("COMPUTERNAME")
            or os.uname().nodename,
            "git_commit": commit,
            "git_dirty": dirty,
            "deployed_commit": deployed_commit,
            "key_id": key_id,
            "artifacts": artifacts,
            "exclusions": [
                "credentials and environment secrets",
                "SSH private keys",
                "raw SQLite WAL/SHM/lock files",
                "unbounded logs",
                "the recovery encryption key",
            ],
        }
        manifest_path = staging / "manifest.json"
        manifest_path.write_bytes(_json_bytes(manifest) + b"\n")
        plain = root / "recovery.tar.gz"
        _write_plain_archive(staging, plain)
        header = _encrypt(plain, encrypted, key, key_id, created_at)
        plain.unlink()

    result = {
        "archive": str(encrypted),
        "archive_bytes": encrypted.stat().st_size,
        "archive_sha256": _sha256(encrypted),
        "created_at": created_at,
        "source_role": source_role,
        "source_host": manifest["source_host"],
        "git_commit": commit,
        "git_dirty": dirty,
        "deployed_commit": deployed_commit,
        "key_id": key_id,
        "artifact_count": len(artifacts),
        "plain_sha256": header["plain_sha256"],
    }
    return result


def verify(encrypted: Path, key_file: Path) -> dict:
    key, key_id = _load_key(key_file)
    with tempfile.TemporaryDirectory(prefix="liquidity-recovery-verify-") as tmp:
        scratch = Path(tmp)
        plain = scratch / "recovery.tar.gz"
        header = _decrypt(encrypted.resolve(), plain, key, key_id)
        restore = scratch / "restore"
        restore.mkdir()
        manifest = _verify_tar(plain, restore)
    return {
        "verified": True,
        "archive": str(encrypted),
        "archive_bytes": encrypted.stat().st_size,
        "archive_sha256": _sha256(encrypted),
        "created_at": manifest["created_at"],
        "source_role": manifest["source_role"],
        "source_host": manifest["source_host"],
        "git_commit": manifest["git_commit"],
        "deployed_commit": manifest.get("deployed_commit"),
        "key_id": header["key_id"],
        "artifact_count": len(manifest["artifacts"]),
        "sqlite_integrity": {
            name: metadata["integrity"]
            for name, metadata in manifest["artifacts"].items()
            if metadata.get("kind") == "sqlite-online-backup"
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build or verify an encrypted liquidity recovery archive")
    commands = parser.add_subparsers(dest="command", required=True)
    initialize = commands.add_parser("init-key")
    initialize.add_argument("--key-file", type=Path, required=True)
    builder = commands.add_parser("build")
    builder.add_argument("--repo", type=Path, default=Path.cwd())
    builder.add_argument("--output-dir", type=Path, required=True)
    builder.add_argument("--key-file", type=Path, required=True)
    builder.add_argument("--source-role", required=True)
    verifier = commands.add_parser("verify")
    verifier.add_argument("archive", type=Path)
    verifier.add_argument("--key-file", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "init-key":
            result = init_key(args.key_file)
        elif args.command == "build":
            result = build(
                args.repo, args.output_dir, args.key_file, args.source_role)
        else:
            result = verify(args.archive, args.key_file)
    except (OSError, sqlite3.Error, tarfile.TarError,
            RecoveryArchiveError) as exc:
        print(json.dumps({
            "ok": False, "error": type(exc).__name__, "detail": str(exc),
        }, sort_keys=True))
        return 1
    print(json.dumps({"ok": True, **result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
