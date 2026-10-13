"""Qualification harness regression tests. These are fixtures, never real evidence."""
import contextlib
import hashlib
import importlib.util
import json
import os
import select
import socket
import subprocess
import sys
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from playground.llm import LocalModel
from playground.models import MotionModel
from playground.research import simulation_controller
from playground.sandbox import ExecutionResult
from test_agent_integration import local_model_server

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("qualify_local_model", ROOT / "scripts/qualify_local_model.py")
qualifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qualifier)


class ReviewedSandboxFixture:
    """Known fixture data, not execution of any generated source on the host."""
    def __init__(self):
        self.calls = []

    def execute(self, workspace, command, timeout_s):
        self.calls.append((command, timeout_s))
        if command == "write reviewed fixture":
            (workspace / "policy.py").write_text("# Reviewed test fixture; never executed\n")
            return ExecutionResult(0)
        if command.startswith("python /workspace/"):
            observation = json.loads((workspace / "observation.json").read_text())
            action = {"left": .25, "right": .25, "duration_s": .2}
            prediction = MotionModel(.075, .075 / .154).predict(observation, action)
            return ExecutionResult(0, json.dumps({"action": action, "prediction": prediction}))
        raise AssertionError("Unexpected fixture command")


@contextlib.contextmanager
def supervisor_fixture(directory):
    controller, clock = simulation_controller()
    sandbox = ReviewedSandboxFixture()
    supervisor = qualifier.QualificationSupervisor(directory, controller, clock=clock,
                         config={"disk_reserve_bytes": 1}, sandbox=sandbox)
    supervisor.start_robot(operator_confirmed=True)
    try:
        yield supervisor, sandbox
    finally:
        supervisor.close()


def response_provider(supervisor, *, fail_recovery=False):
    def response(request):
        message = request["messages"][-1]["content"]
        if message.startswith("Observe"):
            return {"tool": "observe", "arguments": {}}
        if message.startswith("Test rejection") or message.startswith("Recover"):
            left = 2 if message.startswith("Test rejection") or fail_recovery else .25
            action = {"left": left, "right": .25, "duration_s": .2}
            prediction = MotionModel(.075, .075 / .154).predict(supervisor.controller.observe(), action)
            return {"tool": "experiment", "arguments": {"action": action, "prediction": prediction}}
        if message.startswith("Use run_code"):
            return {"tool": "run_code", "arguments": {"command": "write reviewed fixture", "timeout_s": 20}}
        if message.startswith("Finish"):
            return {"tool": "finish", "arguments": {"summary": "Simulation fixture only"}}
        if message.startswith("You are a fresh successor"):
            return {"tool": "artifact_list", "arguments": {}}
        if message.startswith("Import"):
            record = json.loads(request["messages"][-2]["content"])
            return {"tool": "artifact_import", "arguments": {"id": record["artifacts"][0]["id"]}}
        if message.startswith("Use run_policy"):
            record = json.loads(request["messages"][-2]["content"])
            return {"tool": "run_policy", "arguments": {"filename": record["filename"]}}
        raise AssertionError(message)
    return response


LISTENER_SOURCE = """
import json, os, socket, sys
address, port = sys.argv[1], int(sys.argv[2])
family = socket.AF_INET6 if ':' in address else socket.AF_INET
with socket.socket(family) as listener:
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if family == socket.AF_INET6:
        listener.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
    listener.bind((address, port))
    listener.listen()
    print(json.dumps({'pid': os.getpid(), 'port': listener.getsockname()[1]}), flush=True)
    sys.stdin.readline()
"""

COORDINATOR_SOURCE = r"""
import json, subprocess, sys
source, address, port = sys.argv[1], sys.argv[2], sys.argv[3]
child = None
try:
    while True:
        child = subprocess.Popen([sys.executable, '-c', source, address, port],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        line = child.stdout.readline()
        info = json.loads(line)
        port = str(info['port'])
        print(line.strip(), flush=True)
        command = sys.stdin.readline().strip()
        child.stdin.write('exit\n'); child.stdin.flush()
        child.wait(timeout=3)
        child.stdin.close(); child.stdout.close()
        child = None
        if command != 'restart':
            break
finally:
    if child is not None:
        child.terminate(); child.wait(timeout=3)
"""


