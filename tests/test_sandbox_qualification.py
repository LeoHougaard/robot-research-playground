"""Checker contract tests with fake Docker replies; NOT real sandbox qualification.

No daemon is invoked and no container enforcement claim follows from these tests.
Evidence report fixtures live only in temporary directories and are deleted.
"""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from playground.sandbox import DockerSandbox, ExecutionResult, SandboxError, SandboxCleanupError

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("sandbox_qualification_checker", ROOT / "scripts" / "qualify_sandbox.py")
checker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checker)
IMAGE = "python@sha256:" + "a" * 64
IMAGE_ID = "sha256:" + "b" * 64
ENDPOINT = "unix:///tmp/fake-qualification-docker.sock"
VALIDATOR_ERROR = "artifact link or special file rejected"


class FakeDockerCLI:
    """In-memory CLI protocol fixture, never an actual Docker qualification."""

    def __init__(self, *, artifact=tarfile.SYMTYPE, failure=None, error="fixture operation failed"):
        self.calls = []
        self.alive = {}
        self.artifact = artifact
        self.failure = failure
        self.error = error

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        if argv[:3] != ["docker", "--host", ENDPOINT]:
            raise AssertionError("Docker command was not pinned to the verified fixture endpoint: " + repr(argv))
        args = argv[3:]
        operation = args[0]
        if operation == "info":
            return ExecutionResult(0, json.dumps({"ID": "fixture-daemon", "OSType": "linux", "DockerRootDir": "/fixture/docker"}))
        if args[:2] == ["image", "inspect"]:
            return ExecutionResult(0, json.dumps([{"Id": IMAGE_ID, "Architecture": "amd64", "Os": "linux"}]))
        if operation == "run":
            if self.failure == "run":
                return ExecutionResult(1, stderr=self.error)
            name = args[args.index("--name") + 1]
            self.alive[name] = args
            return ExecutionResult(0, name)
        if args[:2] == ["container", "inspect"]:
            name = args[-1]
            if name not in self.alive:
                return ExecutionResult(1, stderr="Error: No such container: " + name)
            run = self.alive[name]
            source = run[run.index("--mount") + 1].split("src=", 1)[1].split(",", 1)[0]
            item = {"Image": IMAGE_ID, "Config": {"User": "1000:1000"},
                    "Mounts": [{"Type": "bind", "Source": source, "Destination": "/input", "RW": False}],
                    "HostConfig": {"NetworkMode": "none", "Privileged": False, "ReadonlyRootfs": True,
                                   "CapDrop": ["ALL"], "SecurityOpt": ["no-new-privileges:true"],
                                   "NanoCpus": 1000000000, "Memory": 64 * 1024**2, "MemorySwap": 64 * 1024**2,
                                   "PidsLimit": 16, "LogConfig": {"Type": "none"},
                                   "Tmpfs": {"/workspace": "size=1048576", "/tmp": "size=16777216"}}}
            return ExecutionResult(0, json.dumps([item]))
        if operation == "exec":
            stage = "setup" if args[-1] == "cp -R /input/. /workspace/" else "command"
            if self.failure == stage:
                return ExecutionResult(1, stderr=self.error)
            return ExecutionResult(0)
        if operation == "pause":
            return ExecutionResult(1, stderr=self.error) if self.failure == "pause" else ExecutionResult(0)
        if operation == "cp":
            if self.failure == "copy":
                return ExecutionResult(1, stderr=self.error)
            with tarfile.open(kwargs["stdout_file"], "w") as archive:
                for name, data in (("seed.txt", b"tampered"), ("nested/original.txt", b"nested original\x00"),
                                   ("unexpected.txt", b"must not publish")):
                    entry = tarfile.TarInfo(name)
                    entry.size = len(data)
                    archive.addfile(entry, io.BytesIO(data))
                if self.artifact is not None:
                    entry = tarfile.TarInfo("unsafe")
                    entry.type = self.artifact
                    entry.linkname = "seed.txt" if self.artifact == tarfile.LNKTYPE else "/input/seed.txt"
                    archive.addfile(entry)
            return ExecutionResult(0, timed_out=self.failure == "copy_timeout")
        if operation == "rm":
            if self.failure == "cleanup":
                return ExecutionResult(1, stderr="fixture removal failed")
            self.alive.pop(args[-1], None)
            return ExecutionResult(0)
        raise AssertionError("Unexpected fixture operation: " + repr(args))


