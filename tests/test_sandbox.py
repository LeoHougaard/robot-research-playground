import os
import io
from pathlib import Path
import sys
import tempfile
import tarfile
import unittest
from unittest.mock import patch

from playground.sandbox import DockerSandbox, ExecutionResult, SandboxError, SandboxCleanupError, bounded_run, validate_workspace, extract_workspace


IMAGE = "python@sha256:" + "a" * 64


class FakeDocker:
    def __init__(self, result=None, malicious=False):
        self.calls = []
        self.result = result or ExecutionResult(0, "done\n")
        self.malicious = malicious

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        if argv[1] == "cp":
            with tarfile.open(kwargs["stdout_file"], "w") as archive:
                payload = b"x" * 2048 if self.malicious else b"42"
                entry = tarfile.TarInfo("answer.txt")
                entry.size = len(payload)
                archive.addfile(entry, io.BytesIO(payload))
        if argv[1] == "exec" and argv[-1] != "cp -R /input/. /workspace/":
            return self.result
        return ExecutionResult(0)


class SandboxTests(unittest.TestCase):
    def test_flags_and_no_host_command_execution(self):
        fake = FakeDocker()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "work"
            path.mkdir()
            (path / "original").write_text("input")
            sandbox = DockerSandbox(IMAGE, runner=fake)
            result = sandbox.execute(path, "echo hi; touch /root/escape")
            self.assertEqual(result.stdout, "done\n")
            run = fake.calls[0]
            for flag in ("--network=none", "--cap-drop=ALL", "--read-only", "--pull=never",
                         "--security-opt=no-new-privileges:true", "--log-driver=none"):
                self.assertIn(flag, run)
            self.assertNotIn("--gpus", run)
            self.assertNotIn("--privileged", run)
            self.assertTrue(run[run.index("--mount")+1].endswith("dst=/input,readonly"))
            self.assertIn("size=67108864", run[run.index("--tmpfs")+1])
            self.assertNotEqual(run[run.index("--user")+1].split(":")[0], "0")
            generated = next(call for call in fake.calls if call[-1] == "echo hi; touch /root/escape")
            self.assertEqual(generated[1:2], ["exec"])
            self.assertEqual(generated[-3:-1], ["/bin/sh", "-c"])
            self.assertEqual((path / "answer.txt").read_text(), "42")
            self.assertFalse((path / "original").exists())
            self.assertEqual(fake.calls[-1][1:3], ["rm", "--force"])

    def test_timeout_removes_container_without_publishing(self):
        fake = FakeDocker(ExecutionResult(124, "", "timeout", True))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path / "keep").write_text("preserve")
            result = DockerSandbox(IMAGE, runner=fake).execute(path, "sleep 300", 0.1)
            self.assertTrue(result.timed_out)
            self.assertTrue((path / "keep").exists())
            self.assertFalse(any(call[1] == "cp" for call in fake.calls))
            self.assertEqual(fake.calls[-1][1], "rm")

    def test_failed_cleanup_cannot_return_success_and_identifies_exact_container(self):
        fake = FakeDocker(ExecutionResult(124, timed_out=True))
        def runner(argv, **kwargs):
            if argv[1] == "rm":
                fake.calls.append(argv)
                return ExecutionResult(1, stderr="daemon could not remove container")
            if argv[1:3] == ["container", "inspect"]:
                fake.calls.append(argv)
                return ExecutionResult(0, "still-present-container-id")
            return fake(argv, **kwargs)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path / "keep").write_text("original")
            sandbox = DockerSandbox(IMAGE, runner=runner)
            with self.assertRaises(SandboxCleanupError) as caught:
                sandbox.execute(path, "sleep 300")
            name = fake.calls[0][fake.calls[0].index("--name") + 1]
            self.assertEqual(caught.exception.container_name, name)
            self.assertEqual(caught.exception.session_id, sandbox.session_id)
            self.assertIn("still exists", str(caught.exception))
            self.assertEqual(fake.calls[-2][-1], name)
            self.assertEqual(fake.calls[-1][-1], name)
            self.assertEqual((path / "keep").read_text(), "original")
            self.assertFalse(any(call[1] == "cp" for call in fake.calls))

    def test_failed_rm_can_be_confirmed_absent_after_known_start(self):
        fake = FakeDocker(ExecutionResult(124, timed_out=True))
        def runner(argv, **kwargs):
            if argv[1] == "rm":
                fake.calls.append(argv)
                return ExecutionResult(1, stderr="container already removed")
            if argv[1:3] == ["container", "inspect"]:
                fake.calls.append(argv)
                return ExecutionResult(1, stderr="Error: No such object: " + argv[-1])
            return fake(argv, **kwargs)
        with tempfile.TemporaryDirectory() as tmp:
            result = DockerSandbox(IMAGE, runner=runner).execute(Path(tmp), "sleep 300")
        self.assertTrue(result.timed_out)
        self.assertEqual(fake.calls[-1][1:3], ["container", "inspect"])

    def test_ambiguous_startup_timeout_stops_even_if_name_currently_absent(self):
        calls = []
        def runner(argv, **kwargs):
            calls.append(argv)
            if argv[1] == "run":
                return ExecutionResult(124, stderr="startup timed out", timed_out=True)
            return ExecutionResult(1, stderr="Error: No such container: " + argv[-1])
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SandboxCleanupError) as caught:
                DockerSandbox(IMAGE, runner=runner).execute(Path(tmp), "true")
        self.assertIn("delayed creation", str(caught.exception))
        self.assertEqual([call[1] for call in calls], ["run", "rm", "container"])

    def test_cleanup_queries_share_one_ten_second_allowance(self):
        fake = FakeDocker(ExecutionResult(124, timed_out=True))
        cleanup_timeouts = []
        elapsed = [0.0]
        def runner(argv, **kwargs):
            if argv[1] == "rm":
                fake.calls.append(argv)
                cleanup_timeouts.append(kwargs["timeout_s"])
                elapsed[0] += 8.0  # Simulate rm consuming its whole timeout.
                return ExecutionResult(1, stderr="removal failed")
            if argv[1:3] == ["container", "inspect"]:
                fake.calls.append(argv)
                cleanup_timeouts.append(kwargs["timeout_s"])
                return ExecutionResult(1, stderr="Error: No such object: " + argv[-1])
            return fake(argv, **kwargs)
        with tempfile.TemporaryDirectory() as tmp:
            with patch("playground.sandbox.time.monotonic", side_effect=lambda: elapsed[0]):
                DockerSandbox(IMAGE, runner=runner).execute(Path(tmp), "sleep 300")
        self.assertEqual(cleanup_timeouts[0], 8)
        self.assertEqual(cleanup_timeouts[1], 2)

    def test_rejects_oversize_output_before_replacing_original(self):
        fake = FakeDocker(malicious=True)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "work"
            path.mkdir()
            (path / "keep").write_text("preserve")
            with self.assertRaises(SandboxError):
                DockerSandbox(IMAGE, workspace_limit_bytes=1024, runner=fake).execute(path, "true")
            self.assertEqual((path / "keep").read_text(), "preserve")
            self.assertEqual(fake.calls[-1][1], "rm")

    def test_long_artifact_path_rejected_before_snapshot_publication(self):
        fake = FakeDocker()
        def runner(argv, **kwargs):
            if argv[1] == "cp":
                fake.calls.append(argv)
                with tarfile.open(kwargs["stdout_file"], "w") as archive:
                    name = "/".join(["a"*180, "b"*180, "c"*180, "bad.py"])
                    entry = tarfile.TarInfo(name)
                    entry.size = 4
                    archive.addfile(entry, io.BytesIO(b"nope"))
                return ExecutionResult(0)
            return fake(argv, **kwargs)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/"work"
            path.mkdir()
            (path/"original.py").write_bytes(b"preserve")
            with self.assertRaisesRegex(SandboxError, "path.*quota"):
                DockerSandbox(IMAGE, runner=runner).execute(path, "true")
            self.assertEqual((path/"original.py").read_bytes(), b"preserve")
            self.assertEqual(list(path.iterdir()), [path/"original.py"])
            self.assertEqual(fake.calls[-1][1], "rm")
            self.assertEqual(list(Path(tmp).glob(".playground-export-*")), [])

    def test_rejects_unpinned_images_and_invalid_resources(self):
        for image in ("python:latest", "", "python@sha256:bad", "--privileged"):
            with self.assertRaises(SandboxError):
                DockerSandbox(image)
        with self.assertRaises(SandboxError):
            DockerSandbox(IMAGE, memory="--privileged")

    def test_daemon_resource_warning_aborts_before_generated_code(self):
        calls = []
        def runner(argv, **kwargs):
            calls.append(argv)
            return ExecutionResult(0, stderr="WARNING: Your kernel does not support swap limit capabilities")
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SandboxError):
                DockerSandbox(IMAGE, runner=runner).execute(Path(tmp), "touch unbounded")
        self.assertFalse(any(call[1] == "exec" for call in calls))
        self.assertEqual(calls[-1][1], "rm")

    def test_workspace_quotas(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path / "file").write_bytes(b"xx")
            with self.assertRaises(SandboxError):
                validate_workspace(path, 1)
            with self.assertRaises(SandboxError):
                validate_workspace(path, 10, max_files=0)
            if hasattr(os, "link"):
                os.link(path / "file", path / "hardlink")
                with self.assertRaises(SandboxError):
                    validate_workspace(path, 10)

    def test_host_runner_bounds_output_and_time(self):
        result = bounded_run([sys.executable, "-c", "print('x'*100000)"], timeout_s=3, output_limit=1024)
        self.assertEqual(result.exit_code, 125)
        self.assertLessEqual(len(result.stdout.encode()), 1024)
        result = bounded_run([sys.executable, "-c", "import time;time.sleep(30)"], timeout_s=0.1, output_limit=1024)
        self.assertTrue(result.timed_out)

    def test_artifact_traversal_links_and_duplicates_rejected(self):
        cases = [("../escape", tarfile.REGTYPE), ("/escape", tarfile.REGTYPE),
                 ("safe", tarfile.SYMTYPE), ("safe", tarfile.LNKTYPE),
                 ("C:/escape", tarfile.REGTYPE), ("safe", tarfile.CHRTYPE)]
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "out.tar"
            for name, kind in cases:
                target = Path(tmp) / "result"
                target.mkdir(exist_ok=True)
                with tarfile.open(archive, "w") as output:
                    member = tarfile.TarInfo(name)
                    member.type = kind
                    member.linkname = "../../escape"
                    output.addfile(member)
                with self.subTest(name=name, kind=kind), self.assertRaises(SandboxError):
                    extract_workspace(archive, target, 1024)
            with tarfile.open(archive, "w") as output:
                for _ in range(2): output.addfile(tarfile.TarInfo("same"), io.BytesIO(b""))
            with self.assertRaises(SandboxError):
                extract_workspace(archive, target, 1024)


if __name__ == "__main__":
    unittest.main()
