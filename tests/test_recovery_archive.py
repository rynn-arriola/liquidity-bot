from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from scripts.recovery_archive import (
    RecoveryArchiveError,
    build,
    init_key,
    verify,
)


class RecoveryArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(
            prefix="liquidity-recovery-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        (self.repo / ".git").mkdir()
        (self.repo / "data").mkdir()
        self._database(self.repo / "data" / "commissioning.db", "orders")
        self._database(self.repo / "data" / "scalp.db", "episodes")
        (self.repo / "DEPLOYED_COMMIT").write_text(
            "0123456789abcdef\n", encoding="utf-8")
        (self.repo / ".env").write_text("SECRET=never\n", encoding="utf-8")
        (self.repo / "credentials.py").write_text(
            "TOKEN='never'\n", encoding="utf-8")
        self.key = self.root / "recovery.key"
        init_key(self.key)

    @staticmethod
    def _database(path: Path, table: str) -> None:
        database = sqlite3.connect(path)
        try:
            database.execute(f"CREATE TABLE {table} (value TEXT NOT NULL)")
            database.execute(f"INSERT INTO {table} VALUES ('preserved')")
            database.commit()
        finally:
            database.close()

    def _build(self) -> dict:
        return build(
            self.repo,
            self.root / "output",
            self.key,
            "test-laptop",
            file_sources=(("runtime/DEPLOYED_COMMIT", "DEPLOYED_COMMIT"),),
            log_sources=(),
        )

    def test_build_and_verify_two_sqlite_snapshots(self):
        built = self._build()
        archive = Path(built["archive"])

        checked = verify(archive, self.key)

        self.assertTrue(checked["verified"])
        self.assertEqual(checked["source_role"], "test-laptop")
        self.assertEqual(checked["deployed_commit"], "0123456789abcdef")
        self.assertEqual(checked["artifact_count"], 3)
        self.assertEqual(checked["sqlite_integrity"], {
            "execution/commissioning.db": "ok",
            "measurement/scalp.db": "ok",
        })
        self.assertNotIn(b"SECRET=never", archive.read_bytes())
        self.assertNotIn(b"TOKEN='never'", archive.read_bytes())

    def test_ciphertext_change_fails_authentication(self):
        built = self._build()
        archive = Path(built["archive"])
        payload = bytearray(archive.read_bytes())
        payload[len(payload) // 2] ^= 1
        damaged = self.root / "damaged.enc"
        damaged.write_bytes(payload)

        with self.assertRaisesRegex(
                RecoveryArchiveError, "authentication failed"):
            verify(damaged, self.key)

    def test_another_key_cannot_decrypt_archive(self):
        built = self._build()
        other = self.root / "other.key"
        init_key(other)

        with self.assertRaisesRegex(RecoveryArchiveError, "another key"):
            verify(Path(built["archive"]), other)

    def test_key_initialization_never_overwrites(self):
        original = json.loads(self.key.read_text(encoding="utf-8"))

        with self.assertRaisesRegex(RecoveryArchiveError, "already exists"):
            init_key(self.key)

        self.assertEqual(
            json.loads(self.key.read_text(encoding="utf-8")), original)

    def test_explicit_secret_source_is_refused(self):
        with self.assertRaisesRegex(RecoveryArchiveError, "forbidden"):
            build(
                self.repo, self.root / "output", self.key, "test",
                file_sources=(("runtime/credentials.py", "credentials.py"),),
                log_sources=(),
            )
