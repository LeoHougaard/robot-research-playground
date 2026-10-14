"""Read-only checks for commissioning records. Never arms, resets or qualifies hardware."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from playground.provenance import file_digest, relative_path, safe_directory, validate_manifest

STAGES = ("mechanical_fit", "electrical_cutoff", "servo_motion", "camera_calibration",
          "camera_freshness", "start_state", "trusted_reset", "endurance_15m",
          "endurance_1h", "endurance_24h", "endurance_72h", "endurance_1w")
DURATIONS = dict(zip(STAGES[7:], (900, 3600, 86400, 259200, 604800)))
INPUTS = {"geometry", "firmware", "calibration", "calibration_validation"}
TOP_FIELDS = {"schema_version", "template", "attempt_id", "recorded_at", "operator",
              "provenance", "boundary", "planned_measurements", "instruments", "records",
              "start_state_contract", "reset_routine", "gates", "endurance_attempts", "notes"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def nonempty(value, name):
    require(isinstance(value, str) and bool(value.strip()), f"{name} must be nonempty text")
    return value


def timestamp(value):
    nonempty(value, "Timestamp")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        require(result.utcoffset() is not None, "Timestamp must include a timezone")
    except (TypeError, ValueError) as exc:
        raise ValueError("Actual timestamp with timezone required") from exc
    return result


def number(value, name):
    require(type(value) in (int, float), f"{name} must be a finite number")
    try:
        valid = math.isfinite(value)
    except OverflowError:
        valid = False
    require(valid, f"{name} must be a finite number")
    return value


def unique(records, name):
    require(isinstance(records, list), f"{name} must be an array")
    result = {}
    for record in records:
        require(isinstance(record, dict), f"{name} entries must be objects")
        key = nonempty(record.get("id"), f"{name} id")
        require(key not in result, f"Duplicate {name} id: {key}")
        result[key] = record
    return result


def read_json(path):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, f"Duplicate JSON key: {key}")
            result[key] = value
        return result
    def invalid(value):
        raise ValueError(f"Nonfinite JSON number: {value}")
    require(file_digest(path)["bytes"] <= 4 * 1024 * 1024, "JSON record is oversized")
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs, parse_constant=invalid)


def artifact(root, reference):
    require(isinstance(reference, dict) and set(reference) == {"path", "sha256", "bytes"},
            "Artifact needs relative path, SHA256 and byte count")
    rel = relative_path(reference["path"])
    require(isinstance(reference["sha256"], str) and
            re.fullmatch(r"[0-9a-f]{64}", reference["sha256"]), "Invalid artifact SHA256")
    require(type(reference["bytes"]) is int and reference["bytes"] >= 0, "Invalid artifact byte count")
    path = root.joinpath(*rel.parts)
    expected = {key: reference[key] for key in ("sha256", "bytes")}
    require(file_digest(path) == expected, f"Artifact bytes changed: {rel}")
    return path


def validate_document(data, root, *, require_stage=None, now=None):
    """Validate claims for review. ``now`` is a trusted test clock, never evidence input."""
    validation_time = datetime.now(timezone.utc) if now is None else now
    require(isinstance(validation_time, datetime) and validation_time.utcoffset() is not None,
            "Validation clock must be timezone-aware")

    def actual_time(value, name):
        result = timestamp(value)
        require(result <= validation_time, f"Future actual timestamp: {name}")
        return result

    root = safe_directory(root)
    json.dumps(data, allow_nan=False)  # Includes nested record values, not just safety fields.
    require(isinstance(data, dict) and set(data) == TOP_FIELDS, "Unknown or incomplete commissioning schema")
    require(type(data["schema_version"]) is int and data["schema_version"] == 1, "Unknown schema version")
    require(type(data["template"]) is bool, "template must be a Boolean")
    require(require_stage is None or require_stage in STAGES, "Unknown required stage")
    boundary = data["boundary"]
    require(isinstance(boundary, dict) and set(boundary) ==
            {"max_total_supply_current_a", "motor_branch_fuse_a"}, "Missing prototype load boundary")
    require(0 < number(boundary["max_total_supply_current_a"], "Total supply limit") <= 2,
            "Prototype total supply limit may not exceed 2 A")
    require(number(boundary["motor_branch_fuse_a"], "Motor fuse") == 2, "Prototype motor fuse must be 2 A")
    planned = unique(data["planned_measurements"], "planned measurements")
    require(all(r.get("kind") == "planned" for r in planned.values()), "Plans must be labeled planned")
    require(isinstance(data["notes"], list), "notes must be an array")
    gates = unique(data["gates"], "gate")
    require(set(gates) == set(STAGES), "All commissioning gates must be present exactly once")
    for gate in gates.values():
        require(gate.get("status") in ("unqualified", "failed", "passed"), "Unknown gate status")
        require(isinstance(gate.get("record_ids"), list), "Gate record_ids must be an array")
    instruments = unique(data["instruments"], "instrument")
    records = unique(data["records"], "record")
    require(isinstance(data["endurance_attempts"], list), "endurance_attempts must be an array")
    for label in ("start_state_contract", "reset_routine"):
        routine = data[label]
        require(isinstance(routine, dict) and routine.get("implementation") in
                ("not_implemented", "implemented"), f"Invalid {label} implementation state")
    reset = data["reset_routine"]
    require(reset.get("automatic_rearm") is False and reset.get("autonomous_tether_unwind") is False,
            "Automatic rearm and autonomous tether unwinding are forbidden")
    provenance = data["provenance"]
    require(isinstance(provenance, dict) and set(provenance) == {"manifest", "fingerprint", "input_files"},
            "Invalid provenance reference")
    require(isinstance(provenance["input_files"], dict) and INPUTS.issubset(provenance["input_files"]),
            "Physical input file references required")
    if data["template"]:
        require(all(g["status"] == "unqualified" and not g["record_ids"] and g.get("reviewer") is None and
                    g.get("reviewed_at") is None for g in gates.values()),
                "Templates cannot claim any physical gate or evidence")
        require(not records and not instruments and not data["endurance_attempts"],
                "Templates cannot contain observed or measured evidence")
        require(all(data[k] is None for k in ("attempt_id", "recorded_at", "operator")),
                "Template specimen and observer must remain unspecified")
        require(provenance["manifest"] is None and provenance["fingerprint"] is None and
                all(v is None for v in provenance["input_files"].values()), "Template provenance must remain unset")
        require(all(data[k]["implementation"] == "not_implemented" and data[k].get("artifact") is None
                    for k in ("start_state_contract", "reset_routine")), "Template cannot claim reset implementation")
        require(require_stage is None, "Planning template has no measured qualification stage")
        return summary(gates, template=True)

    nonempty(data["attempt_id"], "Attempt ID")
    nonempty(data["operator"], "Operator")
    actual_time(data["recorded_at"], "document recorded_at")
    manifest_path = artifact(root, provenance["manifest"])
    manifest = validate_manifest(read_json(manifest_path))
    require(manifest["fingerprint"] == provenance["fingerprint"], "Manifest fingerprint mismatch")
    payload = manifest["payload"]
    require(payload.get("mode") == "physical", "Simulation provenance cannot qualify physical records")
    required_payload = {"mode", "config", "backend", "safety_limits", "tether_limit_rad", "inputs",
                        "runtime_files", "physical_attestation", "geometry"}
    require(required_payload.issubset(payload) and isinstance(payload["config"], dict) and
            isinstance(payload["safety_limits"], dict) and bool(payload["safety_limits"]) and
            isinstance(payload["runtime_files"], dict) and bool(payload["runtime_files"]),
            "Manifest lacks effective configuration, runtime sources or controller limits")
    nonempty(payload["backend"], "Provenance backend")
    for identity in payload["runtime_files"].values():
        require(isinstance(identity, dict) and set(identity) == {"sha256", "bytes"} and
                isinstance(identity["sha256"], str) and re.fullmatch(r"[0-9a-f]{64}", identity["sha256"]) and
                type(identity["bytes"]) is int and identity["bytes"] >= 0, "Invalid runtime source identity")
    require(isinstance(payload.get("inputs"), dict) and INPUTS.issubset(payload["inputs"]),
            "Manifest lacks required physical inputs")
    require(set(provenance["input_files"]) == set(payload["inputs"]), "Input copies must match manifest labels")
    for label, reference in provenance["input_files"].items():
        artifact(root, reference)
        require({k: reference[k] for k in ("sha256", "bytes")} == payload["inputs"][label],
                f"Input copy differs from manifest: {label}")
    calibration_times = {}
    record_times = {}
    review_times = {}
    for instrument in instruments.values():
        for key in ("description", "uncertainty"):
            nonempty(instrument.get(key), f"Instrument {key}")
        calibration_times[instrument["id"]] = actual_time(instrument.get("calibrated_at"), "instrument calibration")
        artifact(root, instrument.get("artifact"))
    for record in records.values():
        require(record.get("kind") in ("observed", "measured"), "Evidence must distinguish observed and measured")
        record_times[record["id"]] = actual_time(record.get("recorded_at"), "observed/measured record")
        require(type(record.get("operator_present")) is bool, "Record must state operator presence")
        for key in ("operator", "method"):
            nonempty(record.get(key), f"Record {key}")
        require(isinstance(record.get("values"), dict) and bool(record["values"]), "Record values/units are required")
        ids = record.get("instrument_ids")
        require(isinstance(ids, list) and all(isinstance(i, str) and i in instruments for i in ids),
                "Record references missing instrument")
        require(record["kind"] != "measured" or bool(ids), "Measured records need calibrated instruments")
        require(all(calibration_times[i] <= record_times[record["id"]] for i in ids),
                "Instrument calibration must precede its linked measurement")
        artifact(root, record.get("artifact"))
    for index, stage in enumerate(STAGES):
        gate = gates[stage]
        ids = gate["record_ids"]
        require(all(isinstance(i, str) and i in records for i in ids), "Gate references missing evidence")
        if gate.get("reviewed_at") is not None:
            review_times[stage] = actual_time(gate["reviewed_at"], "gate review")
            require(all(record_times[i] <= review_times[stage] for i in ids),
                    "Gate review must not precede linked observations or measurements")
        if gate["status"] != "passed":
            continue
        require(all(gates[s]["status"] == "passed" for s in STAGES[:index]), "Earlier commissioning gate is unqualified")
        require(any(records[i]["kind"] == "measured" for i in ids), "Passed gate requires measured evidence")
        if index < 7:
            require(all(records[i]["operator_present"] for i in ids), "Bench gates require operator-present evidence")
        nonempty(gate.get("reviewer"), "Gate reviewer")
        require(stage in review_times, "Passed gate needs an actual review timestamp")
        require(all(review_times[s] <= review_times[stage] for s in STAGES[:index]),
                "Gate reviews must follow prerequisite reviews")
    for label, stage in (("start_state_contract", "start_state"), ("reset_routine", "trusted_reset")):
        routine = data[label]
        if routine["implementation"] == "implemented":
            artifact(root, routine.get("artifact"))
            require({k: routine["artifact"][k] for k in ("sha256", "bytes")} == payload["inputs"].get(label),
                    f"{label} must be bound into runtime provenance")
        else:
            require(routine.get("artifact") is None and gates[stage]["status"] != "passed",
                    f"Unimplemented {label} cannot pass its gate")

    attempts = {}
    attempt_ids = set()
    for attempt in data["endurance_attempts"]:
        require(isinstance(attempt, dict), "Endurance attempt must be an object")
        stage = attempt.get("stage")
        require(stage in DURATIONS and stage not in attempts, "Invalid or duplicate endurance stage")
        attempts[stage] = attempt
        require(attempt.get("status") in ("unqualified", "failed", "passed"), "Invalid endurance status")
        attempt_id = nonempty(attempt.get("attempt_id"), "Endurance attempt ID")
        require(attempt_id not in attempt_ids, "Endurance stages require distinct attempts")
        attempt_ids.add(attempt_id)
        require(attempt.get("fingerprint") == provenance["fingerprint"], "Endurance configuration drift")
        ids = attempt.get("record_ids")
        require(isinstance(ids, list) and all(isinstance(i, str) and i in records for i in ids),
                "Endurance references missing records")
        start = actual_time(attempt["started_at"], "endurance start") if attempt.get("started_at") is not None else None
        end = actual_time(attempt["ended_at"], "endurance end") if attempt.get("ended_at") is not None else None
        require(start is None or end is None or end >= start, "Endurance ends before it starts")
        if attempt["status"] != "passed":
            continue
        require(gates[stage]["status"] == "passed", "Endurance attempt lacks passed reviewed gate")
        require(bool(ids) and set(ids).issubset(gates[stage]["record_ids"]) and
                any(records[i]["kind"] == "measured" for i in ids), "Endurance needs gate-linked measured records")
        elapsed = number(attempt.get("continuous_elapsed_s"), "Continuous endurance duration")
        require(elapsed >= DURATIONS[stage], "Endurance duration is too short")
        require(start is not None and end is not None, "Passed endurance needs actual start and end timestamps")
        require(review_times[stage] >= end, "Gate review must not precede endurance completion")
        previous_stage = STAGES[STAGES.index(stage) - 1]
        require(start >= review_times[previous_stage], "Endurance must start after its prerequisite review")
        wall_elapsed = (end - start).total_seconds()
        require(wall_elapsed >= DURATIONS[stage] and elapsed <= wall_elapsed, "Invalid endurance time interval")
        for key in ("manual_rescues", "disarms", "automatic_rearms", "restarts", "observation_gaps"):
            require(type(attempt.get(key)) is int and attempt[key] == 0,
                    f"Endurance invalidated by unknown or nonzero {key}")
    previous_end = None
    for stage in DURATIONS:
        if gates[stage]["status"] == "passed":
            require(stage in attempts and attempts[stage]["status"] == "passed", "Passed endurance gate lacks complete attempt")
            start = timestamp(attempts[stage]["started_at"])
            require(previous_end is None or start >= previous_end, "Endurance stages may not overlap or run out of order")
            previous_end = timestamp(attempts[stage]["ended_at"])
    if require_stage is not None:
        require(gates[require_stage]["status"] == "passed", f"Stage remains unqualified: {require_stage}")
    return summary(gates, template=False)


def summary(gates, *, template):
    return {"document_valid": True, "template": template,
            "passed_record_stages": [s for s in STAGES if gates[s]["status"] == "passed"],
            "unqualified_stages": [s for s in STAGES if gates[s]["status"] != "passed"],
            "eligible_for_actuation": False, "physical_hardware_verified": False, "unattended_qualified": False,
            "qualification": "unqualified" if template else "requires independent physical review",
            "warning": "Record consistency only. No hardware truth, safety qualification or actuation authorization is established."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path)
    parser.add_argument("--require-stage", choices=STAGES)
    args = parser.parse_args()
    try:
        result = validate_document(read_json(args.evidence), args.evidence.absolute().parent,
                                   require_stage=args.require_stage)
    except (ValueError, OSError, TypeError, KeyError) as exc:
        print(json.dumps({"document_valid": False, "eligible_for_actuation": False, "error": str(exc)}))
        return 1
    print(json.dumps(result, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
