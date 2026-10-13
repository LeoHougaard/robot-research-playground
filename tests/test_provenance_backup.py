import contextlib
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from playground.archive import Archive, canonical
from playground.backup import create_backup, restore_backup, validate_backup, _capacity_preflight, MAX_MANIFEST_BYTES, INODE_RESERVE
from playground.provenance import (bind_archive, bind_state, build_provenance,
                                   file_digest, inspect_state, read_binding)
from playground.research import ResearchSupervisor, simulation_controller
from playground.state import StateLock


class ProvenanceBackupTests(unittest.TestCase):
    def identity(self, config=None, speed=.075):
        controller, _ = simulation_controller(speed=speed)
        try:
            return build_provenance(config or {"disk_reserve_bytes": 1}, controller)
        finally:
            controller.close()

    def make_state(self, root):
        root.mkdir()
        manifest = self.identity()
        bind_state(root, manifest)
        archive = Archive(root / "archive")
        bind_archive(archive.db, manifest)
        run = archive.start_run("simulation", {"disk_reserve_bytes": 1})
        generation = archive.start_generation(run)
        program = root / "workspaces" / generation / "policy.py"
        program.parent.mkdir(parents=True)
        program.write_bytes(b"print('retained program')\n")
        artifact = file_digest(program)
        archive.record_artifact(generation, program.relative_to(root).as_posix(), artifact["sha256"], artifact["bytes"])
        # Include unregistered files so interrupted generations can be recovered.
        (program.parent / "unfinished.txt").write_text("unfinished but preserved")
        archive.end_generation(generation, "completed", "fixture")
        archive.end_run(run, "completed")
        return archive, program, manifest

    def test_identical_config_restarts_and_drift_cannot_mutate_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state"
            archive, program, manifest = self.make_state(state)
            archive.close()
            before_db = (state / "archive/evidence.sqlite3").read_bytes()
            before_binding = (state / "provenance.json").read_bytes()
            bind_state(state, self.identity())
            self.assertEqual(inspect_state(state), manifest)
            for changed in (self.identity({"disk_reserve_bytes": 2}), self.identity(speed=.08)):
                with self.assertRaisesRegex(ValueError, "fingerprint changed"):
                    bind_state(state, changed)
            self.assertEqual((state / "archive/evidence.sqlite3").read_bytes(), before_db)
            self.assertEqual((state / "provenance.json").read_bytes(), before_binding)
            self.assertEqual(program.read_bytes(), b"print('retained program')\n")

    def test_interrupted_database_binding_rolls_back_schema_and_can_restart(self):
        class InterruptedConnection(sqlite3.Connection):
            def execute(self, sql, parameters=(), /):
                if sql.startswith("INSERT INTO provenance"):
                    raise KeyboardInterrupt("injected binding interruption")
                return super().execute(sql, parameters)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self.identity()
            bind_state(root, manifest)
            archive = Archive(root / "archive")
            archive.close()
            path = root / "archive/evidence.sqlite3"
            with contextlib.closing(sqlite3.connect(path, factory=InterruptedConnection)) as connection:
                with self.assertRaises(KeyboardInterrupt):
                    bind_archive(connection, manifest)
                self.assertIsNone(connection.execute("SELECT name FROM sqlite_master WHERE name='provenance'").fetchone())
            self.assertEqual(inspect_state(root), manifest)
            archive = Archive(root / "archive")
            try:
                bind_archive(archive.db, manifest)
                self.assertEqual(inspect_state(root), manifest)
            finally:
                archive.close()

    def test_actual_input_bytes_and_simulator_geometry_are_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            calibration = Path(directory) / "measured.json"
            calibration.write_bytes(b"actual calibration bytes")
            config = {"evidence_inputs": {"calibration": str(calibration)}}
            first = self.identity(config)
            self.assertEqual(first["payload"]["inputs"]["calibration"]["sha256"], hashlib.sha256(calibration.read_bytes()).hexdigest())
            self.assertEqual(first["payload"]["geometry"]["wheel_base"], .154)
            calibration.write_bytes(b"changed calibration bytes")
            self.assertNotEqual(self.identity(config)["fingerprint"], first["fingerprint"])
            with self.assertRaises(FileNotFoundError):
                self.identity({"evidence_inputs": {"firmware": str(Path(directory) / "missing.bin")}})

    def test_physical_requires_actual_files_without_claiming_flash_attestation(self):
        controller, _ = simulation_controller()
        controller.backend.is_real = True  # Identity validation only; no hardware.
        try:
            with self.assertRaisesRegex(ValueError, "Physical provenance requires"):
                build_provenance({}, controller)
            with tempfile.TemporaryDirectory() as directory:
                inputs = {}
                for key in ("geometry", "firmware", "calibration", "calibration_validation"):
                    path = Path(directory) / key
                    path.write_bytes(key.encode())
                    inputs[key] = str(path)
                value = build_provenance({"evidence_inputs": inputs}, controller)
                self.assertEqual(value["payload"]["physical_attestation"], "not established by file fingerprints")
                self.assertEqual(value["payload"]["inputs"]["firmware"], file_digest(inputs["firmware"]))
        finally:
            controller.close()

    def test_unbound_existing_evidence_is_never_adopted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = Archive(root / "archive")
            archive.start_run("simulation", {})
            archive.close()
            original = (root / "archive/evidence.sqlite3").read_bytes()
            with self.assertRaisesRegex(ValueError, "unbound evidence"):
                bind_state(root, self.identity())
            self.assertFalse((root / "provenance.json").exists())
            self.assertEqual((root / "archive/evidence.sqlite3").read_bytes(), original)

    def test_registered_artifact_mutation_refuses_restart_and_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive, program, manifest = self.make_state(root / "state")
            archive.close()
            program.write_text("altered after archival")
            with self.assertRaisesRegex(ValueError, "artifact integrity"):
                bind_state(root / "state", manifest)
            with self.assertRaisesRegex(ValueError, "artifact integrity"):
                create_backup(root / "state", root / "backup")
            self.assertFalse((root / "backup").exists())
            self.assertEqual(program.read_text(), "altered after archival")

    def test_snapshot_captures_committed_wal_and_artifacts_restore_to_new_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive, program, manifest = self.make_state(root / "state")
            try:
                archive.db.execute("PRAGMA wal_autocheckpoint=0")
                second = archive.start_run("simulation", {"wal_fixture": True})
                self.assertGreater((root / "state/archive/evidence.sqlite3-wal").stat().st_size, 0)
                result = create_backup(root / "state", root / "backup")
                self.assertEqual(result["provenance_fingerprint"], manifest["fingerprint"])
                checked = validate_backup(root / "backup")
                self.assertIn(program.relative_to(root / "state").as_posix(), checked["files"])
                self.assertNotIn("supervisor.lock", checked["files"])
                restore_backup(root / "backup", root / "restored")
                self.assertEqual(read_binding(root / "restored"), manifest)
                with contextlib.closing(sqlite3.connect(root / "restored/archive/evidence.sqlite3")) as restored:
                    self.assertEqual(restored.execute("SELECT status FROM runs WHERE id=?", (second,)).fetchone()[0], "running")
                restored_program = root / "restored" / program.relative_to(root / "state")
                self.assertEqual(restored_program.read_bytes(), program.read_bytes())
                self.assertEqual((restored_program.parent / "unfinished.txt").read_text(), "unfinished but preserved")
                # A restore never overwrites even an empty existing directory.
                (root / "existing").mkdir()
                with self.assertRaisesRegex(ValueError, "NEW directory"):
                    restore_backup(root / "backup", root / "existing")
                with self.assertRaisesRegex(ValueError, "NEW directory"):
                    create_backup(root / "state", root / "backup")
            finally:
                archive.close()

    def test_restore_preserves_stale_launcher_records_without_reauthorizing_them(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive, _, _ = self.make_state(root / "state")
            archive.close()
            runtime = root / "state/runtime"
            runtime.mkdir()
            old_record = canonical({"version": 1, "pid": 123, "state_dir": str(root / "state"),
                                    "role": "worker", "session_id": "a" * 32,
                                    "start_ticks": "456", "boot_id": "old-host-identity"})
            (runtime / "worker.pid").write_text(old_record)
            (runtime / "session").write_text("historical session retained verbatim")
            create_backup(root / "state", root / "backup")
            restore_backup(root / "backup", root / "restored")
            self.assertEqual((root / "restored/runtime/worker.pid").read_text(), old_record)
            self.assertEqual((root / "restored/runtime/session").read_text(), "historical session retained verbatim")
            self.assertEqual((runtime / "worker.pid").read_text(), old_record)

    def test_active_supervisor_prevents_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive, _, _ = self.make_state(root / "state")
            archive.close()
            lock = StateLock(root / "state")
            try:
                with self.assertRaisesRegex(RuntimeError, "Another supervisor"):
                    create_backup(root / "state", root / "backup")
            finally:
                lock.close()
            self.assertFalse((root / "backup").exists())

    def test_interrupted_copy_never_publishes_and_preserves_originals(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive, program, _ = self.make_state(root / "state")
            archive.close()
            original = program.read_bytes()
            with patch("playground.backup._copy_file", side_effect=OSError("injected interrupted copy")):
                with self.assertRaisesRegex(OSError, "injected"):
                    create_backup(root / "state", root / "backup")
            self.assertFalse((root / "backup").exists())
            self.assertFalse(list(root.glob(".snapshot-*")))
            self.assertEqual(program.read_bytes(), original)
            create_backup(root / "state", root / "backup")
            with patch("playground.backup._copy_file", side_effect=OSError("injected interrupted restore")):
                with self.assertRaises(OSError):
                    restore_backup(root / "backup", root / "restored")
            self.assertFalse((root / "restored").exists())
            self.assertFalse(list(root.glob(".restore-*")))
            validate_backup(root / "backup")

    def test_corrupt_backup_and_forged_file_hash_refuse_restore(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive, program, _ = self.make_state(root / "state")
            archive.close()
            create_backup(root / "state", root / "backup")
            rel = program.relative_to(root / "state").as_posix()
            corrupt = root / "backup/state" / rel
            corrupt.write_bytes(b"corrupt backup")
            with self.assertRaisesRegex(ValueError, "Snapshot file integrity"):
                restore_backup(root / "backup", root / "restored")
            self.assertFalse((root / "restored").exists())
            # Updating the outer checksum does not override the SQLite artifact hash.
            path = root / "backup/snapshot.json"
            manifest = json.loads(path.read_text())
            manifest["files"][rel] = file_digest(corrupt)
            path.write_text(canonical(manifest))
            with self.assertRaisesRegex(ValueError, "artifact integrity"):
                restore_backup(root / "backup", root / "restored")
            self.assertEqual(program.read_bytes(), b"print('retained program')\n")

    def test_path_escape_symlink_and_missing_manifest_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive, program, _ = self.make_state(root / "state")
            archive.close()
            create_backup(root / "state", root / "backup")
            path = root / "backup/snapshot.json"
            original = path.read_bytes()
            manifest = json.loads(original)
            manifest["files"]["../escape"] = {"sha256": "0" * 64, "bytes": 0}
            path.write_text(canonical(manifest))
            with self.assertRaisesRegex(ValueError, "Unsafe relative"):
                restore_backup(root / "backup", root / "restored")
            path.write_bytes(original)
            (root / "backup/state/linked").symlink_to(program)
            with self.assertRaisesRegex(ValueError, "linked|Linked"):
                validate_backup(root / "backup")
            (root / "backup/state/linked").unlink()
            path.unlink()
            with self.assertRaisesRegex(ValueError, "incomplete"):
                validate_backup(root / "backup")
            self.assertFalse((root / "restored").exists())

    def test_manifest_capacity_refuses_before_copying_and_counts_escaped_paths(self):
        # A legal 512-byte path per file crosses the format cap before a modest
        # 56,000-file archive; no huge filesystem fixture is needed to prove it.
        names = ("workspaces/" + "a" * 32 + "/" + str(i).zfill(8) + "/" + "x" * (512 - 53) for i in range(56000))
        with self.assertRaisesRegex(ValueError, "capacity exceeded before copying"):
            _capacity_preflight(names)
        self.assertGreater(_capacity_preflight(["workspaces/g/é"]) ["estimated_manifest_bytes"],
                           _capacity_preflight(["workspaces/g/e"]) ["estimated_manifest_bytes"])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive, program, _ = self.make_state(root / "state")
            archive.close()
            original_database = (root / "state/archive/evidence.sqlite3").read_bytes()
            with patch("playground.backup.MAX_MANIFEST_BYTES", 512), \
                 patch("playground.backup.tempfile.mkdtemp") as staging, \
                 patch("playground.backup._copy_file") as copy:
                with self.assertRaisesRegex(ValueError, "capacity exceeded before copying"):
                    create_backup(root / "state", root / "backup")
                staging.assert_not_called()
                copy.assert_not_called()
            self.assertFalse((root / "backup").exists())
            self.assertEqual((root / "state/archive/evidence.sqlite3").read_bytes(), original_database)
            self.assertEqual(program.read_bytes(), b"print('retained program')\n")

    def test_destination_inode_reserve_refuses_before_backup_and_restore_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive, program, _ = self.make_state(root / "state")
            archive.close()
            low_inodes = type("StatVFS", (), {"f_files": 100000, "f_favail": INODE_RESERVE})()
            with patch("playground.backup._disk_admission"), \
                 patch("playground.backup.os.statvfs", return_value=low_inodes), \
                 patch("playground.backup.tempfile.mkdtemp") as staging, \
                 patch("playground.backup._copy_file") as copy:
                with self.assertRaisesRegex(RuntimeError, "reserved inodes"):
                    create_backup(root / "state", root / "backup")
                staging.assert_not_called()
                copy.assert_not_called()
            self.assertFalse((root / "backup").exists())
            create_backup(root / "state", root / "backup")
            with patch("playground.backup._disk_admission"), \
                 patch("playground.backup.os.statvfs", return_value=low_inodes), \
                 patch("playground.backup.tempfile.mkdtemp") as staging, \
                 patch("playground.backup._copy_file") as copy:
                with self.assertRaisesRegex(RuntimeError, "reserved inodes"):
                    restore_backup(root / "backup", root / "restored")
                staging.assert_not_called()
                copy.assert_not_called()
            self.assertFalse((root / "restored").exists())
            self.assertEqual(program.read_bytes(), b"print('retained program')\n")
            validate_backup(root / "backup")

    def test_destination_reserve_is_enforced_before_backup_or_restore(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive, program, _ = self.make_state(root / "state")
            archive.close()
            fake_usage = type("Usage", (), {"free": 1})()
            with patch("playground.backup.shutil.disk_usage", return_value=fake_usage):
                with self.assertRaisesRegex(RuntimeError, "disk reserve"):
                    create_backup(root / "state", root / "backup")
            self.assertFalse((root / "backup").exists())
            create_backup(root / "state", root / "backup")
            with patch("playground.backup.shutil.disk_usage", return_value=fake_usage):
                with self.assertRaisesRegex(RuntimeError, "disk reserve"):
                    restore_backup(root / "backup", root / "restored")
            self.assertFalse((root / "restored").exists())
            for invalid in (0, -1, True, 1.5):
                with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, "positive integer"):
                    restore_backup(root / "backup", root / "restored", disk_reserve_bytes=invalid)
            self.assertEqual(program.read_bytes(), b"print('retained program')\n")

    def test_strict_launcher_metadata_can_precede_binding_but_not_legacy_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime"
            runtime.mkdir()
            session = "a" * 32
            (runtime / "session").write_text(canonical({"version": 2, "id": session, "mode": "demo", "docker": None}))
            (runtime / "launcher.lock").touch()
            (runtime / "worker.log").write_text("startup output")
            (runtime / "server.pid").write_text(canonical({"version": 1, "pid": 100,
                "start_ticks": "123", "boot_id": "a" * 8 + "-" + "a" * 4 + "-" + "a" * 4 + "-" + "a" * 4 + "-" + "a" * 12,
                "role": "server", "state_dir": str(root), "session_id": session}))
            (runtime / "sessions").mkdir()
            historical = {"version": 2, "id": "b" * 32, "mode": "demo", "docker": None}
            history = runtime / "sessions" / ("b" * 32 + ".json")
            history.write_text(canonical(historical))
            self.assertIsNone(inspect_state(root))
            history.write_text(canonical({**historical, "id": "c" * 32}))
            with self.assertRaisesRegex(ValueError, "unbound evidence"):
                inspect_state(root)
            history.write_text(canonical(historical))
            for unexpected in ("runtime/evidence.json", "best-model.json", "archive/legacy", "workspaces/old/program.py"):
                path = root / unexpected
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("legacy or unknown content")
                with self.subTest(unexpected=unexpected), self.assertRaisesRegex(ValueError, "unbound evidence"):
                    bind_state(root, self.identity())
                path.unlink()
                # Remove only the fixture's newly created empty evidence tree.
                if unexpected.startswith("archive/"):
                    path.parent.rmdir()
                if unexpected.startswith("workspaces/"):
                    path.parent.rmdir()
                    path.parent.parent.rmdir()
            bind_state(root, self.identity())
            self.assertTrue((root / "provenance.json").exists())

    def test_runtime_hook_rejects_config_drift_before_archive_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            controller, clock = simulation_controller()
            supervisor = ResearchSupervisor(directory, controller, clock=clock, config={"disk_reserve_bytes": 1})
            supervisor.close()
            self.assertTrue((Path(directory) / "provenance.json").is_file())
            original = (Path(directory) / "archive/evidence.sqlite3").read_bytes()
            controller, clock = simulation_controller()
            try:
                with self.assertRaisesRegex(ValueError, "fingerprint changed"):
                    ResearchSupervisor(directory, controller, clock=clock, config={"disk_reserve_bytes": 2})
            finally:
                controller.close()
            self.assertEqual((Path(directory) / "archive/evidence.sqlite3").read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
