"""Opt-in real local-model qualification. No downloads, service starts, or hardware.

Endpoint behavior alone is never proof of real inference. A full pass additionally
requires reviewed local provenance, matching server identity, current Docker
qualification and measured host/device resource limits. Unit fixtures cannot emit
an accepted report through the default (behavior-only) evaluator.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import ipaddress
import json
import math
import os
from pathlib import Path
import platform
import re
import secrets
import statistics
import sys
import tempfile
import threading
import time
import urllib.parse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from playground.archive import canonical
from playground.llm import LocalModel
from playground.research import MISSION, ResearchSupervisor, simulation_controller
from playground.sandbox import DockerSandbox, bounded_run


class QualificationError(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise QualificationError(message)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024**2), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path, limit=1024**2):
    path = Path(path)
    require(path.is_file() and path.stat().st_size <= limit, "Missing or oversized JSON input: " + str(path))
    return json.loads(path.read_text(encoding="utf-8"))


def utc_now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def current_timestamp(value, max_age_s=86400):
    parsed = dt.datetime.fromisoformat(value)
    require(parsed.tzinfo is not None, "Evidence timestamp must include a timezone")
    age = (dt.datetime.now(dt.timezone.utc) - parsed).total_seconds()
    require(0 <= age <= max_age_s, "Evidence is stale or future-dated")


def source_fingerprints():
    paths = list((ROOT / "src/playground").glob("*.py"))
    paths += [Path(__file__).resolve(), ROOT / "scripts/qualify_sandbox.py"]
    return {p.relative_to(ROOT).as_posix(): sha256(p) for p in sorted(paths)}


SANDBOX_TESTS = {
    "workspace_roundtrip", "filesystem_and_authority", "network_denied", "memory_limit",
    "process_limit", "workspace_limit", "stdout_limit", "timeout_and_cleanup",
    "artifact_symlink", "artifact_hardlink", "artifact_fifo",
}


def validate_sandbox_report(path, image):
    value = read_json(path)
    require(value.get("schema_version") == 1 and value.get("passed") is True,
            "A passing real Docker qualification report is required")
    require(value.get("physical_hardware_verified") is False, "Unexpected physical qualification claim")
    require(value.get("image") == image, "Sandbox image does not match qualification")
    require(value.get("sandbox_source_sha256") == sha256(ROOT / "src/playground/sandbox.py"),
            "Sandbox source changed since qualification")
    require(value.get("checker_source_sha256") == sha256(ROOT / "scripts/qualify_sandbox.py"),
            "Sandbox qualifier changed since qualification")
    current_timestamp(value["completed_time_utc"])
    current_timestamp(value["time_utc"])
    tests = value.get("tests", [])
    require(SANDBOX_TESTS <= {x.get("name") for x in tests if x.get("passed") is True},
            "Sandbox report lacks required successful checks")
    require(all(x.get("passed") is True for x in tests), "Sandbox report contains a failed check")
    require(bool(value.get("inspected_containers")) and bool(value.get("docker", {}).get("ID")),
            "Sandbox report lacks real inspected container/daemon evidence")
    return {"path": str(Path(path).resolve()), "sha256": sha256(path),
            "image_id": value["image_id"], "docker_id": value["docker"]["ID"]}


def process_identity(pid):
    # /proc stat comm can contain spaces and closing parentheses.
    fields = (Path("/proc") / str(pid) / "stat").read_text().rsplit(")", 1)[1].split()
    return {"pid": pid, "ppid": int(fields[1]), "start_ticks": int(fields[19])}


def process_tree(pid):
    rows = {}
    for directory in Path("/proc").iterdir():
        if directory.name.isdigit():
            try:
                row = process_identity(int(directory.name))
                rows[row["pid"]] = row
            except (OSError, ValueError, IndexError):
                continue  # An unrelated short-lived process may have exited.
    require(pid in rows, "Model server exited or process identity is unreadable")
    selected = {pid}
    while True:
        added = {p for p, row in rows.items() if row["ppid"] in selected} - selected
        if not added:
            return [rows[p] for p in sorted(selected)]
        selected |= added


def listening_inodes(address, port):
    """Match the connect destination, never just its port.

    Wildcard and IPv4-mapped/dual-stack listeners are intentionally unsupported:
    /proc alone cannot prove their IPV6_V6ONLY/reuseport routing semantics.
    """
    result = set()
    for name, version in (("tcp", 4), ("tcp6", 6)):
        path = Path("/proc/net") / name
        if not path.exists():
            continue
        for line in path.read_text().splitlines()[1:]:
            fields = line.split()
            encoded, socket_port = fields[1].rsplit(":", 1)
            if fields[3] != "0A" or int(socket_port, 16) != port:
                continue
            raw = bytes.fromhex(encoded)
            if sys.byteorder == "little":
                raw = b"".join(raw[i:i + 4][::-1] for i in range(0, len(raw), 4))
            bound = ipaddress.ip_address(raw)
            potentially_dual_stack = address.version == 4 and version == 6 and (
                bound.is_unspecified or bound.ipv4_mapped == address)
            require(not potentially_dual_stack, "Ambiguous wildcard/dual-stack endpoint listener is unsupported")
            if version != address.version:
                continue
            require(not bound.is_unspecified, "Wildcard endpoint listeners are unsupported; bind the exact loopback IP")
            if bound == address:
                result.add(fields[9])
    require(len(result) <= 1, "Multiple matching endpoint listeners are ambiguous")
    return result


def verify_listener(pid, base_url):
    endpoint = urllib.parse.urlsplit(base_url)
    require(endpoint.hostname != "localhost", "Qualification requires an explicit loopback IP, not localhost")
    address = ipaddress.ip_address(endpoint.hostname)
    require(address.is_loopback and not getattr(address, "scope_id", None), "Explicit unscoped loopback endpoint required")
    port = endpoint.port or 80
    wanted = listening_inodes(address, port)
    require(bool(wanted), "No exact local listening socket matches the model endpoint")
    matches = []
    for row in process_tree(pid):
        try:
            inodes = set()
            for descriptor in (Path("/proc") / str(row["pid"]) / "fd").iterdir():
                try:
                    target = os.readlink(descriptor)
                except FileNotFoundError:
                    continue
                if target.startswith("socket:[") and target[8:-1] in wanted:
                    inodes.add(target[8:-1])
            for inode in inodes:
                matches.append({**row, "socket_inode": inode, "address": str(address),
                    "address_family": address.version, "port": port,
                    "command_sha256": sha256(Path("/proc") / str(row["pid"]) / "cmdline"),
                    "executable": str((Path("/proc") / str(row["pid"]) / "exe").resolve(strict=True))})
        except FileNotFoundError:
            continue
    require(len(matches) == 1, "The declared model process tree must uniquely own the exact endpoint listener")
    return matches[0]


def verify_runtime_identity(provenance):
    pid = provenance["pid"]
    require(process_identity(pid) == provenance["process_identity"], "Model process identity changed")
    require(sha256(Path("/proc") / str(pid) / "cmdline") == provenance["command_sha256"],
            "Model server launch command changed")
    require(verify_listener(pid, provenance["base_url"]) == provenance["listener_identity"],
            "Model endpoint listener identity changed")


def revalidate_inputs(report, config_path, provenance_path, sandbox_path):
    """Rehash trusted input bytes before a pass; no cached stat/declared hash substitute."""
    require(sha256(config_path) == report["config"]["sha256"]
            and read_json(config_path) == report["config"]["input"],
            "Input configuration changed during qualification")
    current = validate_provenance(provenance_path, report["model"]["id"], report["model"]["base_url"])
    require(current == report["provenance"], "Model provenance or listener changed during qualification")
    require(sha256(sandbox_path) == report["sandbox"]["sha256"], "Sandbox evidence changed during qualification")
    require(report["source_sha256"] == source_fingerprints(), "Runtime source changed during qualification")


def validate_provenance(path, model, base_url):
    value = read_json(path)
    require(value.get("kind") == "operator_attested_real_local_model", "Real model operator attestation is missing")
    require(value.get("model_id") == model, "Model provenance ID differs from requested model")
    for key in ("model_revision", "runtime_version", "reviewed_by"):
        require(isinstance(value.get(key), str) and 1 <= len(value[key]) <= 512, "Missing provenance field: " + key)
    current_timestamp(value["reviewed_at"])
    require(value.get("acceleration") in {"cpu", "cuda"}, "Reviewed acceleration must be cpu or cuda")
    require(isinstance(value.get("launch_config"), dict) and bool(value["launch_config"]), "Reviewed launch configuration is required")
    pid = value.get("pid")
    require(type(pid) is int and pid > 1 and pid != os.getpid(), "Invalid model server PID")
    identity = process_identity(pid)
    require(identity["start_ticks"] == value.get("process_start_ticks"), "Model server PID was recycled or changed")
    require(sha256(Path("/proc") / str(pid) / "cmdline") == value.get("command_sha256"),
            "Model server launch command changed")
    checked_files = {}
    for group in ("model_files", "runtime_files"):
        files = value.get(group)
        require(isinstance(files, list) and 1 <= len(files) <= 256, "Local model and runtime file fingerprints are required")
        checked_files[group] = []
        for item in files:
            filename = Path(item["path"])
            require(filename.is_absolute() and filename.is_file(), "Provenance files must be existing absolute paths")
            require(re.fullmatch(r"[a-f0-9]{64}", item.get("sha256", "")) is not None, "Invalid provenance SHA-256")
            require(sha256(filename) == item["sha256"], "Provenance file hash mismatch: " + str(filename))
            checked_files[group].append({"path": str(filename.resolve()), "bytes": filename.stat().st_size, "sha256": item["sha256"]})
    executable = str((Path("/proc") / str(pid) / "exe").resolve(strict=True))
    require(executable in {item["path"] for item in checked_files["runtime_files"]},
            "Runtime fingerprints must include the actual server executable")
    listener = verify_listener(pid, base_url)
    require(listener["executable"] in {item["path"] for item in checked_files["runtime_files"]},
            "Runtime fingerprints must include the actual listener executable")
    return {**value, **checked_files, "base_url": base_url, "process_identity": identity, "listener_identity": listener,
            "manifest_sha256": sha256(path), "identity_trust": "operator attestation plus local file/process checks; not cryptographic inference proof"}


class ResourceMonitor:
    """Read-only sampler; aborts new work on limits, never kills the model service."""
    def __init__(self, provenance, *, max_rss_bytes, min_available_bytes, max_device_bytes=None):
        self.provenance = provenance
        self.max_rss_bytes, self.min_available_bytes = max_rss_bytes, min_available_bytes
        self.max_device_bytes = max_device_bytes
        self.stop = threading.Event()
        self.thread = None
        self.error = None
        self.last_gpu = -math.inf
        self.summary = {"sample_count": 0, "peak_process_tree_rss_bytes": None,
                        "minimum_host_available_bytes": None, "peak_device_used_bytes": None,
                        "device_sample_count": 0, "sampling_interval_s": .2,
                        "scope": "sampled process-tree RSS plus host MemAvailable; CUDA totals include other workloads",
                        "transient_peaks_may_be_missed": True}

    def sample(self):
        pid = self.provenance["pid"]
        verify_runtime_identity(self.provenance)
        rss = 0
        for row in process_tree(pid):
            try:
                fields = (Path("/proc") / str(row["pid"]) / "status").read_text().splitlines()
                rss += next(int(line.split()[1]) * 1024 for line in fields if line.startswith("VmRSS:"))
            except (FileNotFoundError, ProcessLookupError):
                if row["pid"] == pid:
                    raise QualificationError("Model process exited during resource sampling")
            except StopIteration as exc:
                raise QualificationError("Process RSS is unavailable; memory cannot be qualified") from exc
        values = {line.split(":", 1)[0]: line.split()[1] for line in Path("/proc/meminfo").read_text().splitlines()}
        available = int(values["MemAvailable"]) * 1024
        old = self.summary
        old["sample_count"] += 1
        old["peak_process_tree_rss_bytes"] = max(old["peak_process_tree_rss_bytes"] or 0, rss)
        old["minimum_host_available_bytes"] = min(old["minimum_host_available_bytes"] if old["minimum_host_available_bytes"] is not None else available, available)
        require(rss <= self.max_rss_bytes, "Model process-tree RSS limit exceeded")
        require(available >= self.min_available_bytes, "Host available-memory reserve violated")
        if self.provenance["acceleration"] == "cuda" and time.monotonic() - self.last_gpu >= 1:
            require(self.max_device_bytes is not None, "CUDA qualification requires --max-device-memory-bytes")
            result = bounded_run(["nvidia-smi", "--query-gpu=uuid,memory.used,memory.total", "--format=csv,noheader,nounits"], timeout_s=2, output_limit=16384)
            require(result.exit_code == 0 and not result.timed_out, "CUDA memory telemetry unavailable")
            devices = []
            for line in result.stdout.strip().splitlines():
                parts = [part.strip() for part in line.split(",")]
                require(len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit(), "CUDA memory telemetry is unknown; cannot qualify memory")
                devices.append({"uuid": parts[0], "used_bytes": int(parts[1]) * 1024**2, "total_bytes": int(parts[2]) * 1024**2})
            require(bool(devices), "No measured CUDA devices")
            used = sum(device["used_bytes"] for device in devices)
            old["device_sample_count"] += 1
            old["devices_last_sample"] = devices
            old["peak_device_used_bytes"] = max(old["peak_device_used_bytes"] or 0, used)
            self.last_gpu = time.monotonic()
            require(used <= self.max_device_bytes, "Aggregate device-memory limit exceeded")

    def check(self):
        if self.error is not None:
            raise QualificationError(self.error)
        verify_runtime_identity(self.provenance)

    def _loop(self):
        while not self.stop.wait(.2):
            try:
                self.sample()
            except Exception as exc:
                self.error = str(exc)
                return

    def __enter__(self):
        self.sample()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=3)
        if self.thread and self.thread.is_alive():
            self.error = "Resource sampler did not terminate within its bounded deadline"


class QualificationSupervisor(ResearchSupervisor):
    def __init__(self, *args, sandbox, **kwargs):
        super().__init__(*args, **kwargs)
        self.qualification_sandbox = sandbox

    def sandbox_command(self, command, timeout=30):
        remaining = min(timeout, self.deadline - time.monotonic())
        require(remaining >= 1, "Insufficient remaining sandbox time budget")
        return self.qualification_sandbox.execute(self.current_workspace, command, timeout_s=remaining)


def measured_trial(supervisor, result, max_error_m):
    require(result.get("source") == "simulation" and result.get("status") != "inconclusive", "No completed simulation experiment")
    row = supervisor.archive.db.execute("SELECT * FROM experiments WHERE id=?", (result.get("id"),)).fetchone()
    require(row is not None and row["status"] == "completed" and row["source"] == "simulation", "Experiment lacks committed trusted evidence")
    before, after = json.loads(row["before_json"])["robot"], json.loads(row["after_json"])["robot"]
    motion = sum(abs(after[key] - before[key]) for key in ("x", "y", "heading"))
    require(motion > 1e-5, "Experiment did not measurably move the simulator")
    require(row["error"] is not None and math.isfinite(row["error"]) and row["error"] <= max_error_m,
            "Measured prediction error exceeds qualification tolerance")
    return {"id": row["id"], "error_m_equivalent": row["error"], "motion": motion}


def exercise(model, supervisor, report, *, max_seconds, max_decision_s, max_error_m, monitor=None):
    """Run behavior only. This function deliberately never sets report.passed."""
    supervisor.deadline = time.monotonic() + max_seconds
    run_id = supervisor.archive.start_run("simulation", supervisor.config)
    report["run_id"] = run_id
    report["latency_seconds"] = []
    report["transcript"] = []
    report["checks"] = []
    status = "failed"
    active_generation = None
    calls = 0
    messages = []

    def call(stage, tool, instruction):
        nonlocal calls
        require(calls < 12, "Structured decision count budget exhausted")
        require(not supervisor.controller.backend.is_real, "Qualification refuses physical backends")
        if monitor:
            monitor.check()
        remaining = supervisor.deadline - time.monotonic()
        require(remaining > 0, "Qualification run deadline exceeded")
        report["active_stage"] = stage
        messages.append({"role": "user", "content": instruction})
        started = time.monotonic()
        decision, raw = model.decide(messages, timeout_seconds=min(max_decision_s, remaining))
        elapsed = time.monotonic() - started
        calls += 1
        report["latency_seconds"].append(elapsed)
        require(elapsed <= max_decision_s and time.monotonic() < supervisor.deadline, "Decision latency/run deadline exceeded")
        if monitor:
            monitor.check()
        require(decision["tool"] == tool, f"{stage}: expected {tool}, received {decision['tool']}")
        record = {"stage": stage, "decision": decision, "latency_s": elapsed}
        report["transcript"].append(record)
        try:
            result = supervisor.tool(tool, decision.get("arguments", {}))
        except (ValueError, KeyError) as exc:
            result = {"error": str(exc)}
        record["result"] = result
        messages.extend([{"role": "assistant", "content": raw}, {"role": "user", "content": canonical(result)}])
        report.pop("active_stage", None)
        return decision, result

    def check(name, condition=True, detail=None):
        require(condition, name + " did not pass")
        report["checks"].append({"name": name, "passed": True, "detail": detail})

    def generation():
        nonlocal active_generation, messages
        supervisor.check_storage()
        supervisor.storage.begin_generation()
        active_generation = supervisor.archive.start_generation(run_id)
        supervisor.active_generation = active_generation
        supervisor.current_workspace = supervisor.state_dir / "workspaces" / active_generation
        supervisor.current_workspace.mkdir(parents=True)
        supervisor.storage.active_workspace = supervisor.current_workspace
        messages = [{"role": "system", "content": MISSION}, {"role": "user", "content":
            "This is an operator-run simulation-only qualification. Follow the requested stages. "
            "Use only the specified tool at each stage. Preserve a working policy program for a new successor. "
            "The simulator's nominal speed is 0.075 m/s and wheel track is 0.154 m. "
            "No physical hardware exists. Challenge nonce: " + secrets.token_hex(16)}]
        report.setdefault("generation_ids", []).append(active_generation)

    try:
        generation()
        _, observation = call("observe", "observe", "Observe the current simulation scene with observe {}.")
        check("structured_observation", observation.get("real") is False and "robot" in observation)
        before_count = supervisor.archive.db.execute("SELECT COUNT(*) FROM experiments").fetchone()[0]
        before_pose = supervisor.controller.observe()["robot"]
        decision, rejected = call("rejection", "experiment",
            "Test rejection in this simulator only. Submit experiment with left=2, right=0.25 and duration_s=0.2, "
            "and a finite prediction using the observed pose. This deliberately exceeds the fixed wheel limit and must not move the simulator.")
        check("rejected_action_is_harmless", decision["arguments"].get("action", {}).get("left") == 2
              and "fixed V1 limits" in rejected.get("error", "")
              and before_count == supervisor.archive.db.execute("SELECT COUNT(*) FROM experiments").fetchone()[0]
              and before_pose == supervisor.controller.observe()["robot"])
        _, trial = call("recovery", "experiment",
            "Recover from that error. Submit a valid moving experiment with both wheel values between 0.2 and 0.5, "
            "duration_s=0.2, and a numerical pose prediction with uncertainty_m=0.03. Use the real last observation.")
        check("recovery_measured_experiment", detail=measured_trial(supervisor, trial, max_error_m))
        _, written = call("write_policy", "run_code",
            "Use run_code to write /workspace/policy.py. It must use Python stdlib, read /workspace/observation.json, "
            "and print only a JSON object with action and prediction. Choose both wheels=0.25 and duration_s=0.2, "
            "predict short motion from the input pose using speed 0.075 m/s, and uncertainty_m=0.03. "
            "Compute from the input rather than hardcoding a pose. Do not execute the policy yet. timeout_s=20.")
        policy = supervisor.current_workspace / "policy.py"
        check("sandbox_program_created", written.get("exit_code") == 0 and written.get("timed_out") is False
              and policy.is_file() and not policy.is_symlink() and 0 < policy.stat().st_size <= 32768)
        _, finished = call("producer_finish", "finish", "Finish this generation with a short evidence-based summary and tell the successor to inherit policy.py.")
        check("producer_finished", finished.get("finished") is True)
        supervisor.storage.finish_generation()
        supervisor.archive_workspace()
        artifact = supervisor.archive.db.execute("SELECT * FROM artifacts WHERE generation_id=? AND path LIKE '%/policy.py'", (active_generation,)).fetchone()
        require(artifact is not None, "Policy did not enter the inherited artifact archive")
        original = Path(supervisor.state_dir) / artifact["path"]
        original_sha = sha256(original)
        supervisor.archive.end_generation(active_generation, "completed", "Qualification producer completed")
        active_generation = None
        supervisor.storage.end_generation()
        # A changed simulator pose makes a hardcoded predecessor pose unusable.
        backend = supervisor.controller.backend
        backend.x = .30 + secrets.randbelow(40) / 1000
        backend.y = .65 + secrets.randbelow(40) / 1000
        backend.theta = 1.3 + secrets.randbelow(40) / 100
        report["successor_start_pose"] = supervisor.controller.observe()["robot"]
        generation()  # New model conversation; no predecessor messages or filename shortcut.
        _, listed = call("successor_list", "artifact_list", "You are a fresh successor. List inherited artifacts to locate the predecessor's policy.py.")
        check("successor_discovers_artifact", any(x.get("id") == artifact["id"] for x in listed.get("artifacts", [])))
        decision, imported = call("successor_import", "artifact_import", "Import the policy.py artifact by its ID from your artifact_list result.")
        check("successor_imports_same_artifact", decision["arguments"].get("id") == artifact["id"]
              and imported.get("sha256") == original_sha)
        decision, successor_trial = call("successor_policy", "run_policy", "Use run_policy with the exact imported filename. The supervisor supplies a new observation.json and validates the proposed action.")
        check("successor_uses_inherited_program", decision["arguments"].get("filename") == imported.get("filename"),
              measured_trial(supervisor, successor_trial, max_error_m))
        check("original_artifact_unchanged", sha256(original) == original_sha)
        _, finished = call("successor_finish", "finish", "Finish with what the inherited program actually achieved and its simulation-only limits.")
        check("successor_finished", finished.get("finished") is True)
        supervisor.storage.finish_generation()
        supervisor.archive_workspace()
        supervisor.archive.end_generation(active_generation, "completed", "Qualification successor completed")
        active_generation = None
        supervisor.storage.end_generation()
        report["artifact"] = {"id": artifact["id"], "sha256": original_sha, "path": artifact["path"], "successor_filename": imported["filename"]}
        require(time.monotonic() < supervisor.deadline, "Qualification run deadline exceeded")
        if monitor:
            monitor.check()
        check("archive_integrity", supervisor.archive.db.execute("PRAGMA integrity_check").fetchone()[0] == "ok")
        report["behavior_passed"] = True
        status = "completed"
    finally:
        supervisor.storage.finish_generation()
        if active_generation:
            supervisor.archive.end_generation(active_generation, "failed", "Qualification failed; inspect report")
        supervisor.archive.end_run(run_id, status)
        supervisor.controller.stop("qualification ended")
        supervisor.archive.export_digest()
        times = report["latency_seconds"]
        report["latency_summary"] = {"decisions": len(times), "max_s": max(times) if times else None,
                                     "median_s": statistics.median(times) if times else None,
                                     "total_s": sum(times)}


def set_outcome(report, *, real_provenance=False, resources_checked=False, sandbox_checked=False):
    report["passed"] = bool(report.get("behavior_passed") is True and real_provenance
                            and resources_checked and sandbox_checked and not report.get("error"))
    report["real_model_qualified"] = report["passed"]
    report["physical_hardware_verified"] = False


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "configs/research.json")
    parser.add_argument("--model", required=True, help="Exact served model ID, never auto-discovered")
    parser.add_argument("--image", required=True, help="Reviewed installed digest-pinned sandbox image")
    parser.add_argument("--sandbox-report", type=Path, required=True)
    parser.add_argument("--provenance", type=Path, required=True, help="Operator-reviewed actual-host model manifest")
    parser.add_argument("--state-root", type=Path, default=ROOT / "state/qualification")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--max-run-seconds", type=float, default=600)
    parser.add_argument("--max-decision-seconds", type=float, default=60)
    parser.add_argument("--max-prediction-error-m", type=float, default=.03)
    parser.add_argument("--max-process-rss-bytes", type=int, required=True)
    parser.add_argument("--min-host-available-bytes", type=int, required=True)
    parser.add_argument("--max-device-memory-bytes", type=int)
    args = parser.parse_args(argv)
    report = {"schema_version": 1, "evidence_kind": "real_local_model_qualification",
              "time_utc": utc_now(), "platform": platform.platform(), "source_sha256": source_fingerprints(),
              "behavior_passed": False, "passed": False, "real_model_qualified": False,
              "physical_hardware_verified": False}
    supervisor = monitor = None
    try:
        require(platform.system() == "Linux" and os.getuid() != 0, "Run as a non-root Linux user on the actual model/Docker host")
        require(not args.report.exists(), "Report destination already exists; preserve prior evidence")
        require(0 < args.max_decision_seconds <= 120 and math.isfinite(args.max_decision_seconds), "Decision budget must be 0..120 seconds")
        require(0 < args.max_run_seconds <= 1800 and math.isfinite(args.max_run_seconds), "Run budget must be 0..1800 seconds")
        require(0 < args.max_prediction_error_m <= .1 and math.isfinite(args.max_prediction_error_m), "Prediction tolerance must be 0..0.1 m-equivalent")
        require(args.max_process_rss_bytes > 0 and args.min_host_available_bytes > 0, "Explicit positive memory limits are required")
        require(args.max_device_memory_bytes is None or args.max_device_memory_bytes > 0, "Device memory limit must be positive")
        config = read_json(args.config)
        require(config.get("mode") == "simulation", "Qualification accepts only a simulation configuration")
        llm = config["llm"]
        model = LocalModel(llm["base_url"], model=args.model, timeout_seconds=args.max_decision_seconds,
                           max_tokens=llm.get("max_tokens", 1024))
        report["model"] = {"base_url": model.base_url, "id": args.model, "temperature": .3, "max_tokens": model.max_tokens}
        report["config"] = {"path": str(args.config.resolve()), "sha256": sha256(args.config), "input": config,
                            "qualification_limits": {key: getattr(args, key) for key in (
                                "max_run_seconds", "max_decision_seconds", "max_prediction_error_m",
                                "max_process_rss_bytes", "min_host_available_bytes", "max_device_memory_bytes")}}
        report["sandbox"] = validate_sandbox_report(args.sandbox_report, args.image)
        DockerSandbox(args.image)  # Validate reviewed image before any Docker command.
        # A report from a different daemon cannot authorize local execution.
        require(not os.environ.get("DOCKER_CONTEXT"), "Unset DOCKER_CONTEXT for unambiguous local Docker qualification")
        endpoint = os.environ.get("DOCKER_HOST")
        if endpoint is None:
            found = bounded_run(["docker", "context", "inspect", "--format", "{{json .Endpoints.docker.Host}}"], timeout_s=5, output_limit=4096)
            require(found.exit_code == 0 and not found.timed_out, "Cannot resolve local Docker context")
            endpoint = json.loads(found.stdout)
        require(isinstance(endpoint, str) and endpoint.startswith("unix:///") and endpoint != "unix:///", "A local Unix-socket Docker daemon is required")
        def docker_runner(argv, **kwargs):
            require(not os.environ.get("DOCKER_CONTEXT"), "DOCKER_CONTEXT changed during qualification")
            require(argv[0] == "docker", "Qualification sandbox only invokes Docker")
            return bounded_run(["docker", "--host", endpoint, *argv[1:]], **kwargs)
        sandbox = DockerSandbox(args.image, runner=docker_runner)
        daemon = docker_runner(["docker", "info", "--format", "{{.ID}}"], timeout_s=5, output_limit=4096)
        require(daemon.exit_code == 0 and not daemon.timed_out and daemon.stdout.strip() == report["sandbox"]["docker_id"], "Current Docker daemon differs from qualification")
        image = docker_runner(["docker", "image", "inspect", args.image, "--format", "{{.Id}}"], timeout_s=5, output_limit=4096)
        require(image.exit_code == 0 and not image.timed_out and image.stdout.strip() == report["sandbox"]["image_id"], "Current installed image differs from qualification")
        report["provenance"] = validate_provenance(args.provenance, args.model, model.base_url)
        verify_runtime_identity(report["provenance"])
        models = model._request("/models", timeout_seconds=args.max_decision_seconds)
        verify_runtime_identity(report["provenance"])
        require(args.model in {x.get("id") for x in models.get("data", [])}, "Exact requested model is not listed by the endpoint")
        args.state_root.mkdir(parents=True, exist_ok=True)
        state = Path(tempfile.mkdtemp(prefix="local-model-", dir=args.state_root))
        report["state_dir"] = str(state.resolve())
        controller, clock = simulation_controller()
        effective = {**config, "state_dir": str(state.resolve()), "qualification": True,
                     "llm": {**llm, "model": args.model, "timeout_seconds": args.max_decision_seconds, "max_tokens": model.max_tokens}}
        report["config"]["effective"] = effective
        supervisor = QualificationSupervisor(state, controller, clock=clock, config=effective, sandbox=sandbox)
        supervisor.start_robot(operator_confirmed=True)
        monitor = ResourceMonitor(report["provenance"], max_rss_bytes=args.max_process_rss_bytes,
                                  min_available_bytes=args.min_host_available_bytes,
                                  max_device_bytes=args.max_device_memory_bytes)
        with monitor:
            exercise(model, supervisor, report, max_seconds=args.max_run_seconds,
                     max_decision_s=args.max_decision_seconds, max_error_m=args.max_prediction_error_m, monitor=monitor)
        monitor.check()
        revalidate_inputs(report, args.config, args.provenance, args.sandbox_report)
        verify_runtime_identity(report["provenance"])
        set_outcome(report, real_provenance=True, resources_checked=True, sandbox_checked=True)
    except (Exception, KeyboardInterrupt) as exc:
        report["error"] = str(exc)
        set_outcome(report)
    finally:
        if monitor:
            report["resources"] = monitor.summary
        if supervisor:
            try:
                supervisor.close()
            except Exception as exc:
                report["error"] = "Supervisor cleanup failed: " + str(exc)
                set_outcome(report)
        report["completed_time_utc"] = utc_now()
        # Never overwrite earlier reports, including failed evidence.
        args.report.parent.mkdir(parents=True, exist_ok=True)
        try:
            with args.report.open("x", encoding="utf-8") as output:
                json.dump(report, output, indent=2, allow_nan=False)
        except FileExistsError:
            print("Evidence destination already exists; no report was overwritten", file=sys.stderr)
            return 1
    print("Qualified" if report["passed"] else "Not qualified: " + report.get("error", "missing evidence"))
    print("Evidence: " + str(args.report))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