SLEEP_LISTENER_SOURCE = """
import json, socket, subprocess, sys
with socket.socket() as listener:
    listener.bind((sys.argv[1], int(sys.argv[2])))
    listener.listen()
    port = listener.getsockname()[1]
    child = subprocess.Popen(['/bin/sleep', '60'], pass_fds=[listener.fileno()])
try:
    print(json.dumps({'pid': child.pid, 'port': port}), flush=True)
    sys.stdin.readline()
finally:
    child.terminate(); child.wait(timeout=3)
"""


def child_reply(process):
    if not select.select([process.stdout], [], [], 3)[0]:
        raise AssertionError('Socket fixture startup timed out')
    line = process.stdout.readline()
    if not line:
        raise AssertionError('Socket fixture exited before binding')
    return json.loads(line)


@contextlib.contextmanager
def socket_process(address, port=0, *, coordinator=False, sleeping_child=False):
    command = [sys.executable, '-c', COORDINATOR_SOURCE, LISTENER_SOURCE, address, str(port)] if coordinator else [
        sys.executable, '-c', LISTENER_SOURCE, address, str(port)]
    if sleeping_child:
        command = [sys.executable, '-c', SLEEP_LISTENER_SOURCE, address, str(port)]
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        yield process, child_reply(process)
    finally:
        if process.poll() is None:
            try:
                process.stdin.write('exit\n'); process.stdin.flush()
                process.wait(timeout=3)
            except (BrokenPipeError, subprocess.TimeoutExpired):
                process.terminate(); process.wait(timeout=3)
        process.stdin.close(); process.stdout.close()


def manifest_for_fixture(directory, process, url):
    model = Path(directory) / 'weights.bin'
    model.write_bytes(b'reviewed inert test data, not a real model')
    runtime = Path('/proc') / str(process.pid) / 'exe'
    identity = qualifier.process_identity(process.pid)
    value = {'kind': 'operator_attested_real_local_model', 'model_id': 'test-model',
        'model_revision': 'inert fixture', 'runtime_version': 'inert fixture', 'reviewed_by': 'unit test',
        'reviewed_at': qualifier.utc_now(), 'acceleration': 'cpu', 'launch_config': {'test_fixture': True},
        'pid': process.pid, 'process_start_ticks': identity['start_ticks'],
        'command_sha256': qualifier.sha256(Path('/proc') / str(process.pid) / 'cmdline'),
        'model_files': [{'path': str(model.resolve()), 'sha256': qualifier.sha256(model)}],
        'runtime_files': [{'path': str(runtime.resolve()), 'sha256': qualifier.sha256(runtime)}]}
    manifest = Path(directory) / 'manifest.json'
    manifest.write_text(json.dumps(value))
    return manifest, qualifier.validate_provenance(manifest, 'test-model', url)


