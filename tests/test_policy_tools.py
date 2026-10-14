import contextlib
import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from playground.models import MotionModel
from playground.research import ResearchSupervisor, simulation_controller
from playground.sandbox import ExecutionResult


@contextlib.contextmanager
def supervisor_fixture():
    with tempfile.TemporaryDirectory() as directory:
        controller, clock = simulation_controller()
        supervisor = ResearchSupervisor(directory, controller, clock=clock, config={"disk_reserve_bytes": 1})
        supervisor.start_robot(operator_confirmed=True)
        run = supervisor.archive.start_run("simulation", {})
        supervisor.active_generation = supervisor.archive.start_generation(run)
        supervisor.current_workspace = Path(directory) / "workspaces" / supervisor.active_generation
        supervisor.current_workspace.mkdir(parents=True)
        try:
            yield supervisor
        finally:
            supervisor.close()


def proposal(supervisor, objects=None):
    action = {"left": .5, "right": .5, "duration_s": .1}
    prediction = MotionModel(.075, .075/.154).predict(supervisor.controller.observe(), action)
    if objects is not None: prediction["objects"] = objects
    return {"action": action, "prediction": prediction}


class PolicyToolTests(unittest.TestCase):
    def test_policy_program_never_runs_on_host_and_prediction_precedes_action(self):
        with supervisor_fixture() as supervisor:
            host_marker = supervisor.state_dir / "host-execution-marker"
            # If evaluated on the host, this source would write the marker.
            source = "from pathlib import Path\nPath(" + repr(str(host_marker)) + ").write_text('unsafe host execution')\n"
            (supervisor.current_workspace / "policy.py").write_text(source)
            expected = proposal(supervisor, [{"id": "block", "x": .65, "y": .5}])
            calls, committed = [], []
            def fake_sandbox(workspace, command, timeout_s=30):
                calls.append((workspace, command, timeout_s))
                observation = json.loads((workspace / "observation.json").read_text())
                self.assertEqual(observation["state"], "ARMED")
                return ExecutionResult(0, json.dumps(expected))
            original = supervisor.controller.action
            def checked_action(*args, **kwargs):
                row = supervisor.archive.db.execute("SELECT status,prediction_json,after_json FROM experiments ORDER BY started DESC LIMIT 1").fetchone()
                self.assertEqual(row["status"], "pending")
                self.assertIsNone(row["after_json"])
                self.assertEqual(json.loads(row["prediction_json"])["objects"], expected["prediction"]["objects"])
                committed.append(True)
                return original(*args, **kwargs)
            with patch.dict("os.environ", {"PLAYGROUND_SANDBOX_IMAGE": "python@sha256:" + "a" * 64}), \
                 patch("playground.sandbox.DockerSandbox.execute", side_effect=fake_sandbox), \
                 patch.object(supervisor.controller, "action", side_effect=checked_action), \
                 patch("subprocess.Popen", side_effect=AssertionError("No policy host subprocess permitted")):
                result = supervisor.tool("run_policy", {"filename": "policy.py"})
            self.assertFalse(host_marker.exists())
            self.assertEqual(committed, [True])
            self.assertEqual(calls[0][:2], (supervisor.current_workspace, "python /workspace/policy.py"))
            self.assertEqual(calls[0][2], 10)
            self.assertLess(result["error_breakdown"]["robot_m_equivalent"], 1e-12)
            self.assertEqual(result["error_breakdown"]["objects_m"], {"block": 0})
            self.assertTrue(result["within_declared_uncertainty"])

    def test_policy_paths_are_rejected_before_sandbox_or_action(self):
        with supervisor_fixture() as supervisor, patch.object(supervisor.controller, "action") as action, \
             patch("playground.sandbox.DockerSandbox.execute") as execution:
            for filename in ("../policy.py", "/tmp/policy.py", "nested/policy.py", "policy.py; touch escape", "policy.py\n", "a" * 65 + ".py"):
                with self.subTest(filename=filename), self.assertRaises(ValueError):
                    supervisor.tool("run_policy", {"filename": filename})
            execution.assert_not_called()
            action.assert_not_called()
            self.assertEqual(supervisor.archive.summary()["experiments"], 0)

    def test_invalid_policy_output_cannot_execute_an_action(self):
        with supervisor_fixture() as supervisor:
            (supervisor.current_workspace / "policy.py").write_text("# Reviewed fixture\n")
            invalid = []
            for left in (2, True, float("nan")):
                value = proposal(supervisor)
                value["action"]["left"] = left
                invalid.append(json.dumps(value))
            value = proposal(supervisor)
            value["prediction"]["objects"] = [{"id": "../block", "x": 0, "y": 0}]
            invalid.append(json.dumps(value))
            for key in ("x", "y", "heading"):
                for number in (True, "1", 1e308, 10**1000):
                    value = proposal(supervisor)
                    value["prediction"][key] = number
                    invalid.append(json.dumps(value))
            value = proposal(supervisor, [{"id": "block", "x": 1e308, "y": .5}])
            invalid.append(json.dumps(value))
            invalid.extend(["not json", json.dumps({"arm": True, **proposal(supervisor)}), "x" * 8193])
            with patch.object(supervisor.controller, "action") as action, \
                 patch.dict("os.environ", {"PLAYGROUND_SANDBOX_IMAGE": "python@sha256:" + "a" * 64}):
                for stdout in invalid:
                    with self.subTest(stdout=stdout[:80]), \
                         patch("playground.sandbox.DockerSandbox.execute", return_value=ExecutionResult(0, stdout)), \
                         self.assertRaises(ValueError):
                        supervisor.tool("run_policy", {"filename": "policy.py"})
                action.assert_not_called()
            self.assertEqual(supervisor.archive.summary()["experiments"], 0)

    def test_failed_or_timed_out_program_has_no_action(self):
        with supervisor_fixture() as supervisor:
            (supervisor.current_workspace / "policy.py").write_text("# Reviewed fixture\n")
            with patch.object(supervisor.controller, "action") as action, \
                 patch.dict("os.environ", {"PLAYGROUND_SANDBOX_IMAGE": "python@sha256:" + "a" * 64}):
                for execution in (ExecutionResult(1, json.dumps(proposal(supervisor))), ExecutionResult(124, timed_out=True)):
                    with patch("playground.sandbox.DockerSandbox.execute", return_value=execution), self.assertRaises(ValueError):
                        supervisor.tool("run_policy", {"filename": "policy.py"})
                action.assert_not_called()

    def test_policy_deadline_rechecked_after_program_before_action(self):
        with supervisor_fixture() as supervisor:
            (supervisor.current_workspace / "policy.py").write_text("# Reviewed fixture\n")
            expected = proposal(supervisor)
            def expired_program(*args, **kwargs):
                supervisor.deadline = time.monotonic() - 1
                return ExecutionResult(0, json.dumps(expected))
            with patch.object(supervisor.controller, "action") as action, \
                 patch.dict("os.environ", {"PLAYGROUND_SANDBOX_IMAGE": "python@sha256:" + "a" * 64}), \
                 patch("playground.sandbox.DockerSandbox.execute", side_effect=expired_program), self.assertRaises(RuntimeError):
                supervisor.tool("run_policy", {"filename": "policy.py"})
            action.assert_not_called()
            self.assertEqual(supervisor.archive.summary()["experiments"], 0)

    def test_training_export_contains_completed_exploration_without_holdout(self):
        with supervisor_fixture() as supervisor:
            action = {"left": .5, "right": .5, "duration_s": .1}
            training = supervisor.experiment(action, MotionModel().predict(supervisor.controller.observe(), action))
            holdout = supervisor.experiment(action, MotionModel().predict(supervisor.controller.observe(), action), phase="holdout")
            exported = supervisor.tool("export_trials", {"limit": 500})
            content = (supervisor.current_workspace / "trials.jsonl").read_bytes()
            rows = [json.loads(line) for line in content.splitlines()]
            self.assertEqual(exported["trials"], 1)
            self.assertEqual(exported["sha256"], hashlib.sha256(content).hexdigest())
            self.assertEqual([row["id"] for row in rows], [training["id"]])
            self.assertEqual(rows[0]["phase"], "exploration")
            self.assertEqual(rows[0]["status"], "completed")
            self.assertEqual(rows[0]["source"], "simulation")
            self.assertIn("before", rows[0])
            self.assertIn("after", rows[0])
            self.assertNotIn(holdout["id"], content.decode())
            queried = supervisor.tool("archive_query", {"limit": 30})
            self.assertEqual([row["id"] for row in queried["trials"]], [training["id"]])
            for limit in (True, 0, 501, "20"):
                with self.assertRaises(ValueError): supervisor.tool("export_trials", {"limit": limit})

    def test_missing_object_is_inconclusive_and_cannot_support_a_claim(self):
        with supervisor_fixture() as supervisor:
            expected = proposal(supervisor, [{"id": "missing", "x": .65, "y": .5}])
            result = supervisor.experiment(expected["action"], expected["prediction"])
            self.assertEqual(result["status"], "inconclusive")
            self.assertEqual(result["unavailable_objects"], ["missing"])
            self.assertEqual(supervisor.controller.state, "ARMED")
            row = supervisor.archive.db.execute("SELECT status,error,after_json FROM experiments WHERE id=?", (result["id"],)).fetchone()
            self.assertEqual(row["status"], "inconclusive")
            self.assertIsNone(row["error"])
            self.assertIsNotNone(row["after_json"])
            with self.assertRaises(ValueError):
                supervisor.tool("submit_finding", {"claim": "Unmeasured success", "evidence_ids": [result["id"]]})
            self.assertEqual(supervisor.tool("export_trials", {"limit": 10})["trials"], 0)


if __name__ == "__main__":
    unittest.main()
