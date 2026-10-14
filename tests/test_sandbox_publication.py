"""Copyback interruption/restart fixtures with fake Docker, never a daemon."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from playground.archive import canonical
from playground.research import ResearchSupervisor, simulation_controller
from playground.sandbox import (DockerSandbox, ExecutionResult, SandboxCleanupError,
                                SandboxError, recover_workspace_publications)
from playground.storage import tree_bytes


IMAGE = "python@sha256:" + "a" * 64


class FakeCopyback:
    def __call__(self, argv, **kwargs):
        if argv[1] == "cp":
            with tarfile.open(kwargs["stdout_file"], "w") as archive:
                data = b"new candidate"
                entry = tarfile.TarInfo("candidate.py")
                entry.size = len(data)
                archive.addfile(entry, io.BytesIO(data))
        return ExecutionResult(0)


class FinishModel:
    def decide(self, *args, **kwargs):
        value = {"tool": "finish", "arguments": {}}
        return value, canonical(value)


class SandboxPublicationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="sandbox-publication-fixture-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.workspace = self.root / "work"
        self.workspace.mkdir()
        (self.workspace / "original.py").write_bytes(b"accepted original")
        # Unit fixtures don't pretend to be a Spark launcher session.
        self.environment = patch.dict(os.environ)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        os.environ.pop("PLAYGROUND_SESSION_ID", None)
        os.environ.pop("PLAYGROUND_STATE_DIR", None)

    def execute(self):
        return DockerSandbox(IMAGE, runner=FakeCopyback(), min_free_disk_bytes=1).execute(self.workspace, "true")

    def stages(self):
        return list(self.root.glob(".playground-export-*"))

    def test_keyboard_interrupt_before_candidate_rename_restores_original(self):
        rename = Path.rename
        def interrupt(path, target):
            if path.name == "workspace" and path.parent.name.startswith(".playground-export-"):
                raise KeyboardInterrupt()
            return rename(path, target)
        with patch.object(Path, "rename", interrupt), self.assertRaises(KeyboardInterrupt):
            self.execute()
        self.assertEqual((self.workspace / "original.py").read_bytes(), b"accepted original")
        stage, = self.stages()
        self.assertEqual((stage / "workspace" / "candidate.py").read_bytes(), b"new candidate")
        self.assertEqual(json.loads((stage / "publication.json").read_text())["phase"], "rolled_back")
        self.assertEqual(recover_workspace_publications(self.root, 1024**2), 0)

    def test_keyboard_interrupt_after_original_rename_is_also_rolled_back(self):
        rename = Path.rename
        def interrupt(path, target):
            result = rename(path, target)
            if path == self.workspace: raise KeyboardInterrupt()
            return result
        with patch.object(Path, "rename", interrupt), self.assertRaises(KeyboardInterrupt):
            self.execute()
        self.assertEqual((self.workspace / "original.py").read_bytes(), b"accepted original")
        self.assertFalse((self.stages()[0] / "previous").exists())

    def test_keyboard_interrupt_after_candidate_rename_keeps_both_versions(self):
        rename = Path.rename
        fired = False
        def interrupt(path, target):
            nonlocal fired
            result = rename(path, target)
            if not fired and path.name == "workspace" and path.parent.name.startswith(".playground-export-"):
                fired = True
                raise KeyboardInterrupt()
            return result
        with patch.object(Path, "rename", interrupt), self.assertRaises(KeyboardInterrupt):
            self.execute()
        self.assertEqual((self.workspace / "original.py").read_bytes(), b"accepted original")
        self.assertEqual((self.stages()[0] / "workspace" / "candidate.py").read_bytes(), b"new candidate")

    def test_failed_rollback_keeps_only_original_then_recovery_restores_it(self):
        rename = Path.rename
        def interrupt(path, target):
            if path.name == "workspace" and path.parent.name.startswith(".playground-export-"):
                raise KeyboardInterrupt()
            if path.name == "previous": raise OSError("fixture rollback failure")
            return rename(path, target)
        with patch.object(Path, "rename", interrupt), self.assertRaises(SandboxCleanupError):
            self.execute()
        self.assertFalse(self.workspace.exists())
        stage, = self.stages()
        self.assertEqual((stage / "previous" / "original.py").read_bytes(), b"accepted original")
        self.assertTrue((stage / "workspace" / "candidate.py").exists())
        self.assertGreater(tree_bytes(self.root), tree_bytes(stage / "previous"))
        self.assertEqual(recover_workspace_publications(self.root, 1024**2), 1)
        self.assertEqual((self.workspace / "original.py").read_bytes(), b"accepted original")
        self.assertEqual(recover_workspace_publications(self.root, 1024**2), 0)

    def test_invalid_recovery_target_cannot_escape_workspace_root(self):
        stage = self.root / ".playground-export-fixture"
        stage.mkdir()
        (stage / "previous").mkdir()
        (stage / "previous" / "keep").write_text("only original")
        for target in ("../outside", "/outside", "..", "nested/work", "nested\\work"):
            with self.subTest(target=target):
                (stage / "publication.json").write_text(json.dumps({"version": 1, "workspace_name": target, "phase": "prepared"}))
                with self.assertRaises(SandboxError): recover_workspace_publications(self.root, 1024**2)
                self.assertEqual((stage / "previous" / "keep").read_text(), "only original")

    def test_cleanup_interruption_after_commit_preserves_published_workspace(self):
        import playground.sandbox as sandbox_module
        discard = sandbox_module._discard_publication
        def interrupt(stage):
            if json.loads((stage / "publication.json").read_text())["phase"] == "committed":
                raise KeyboardInterrupt()
            return discard(stage)
        with patch.object(sandbox_module, "_discard_publication", interrupt), self.assertRaises(KeyboardInterrupt):
            self.execute()
        self.assertEqual((self.workspace / "candidate.py").read_bytes(), b"new candidate")
        self.assertEqual((self.stages()[0] / "previous" / "original.py").read_bytes(), b"accepted original")
        self.assertEqual(recover_workspace_publications(self.root, 1024**2), 1)
        self.assertFalse(self.stages())
        self.assertEqual((self.workspace / "candidate.py").read_bytes(), b"new candidate")

    @unittest.skipUnless(sys.platform.startswith("linux"), "Abrupt process-exit fixture requires Linux")
    def test_abrupt_rename_gap_restart_restores_and_archives_interrupted_generation(self):
        state = self.root / "state"
        controller, clock = simulation_controller()
        supervisor = ResearchSupervisor(state, controller, clock=clock, config={"disk_reserve_bytes": 1})
        try:
            run = supervisor.archive.start_run("simulation", supervisor.config)
            generation = supervisor.archive.start_generation(run)
            workspace = state / "workspaces" / generation
            workspace.mkdir(parents=True)
            (workspace / "original.py").write_bytes(b"accepted original")
        finally:
            supervisor.close()
        source = '''import io,os,sys,tarfile
from pathlib import Path
from playground.sandbox import DockerSandbox,ExecutionResult
def runner(argv,**kwargs):
    if argv[1]=='cp':
        with tarfile.open(kwargs['stdout_file'],'w') as out:
            item=tarfile.TarInfo('candidate.py');item.size=13
            out.addfile(item,io.BytesIO(b'new candidate'))
    return ExecutionResult(0)
rename=Path.rename
def die(path,target):
    result=rename(path,target)
    if Path(target).name=='previous': os._exit(81)
    return result
Path.rename=die
DockerSandbox(sys.argv[2],runner=runner,min_free_disk_bytes=1).execute(Path(sys.argv[1]),'true')
'''
        crashed = subprocess.run([sys.executable, "-c", source, str(workspace), IMAGE], capture_output=True, text=True, timeout=5)
        self.assertEqual(crashed.returncode, 81, crashed.stderr)
        self.assertFalse(workspace.exists())
        stage, = (state / "workspaces").glob(".playground-export-*")
        self.assertEqual((stage / "previous" / "original.py").read_bytes(), b"accepted original")
        # Configuration drift must refuse before any recovery mutation.
        changed_controller, changed_clock = simulation_controller()
        with self.assertRaisesRegex(ValueError, "fingerprint changed"):
            ResearchSupervisor(state, changed_controller, clock=changed_clock, config={"disk_reserve_bytes": 1, "mission": "changed"})
        self.assertFalse(workspace.exists())
        controller, clock = simulation_controller()
        recovered = ResearchSupervisor(state, controller, clock=clock, config={"disk_reserve_bytes": 1})
        try:
            recovered.start_robot(operator_confirmed=True)
            recovered.run(generations=1, steps=1, model=FinishModel())
            self.assertEqual((workspace / "original.py").read_bytes(), b"accepted original")
            artifact = recovered.archive.db.execute("SELECT path FROM artifacts WHERE generation_id=?", (generation,)).fetchone()
            self.assertEqual(artifact["path"], f"workspaces/{generation}/original.py")
            old_generation = recovered.archive.db.execute("SELECT status FROM generations WHERE id=?", (generation,)).fetchone()
            self.assertEqual(old_generation["status"], "interrupted")
            self.assertTrue((stage / "workspace" / "candidate.py").exists())
            self.assertGreater(recovered.storage.refresh(), tree_bytes(stage))
        finally:
            recovered.close()