class SandboxQualificationTests(unittest.TestCase):
    """All runner behavior here is mocked, including apparent successes."""

    def setUp(self):
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_context_override_is_rejected_before_any_docker_call(self):
        for environment in ({"DOCKER_CONTEXT": "remote", "DOCKER_HOST": ENDPOINT},
                            {"DOCKER_CONTEXT": "local"}):
            with self.subTest(environment=environment), patch.dict(os.environ, environment, clear=True), \
                    patch.object(checker, "bounded_run") as run:
                with self.assertRaisesRegex(SandboxError, "DOCKER_CONTEXT"):
                    checker.qualify(IMAGE, ROOT, {})
                run.assert_not_called()

    def test_nonlocal_or_invalid_endpoints_are_rejected_before_daemon_access(self):
        for endpoint in ("tcp://remote:2375", "ssh://remote", "unix://relative", "unix:///", None, 7):
            with self.subTest(endpoint=endpoint), patch.object(checker, "bounded_run", return_value=ExecutionResult(0, json.dumps(endpoint))) as run:
                with self.assertRaisesRegex(SandboxError, "local Linux Unix-socket"):
                    checker.local_docker_endpoint()
                self.assertEqual(run.call_count, 1)
                self.assertEqual(run.call_args.args[0][:3], ["docker", "context", "inspect"])

    def test_local_context_is_resolved_once_and_explicit_host_is_not_re_resolved(self):
        with patch.object(checker, "bounded_run", return_value=ExecutionResult(0, json.dumps(ENDPOINT))) as run:
            self.assertEqual(checker.local_docker_endpoint(), ENDPOINT)
            run.assert_called_once()
        with patch.dict(os.environ, {"DOCKER_HOST": ENDPOINT}), patch.object(checker, "bounded_run") as run:
            self.assertEqual(checker.local_docker_endpoint(), ENDPOINT)
            run.assert_not_called()

    def test_context_resolution_failure_and_timeout_are_not_local_admission(self):
        for reply in (ExecutionResult(1, stderr="fixture unavailable"), ExecutionResult(0, json.dumps(ENDPOINT), timed_out=True)):
            with self.subTest(reply=reply), patch.object(checker, "bounded_run", return_value=reply):
                with self.assertRaisesRegex(SandboxError, "Cannot resolve Docker context"):
                    checker.local_docker_endpoint()

    def test_all_admission_calls_pin_the_verified_local_daemon(self):
        fake = FakeDockerCLI()
        report = {"tests": [], "inspected_containers": []}
        with patch.dict(os.environ, {"DOCKER_HOST": ENDPOINT}), patch.object(checker, "bounded_run", side_effect=fake), \
                patch.object(checker.shutil, "disk_usage", return_value=SimpleNamespace(free=21 * 1024**3)), \
                patch.object(checker.platform, "machine", return_value="x86_64"), \
                patch.object(checker, "check_case") as check:
            checker.qualify(IMAGE, ROOT, report)
        self.assertEqual(report["docker_endpoint"], ENDPOINT)
        self.assertEqual(report["image_id"], IMAGE_ID)
        self.assertEqual([call[3] for call in fake.calls], ["info", "image"])
        self.assertEqual(check.call_count, 11)
        # The mock bypassed every test; this dictionary is not qualification evidence.
        self.assertEqual(report["tests"], [])

    def test_host_environment_changes_cannot_redirect_pinned_calls(self):
        with patch.dict(os.environ, {"DOCKER_HOST": "tcp://remote:2375"}), patch.object(checker, "bounded_run", return_value=ExecutionResult(0)) as run:
            checker.docker_run(ENDPOINT, ["info"], timeout_s=1, output_limit=1)
            self.assertEqual(run.call_args.args[0], ["docker", "--host", ENDPOINT, "info"])
        with patch.dict(os.environ, {"DOCKER_CONTEXT": "remote"}), patch.object(checker, "bounded_run") as run:
            with self.assertRaisesRegex(SandboxError, "DOCKER_CONTEXT changed"):
                checker.docker_run(ENDPOINT, ["info"], timeout_s=1, output_limit=1)
            run.assert_not_called()

    def run_artifact_case(self, fake):
        report = {"image_id": IMAGE_ID, "tests": [], "inspected_containers": []}
        runner = checker.RealDocker(report, ENDPOINT)
        sandbox = DockerSandbox(IMAGE, runner=runner, workspace_limit_bytes=1024**2,
                                stdout_limit_bytes=4096, memory="64m", pids=16, min_free_disk_bytes=0)
        with tempfile.TemporaryDirectory() as tmp, patch.object(checker, "bounded_run", side_effect=fake), \
                patch("playground.sandbox.os.getuid", return_value=1000), contextlib.redirect_stdout(io.StringIO()):
            checker.check_case(sandbox, runner, Path(tmp), report, "fixture_invalid_artifact", "fixture only; never executed", "invalid_artifact")
        return report

    def test_each_malicious_artifact_requires_validator_rejection_after_successful_export(self):
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE):
            with self.subTest(kind=kind):
                fake = FakeDockerCLI(artifact=kind)
                report = self.run_artifact_case(fake)
                self.assertEqual(report["tests"][0]["detail"]["expected_rejection"], VALIDATOR_ERROR)
                self.assertEqual([op["operation"] for op in report["tests"][0]["detail"]["operations"]],
                                 ["run", "exec", "exec", "pause", "cp", "rm"])
                self.assertFalse(fake.alive)
                self.assertTrue(all(call[:3] == ["docker", "--host", ENDPOINT] for call in fake.calls))

    def test_setup_pause_copy_and_command_errors_cannot_pass_artifact_test(self):
        for failure in ("run", "setup", "pause", "copy", "command", "copy_timeout"):
            for error in ("fixture operation failed", VALIDATOR_ERROR):
                with self.subTest(failure=failure, error=error):
                    # Ambiguous Docker startup now aborts at the stronger
                    # cleanup boundary; later-stage failures fail the checker.
                    rejection = SandboxCleanupError if failure == "run" else AssertionError
                    with self.assertRaises(rejection):
                        self.run_artifact_case(FakeDockerCLI(failure=failure, error=error))

    def test_accepted_artifacts_and_cleanup_failures_do_not_pass(self):
        with self.assertRaises(AssertionError):
            self.run_artifact_case(FakeDockerCLI(artifact=None))
        with self.assertRaises(SandboxCleanupError):
            self.run_artifact_case(FakeDockerCLI(failure="cleanup"))

    def test_extra_copyback_modified_nested_original_and_changed_seed_are_detected(self):
        for mutation in ("extra", "nested", "seed", "delete"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as tmp:
                runner = SimpleNamespace(names=[], operations=[])
                def execute(workspace, command, **kwargs):
                    if mutation == "extra":
                        (workspace / "unexpected.txt").write_text("unwanted")
                    elif mutation == "nested":
                        (workspace / "nested" / "original.txt").write_text("tampered")
                    elif mutation == "seed":
                        (workspace / "seed.txt").write_text("tampered")
                    else:
                        (workspace / "nested" / "original.txt").unlink()
                    raise SandboxError(VALIDATOR_ERROR)
                sandbox = SimpleNamespace(execute=execute)
                with self.assertRaisesRegex(AssertionError, "original workspace or published unexpected"):
                    checker.check_case(sandbox, runner, Path(tmp), {"tests": []}, "fixture", "unused", "invalid_artifact")

    def test_container_image_must_match_the_admitted_image(self):
        fake = FakeDockerCLI()
        runner = checker.RealDocker({"image_id": "wrong", "inspected_containers": []}, ENDPOINT)
        with patch.object(checker, "bounded_run", side_effect=fake):
            with self.assertRaisesRegex(AssertionError, "Container image differs"):
                runner(["docker", "run", "--name", "fixture", "--mount", "type=bind,src=/fixture,dst=/input,readonly"], timeout_s=1, output_limit=1024)

    def test_absence_confirmation_must_match_exact_container_name(self):
        runner = checker.RealDocker({}, ENDPOINT)
        with patch.object(checker, "bounded_run", return_value=ExecutionResult(1, stderr="No such container: fixture-other")):
            with self.assertRaisesRegex(AssertionError, "removal is unconfirmed"):
                runner.assert_removed("fixture")
        with patch.object(checker, "bounded_run", return_value=ExecutionResult(1, stderr="No such container: fixture\n")):
            runner.assert_removed("fixture")

    def invoke_main(self, path, work_root):
        argv = ["qualify_sandbox.py", "--image", IMAGE, "--work-root", str(work_root), "--report", str(path)]
        with patch.object(checker.sys, "argv", argv), patch.object(checker.platform, "system", return_value="Linux"), \
                patch.object(checker.os, "getuid", return_value=1000), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return checker.main()

    def test_existing_evidence_and_symlink_are_refused_without_running_qualification(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            evidence = root / "existing.json"
            evidence.write_bytes(b"original evidence\n")
            link = root / "link.json"
            link.symlink_to(evidence)
            for path in (evidence, link):
                with self.subTest(path=path), patch.object(checker, "qualify") as qualify:
                    self.assertEqual(self.invoke_main(path, root), 1)
                    qualify.assert_not_called()
                    self.assertEqual(evidence.read_bytes(), b"original evidence\n")
            self.assertTrue(link.is_symlink())

    def test_failure_report_is_preserved_and_never_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            evidence = root / "failed.json"
            with patch.object(checker, "qualify", side_effect=SandboxError("fixture preflight failure")) as qualify:
                self.assertEqual(self.invoke_main(evidence, root), 1)
                before = evidence.read_bytes()
                self.assertEqual(self.invoke_main(evidence, root), 1)
                qualify.assert_called_once()
            report = json.loads(before)
            self.assertFalse(report["passed"])
            self.assertFalse(report["physical_hardware_verified"])
            self.assertEqual(report["error"], "fixture preflight failure")
            self.assertEqual(evidence.read_bytes(), before)
            self.assertIn("checker_source_sha256", report)
            self.assertIn("completed_time_utc", report)

    def test_source_changes_invalidate_an_otherwise_successful_fixture(self):
        original = {"src/playground/sandbox.py": "a" * 64, "scripts/qualify_sandbox.py": "b" * 64}
        changed = dict(original, **{"src/playground/sandbox.py": "c" * 64})
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            evidence = root / "fixture.json"
            with patch.object(checker, "qualify"), patch.object(checker, "source_hashes", side_effect=[original, changed]):
                self.assertEqual(self.invoke_main(evidence, root), 1)
            report = json.loads(evidence.read_text())
            self.assertFalse(report["passed"])
            self.assertFalse(report["source_unchanged"])
            self.assertEqual(report["source_sha256"], original)
            self.assertEqual(report["completed_source_sha256"], changed)

    def test_stable_source_provenance_in_ephemeral_success_fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            evidence = root / "fixture.json"
            with patch.object(checker, "qualify"):
                self.assertEqual(self.invoke_main(evidence, root), 0)
            report = json.loads(evidence.read_text())
            self.assertTrue(report["source_unchanged"])
            self.assertEqual(report["source_sha256"], report["completed_source_sha256"])
            self.assertEqual(len(report["run_id"]), 32)
            self.assertFalse(report["physical_hardware_verified"])
            # This mocked report is deleted now. It must never be used as evidence.


if __name__ == "__main__":
    unittest.main()
