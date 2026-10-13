"""Synthetic, temporary record checks only; never physical commissioning evidence."""
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from dataclasses import asdict
from playground.robot import SafetyLimits

from playground.archive import canonical

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("commissioning", ROOT / "scripts/validate_commissioning.py")
commissioning = importlib.util.module_from_spec(spec)
spec.loader.exec_module(commissioning)
NOW = "2026-09-01T00:00:00+00:00"
VALIDATION_TIME = datetime(2026, 10, 1, tzinfo=timezone.utc)


class CommissioningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.example = json.loads((ROOT / "configs/commissioning-evidence.example.json").read_text())

    def tearDown(self):
        self.temp.cleanup()

    def artifact(self, name, content="SYNTHETIC TEST FIXTURE, NOT PHYSICAL EVIDENCE"):
        path = self.root / name
        path.write_text(content)
        return {"path": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size}

    def specimen(self):
        data = copy.deepcopy(self.example)
        data.update(template=False, attempt_id="synthetic-unit-test", recorded_at=NOW, operator="unit-test fixture")
        inputs = {label: self.artifact(label + ".txt") for label in commissioning.INPUTS |
                  {"start_state_contract", "reset_routine"}}
        runtime = self.artifact("synthetic-runtime.txt")
        payload = {"mode": "physical", "config": {"synthetic_unit_test": True},
                   "backend": "synthetic.UnitTestOnly", "safety_limits": asdict(SafetyLimits()),
                   "tether_limit_rad": 3.14, "geometry": {"source": "inputs.geometry"},
                   "physical_attestation": "not established by file fingerprints",
                   "runtime_files": {"synthetic-runtime.txt": {k: runtime[k] for k in ("sha256", "bytes")}},
                   "inputs": {label: {k: value[k] for k in ("sha256", "bytes")} for label, value in inputs.items()}}
        manifest = {"format_version": 1, "fingerprint": hashlib.sha256(canonical(payload).encode()).hexdigest(),
                    "payload": payload}
        data["provenance"] = {"manifest": self.artifact("provenance.json", canonical(manifest)),
                              "fingerprint": manifest["fingerprint"], "input_files": inputs}
        data["instruments"] = [{"id": "synthetic-timer", "description": "Unit-test fixture only",
                                "calibrated_at": NOW, "uncertainty": "Synthetic 0 seconds; no instrument exists",
                                "artifact": self.artifact("instrument.txt")}]
        data["records"] = [{"id": "synthetic-record", "kind": "measured", "recorded_at": NOW,
                            "operator": "unit-test fixture", "operator_present": True,
                            "method": "Synthetic record to test parser, no physical measurement",
                            "instrument_ids": ["synthetic-timer"], "values": {"synthetic": "not hardware"},
                            "artifact": self.artifact("record.txt")}]
        return data

    def pass_through(self, data, last):
        for gate in data["gates"][:commissioning.STAGES.index(last) + 1]:
            gate.update(status="passed", record_ids=["synthetic-record"], reviewer="unit-test fixture", reviewed_at=NOW)
        for key in ("start_state_contract", "reset_routine"):
            data[key].update(implementation="implemented", artifact=data["provenance"]["input_files"][key])
        start = datetime.fromisoformat(NOW)
        for stage, seconds in commissioning.DURATIONS.items():
            if commissioning.STAGES.index(stage) > commissioning.STAGES.index(last):
                continue
            data["endurance_attempts"].append({"stage": stage, "attempt_id": "synthetic-" + stage,
                "status": "passed", "fingerprint": data["provenance"]["fingerprint"], "started_at": start.isoformat(),
                "ended_at": (start + timedelta(seconds=seconds)).isoformat(),
                "continuous_elapsed_s": seconds, "manual_rescues": 0, "disarms": 0,
                "automatic_rearms": 0, "restarts": 0, "observation_gaps": 0,
                "record_ids": ["synthetic-record"]})
            start += timedelta(seconds=seconds)
            next(g for g in data["gates"] if g["id"] == stage)["reviewed_at"] = start.isoformat()

    def validate(self, data, **kwargs):
        return commissioning.validate_document(data, self.root, now=VALIDATION_TIME, **kwargs)

    def test_example_is_valid_but_entirely_unqualified(self):
        result = self.validate(self.example)
        self.assertEqual(result["qualification"], "unqualified")
        self.assertEqual(result["passed_record_stages"], [])
        self.assertFalse(result["eligible_for_actuation"])

    def test_example_cannot_satisfy_measured_stage(self):
        with self.assertRaisesRegex(ValueError, "Planning template"):
            self.validate(self.example, require_stage="mechanical_fit")

    def test_template_cannot_claim_gate_or_implementation(self):
        for change in ("gate", "routine", "operator", "review"):

            data = copy.deepcopy(self.example)
            if change == "gate": data["gates"][0]["status"] = "passed"
            elif change == "routine": data["reset_routine"]["implementation"] = "implemented"
            elif change == "operator": data["operator"] = "someone"
            else: data["gates"][0]["reviewed_at"] = NOW
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.validate(data)

    def test_consistent_records_never_authorize_hardware(self):
        data = self.specimen()
        self.pass_through(data, "endurance_1w")
        result = self.validate(data, require_stage="endurance_1w")
        self.assertEqual(len(result["passed_record_stages"]), len(commissioning.STAGES))
        self.assertFalse(result["eligible_for_actuation"])
        self.assertEqual(result["qualification"], "requires independent physical review")

    def test_simulation_manifest_is_rejected(self):
        data = self.specimen()
        manifest = json.loads((self.root / "provenance.json").read_text())
        manifest["payload"]["mode"] = "simulation"
        manifest["fingerprint"] = hashlib.sha256(canonical(manifest["payload"]).encode()).hexdigest()
        data["provenance"].update(manifest=self.artifact("provenance.json", canonical(manifest)),
                                  fingerprint=manifest["fingerprint"])
        with self.assertRaisesRegex(ValueError, "Simulation provenance"):
            self.validate(data)

    def test_artifact_tamper_and_provenance_drift_fail(self):
        data = self.specimen()
        (self.root / "record.txt").write_text("tampered")
        with self.assertRaisesRegex(ValueError, "changed"):
            self.validate(data)
        data = self.specimen()
        data["provenance"]["fingerprint"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "fingerprint mismatch"):
            self.validate(data)
        data = self.specimen()
        data["provenance"]["input_files"]["geometry"] = self.artifact("other-geometry.txt", "different")
        with self.assertRaisesRegex(ValueError, "differs from manifest"):
            self.validate(data)

    def test_unsafe_paths_and_links_are_rejected(self):
        for path in ("../escape", "/absolute", "sub/../record.txt", "a\\b", "C:drive"):
            data = self.specimen()
            data["records"][0]["artifact"]["path"] = path
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.validate(data)
        data = self.specimen()
        (self.root / "linked.txt").symlink_to(self.root / "record.txt")
        data["records"][0]["artifact"]["path"] = "linked.txt"
        with self.assertRaises(ValueError):
            self.validate(data)

    def test_load_boundary_cannot_be_relaxed_or_nonfinite(self):
        for value in (2.1, True, 0, float("nan"), float("inf")):
            data = copy.deepcopy(self.example)
            data["boundary"]["max_total_supply_current_a"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.validate(data)

    def test_automatic_rearm_and_unwinding_rejected(self):
        for field in ("automatic_rearm", "autonomous_tether_unwind"):
            data = copy.deepcopy(self.example)
            data["reset_routine"][field] = True
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.validate(data)

    def test_bench_pass_requires_measurement_instrument_and_present_operator(self):
        for alteration in ("observed", "instrument", "absent"):
            data = self.specimen()
            self.pass_through(data, "mechanical_fit")
            if alteration == "observed": data["records"][0]["kind"] = "observed"
            elif alteration == "instrument": data["records"][0]["instrument_ids"] = []
            else: data["records"][0]["operator_present"] = False
            with self.subTest(alteration=alteration), self.assertRaises(ValueError):
                self.validate(data)

    def test_missing_prerequisite_or_reset_implementation_rejected(self):
        data = self.specimen()
        self.pass_through(data, "trusted_reset")
        data["gates"][0]["status"] = "unqualified"
        with self.assertRaisesRegex(ValueError, "Earlier"):
            self.validate(data)
        data["gates"][0]["status"] = "passed"
        data["reset_routine"].update(implementation="not_implemented", artifact=None)
        with self.assertRaisesRegex(ValueError, "Unimplemented"):
            self.validate(data)

    def test_manual_rescue_unknown_counts_restarts_and_gaps_invalidate_endurance(self):
        for field in ("manual_rescues", "disarms", "automatic_rearms", "restarts", "observation_gaps"):
            for value in (1, None, False):
                data = self.specimen()
                self.pass_through(data, "endurance_15m")
                data["endurance_attempts"][0][field] = value
                with self.subTest(field=field, value=value), self.assertRaisesRegex(ValueError, "invalidated"):
                    self.validate(data)

    def test_endurance_needs_complete_duration_interval_and_linked_evidence(self):
        for change in ("short", "time", "record", "missing"):
            data = self.specimen()
            self.pass_through(data, "endurance_15m")
            if change == "short": data["endurance_attempts"][0]["continuous_elapsed_s"] = 899
            elif change == "time": data["endurance_attempts"][0]["ended_at"] = NOW
            elif change == "record": data["endurance_attempts"][0]["record_ids"] = []
            else: data["endurance_attempts"] = []
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.validate(data)

    def test_endurance_stages_need_distinct_nonoverlapping_attempts(self):
        for change in ("id", "overlap"):
            data = self.specimen()
            self.pass_through(data, "endurance_1h")
            previous, attempt = data["endurance_attempts"]
            if change == "id": attempt["attempt_id"] = previous["attempt_id"]
            else: attempt["started_at"] = previous["started_at"]
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.validate(data)

    def test_manifest_cannot_omit_runtime_identity(self):
        data = self.specimen()
        manifest = json.loads((self.root / "provenance.json").read_text())
        del manifest["payload"]["runtime_files"]
        manifest["fingerprint"] = hashlib.sha256(canonical(manifest["payload"]).encode()).hexdigest()
        data["provenance"].update(manifest=self.artifact("provenance.json", canonical(manifest)),
                                  fingerprint=manifest["fingerprint"])
        with self.assertRaisesRegex(ValueError, "provenance payload|runtime sources"):
            self.validate(data)

    def test_future_actual_timestamps_fail_closed(self):
        future = (VALIDATION_TIME + timedelta(seconds=1)).isoformat()
        for field in ("document", "instrument", "observation", "measurement", "review", "start", "end"):
            data = self.specimen()
            self.pass_through(data, "endurance_15m")
            if field == "document": data["recorded_at"] = future
            elif field == "instrument": data["instruments"][0]["calibrated_at"] = future
            elif field in ("observation", "measurement"):
                data["records"][0]["recorded_at"] = future
                data["records"][0]["kind"] = "observed" if field == "observation" else "measured"
            elif field == "review": data["gates"][0]["reviewed_at"] = future
            elif field == "start": data["endurance_attempts"][0]["started_at"] = future
            else: data["endurance_attempts"][0]["ended_at"] = future
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "Future actual timestamp"):
                self.validate(data)

    def test_review_cannot_precede_linked_measurement_or_endurance_end(self):
        for field in ("measurement", "endurance"):
            data = self.specimen()
            self.pass_through(data, "endurance_15m")
            if field == "measurement":
                data["records"][0]["recorded_at"] = (datetime.fromisoformat(NOW) + timedelta(seconds=1)).isoformat()
            else:
                next(g for g in data["gates"] if g["id"] == "endurance_15m")["reviewed_at"] = NOW
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "review must not precede"):
                self.validate(data)

    def test_endurance_cannot_begin_before_prerequisite_review(self):
        data = self.specimen()
        self.pass_through(data, "endurance_15m")
        next(g for g in data["gates"] if g["id"] == "trusted_reset")["reviewed_at"] = (
            datetime.fromisoformat(NOW) + timedelta(seconds=1)).isoformat()
        with self.assertRaisesRegex(ValueError, "prerequisite review"):
            self.validate(data)

    def test_calibration_cannot_postdate_linked_measurement(self):
        data = self.specimen()
        data["instruments"][0]["calibrated_at"] = (datetime.fromisoformat(NOW) + timedelta(seconds=1)).isoformat()
        with self.assertRaisesRegex(ValueError, "calibration must precede"):
            self.validate(data)

    def test_failed_or_unqualified_records_cannot_hide_future_actual_times(self):
        future = (VALIDATION_TIME + timedelta(seconds=1)).isoformat()
        for field in ("review", "end"):
            data = self.specimen()
            self.pass_through(data, "endurance_15m")
            gate = next(g for g in data["gates"] if g["id"] == "endurance_15m")
            gate["status"] = "failed"
            data["endurance_attempts"][0]["status"] = "failed"
            if field == "review": gate["reviewed_at"] = future
            else: data["endurance_attempts"][0]["ended_at"] = future
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "Future actual timestamp"):
                self.validate(data)

    def test_original_future_week_reproduction_is_rejected(self):
        data = self.specimen()
        self.pass_through(data, "endurance_1w")
        attempt = data["endurance_attempts"][-1]
        attempt["started_at"] = "2026-10-17T00:00:00+00:00"
        attempt["ended_at"] = "2026-10-24T00:00:00+00:00"
        with self.assertRaisesRegex(ValueError, "Future actual timestamp"):
            commissioning.validate_document(data, self.root, require_stage="endurance_1w",
                                             now=datetime(2026, 10, 13, tzinfo=timezone.utc))

    def test_duplicate_json_keys_and_nested_nonfinite_rejected(self):
        for contents in ('{"a": 1, "a": 2}', '{"a": NaN}'):
            path = self.root / "bad.json"
            path.write_text(contents)
            with self.assertRaises(ValueError):
                commissioning.read_json(path)
        data = self.specimen()
        data["records"][0]["values"] = {"nested": [float("inf")]}
        with self.assertRaises(ValueError):
            self.validate(data)

    def test_duplicate_and_naive_timestamps_rejected(self):
        data = self.specimen()
        data["records"].append(copy.deepcopy(data["records"][0]))
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            self.validate(data)
        data = self.specimen()
        data["records"][0]["recorded_at"] = "2026-10-13T00:00:00"
        with self.assertRaisesRegex(ValueError, "timezone"):
            self.validate(data)


if __name__ == "__main__":
    unittest.main()