class LocalModelQualificationTests(unittest.TestCase):
    def test_fixture_behavior_pass_never_becomes_real_model_qualification(self):
        with tempfile.TemporaryDirectory() as directory, supervisor_fixture(directory) as (supervisor, sandbox):
            report = {"passed": False, "behavior_passed": False}
            with local_model_server(response_provider(supervisor)) as (url, requests):
                qualifier.exercise(LocalModel(url, model="test-model"), supervisor, report,
                                   max_seconds=10, max_decision_s=2, max_error_m=.03)
            qualifier.set_outcome(report)
            self.assertTrue(report["behavior_passed"])
            self.assertFalse(report["passed"])
            self.assertFalse(report["real_model_qualified"])
            self.assertFalse(report["physical_hardware_verified"])
            self.assertEqual(report["latency_summary"]["decisions"], 9)
            self.assertEqual(len(report["generation_ids"]), 2)
            self.assertEqual(supervisor.archive.summary()["experiments"], 2)
            self.assertEqual(len(sandbox.calls), 2)
            successor = requests[5]
            self.assertEqual(len(successor["messages"]), 3)
            self.assertNotIn(report["artifact"]["id"], successor["messages"][1]["content"])
            self.assertEqual(report["artifact"]["sha256"], qualifier.sha256(Path(directory) / report["artifact"]["path"]))

    def test_rejected_action_without_recovery_fails_and_preserves_failed_run(self):
        with tempfile.TemporaryDirectory() as directory, supervisor_fixture(directory) as (supervisor, _):
            report = {"behavior_passed": False}
            with local_model_server(response_provider(supervisor, fail_recovery=True)) as (url, _):
                with self.assertRaisesRegex(qualifier.QualificationError, "completed simulation"):
                    qualifier.exercise(LocalModel(url, model="test-model"), supervisor, report,
                                       max_seconds=10, max_decision_s=2, max_error_m=.03)
            self.assertFalse(report["behavior_passed"])
            self.assertEqual(supervisor.archive.summary()["experiments"], 0)
            self.assertEqual(supervisor.archive.db.execute("SELECT status FROM runs").fetchone()[0], "failed")

    def test_wrong_tool_never_runs_generated_code(self):
        with tempfile.TemporaryDirectory() as directory, supervisor_fixture(directory) as (supervisor, sandbox):
            with local_model_server(lambda _: {"tool": "run_code", "arguments": {"command": "unsafe"}}) as (url, _):
                with self.assertRaisesRegex(qualifier.QualificationError, "expected observe"):
                    qualifier.exercise(LocalModel(url, model="test-model"), supervisor, {},
                                       max_seconds=10, max_decision_s=2, max_error_m=.03)
            self.assertEqual(sandbox.calls, [])

    def test_report_requires_all_independent_gates(self):
        for missing in ("real_provenance", "resources_checked", "sandbox_checked"):
            report = {"behavior_passed": True}
            gates = dict(real_provenance=True, resources_checked=True, sandbox_checked=True)
            gates[missing] = False
            qualifier.set_outcome(report, **gates)
            self.assertFalse(report["passed"])
        report = {"behavior_passed": True, "error": "failed cleanup"}
        qualifier.set_outcome(report, real_provenance=True, resources_checked=True, sandbox_checked=True)
        self.assertFalse(report["passed"])

    def test_real_provenance_rejects_fixture_declaration_before_process_reads(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "manifest.json"
            manifest.write_text(json.dumps({"kind": "fixture", "model_id": "test-model"}))
            with patch.object(qualifier, "process_identity") as proc:
                with self.assertRaisesRegex(qualifier.QualificationError, "attestation"):
                    qualifier.validate_provenance(manifest, "test-model", "http://127.0.0.1:1/v1")
                proc.assert_not_called()

    def test_provenance_requires_actual_files_and_matching_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "manifest.json"
            manifest.write_text(json.dumps({"kind": "operator_attested_real_local_model", "model_id": "test-model",
                "model_revision": "fixture", "runtime_version": "fixture", "reviewed_by": "test",
                "reviewed_at": qualifier.utc_now(), "acceleration": "cpu", "launch_config": {"fixture": True},
                "pid": 123, "process_start_ticks": 10, "command_sha256": "a" * 64,
                "model_files": [{"path": str(Path(directory) / "absent-model"), "sha256": "a" * 64}],
                "runtime_files": []}))
            with patch.object(qualifier, "process_identity", return_value={"pid": 123, "ppid": 1, "start_ticks": 10}), \
                 patch.object(qualifier, "sha256", return_value="a" * 64):
                with self.assertRaisesRegex(qualifier.QualificationError, "existing absolute"):
                    qualifier.validate_provenance(manifest, "test-model", "http://127.0.0.1:1/v1")

    def test_sandbox_report_cannot_pass_with_only_passed_boolean(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sandbox.json"
            path.write_text(json.dumps({"schema_version": 1, "passed": True, "physical_hardware_verified": False,
                                       "image": "fixture"}))
            with self.assertRaisesRegex(qualifier.QualificationError, "source changed"):
                qualifier.validate_sandbox_report(path, "fixture")

    def test_unknown_gpu_memory_is_rejected_not_zero(self):
        identity = {"pid": 123, "ppid": 1, "start_ticks": 10}
        monitor = qualifier.ResourceMonitor({"pid": 123, "process_identity": identity, "acceleration": "cuda"},
                                             max_rss_bytes=10**12, min_available_bytes=1, max_device_bytes=10**12)
        original_read_text = Path.read_text
        def read_text(path, *args, **kwargs):
            if str(path) == "/proc/123/status":
                return "VmRSS:\t100 kB\n"
            if str(path) == "/proc/meminfo":
                return "MemAvailable: 1000000 kB\n"
            return original_read_text(path, *args, **kwargs)
        with patch.object(qualifier, "process_identity", return_value=identity), \
             patch.object(qualifier, "process_tree", return_value=[identity]), \
             patch.object(qualifier, "verify_runtime_identity"), \
             patch.object(Path, "read_text", read_text), \
             patch.object(qualifier, "bounded_run", return_value=ExecutionResult(0, "GPU-fixture, [N/A], [N/A]\n")):
            with self.assertRaisesRegex(qualifier.QualificationError, "unknown"):
                monitor.sample()
        self.assertIsNone(monitor.summary["peak_device_used_bytes"])
        self.assertEqual(monitor.summary["device_sample_count"], 0)

    def test_process_identity_change_fails_closed(self):
        monitor = qualifier.ResourceMonitor({"pid": 123, "process_identity": {"pid": 123, "ppid": 1, "start_ticks": 10}, "acceleration": "cpu"},
                                             max_rss_bytes=10**12, min_available_bytes=1)
        with patch.object(qualifier, "process_identity", return_value={"pid": 123, "ppid": 1, "start_ticks": 11}):
            with self.assertRaisesRegex(qualifier.QualificationError, "identity changed"):
                monitor.sample()
        self.assertIsNone(monitor.summary["peak_process_tree_rss_bytes"])

    def test_stale_future_and_naive_timestamps_fail(self):
        for value in ("2000-01-01T00:00:00+00:00", "2999-01-01T00:00:00+00:00", "2026-10-13T00:00:00"):
            with self.subTest(value=value), self.assertRaises(qualifier.QualificationError):
                qualifier.current_timestamp(value)

    def test_exact_listener_address_does_not_accept_same_port_other_loopback(self):
        with socket.socket() as actual:
            actual.bind(('127.0.0.1', 0)); actual.listen()
            port = actual.getsockname()[1]
            with socket_process('127.0.0.2', port) as (other, _):
                with self.assertRaisesRegex(qualifier.QualificationError, 'uniquely own'):
                    qualifier.verify_listener(other.pid, f'http://127.0.0.1:{port}/v1')
                match = qualifier.verify_listener(other.pid, f'http://127.0.0.2:{port}/v1')
                self.assertEqual(match['pid'], other.pid)
                self.assertEqual(match['address'], '127.0.0.2')
                self.assertEqual(match['address_family'], 4)

    @unittest.skipUnless(socket.has_ipv6, 'IPv6 sockets are unavailable')
    def test_exact_listener_address_family_is_pinned(self):
        with socket.socket() as actual:
            actual.bind(('127.0.0.1', 0)); actual.listen()
            port = actual.getsockname()[1]
            with socket_process('::1', port) as (other, _):
                with self.assertRaises(qualifier.QualificationError):
                    qualifier.verify_listener(other.pid, f'http://127.0.0.1:{port}/v1')
                match = qualifier.verify_listener(other.pid, f'http://[::1]:{port}/v1')
                self.assertEqual(match['address_family'], 6)
                self.assertEqual(match['address'], '::1')

    def test_wildcard_listener_is_not_accepted_as_exact_destination(self):
        with socket_process('0.0.0.0') as (process, info):
            with self.assertRaisesRegex(qualifier.QualificationError, 'Wildcard'):
                qualifier.verify_listener(process.pid, f"http://127.0.0.1:{info['port']}/v1")

    def test_listener_replacement_under_live_parent_fails_check_and_sample(self):
        with tempfile.TemporaryDirectory() as directory, socket_process('127.0.0.1', coordinator=True) as (process, info):
            url = f"http://127.0.0.1:{info['port']}/v1"
            manifest, provenance = manifest_for_fixture(directory, process, url)
            monitor = qualifier.ResourceMonitor(provenance, max_rss_bytes=10**12, min_available_bytes=1)
            monitor.check()
            parent_before = qualifier.process_identity(process.pid)
            process.stdin.write('restart\n'); process.stdin.flush()
            replacement = child_reply(process)
            self.assertNotEqual(replacement['pid'], info['pid'])
            self.assertEqual(qualifier.process_identity(process.pid), parent_before)
            self.assertNotEqual(qualifier.verify_listener(process.pid, url), provenance['listener_identity'])
            with self.assertRaisesRegex(qualifier.QualificationError, 'listener identity changed'):
                monitor.check()
            with self.assertRaisesRegex(qualifier.QualificationError, 'listener identity changed'):
                monitor.sample()

    def test_descendant_listener_requires_its_own_executable_fingerprint(self):
        with tempfile.TemporaryDirectory() as directory, socket_process('127.0.0.1', sleeping_child=True) as (process, info):
            url = f"http://127.0.0.1:{info['port']}/v1"
            with self.assertRaisesRegex(qualifier.QualificationError, 'actual listener executable'):
                manifest_for_fixture(directory, process, url)
            manifest = Path(directory) / 'manifest.json'
            value = json.loads(manifest.read_text())
            child_executable = (Path('/proc') / str(info['pid']) / 'exe').resolve()
            self.assertNotEqual(str(child_executable), value['runtime_files'][0]['path'])
            value['runtime_files'].append({'path': str(child_executable), 'sha256': qualifier.sha256(child_executable)})
            manifest.write_text(json.dumps(value))
            validated = qualifier.validate_provenance(manifest, 'test-model', url)
            self.assertEqual(validated['listener_identity']['executable'], str(child_executable))

    def test_final_revalidation_rehashes_config_manifest_weights_and_runtime(self):
        with tempfile.TemporaryDirectory() as directory, socket_process('127.0.0.1') as (process, info):
            url = f"http://127.0.0.1:{info['port']}/v1"
            manifest, provenance = manifest_for_fixture(directory, process, url)
            runtime_data = Path(directory) / 'runtime-build.txt'
            runtime_data.write_text('reviewed server build fixture')
            value = json.loads(manifest.read_text())
            value['runtime_files'].append({'path': str(runtime_data.resolve()), 'sha256': qualifier.sha256(runtime_data)})
            manifest.write_text(json.dumps(value))
            provenance = qualifier.validate_provenance(manifest, 'test-model', url)
            config = Path(directory) / 'config.json'; config.write_text('{"mode":"simulation"}')
            sandbox = Path(directory) / 'sandbox.json'; sandbox.write_text('{}')
            report = {'model': {'id': 'test-model', 'base_url': url}, 'provenance': provenance,
                'config': {'sha256': qualifier.sha256(config), 'input': json.loads(config.read_text())},
                'sandbox': {'sha256': qualifier.sha256(sandbox)}, 'source_sha256': qualifier.source_fingerprints()}
            qualifier.revalidate_inputs(report, config, manifest, sandbox)
            for path in (config, manifest, Path(provenance['model_files'][0]['path']), runtime_data, sandbox):
                original = path.read_bytes()
                try:
                    path.write_bytes(original + b' ')
                    with self.subTest(path=path.name), self.assertRaises(qualifier.QualificationError):
                        qualifier.revalidate_inputs(report, config, manifest, sandbox)
                finally:
                    path.write_bytes(original)
            bad = dict(provenance, command_sha256='0' * 64)
            with self.assertRaisesRegex(qualifier.QualificationError, 'launch command changed'):
                qualifier.verify_runtime_identity(bad)

    def test_existing_report_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "report.json"
            report.write_text("preserve")
            with contextlib.redirect_stdout(__import__("io").StringIO()), contextlib.redirect_stderr(__import__("io").StringIO()):
                exit_code = qualifier.main(["--model", "fixture", "--image", "fixture", "--sandbox-report", "absent",
                    "--provenance", "absent", "--report", str(report), "--max-process-rss-bytes", "1", "--min-host-available-bytes", "1"])
            self.assertEqual(exit_code, 1)
            self.assertEqual(report.read_text(), "preserve")


if __name__ == "__main__":
    unittest.main()
