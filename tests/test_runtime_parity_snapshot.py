import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "runtime_parity_snapshot.py"
)
SPEC = importlib.util.spec_from_file_location("runtime_parity_snapshot", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
runtime_snapshot = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runtime_snapshot)


class RuntimeParitySnapshotTests(unittest.TestCase):
    def _repo_with_ledger(self, root: Path) -> Path:
        repo = root / "repo"
        (repo / ".git").mkdir(parents=True)
        (repo / "data").mkdir()
        connection = sqlite3.connect(repo / "data" / "commissioning.db")
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, qty INT)")
            connection.execute("INSERT INTO orders(qty) VALUES (13)")
            connection.commit()
        finally:
            connection.close()
        (repo / "DEPLOYED_COMMIT").write_text("abc123\n", encoding="utf-8")
        return repo

    def test_create_uses_online_backup_and_records_private_classification(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self._repo_with_ledger(root)
            output = root / "output"

            manifest = runtime_snapshot.create_snapshot(repo, output)

            self.assertEqual(manifest["capture_method"], "sqlite-online-backup")
            self.assertEqual(
                manifest["classification"],
                "private-recovery-copy-not-writable-authority",
            )
            self.assertEqual(manifest["deployed_commit"], "abc123")
            self.assertEqual(manifest["database"]["integrity"], "ok")
            self.assertIn("orders", manifest["database"]["tables"])
            copied = sqlite3.connect(output / runtime_snapshot.DATABASE_NAME)
            try:
                self.assertEqual(
                    copied.execute("SELECT qty FROM orders").fetchone()[0], 13
                )
            finally:
                copied.close()
            self.assertEqual(
                sorted(path.name for path in output.iterdir()),
                [runtime_snapshot.DATABASE_NAME, runtime_snapshot.MANIFEST_NAME],
            )

    def test_verify_rejects_database_tampering(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "output"
            runtime_snapshot.create_snapshot(self._repo_with_ledger(root), output)
            with (output / runtime_snapshot.DATABASE_NAME).open("ab") as target:
                target.write(b"tampered")

            with self.assertRaises(runtime_snapshot.RuntimeSnapshotError):
                runtime_snapshot.verify_snapshot(output)

    def test_create_integrity_checks_native_backup_only_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self._repo_with_ledger(root)
            output = root / "output"

            with mock.patch.object(
                runtime_snapshot,
                "inspect_database",
                wraps=runtime_snapshot.inspect_database,
            ) as inspect:
                runtime_snapshot.create_snapshot(repo, output)

            self.assertEqual(inspect.call_count, 1)
            inspected = Path(inspect.call_args.args[0])
            self.assertNotEqual(inspected.parent, output)

    def test_create_rejects_copy_corruption_without_destination_sqlite_walk(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self._repo_with_ledger(root)
            output = root / "output"
            original_copy = runtime_snapshot.shutil.copy2

            def corrupt_database_copy(source, destination):
                copied = original_copy(source, destination)
                if Path(destination).name.startswith(
                    f".{runtime_snapshot.DATABASE_NAME}."
                ):
                    with Path(destination).open("ab") as target:
                        target.write(b"corrupt")
                return copied

            with mock.patch.object(
                runtime_snapshot.shutil, "copy2", side_effect=corrupt_database_copy
            ):
                with self.assertRaises(runtime_snapshot.RuntimeSnapshotError):
                    runtime_snapshot.create_snapshot(repo, output)

    def test_install_verifies_before_replacing_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            destination = root / "destination"
            runtime_snapshot.create_snapshot(self._repo_with_ledger(root), source)
            destination.mkdir()
            for suffix in ("-wal", "-shm", "-journal"):
                (destination / f"{runtime_snapshot.DATABASE_NAME}{suffix}").write_bytes(
                    b"stale-sidecar"
                )

            installed = runtime_snapshot.install_snapshot(source, destination)

            self.assertEqual(
                runtime_snapshot.verify_snapshot(destination)["database"],
                installed["database"],
            )
            parsed = json.loads(
                (destination / runtime_snapshot.MANIFEST_NAME).read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(parsed["source"], "data/commissioning.db")
            self.assertFalse(
                any(
                    (destination / f"{runtime_snapshot.DATABASE_NAME}{suffix}").exists()
                    for suffix in ("-wal", "-shm", "-journal")
                )
            )

    def test_install_integrity_checks_external_source_not_renamed_copies(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            destination = root / "destination"
            runtime_snapshot.create_snapshot(self._repo_with_ledger(root), source)

            with mock.patch.object(
                runtime_snapshot,
                "inspect_database",
                wraps=runtime_snapshot.inspect_database,
            ) as inspect:
                runtime_snapshot.install_snapshot(source, destination)

            self.assertEqual(inspect.call_count, 1)
            self.assertEqual(
                Path(inspect.call_args.args[0]),
                source / runtime_snapshot.DATABASE_NAME,
            )

    def test_source_must_remain_inside_repository(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self._repo_with_ledger(root)
            outside = root / "outside.db"
            sqlite3.connect(outside).close()

            with self.assertRaises(runtime_snapshot.RuntimeSnapshotError):
                runtime_snapshot.create_snapshot(
                    repo, root / "output", source="../outside.db"
                )


if __name__ == "__main__":
    unittest.main()
