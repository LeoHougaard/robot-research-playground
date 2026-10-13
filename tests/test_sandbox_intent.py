"""Durable launch-intent fixtures; no Docker daemon is contacted."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from playground.sandbox import DockerSandbox, ExecutionResult, SandboxCleanupError


IMAGE = "python@sha256:" + "a" * 64


class SandboxIntentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="docker-intent-fixture-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.runtime = self.root / "runtime"
        self.runtime.mkdir()
        self.workspace = self.root / "workspaces" / "fixture"
        self.workspace.mkdir(parents=True)
        self.session = "a" * 32
        self.marker = self.runtime / f"docker-inflight-{self.session}.json"
        env = patch.dict(os.environ, PLAYGROUND_STATE_DIR=str(self.root), PLAYGROUND_SESSION_ID=self.session)
        env.start()
        self.addCleanup(env.stop)

    def sandbox(self, runner):
        return DockerSandbox(IMAGE, runner=runner, min_free_disk_bytes=1)

    def test_intent_is_present_before_launch_and_cleared_after_confirmed_cleanup(self):
        calls = []
        def runner(argv, **kwargs):
            calls.append(argv)
            value = json.loads(self.marker.read_text())
            self.assertEqual(value["session_id"], self.session)
            if argv[1] == "run": self.assertEqual(value["container_name"], argv[argv.index("--name") + 1])
            if argv[1] == "exec" and argv[-1] == "wait": return ExecutionResult(124, timed_out=True)
            return ExecutionResult(0)
        result = self.sandbox(runner).execute(self.workspace, "wait")
        self.assertTrue(result.timed_out)
        self.assertEqual(calls[-1][1], "rm")
        self.assertFalse(self.marker.exists())

    def test_ambiguous_startup_keeps_intent_even_when_rm_acknowledged(self):
        def runner(argv, **kwargs):
            if argv[1] == "run": return ExecutionResult(124, timed_out=True)
            return ExecutionResult(0)
        with self.assertRaises(SandboxCleanupError):
            self.sandbox(runner).execute(self.workspace, "true")
        self.assertTrue(self.marker.exists())
        with self.assertRaises(SandboxCleanupError):
            self.sandbox(lambda *a, **k: self.fail("new Docker invocation after retained intent")).execute(self.workspace, "true")

    def test_failed_cleanup_preserves_intent_and_no_new_session_work(self):
        def runner(argv, **kwargs):
            if argv[1] == "exec" and argv[-1] == "wait": return ExecutionResult(124, timed_out=True)
            if argv[1] == "rm": return ExecutionResult(1, stderr="fixture removal failed")
            if argv[1] == "container": return ExecutionResult(0, stdout="still exists")
            return ExecutionResult(0)
        with self.assertRaises(SandboxCleanupError):
            self.sandbox(runner).execute(self.workspace, "wait")
        self.assertTrue(self.marker.exists())

    def test_no_docker_process_started_clears_own_intent(self):
        def runner(*args, **kwargs):
            self.assertTrue(self.marker.exists())
            raise FileNotFoundError("fixture missing Docker")
        with self.assertRaisesRegex(Exception, "no host fallback"):
            self.sandbox(runner).execute(self.workspace, "true")
        self.assertFalse(self.marker.exists())

    def test_intent_persistence_failure_never_calls_docker(self):
        with patch("playground.sandbox.os.fsync", side_effect=OSError("fixture fsync failure")):
            with self.assertRaises(OSError):
                self.sandbox(lambda *a, **kw: self.fail("Docker before durable intent")).execute(self.workspace, "true")
        self.assertTrue(self.marker.exists())

    @unittest.skipUnless(sys.platform.startswith("linux"), "SIGKILL crash fixture requires Linux")
    def test_supervisor_sigkill_leaves_intent_for_launcher_recovery(self):
        source = '''import sys,time
from pathlib import Path
from playground.sandbox import DockerSandbox,ExecutionResult
def runner(argv,**kwargs):
    if argv[1]=='run':
        Path(sys.argv[2]).write_text('run reached')
        time.sleep(90)
    return ExecutionResult(0)
DockerSandbox(sys.argv[3],runner=runner,min_free_disk_bytes=1).execute(Path(sys.argv[1]),'true')
'''
        reached = self.root / "reached"
        process = subprocess.Popen([sys.executable, "-c", source, str(self.workspace), str(reached), IMAGE],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 5
            while not reached.exists() and time.monotonic() < deadline and process.poll() is None:
                time.sleep(.02)
            self.assertTrue(reached.exists())
            self.assertTrue(self.marker.exists())
            process.kill()
            process.communicate(timeout=3)
            value = json.loads(self.marker.read_text())
            self.assertEqual(value["session_id"], self.session)
            self.assertTrue(value["container_name"].startswith("playground-job-"))
        finally:
            if process.poll() is None: process.kill()
            process.communicate(timeout=3)
