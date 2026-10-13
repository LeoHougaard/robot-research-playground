from __future__ import annotations
import argparse
import json
import hashlib
import os
from pathlib import Path
import shutil
import sys
import time

from .llm import LocalModel
from .research import ResearchSupervisor, simulation_controller
from .viewer import read_status, serve
from .storage import validate_storage_config


def read_config(path):
    config = json.loads(Path(path).read_text(encoding="utf-8"))
    validate_storage_config(config)
    if config.get("mode", "simulation") != "simulation":
        raise ValueError("run uses simulation only; physical operation requires the hardware command")
    if os.environ.get("PLAYGROUND_LLM_BASE_URL"):
        config.setdefault("llm", {})["base_url"] = os.environ["PLAYGROUND_LLM_BASE_URL"]
    if os.environ.get("PLAYGROUND_LLM_MODEL"):
        config.setdefault("llm", {})["model"] = os.environ["PLAYGROUND_LLM_MODEL"]
    return config


def main(argv=None):
    parser = argparse.ArgumentParser(description="Disposable robot researchers with persistent evidence")
    commands = parser.add_subparsers(dest="command", required=True)
    demo = commands.add_parser("demo", help="Built-in simulation acceptance; no model or hardware required")
    demo.add_argument("--state-dir", default="state/demo")
    demo.add_argument("--generations", type=int, default=4)
    demo.add_argument("--steps", type=int, default=12)
    run = commands.add_parser("run", help="Bounded local-model research in simulation")
    run.add_argument("--config", default="configs/research.json")
    run.add_argument("--state-dir")
    hardware = commands.add_parser("hardware", help="Operator-only physical research after documented bench acceptance")
    hardware.add_argument("--config", default="configs/research.json")
    hardware.add_argument("--state-dir", default="state/physical")
    hardware.add_argument("--serial-port", required=True)
    hardware.add_argument("--calibration", required=True)
    hardware.add_argument("--camera", type=int, default=0)
    hardware.add_argument("--bench-accepted", action="store_true")
    status = commands.add_parser("status")
    status.add_argument("--state-dir", default="state")
    viewer = commands.add_parser("serve")
    viewer.add_argument("--state-dir", default="state")
    viewer.add_argument("--host", default="127.0.0.1")
    viewer.add_argument("--port", type=int, default=8765)
    backup = commands.add_parser("backup", help="Back up a stopped state directory without changing its evidence")
    backup.add_argument("--state-dir", required=True)
    backup.add_argument("--destination", required=True)
    backup.add_argument("--disk-reserve-bytes", type=int, help="Optional stricter free-space floor")
    restore = commands.add_parser("restore", help="Validate and restore a backup into a new state directory")
    restore.add_argument("--backup", required=True)
    restore.add_argument("--state-dir", required=True)
    restore.add_argument("--disk-reserve-bytes", type=int, help="Optional stricter free-space floor")
    commands.add_parser("self-check", help="Report software capabilities without motion or deployment")
    args = parser.parse_args(argv)
    try:
        if args.command == "backup":
            from .backup import create_backup
            result = create_backup(args.state_dir, args.destination, disk_reserve_bytes=args.disk_reserve_bytes)
            print(json.dumps(result, indent=2))
            return 0
        if args.command == "restore":
            from .backup import restore_backup
            result = restore_backup(args.backup, args.state_dir, disk_reserve_bytes=args.disk_reserve_bytes)
            print(json.dumps(result, indent=2))
            return 0
        if args.command == "serve":
            serve(args.state_dir, args.host, args.port)
            return 0
        if args.command == "status":
            print(json.dumps(read_status(args.state_dir), indent=2))
            return 0
        if args.command == "self-check":
            print(json.dumps({"python": sys.version.split()[0], "docker_cli": bool(shutil.which("docker")),
                              "sandbox_image_configured": bool(os.environ.get("PLAYGROUND_SANDBOX_IMAGE")),
                              "hardware_default": "DISARMED", "gpu_generated_code": "disabled",
                              "free_disk_bytes": shutil.disk_usage(Path.cwd()).free}, indent=2))
            return 0
        if args.command == "hardware":
            if not args.bench_accepted:
                raise ValueError("Complete docs/acceptance.md hardware gates before using --bench-accepted")
            from .vision import Calibration, CameraTracker
            from .robot import RobotController, SerialBackend
            config = read_config(args.config)
            calibration_path = Path(args.calibration)
            report_path = calibration_path.with_suffix(".validation.json")
            report = json.loads(report_path.read_text(encoding="utf-8"))
            if report.get("passed") is not True or not 0 <= report.get("max_error_mm", float("inf")) <= report.get("allowed_max_error_mm", 0) <= 5:
                raise ValueError("Physical camera calibration did not pass independent measured-point acceptance")
            if report.get("calibration_sha256") != hashlib.sha256(calibration_path.read_bytes()).hexdigest():
                raise ValueError("Calibration file changed after validation")
            state_dir = Path(args.state_dir)
            state_dir.mkdir(parents=True, exist_ok=True)
            if shutil.disk_usage(state_dir).free < config.get("disk_reserve_bytes", 20 * 1024**3):
                raise RuntimeError("Physical research refused: disk reserve unavailable")
            dimensions = report.get("source_image_size_px")
            if not isinstance(dimensions, list) or len(dimensions) != 2 or any(type(v) is not int or v <= 0 for v in dimensions):
                raise ValueError("Calibration report must specify the measured camera frame dimensions")
            config["mode"] = "physical"
            config.setdefault("evidence_inputs", {}).update({
                "calibration": str(calibration_path.resolve()),
                "calibration_validation": str(report_path.resolve()),
            })
            if not all(config["evidence_inputs"].get(key) for key in ("geometry", "firmware")):
                raise ValueError("Physical research requires evidence_inputs geometry and firmware source files")
            tracker = CameraTracker(Calibration.load(args.calibration), camera=args.camera, expected_frame_size=tuple(dimensions))
            supervisor = None
            controller = None
            try:
                deadline = time.monotonic() + 3
                while not tracker.tracking()["valid"] and time.monotonic() < deadline:
                    time.sleep(.05)
                backend = SerialBackend(args.serial_port, tracker.observe)
                controller = RobotController(backend)
                supervisor = ResearchSupervisor(state_dir, controller, config=config)
                supervisor.camera = tracker
                print("Physical operation requires your presence. Inspect the arena and fully unwind the tether with motor power off.")
                if input("Type ARM ROBOT to authorize this run: ").strip() != "ARM ROBOT":
                    raise ValueError("Physical arming cancelled")
                input("Release E-stop, press the MCU authorization button, then press Enter within five seconds: ")
                controller.reset_tether(human_confirmed=True)
                supervisor.start_robot(operator_confirmed=True)
                llm = config.get("llm", {})
                model = LocalModel(llm.get("base_url", "http://127.0.0.1:18080/v1"), llm.get("model", ""),
                                   llm.get("timeout_seconds", 120), llm.get("max_tokens", 1024))
                result = supervisor.run(generations=config.get("max_generations", 8), steps=config.get("steps_per_generation", 12),
                                        model=model, max_seconds=config.get("max_run_seconds", 3600))
                print(json.dumps(result, indent=2))
                return 0 if result["status"] == "completed" else 1
            finally:
                if supervisor is not None:
                    supervisor.close()
                elif controller is not None:
                    controller.close()
                tracker.close()
        config = {} if args.command == "demo" else read_config(args.config)
        state_dir = args.state_dir or config.get("state_dir", "state")
        if args.command == "run":
            reserve = config.get("disk_reserve_bytes", 20 * 1024**3)
            Path(state_dir).mkdir(parents=True, exist_ok=True)
            if shutil.disk_usage(state_dir).free < reserve:
                raise RuntimeError("Research admission refused: disk reserve unavailable")
        controller, clock = simulation_controller()
        supervisor = ResearchSupervisor(state_dir, controller, clock=clock, config=config)
        try:
            supervisor.start_robot(operator_confirmed=True)
            if args.command == "demo":
                result = supervisor.run(generations=args.generations, steps=args.steps, demo=True)
            else:
                llm = config.get("llm", {})
                model = LocalModel(llm.get("base_url", "http://127.0.0.1:18080/v1"), llm.get("model", ""),
                                   llm.get("timeout_seconds", 120), llm.get("max_tokens", 1024))
                result = supervisor.run(generations=config.get("max_generations", 8), steps=config.get("steps_per_generation", 12),
                                        model=model, max_seconds=config.get("max_run_seconds", 3600))
            print(json.dumps(result, indent=2))
            return 0 if result["status"] == "completed" else 1
        finally:
            supervisor.close()
    except KeyboardInterrupt:
        print("Stopped. Evidence is preserved; hardware is disarmed.", file=sys.stderr)
        return 130
    except (OSError, ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
