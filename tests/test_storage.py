"""Storage admission fixtures; no real Docker, Spark or hardware claims."""
import contextlib
import hashlib
import io
import math
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

from playground.archive import Archive
from playground.research import ResearchSupervisor, simulation_controller
from playground.storage import (AUXILIARY_BYTES, LOG_HEADROOM_BYTES, MIB, RECOVERY_BYTES,
                                StateStorage, StorageLimitError, tree_bytes,
                                validate_storage_config)


class StorageTests(unittest.TestCase):
    def test_configuration_is_strict_positive_and_finite(self):
        for key in ("state_limit_bytes", "disk_reserve_bytes", "workspace_limit_bytes", "generation_database_limit_bytes"):
            for value in (0, -1, True, 1.5, "1024", math.inf, math.nan):
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    validate_storage_config({key: value})
        for key in ("max_run_seconds", "max_action_seconds", "steps_per_generation", "max_generations"):
            for value in (0, -1, True, "2", math.inf, math.nan, 10**1000):
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    validate_storage_config({key: value})
        with self.assertRaises(ValueError):
            validate_storage_config({"mission": "x" * 33000})
        with self.assertRaises(ValueError):
            validate_storage_config([])
        validate_storage_config({"disk_reserve_bytes": 1, "max_run_seconds": .5})

    def test_scan_counts_wal_logs_unknown_files_sparse_bytes_and_rejects_links(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("archive/evidence.sqlite3", "archive/evidence.sqlite3-wal", "runtime/logs/old.log", "unknown/preserved.bin"):
                path = root/name
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("wb") as stream:
                    stream.truncate(MIB)
            self.assertGreaterEqual(tree_bytes(root), 4 * MIB)
            (root/"escape").symlink_to(root/"unknown", target_is_directory=True)
            with self.assertRaises(StorageLimitError):
                tree_bytes(root)

    def test_empty_files_keep_entry_charge_and_low_inodes_refuse_admission(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            before = tree_bytes(root)
            for index in range(20):
                (root/str(index)).touch()
            self.assertGreaterEqual(tree_bytes(root) - before, 20*8192)
            storage = StateStorage(root, {"disk_reserve_bytes": 1})
            archive = Archive(root/"archive", storage=storage)
            try:
                information = type("Filesystem", (), {"f_files": 10000, "f_favail": 200})()
                with patch("playground.storage.os.statvfs", return_value=information):
                    with self.assertRaisesRegex(StorageLimitError, "inodes"):
                        storage.begin_generation()
                self.assertEqual(len(list(root.glob("[0-9]*"))), 20)
            finally:
                archive.close()

    def test_refuses_generation_without_touching_original_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root/"original.bin"
            controller, clock = simulation_controller()
            supervisor = ResearchSupervisor(root, controller, clock=clock, config={"disk_reserve_bytes": 1, "state_limit_bytes": 128*MIB})
            try:
                original.write_bytes(b"preserve this evidence")
                expected = hashlib.sha256(original.read_bytes()).hexdigest()
                supervisor.start_robot(operator_confirmed=True)
                with contextlib.redirect_stdout(io.StringIO()):
                    result = supervisor.run(generations=2, steps=6, demo=True)
                self.assertEqual(result["status"], "storage_limit")
                self.assertEqual(result["generations"], 0)
                self.assertEqual(result["experiments"], 0)
                self.assertEqual(controller.state, "DISARMED")
                self.assertEqual(hashlib.sha256(original.read_bytes()).hexdigest(), expected)
                self.assertLess(tree_bytes(root), 128*MIB)
            finally:
                supervisor.close()

    def test_exact_generation_reservation_boundary_and_disk_headroom(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = StateStorage(directory, {"disk_reserve_bytes": 1, "workspace_limit_bytes": MIB})
            archive = Archive(Path(directory)/"archive", storage=storage)
            try:
                storage.before_transaction(archive.db)
                extra = (storage.workspace_peak + storage.database_growth + RECOVERY_BYTES +
                         storage._wal_bytes(storage.database_bytes() + storage.database_growth + RECOVERY_BYTES) +
                         LOG_HEADROOM_BYTES + AUXILIARY_BYTES)
                current = storage.refresh()
                storage.limit = current + extra - 1
                with self.assertRaises(StorageLimitError):
                    storage.begin_generation()
                storage.limit += 1
                result = storage.begin_generation()
                self.assertEqual(result.additional_bytes, extra)
                self.assertEqual(result.existing_bytes + result.additional_bytes, storage.limit)
                storage.finish_generation()
                storage.end_generation()
                available = storage.disk_reserve + extra - 1
                with patch("playground.storage.shutil.disk_usage", return_value=type("Disk", (), {"free": available})()):
                    with self.assertRaisesRegex(StorageLimitError, "full bounded generation"):
                        storage.begin_generation()
            finally:
                archive.close()

    def test_database_hard_growth_cap_leaves_recovery_headroom(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = StateStorage(directory, {"disk_reserve_bytes": 1, "generation_database_limit_bytes": 32768})
            archive = Archive(Path(directory)/"archive", storage=storage)
            try:
                run = archive.start_run("fixture", {})
                storage.begin_generation()
                cap = storage.database_cap
                archive.db.execute("CREATE TABLE bounded_payload (payload TEXT)")
                archive.db.commit()
                with self.assertRaisesRegex(StorageLimitError, "growth limit"):
                    for _ in range(20):
                        archive.db.execute("INSERT INTO bounded_payload VALUES(?)", ("x"*16384,))
                        archive.db.commit()
                self.assertLessEqual(storage.database_bytes(), cap)
                storage.finish_generation()
                archive.end_run(run, "storage_limit")
                self.assertEqual(archive.db.execute("SELECT status FROM runs WHERE id=?", (run,)).fetchone()[0], "storage_limit")
                self.assertEqual(archive.db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            finally:
                archive.close()

    def test_maximum_artifact_metadata_fits_reserved_finalization_tail(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = StateStorage(directory, {"disk_reserve_bytes": 1, "generation_database_limit_bytes": 32768})
            archive = Archive(Path(directory)/"archive", storage=storage)
            try:
                run = archive.start_run("fixture", {})
                generation = archive.start_generation(run)
                storage.begin_generation()
                archive.db.execute("CREATE TABLE bounded_payload (payload TEXT)")
                archive.db.commit()
                with self.assertRaises(StorageLimitError):
                    for _ in range(20):
                        archive.db.execute("INSERT INTO bounded_payload VALUES(?)", ("x"*16384,))
                        archive.db.commit()
                storage.finish_generation()
                admitted_cap = storage.database_cap
                for index in range(4096):
                    path = ("workspaces/" + generation + "/" + "d"*460 + f"{index:08}")
                    self.assertLessEqual(len(path.encode()), 512)
                    archive.record_artifact(generation, path, "a"*64, 0)
                archive.end_generation(generation, "stopped", "metadata fixture")
                archive.end_run(run, "storage_limit")
                storage.finish_generation()
                self.assertEqual(storage.database_cap, admitted_cap)
                self.assertLessEqual(storage.database_bytes(), admitted_cap)
                self.assertEqual(archive.summary()["artifacts"], 4096)
                self.assertEqual(archive.db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            finally:
                archive.close()

    def test_excessive_artifact_path_is_preserved_without_partial_registration(self):
        with tempfile.TemporaryDirectory() as directory:
            controller, clock = simulation_controller()
            supervisor = ResearchSupervisor(directory, controller, clock=clock, config={"disk_reserve_bytes": 1})
            try:
                workspace = Path(directory)/"workspaces"/("a"*32)
                target = workspace/("b"*180)/("c"*180)/("d"*180)/"preserved.txt"
                target.parent.mkdir(parents=True)
                target.write_bytes(b"original data")
                with self.assertRaisesRegex(ValueError, "512-byte"):
                    supervisor.archive_workspace(workspace, "a"*32)
                self.assertEqual(target.read_bytes(), b"original data")
                self.assertEqual(supervisor.archive.summary()["artifacts"], 0)
            finally:
                supervisor.close()

    def test_restart_reserves_committed_pages_still_only_in_wal(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"archive/evidence.sqlite3"
            path.parent.mkdir()
            writer = sqlite3.connect(path)
            guarded = None
            try:
                writer.execute("PRAGMA journal_mode=WAL")
                writer.execute("PRAGMA wal_autocheckpoint=0")
                writer.execute("CREATE TABLE retained(payload BLOB)")
                writer.execute("INSERT INTO retained VALUES(?)", (b"x"*(2*MIB),))
                writer.commit()
                logical_bound = path.stat().st_size + Path(str(path)+"-wal").stat().st_size
                storage = StateStorage(directory, {"disk_reserve_bytes": 1})
                minimum = RECOVERY_BYTES + storage._wal_bytes(logical_bound + RECOVERY_BYTES) + LOG_HEADROOM_BYTES + AUXILIARY_BYTES
                with patch.object(storage, "_admit", wraps=storage._admit) as admit:
                    guarded = storage.connect(path)
                self.assertGreaterEqual(admit.call_args.args[0], minimum)
                self.assertEqual(guarded.execute("SELECT length(payload) FROM retained").fetchone()[0], 2*MIB)
            finally:
                if guarded is not None:
                    guarded.close()
                writer.close()

    def test_pinned_reader_stops_before_second_wal_transaction_and_can_recover(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = StateStorage(directory, {"disk_reserve_bytes": 1})
            archive = Archive(Path(directory)/"archive", storage=storage)
            reader = None
            try:
                run = archive.start_run("fixture", {})
                storage.begin_generation()
                reader = sqlite3.connect(Path(directory)/"archive/evidence.sqlite3")
                reader.execute("BEGIN")
                reader.execute("SELECT * FROM runs").fetchall()
                generation = archive.start_generation(run)
                wal = Path(directory)/"archive/evidence.sqlite3-wal"
                before = wal.read_bytes()
                started = time.monotonic()
                with self.assertRaisesRegex(StorageLimitError, "reader"):
                    archive.end_generation(generation, "completed", "must not write")
                self.assertLess(time.monotonic() - started, 1)
                self.assertEqual(wal.read_bytes(), before)
                self.assertEqual(archive.db.execute("SELECT status FROM generations").fetchone()[0], "running")
                reader.close()
                reader = None
                storage.finish_generation()
                archive.recover_interrupted()
                self.assertEqual(archive.db.execute("SELECT status FROM generations").fetchone()[0], "interrupted")
                self.assertEqual(archive.db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            finally:
                if reader is not None:
                    reader.close()
                archive.close()

    def test_normal_tools_do_not_rescan_old_workspaces(self):
        with tempfile.TemporaryDirectory() as directory:
            controller, clock = simulation_controller()
            supervisor = ResearchSupervisor(directory, controller, clock=clock, config={"disk_reserve_bytes": 1})
            try:
                supervisor.start_robot(operator_confirmed=True)
                with patch("playground.storage.tree_bytes", wraps=tree_bytes) as measured:
                    supervisor.tool("observe", {})
                    self.assertNotIn(Path(directory), [call.args[0] for call in measured.call_args_list])
                    self.assertNotIn(Path(directory)/"workspaces", [call.args[0] for call in measured.call_args_list])
            finally:
                supervisor.close()

    def test_free_space_loss_stops_before_action_without_erasing_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            controller, clock = simulation_controller()
            supervisor = ResearchSupervisor(directory, controller, clock=clock, config={"disk_reserve_bytes": 1})
            try:
                supervisor.start_robot(operator_confirmed=True)
                with patch("playground.storage.shutil.disk_usage", return_value=type("Disk", (), {"free": 0})()):
                    with self.assertRaises(StorageLimitError):
                        supervisor.tool("experiment", {"action": {}, "prediction": {}})
                self.assertEqual(controller.state, "DISARMED")
                self.assertEqual(supervisor.archive.summary()["experiments"], 0)
                self.assertTrue((Path(directory)/"archive/evidence.sqlite3").exists())
            finally:
                supervisor.close()


if __name__ == "__main__":
    unittest.main()
