import contextlib
import io
import math
import tempfile
import unittest
from playground.archive import Archive
from playground.llm import LocalModel
from playground.models import MotionModel, checked_prediction
from playground.research import ResearchSupervisor, simulation_controller, validate_action
from playground.state import StateLock


class ResearchTests(unittest.TestCase):
    def test_only_one_supervisor_can_own_state(self):
        with tempfile.TemporaryDirectory() as directory:
            first = StateLock(directory)
            try:
                with self.assertRaises(RuntimeError):
                    StateLock(directory)
            finally:
                first.close()
            second = StateLock(directory)
            second.close()
    def test_predictions_are_saved_before_outcome_and_recover_as_interrupted(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Archive(directory)
            run = archive.start_run("simulation", {})
            generation = archive.start_generation(run)
            eid = archive.begin_experiment(generation, "simulation", "exploration", {"robot": {"x": 0, "y": 0, "heading": 0}},
                                           {"left": 1, "right": 1, "duration_s": .1}, {"x": .01, "y": 0, "heading": 0})
            row = archive.db.execute("SELECT * FROM experiments WHERE id=?", (eid,)).fetchone()
            self.assertEqual(row["status"], "pending")
            self.assertIsNone(row["after_json"])
            archive.recover_interrupted()
            self.assertEqual(archive.db.execute("SELECT status FROM experiments WHERE id=?", (eid,)).fetchone()[0], "interrupted")
            self.assertEqual(archive.experiments(), [])
            archive.close()

    def test_finding_cannot_invent_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Archive(directory)
            with self.assertRaises(ValueError):
                archive.submit_finding("generation", "I learned physics", "all worlds", ["made-up"])
            archive.close()

    def test_nonfinite_and_out_of_envelope_actions_fail(self):
        for bad in (math.nan, math.inf, True, 2.0, 10**1000):
            with self.assertRaises(ValueError):
                validate_action({"left": bad, "right": 0, "duration_s": .1})
        with self.assertRaises(ValueError):
            validate_action({"left": .5, "right": .5, "duration_s": .001})
        with self.assertRaises(ValueError):
            checked_prediction({"x": math.inf, "y": 0, "heading": 0})

    def test_demo_learns_and_survives_multiple_agent_generations(self):
        with tempfile.TemporaryDirectory() as directory:
            controller, clock = simulation_controller()
            supervisor = ResearchSupervisor(directory, controller, clock=clock)
            try:
                supervisor.start_robot(operator_confirmed=True)
                with contextlib.redirect_stdout(io.StringIO()):
                    result = supervisor.run(generations=3, steps=12, demo=True)
                self.assertEqual(result["status"], "completed")
                self.assertEqual(result["generations"], 3)
                self.assertEqual(result["evaluations"], 3)
                self.assertEqual(result["experiments"], 84)
                self.assertAlmostEqual(supervisor.model.linear_mps, .075, places=6)
                for row in supervisor.archive.db.execute("SELECT results_json FROM evaluations"):
                    import json
                    evaluation = json.loads(row[0])
                    self.assertTrue(evaluation["passed"])
                    self.assertLess(evaluation["candidate_mean_error"], evaluation["baseline_mean_error"])
                self.assertEqual(controller.state, "DISARMED")
            finally:
                supervisor.close()

    def test_agent_finding_cannot_assign_verified_status(self):
        with tempfile.TemporaryDirectory() as directory:
            controller, clock = simulation_controller()
            supervisor = ResearchSupervisor(directory, controller, clock=clock)
            try:
                run = supervisor.archive.start_run("simulation", {})
                supervisor.active_generation = supervisor.archive.start_generation(run)
                supervisor.start_robot(operator_confirmed=True)
                action = {"left": .5, "right": .5, "duration_s": .1}
                trial = supervisor.experiment(action, MotionModel().predict(controller.observe(), action))
                finding = supervisor.tool("submit_finding", {"claim": "Candidate claim", "evidence_ids": [trial["id"]], "verified": True})
                self.assertEqual(finding["status"], "hypothesis")
            finally:
                supervisor.close()

    def test_model_endpoint_cannot_be_used_for_network_escape(self):
        for url in ("http://192.168.1.1/v1", "https://example.com/v1", "http://user:secret@localhost/v1", "file:///etc/passwd"):
            with self.assertRaises(ValueError):
                LocalModel(url)
        LocalModel("http://127.0.0.1:18080/v1")


if __name__ == "__main__":
    unittest.main()
