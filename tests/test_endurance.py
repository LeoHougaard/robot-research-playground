import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from playground.archive import canonical
from playground.models import MotionModel
from playground.research import ResearchSupervisor, simulation_controller


class FixedModel:
    def __init__(self, tool):
        self.tool = tool
    def decide(self, messages, **kwargs):
        value = {"tool": self.tool, "arguments": {}}
        return value, canonical(value)


class EnduranceTests(unittest.TestCase):
    def make_supervisor(self, directory):
        controller, clock = simulation_controller()
        value = ResearchSupervisor(directory, controller, clock=clock, config={"disk_reserve_bytes": 1})
        value.start_robot(operator_confirmed=True)
        return value

    def test_persistent_tool_errors_and_read_only_churn_stop_truthfully(self):
        for tool in ("unknown-tool", "observe"):
            with self.subTest(tool=tool), tempfile.TemporaryDirectory() as directory:
                s = self.make_supervisor(directory)
                try:
                    with contextlib.redirect_stdout(io.StringIO()):
                        result = s.run(generations=10, steps=4, model=FixedModel(tool))
                    self.assertEqual(result["status"], "no_progress")
                    self.assertEqual(result["generations"], 3)
                    self.assertEqual(result["experiments"], 0)
                    self.assertEqual([r[0] for r in s.archive.db.execute("SELECT status FROM generations")], ["no_progress"] * 3)
                    self.assertEqual(s.controller.state, "DISARMED")
                finally:
                    s.close()

    def physical_holdout_fixture(self, s):
        # Simulation only. The label selects recorded-holdout evaluation.
        s.controller.backend.is_real = True
        run = s.archive.start_run("physical-fixture", {})
        s.active_generation = s.archive.start_generation(run)
        s.model = MotionModel(.075, .075/.154)
        before = s.controller.observe()
        action = {"left": .8, "right": .8, "duration_s": .35}
        after = {**before, "robot": s.model.predict(before, action)}
        for _ in range(101):
            eid = s.archive.begin_experiment(s.active_generation, "physical", "holdout", before, action, s.model.predict(before, action))
            s.archive.finish_experiment(eid, after, 0)

    def test_over_100_holdouts_save_complete_evaluation_and_restore_without_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            s = self.make_supervisor(directory)
            try:
                self.physical_holdout_fixture(s)
                result = s.evaluate_model()
                self.assertTrue(result["passed"])
                self.assertEqual(result["trials"], 100)
                finding = s.archive.db.execute("SELECT evidence_json FROM findings").fetchone()
                self.assertEqual(json.loads(finding[0]), result["evidence_ids"])
                (Path(directory)/"best-model.json").unlink()
            finally:
                s.close()
            s = self.make_supervisor(directory)
            try:
                self.assertAlmostEqual(s.best_model.linear_mps, .075)
            finally:
                s.close()

    def test_finding_failure_cannot_commit_evaluation_or_promote_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            s = self.make_supervisor(directory)
            try:
                self.physical_holdout_fixture(s)
                with patch.object(s.archive, "submit_finding", side_effect=ValueError("fixture commit failure")), self.assertRaises(ValueError):
                    s.evaluate_model()
                self.assertEqual(s.archive.summary()["evaluations"], 0)
                self.assertIsNone(s.best_model)
                self.assertFalse((Path(directory)/"best-model.json").exists())
            finally:
                s.close()

    def test_artifact_cursor_reaches_older_program(self):
        with tempfile.TemporaryDirectory() as directory:
            s = self.make_supervisor(directory)
            try:
                old = s.archive.record_artifact("old", "workspaces/old/policy.py", "a"*64, 10)
                for i in range(100):
                    s.archive.record_artifact("new", "workspaces/new/data-%d" % i, "b"*64, 10)
                ids, cursor = [], None
                while True:
                    result = s.tool("artifact_list", {"before": cursor})
                    ids.extend(x["id"] for x in result["artifacts"])
                    cursor = result["next_before"]
                    if cursor is None:
                        break
                self.assertEqual(len(ids), 101)
                self.assertEqual(len(set(ids)), 101)
                self.assertIn(old, ids)
            finally:
                s.close()

    def test_interrupted_workspace_recovers_once_with_original_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            s = self.make_supervisor(directory)
            try:
                run = s.archive.start_run("simulation", {})
                generation = s.archive.start_generation(run)
                workspace = Path(directory)/"workspaces"/generation
                workspace.mkdir(parents=True)
                (workspace/"useful.py").write_text("# preserved pre-crash program\n")
            finally:
                s.close()
            s = self.make_supervisor(directory)
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    s.run(generations=1, steps=1, model=FixedModel("finish"))
                artifacts = s.tool("artifact_list", {})["artifacts"]
                self.assertEqual(len(artifacts), 1)
                self.assertEqual(artifacts[0]["generation_id"], generation)
                copied = s.import_artifact(artifacts[0]["id"])
                self.assertEqual((s.current_workspace/copied["filename"]).read_bytes(), (workspace/"useful.py").read_bytes())
                s.archive_workspace(workspace, generation)
                self.assertEqual(s.archive.summary()["artifacts"], 1)
            finally:
                s.close()
