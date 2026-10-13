"""Actual model HTTP client and supervisor loop, with local deterministic fixtures.

The sandbox execution fixture writes reviewed test data only. These tests do not
claim that Docker or a real local model was function-tested.
"""
import contextlib
from dataclasses import dataclass
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from playground.llm import LocalModel, ModelError
from playground.archive import canonical
from playground.research import ResearchSupervisor, simulation_controller
from playground.sandbox import ExecutionResult


@dataclass
class Reply:
    decision: object = None
    body: bytes | None = None
    status: int = 200
    location: str | None = None
    delay_s: float = 0
    drip_s: float = 0
    header_drip_s: float = 0


@contextlib.contextmanager
def local_model_server(provider):
    requests = []
    stop = threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append({"path": self.path})
            payload = json.dumps({"data": [{"id": "test-model"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_POST(self):
            length = int(self.headers["Content-Length"])
            request = json.loads(self.rfile.read(length))
            requests.append(request)
            reply = provider(request)
            if not isinstance(reply, Reply):
                reply = Reply(decision=reply)
            if reply.delay_s:
                stop.wait(reply.delay_s)
            content = reply.decision if isinstance(reply.decision, str) else json.dumps(reply.decision)
            payload = reply.body if reply.body is not None else json.dumps({"choices": [{"message": {"content": content}}]}).encode()
            if reply.header_drip_s:
                # Keep a legal header incomplete, with bytes arriving before each
                # socket inactivity timeout. Only the total watchdog can abort it.
                header = b"HTTP/1.1 200 OK\r\nX-Test: " + b"x" * 1000 + b"\r\n\r\n"
                try:
                    for byte in header:
                        if stop.wait(reply.header_drip_s): break
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
                return
            self.send_response(reply.status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            if reply.location:
                self.send_header("Location", reply.location)
            self.end_headers()
            try:
                if reply.drip_s:
                    for byte in payload:
                        if stop.wait(reply.drip_s): break
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                else:
                    self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=.01), daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", requests
    finally:
        stop.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


def request_context(request):
    return json.loads(request["messages"][1]["content"])


def prior_results(request):
    values = []
    for item in request["messages"][2:]:
        if item["role"] == "user" and isinstance(item["content"], str):
            values.append(json.loads(item["content"]))
    return values


class AgentIntegrationTests(unittest.TestCase):
    def make_supervisor(self, directory):
        controller, clock = simulation_controller()
        supervisor = ResearchSupervisor(directory, controller, clock=clock, config={"disk_reserve_bytes": 0})
        supervisor.start_robot(operator_confirmed=True)
        return supervisor

    def test_actual_http_tools_replacement_artifact_inheritance_and_evidence(self):
        counters = {}
        helper = b"# Reviewed fixture, never execute model text on the host.\nVALUE = 42\n"
        def decisions(request):
            context = request_context(request)
            generation = context["generation"]
            index = counters.get(generation, 0) + 1
            counters[generation] = index
            if generation == 1:
                if index == 1:
                    return {"tool": "run_code", "arguments": {"command": "write helper inside sandbox"}}
                if index == 2:
                    pose = context["observation"]["robot"]
                    return {"tool": "experiment", "arguments": {
                        "action": {"left": .4, "right": .4, "duration_s": .1},
                        "prediction": {"x": pose["x"], "y": pose["y"], "heading": pose["heading"], "uncertainty_m": .05}}}
                if index == 3:
                    return {"tool": "submit_finding", "arguments": {"claim": "Fabricated result", "evidence_ids": ["f" * 32]}}
                if index == 4:
                    trial = next(value for value in prior_results(request) if "action" in value and "id" in value)
                    return {"tool": "submit_finding", "arguments": {
                        "claim": "The measured motion stayed within this prediction's uncertainty.",
                        "evidence_ids": [trial["id"]], "verified": True}}
                return {"tool": "finish", "arguments": {"summary": "Successor should inspect the measured trial and helper."}}
            if index == 1:
                return {"tool": "artifact_list", "arguments": {}}
            if index == 2:
                artifacts = prior_results(request)[-1]["artifacts"]
                return {"tool": "artifact_import", "arguments": {"id": artifacts[0]["id"]}}
            if index == 3:
                return {"tool": "arm_hardware", "arguments": {}}
            return {"tool": "finish", "arguments": {"summary": "Imported a predecessor helper without changing its source."}}

        def trusted_sandbox_fixture(workspace, command, timeout_s=30):
            self.assertEqual(command, "write helper inside sandbox")
            self.assertLessEqual(timeout_s, 30)
            (workspace / "helper.py").write_bytes(helper)
            return ExecutionResult(0, "fixture saved")

        with tempfile.TemporaryDirectory() as directory, local_model_server(decisions) as (url, requests):
            supervisor = self.make_supervisor(directory)
            pending_checked = []
            original_action = supervisor.controller.action
            def action_after_prediction(*args, **kwargs):
                row = supervisor.archive.db.execute("SELECT status,after_json,prediction_json FROM experiments ORDER BY started DESC LIMIT 1").fetchone()
                self.assertEqual(row["status"], "pending")
                self.assertIsNone(row["after_json"])
                self.assertIn("uncertainty_m", json.loads(row["prediction_json"]))
                pending_checked.append(True)
                return original_action(*args, **kwargs)
            try:
                with patch("playground.sandbox.DockerSandbox.execute", side_effect=trusted_sandbox_fixture), \
                     patch.object(supervisor.controller, "action", side_effect=action_after_prediction), \
                     contextlib.redirect_stdout(io.StringIO()), \
                     patch.dict("os.environ", {"PLAYGROUND_SANDBOX_IMAGE": "python@sha256:" + "a" * 64}):
                    result = supervisor.run(generations=2, steps=6, model=LocalModel(url), max_seconds=10)
                self.assertEqual(result["status"], "completed")
                self.assertEqual(result["generations"], 2)
                self.assertEqual(result["experiments"], 1)
                self.assertEqual(result["findings"], 1)
                self.assertEqual(pending_checked, [True])
                findings = list(supervisor.archive.db.execute("SELECT claim,status FROM findings"))
                self.assertEqual(findings[0]["status"], "hypothesis")
                self.assertNotIn("Fabricated", findings[0]["claim"])
                workspaces = sorted((Path(directory) / "workspaces").iterdir())
                self.assertEqual(len(workspaces), 2)
                files = list((Path(directory) / "workspaces").rglob("*helper.py"))
                self.assertEqual(len(files), 2)
                self.assertTrue(all(path.read_bytes() == helper for path in files))
                artifacts = supervisor.archive.artifacts()
                self.assertEqual(len(artifacts), 2)
                self.assertTrue(all(item["sha256"] == hashlib.sha256(helper).hexdigest() for item in artifacts))
                transcripts = [json.loads(path.read_text()) for path in (Path(directory) / "workspaces").rglob("transcript.json")]
                errors = [record["result"].get("error", "") for transcript in transcripts for record in transcript]
                self.assertTrue(any("Evidence must reference" in error for error in errors))
                self.assertIn("Unknown tool", errors)
                starts = [request_context(req) for req in requests if "messages" in req and len(req["messages"]) == 2]
                self.assertEqual([value["generation"] for value in starts], [1, 2])
                self.assertEqual(starts[1]["inherited"]["findings"], 1)
                self.assertEqual(supervisor.controller.state, "DISARMED")
            finally:
                supervisor.close()

    def test_invalid_model_decisions_stop_after_three_replacements(self):
        with tempfile.TemporaryDirectory() as directory, local_model_server(lambda _: {"tool": "observe", "arguments": []}) as (url, requests):
            supervisor = self.make_supervisor(directory)
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    result = supervisor.run(generations=8, steps=3, model=LocalModel(url), max_seconds=10)
                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["generations"], 3)
                self.assertEqual(result["experiments"], 0)
                states = [row[0] for row in supervisor.archive.db.execute("SELECT status FROM generations")]
                self.assertEqual(states, ["failed"] * 3)
                self.assertEqual(supervisor.controller.state, "DISARMED")
                self.assertEqual(len([req for req in requests if "messages" in req]), 3)
            finally:
                supervisor.close()

    def test_drip_response_cannot_extend_run_or_trigger_late_action(self):
        def decision(request):
            pose = request_context(request)["observation"]["robot"]
            return Reply(drip_s=.005, decision={"tool": "experiment", "arguments": {
                "action": {"left": .4, "right": .4, "duration_s": .1},
                "prediction": {"x": pose["x"], "y": pose["y"], "heading": pose["heading"]}}})
        with tempfile.TemporaryDirectory() as directory, local_model_server(decision) as (url, _):
            supervisor = self.make_supervisor(directory)
            try:
                started = time.monotonic()
                with contextlib.redirect_stdout(io.StringIO()):
                    result = supervisor.run(generations=2, steps=3, model=LocalModel(url, model="test-model"), max_seconds=.08)
                elapsed = time.monotonic() - started
                self.assertLess(elapsed, .5, "HTTP socket inactivity timeout must not replace the total response budget")
                self.assertEqual(result["experiments"], 0)
                self.assertEqual(result["status"], "stopped")
                self.assertEqual(supervisor.controller.state, "DISARMED")
            finally:
                supervisor.close()

    def test_incomplete_dripping_headers_are_interrupted_before_late_action(self):
        with tempfile.TemporaryDirectory() as directory, local_model_server(lambda _: Reply(header_drip_s=.005)) as (url, requests):
            supervisor = self.make_supervisor(directory)
            try:
                started = time.monotonic()
                with contextlib.redirect_stdout(io.StringIO()):
                    result = supervisor.run(generations=2, steps=3, model=LocalModel(url, model="test-model"), max_seconds=.08)
                self.assertLess(time.monotonic() - started, .5)
                self.assertEqual(result["experiments"], 0)
                self.assertEqual(result["status"], "stopped")
                self.assertEqual(supervisor.controller.state, "DISARMED")
                self.assertEqual(len(requests), 1)
            finally:
                supervisor.close()

    def test_transcript_bound_uses_bytes_actually_written(self):
        count = 0
        padding = ""
        def decision(_):
            nonlocal count
            count += 1
            return {"tool": "observe", "arguments": {"padding": padding}} if count <= 64 else {"tool": "finish", "arguments": {}}
        with tempfile.TemporaryDirectory() as directory, local_model_server(decision) as (url, _):
            supervisor = self.make_supervisor(directory)
            try:
                base_record = {"decision": {"tool": "observe", "arguments": {"padding": ""}}, "result": supervisor.tool("observe", {})}
                padding = "x" * (4096 - len(canonical(base_record).encode()))
                with contextlib.redirect_stdout(io.StringIO()):
                    result = supervisor.run(generations=1, steps=66, model=LocalModel(url), max_seconds=20)
                self.assertEqual(result["status"], "completed")
                transcript = next((Path(directory) / "workspaces").rglob("transcript.json"))
                self.assertLessEqual(transcript.stat().st_size, 256 * 1024)
            finally:
                supervisor.close()

    def test_deep_model_decision_rejected_before_tools_or_transcript_expansion(self):
        nested = 0
        for _ in range(200): nested = [nested]
        with tempfile.TemporaryDirectory() as directory, local_model_server(lambda _: {"tool": "observe", "arguments": {"unused": nested}}) as (url, _):
            supervisor = self.make_supervisor(directory)
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    result = supervisor.run(generations=1, steps=3, model=LocalModel(url), max_seconds=10)
                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["experiments"], 0)
                transcript = next((Path(directory) / "workspaces").rglob("transcript.json"))
                self.assertEqual(json.loads(transcript.read_text()), [])
            finally:
                supervisor.close()

    def test_corrupted_predecessor_artifact_cannot_be_imported(self):
        with tempfile.TemporaryDirectory() as directory:
            predecessor = self.make_supervisor(directory)
            try:
                run = predecessor.archive.start_run("simulation", {})
                generation = predecessor.archive.start_generation(run)
                legacy = Path(directory) / "workspaces" / generation / "helper.py"
                legacy.parent.mkdir(parents=True)
                legacy.write_bytes(b"SAFE")
                artifact_id = predecessor.archive.record_artifact(generation, legacy.relative_to(directory).as_posix(), hashlib.sha256(b"SAFE").hexdigest(), 4)
                predecessor.archive.end_generation(generation, "completed", "fixture")
                predecessor.archive.end_run(run, "completed")
            finally:
                predecessor.close()
            legacy.write_bytes(b"EVIL")  # Trusted fault injection, same length.
            calls = 0
            def decision(_):
                nonlocal calls
                calls += 1
                return {"tool": "artifact_import", "arguments": {"id": artifact_id}} if calls == 1 else {"tool": "finish", "arguments": {}}
            with local_model_server(decision) as (url, _):
                successor = self.make_supervisor(directory)
                try:
                    with contextlib.redirect_stdout(io.StringIO()):
                        result = successor.run(generations=1, steps=3, model=LocalModel(url), max_seconds=10)
                    self.assertEqual(result["status"], "completed")
                    transcript = json.loads((successor.current_workspace / "transcript.json").read_text())
                    self.assertIn("integrity", transcript[0]["result"]["error"])
                    self.assertEqual(list(successor.current_workspace.glob("*helper.py")), [])
                finally:
                    successor.close()

    def test_redirect_does_not_reach_second_local_endpoint(self):
        target = {}
        with local_model_server(lambda _: Reply(status=302, location=target["url"] + "/trap")) as (url, requests):
            target["url"] = url
            model = LocalModel(url, model="test-model")
            with self.assertRaises(ModelError):
                model.decide([{"role": "user", "content": "fixture"}])
            self.assertFalse(any(req.get("path", "").endswith("/trap") for req in requests))

    def test_camera_admission_rejects_before_writing_past_workspace_quota(self):
        calls = 0
        def decision(_):
            nonlocal calls
            calls += 1
            if calls == 1:
                return {"tool": "run_code", "arguments": {"command": "reviewed quota fixture"}}
            if calls == 2:
                return {"tool": "camera_frame", "arguments": {}}
            return {"tool": "finish", "arguments": {}}
        def sandbox_fixture(workspace, command, timeout_s=30):
            self.assertEqual(command, "reviewed quota fixture")
            with (workspace / "existing-data.bin").open("wb") as output:
                output.truncate(63 * 1024**2)
            return ExecutionResult(0)
        class CameraFixture:
            def snapshot(self, max_width):
                return b"\xff\xd8" + b"x" * 1024, {"fixture": True}
        with tempfile.TemporaryDirectory() as directory, local_model_server(decision) as (url, _):
            supervisor = self.make_supervisor(directory)
            supervisor.camera = CameraFixture()
            try:
                with patch("playground.sandbox.DockerSandbox.execute", side_effect=sandbox_fixture), \
                     patch.dict("os.environ", {"PLAYGROUND_SANDBOX_IMAGE": "python@sha256:" + "a" * 64}), \
                     contextlib.redirect_stdout(io.StringIO()):
                    result = supervisor.run(generations=1, steps=4, model=LocalModel(url), max_seconds=10)
                self.assertEqual(result["status"], "completed")
                transcript = json.loads((supervisor.current_workspace / "transcript.json").read_text())
                self.assertIn("quota", transcript[1]["result"]["error"].lower())
                self.assertEqual(list(supervisor.current_workspace.glob("camera-*.jpg")), [])
                self.assertLessEqual(sum(path.stat().st_size for path in supervisor.current_workspace.iterdir()), 64 * 1024**2)
            finally:
                supervisor.close()

    def test_second_supervisor_cannot_recover_live_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            supervisor = self.make_supervisor(directory)
            second_controller, second_clock = simulation_controller()
            try:
                run_id = supervisor.archive.start_run("simulation", {})
                with self.assertRaises(RuntimeError):
                    ResearchSupervisor(directory, second_controller, clock=second_clock)
                state = supervisor.archive.db.execute("SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()[0]
                self.assertEqual(state, "running")
            finally:
                second_controller.close()
                supervisor.close()


if __name__ == "__main__":
    unittest.main()
