"""Opt-in real Docker isolation checks. Never pull images or alter unrelated resources."""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from playground.sandbox import DockerSandbox, ExecutionResult, SandboxError, SandboxCleanupError, bounded_run


def python_command(source: str) -> str:
    return "python -I - <<'PY'\n" + source + "\nPY"


def local_docker_endpoint() -> str:
    # DOCKER_CONTEXT overrides DOCKER_HOST. Reject the ambient override rather
    # than admitting against one endpoint and running commands against another.
    if os.environ.get("DOCKER_CONTEXT"):
        raise SandboxError("Unset DOCKER_CONTEXT before qualification; a single local Docker endpoint is required")
    endpoint = os.environ.get("DOCKER_HOST")
    if not endpoint:
        context = bounded_run(["docker", "context", "inspect", "--format", "{{json .Endpoints.docker.Host}}"],
                              timeout_s=5, output_limit=4096)
        if context.exit_code or context.timed_out:
            raise SandboxError("Cannot resolve Docker context: " + context.stderr)
        endpoint = json.loads(context.stdout)
    if not isinstance(endpoint, str) or not endpoint.startswith("unix:///") or endpoint == "unix:///":
        raise SandboxError("Qualification requires the local Linux Unix-socket Docker daemon")
    return endpoint


def docker_run(endpoint: str, args: list[str], **kwargs) -> ExecutionResult:
    # Pin admission, execution, copies, inspection and cleanup to the same host.
    # A newly introduced context override must fail closed, including at cleanup.
    if os.environ.get("DOCKER_CONTEXT"):
        raise SandboxError("DOCKER_CONTEXT changed during qualification")
    return bounded_run(["docker", "--host", endpoint, *args], **kwargs)


class RealDocker:
    def __init__(self, report: dict, endpoint: str):
        self.report = report
        self.endpoint = endpoint
        self.names: list[str] = []
        self.operations: list[dict] = []

    def __call__(self, argv: list[str], **kwargs) -> ExecutionResult:
        if not argv or argv[0] != "docker":
            raise SandboxError("Qualification runner only accepts Docker commands")
        result = docker_run(self.endpoint, argv[1:], **kwargs)
        self.operations.append({"args": argv[1:], "exit_code": result.exit_code, "timed_out": result.timed_out})
        if argv[1] == "run":
            name = argv[argv.index("--name") + 1]
            self.names.append(name)
            if result.exit_code == 0 and not result.timed_out:
                inspected = docker_run(self.endpoint, ["container", "inspect", name],
                                        timeout_s=5, output_limit=1024**2)
                if inspected.exit_code or inspected.timed_out:
                    raise SandboxError("Cannot inspect exact qualification container")
                item = json.loads(inspected.stdout)[0]
                assert item["Image"] == self.report["image_id"], "Container image differs from inspected image"
                host = item["HostConfig"]
                selected = {key: host.get(key) for key in (
                    "NetworkMode", "Privileged", "CapDrop", "CapAdd", "SecurityOpt",
                    "ReadonlyRootfs", "NanoCpus", "Memory", "MemorySwap", "PidsLimit",
                    "Tmpfs", "Devices", "DeviceRequests", "Binds", "LogConfig", "Ulimits",
                    "PidMode", "IpcMode", "PortBindings")}
                config = {"name": name, "image_id": item["Image"], "host_config": selected,
                          "mounts": item["Mounts"], "user": item["Config"]["User"]}
                self.report["inspected_containers"].append(config)
                assert host["NetworkMode"] == "none", config
                assert host["Privileged"] is False and host["ReadonlyRootfs"] is True, config
                assert host["CapDrop"] == ["ALL"] and not host.get("CapAdd"), config
                assert "no-new-privileges:true" in host["SecurityOpt"], config
                assert host["NanoCpus"] == 1000000000, config
                assert host["Memory"] == host["MemorySwap"] == 64 * 1024**2, config
                assert host["PidsLimit"] == 16, config
                assert not host.get("Devices") and not host.get("DeviceRequests"), config
                assert not host.get("PortBindings") and not host.get("Binds"), config
                assert host["LogConfig"]["Type"] == "none", config
                assert item["Config"]["User"].split(":")[0] != "0", config
                assert host.get("PidMode", "") != "host" and host.get("IpcMode") != "host", config
                mounts = [m for m in item["Mounts"] if m["Type"] != "tmpfs"]
                assert len(mounts) == 1 and mounts[0]["Destination"] == "/input" and not mounts[0]["RW"], config
                expected_source = argv[argv.index("--mount") + 1].split("src=", 1)[1].split(",", 1)[0]
                assert mounts[0]["Source"] == expected_source, config
                assert set(host["Tmpfs"]) == {"/workspace", "/tmp"}, config
                assert "size=1048576" in host["Tmpfs"]["/workspace"], config
        return result

    def assert_removed(self, name: str):
        result = docker_run(self.endpoint, ["container", "inspect", name], timeout_s=5, output_limit=4096)
        assert not result.timed_out and result.exit_code != 0 and re.search(
            rf"No such (?:object|container):\s*{re.escape(name)}(?:\s|$)", result.stderr
        ), "Exact qualification container removal is unconfirmed: " + name


def workspace_state(workspace: Path) -> dict:
    """Include every entry so unexpected copyback cannot hide behind one seed."""
    state = {}
    for path in [workspace, *sorted(workspace.rglob("*"))]:
        info = path.lstat()
        state[path.relative_to(workspace).as_posix()] = {
            "mode": info.st_mode, "links": info.st_nlink,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest() if stat.S_ISREG(info.st_mode) else None,
        }
    return state


def check_case(sandbox, runner: RealDocker, work_root: Path, report: dict,
               name: str, command: str, expect="success", seconds=12) -> None:
    report["active_test"] = name
    with tempfile.TemporaryDirectory(prefix="sandbox-check-", dir=work_root) as tmp:
        workspace = Path(tmp) / "work"
        workspace.mkdir()
        (workspace / "seed.txt").write_text("preserve original", encoding="utf-8")
        (workspace / "nested").mkdir()
        (workspace / "nested" / "original.txt").write_bytes(b"nested original\x00")
        original = workspace_state(workspace)
        error = None
        result = None
        before_names = len(runner.names)
        before_operations = len(runner.operations)
        try:
            result = sandbox.execute(workspace, python_command(command), timeout_s=seconds)
        except SandboxCleanupError:
            raise
        except SandboxError as exc:
            error = str(exc)
        names = runner.names[before_names:]
        for container in names:
            runner.assert_removed(container)
        operations = runner.operations[before_operations:]
        if expect == "invalid_artifact":
            assert workspace_state(workspace) == original, "Rejected artifacts modified the original workspace or published unexpected entries"
            assert error == "artifact link or special file rejected", "Expected artifact-validator rejection, got: " + str(error)
            # Daemon/setup/copy failures must never count as isolation successes,
            # even if stderr happens to contain the validator's error text.
            command_runs = [op for op in operations if op["args"] == ["exec", names[0], "/bin/sh", "-c", python_command(command)]] if len(names) == 1 else []
            copies = [op for op in operations if op["args"] == ["cp", names[0] + ":/workspace/.", "-"]] if len(names) == 1 else []
            assert len(command_runs) == len(copies) == 1, "Artifact rejection did not follow command execution and export"
            assert all(op["exit_code"] == 0 and not op["timed_out"] for op in command_runs + copies), "Command or export failed before artifact validation"
        else:
            assert error is None and result is not None, error
            if expect == "success":
                assert result.exit_code == 0 and not result.timed_out, result.to_dict()
            elif expect == "roundtrip":
                assert result.exit_code == 0 and not result.timed_out and (workspace / "proof.txt").read_text() == "real copyback", result.to_dict()
            elif expect == "oom":
                assert result.exit_code == 137 and not result.timed_out, result.to_dict()
            elif expect == "output":
                assert result.exit_code == 125 and not result.timed_out, result.to_dict()
            elif expect == "timeout":
                assert result.timed_out, result.to_dict()
            else:
                raise ValueError("Unknown qualification expectation: " + expect)
            if expect in {"output", "timeout"}:
                assert workspace_state(workspace) == original, "Interrupted command modified original workspace or published unexpected entries"
        detail = {"exit_code": result.exit_code if result else None, "timed_out": result.timed_out if result else None,
                  "expected_rejection": error, "containers_removed": names,
                  "operations": [{key: op[key] for key in ("exit_code", "timed_out")} | {"operation": op["args"][0]} for op in operations]}
        report["tests"].append({"name": name, "passed": True, "detail": detail})
        report.pop("active_test", None)
        print(name + " passed", flush=True)


def qualify(image: str, work_root: Path, report: dict) -> None:
    # Only a local daemon can enforce admission against this host's filesystems.
    endpoint = local_docker_endpoint()
    report["docker_endpoint"] = endpoint
    daemon = docker_run(endpoint, ["info", "--format", "{{json .}}"], timeout_s=5, output_limit=1024**2)
    assert daemon.exit_code == 0 and not daemon.timed_out, daemon.stderr
    info = json.loads(daemon.stdout)
    report["docker"] = {key: info.get(key) for key in ("ID", "Name", "ServerVersion", "OSType", "Architecture", "DockerRootDir", "CgroupVersion")}
    assert info["OSType"] == "linux", "Linux Docker daemon required"
    for path in (work_root, Path(info["DockerRootDir"])):
        assert shutil.disk_usage(path).free >= 20 * 1024**3, "20 GiB disk reserve unavailable at " + str(path)
    image_result = docker_run(endpoint, ["image", "inspect", image], timeout_s=5, output_limit=1024**2)
    assert image_result.exit_code == 0 and not image_result.timed_out, "Pinned image must already be installed"
    image_data = json.loads(image_result.stdout)[0]
    report["image_id"] = image_data["Id"]
    report["image_architecture"] = image_data["Architecture"]
    machine = platform.machine().lower()
    expected_arch = "arm64" if machine in {"aarch64", "arm64"} else "amd64" if machine in {"x86_64", "amd64"} else machine
    assert image_data["Architecture"] == expected_arch and image_data["Os"] == "linux", "Image platform mismatch"
    runner = RealDocker(report, endpoint)
    sandbox = DockerSandbox(image, runner=runner, workspace_limit_bytes=1024**2,
                            stdout_limit_bytes=4096, memory="64m", pids=16)
    report["session_id"] = sandbox.session_id

    def check(name, command, expect="success", seconds=12):
        check_case(sandbox, runner, work_root, report, name, command, expect, seconds)

    check("workspace_roundtrip", "from pathlib import Path\nPath('proof.txt').write_text('real copyback')", "roundtrip")
    check("filesystem_and_authority", """import os
from pathlib import Path
assert os.getuid() != 0
for path in ['/var/run/docker.sock', '/dev/nvidia0', '/dev/ttyUSB0']:
    assert not Path(path).exists(), path
try:
    Path('/root/.ssh').stat()
except (PermissionError, FileNotFoundError):
    pass
else:
    raise AssertionError('root SSH directory accessible')
for path in ['/input/seed.txt', '/host-write-test']:
    try:
        Path(path).write_text('escape')
    except OSError:
        pass
    else:
        raise AssertionError('host or root write permitted: '+path)
""")
    check("network_denied", """import socket
assert socket.if_nameindex() == [(1, 'lo')]
for endpoint in [('1.1.1.1', 443), ('127.0.0.1', 18080)]:
    try:
        socket.create_connection(endpoint, timeout=1)
    except OSError:
        pass
    else:
        raise AssertionError('network endpoint reachable')
""")
    check("memory_limit", "x = bytearray(96 * 1024**2)", "oom")
    check("process_limit", """import subprocess
children = []
try:
    for _ in range(32):
        try:
            children.append(subprocess.Popen(['/bin/sleep', '10']))
        except OSError:
            break
    assert 0 < len(children) < 16, len(children)
finally:
    for child in children:
        child.kill()
        child.wait()
""")
    check("workspace_limit", """from pathlib import Path
try:
    Path('first').write_bytes(b'x' * (600 * 1024))
    Path('second').write_bytes(b'x' * (600 * 1024))
except OSError:
    pass
else:
    raise AssertionError('workspace quota not enforced')
""")
    check("stdout_limit", "from pathlib import Path\nPath('partial.txt').write_text('discard')\nprint('x' * 100000)", "output")
    check("timeout_and_cleanup", "from pathlib import Path\nimport time\nPath('partial.txt').write_text('discard')\ntime.sleep(30)", "timeout", 4)
    tamper = "from pathlib import Path\nPath('seed.txt').write_text('tampered')\nPath('unexpected.txt').write_text('must not publish')\n"
    check("artifact_symlink", tamper + "Path('escape').symlink_to('/input/seed.txt')", "invalid_artifact")
    check("artifact_hardlink", tamper + "import os\nPath('a').write_text('x')\nos.link('a','b')", "invalid_artifact")
    check("artifact_fifo", tamper + "import os\nos.mkfifo('fifo')", "invalid_artifact")


def source_hashes() -> dict[str, str]:
    return {str(path.relative_to(ROOT)).replace('\\', '/'): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (ROOT / "src/playground/sandbox.py", Path(__file__).resolve())}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default=os.environ.get("PLAYGROUND_SANDBOX_IMAGE", ""), help="Reviewed installed image pinned by sha256 digest")
    parser.add_argument("--work-root", type=Path, default=ROOT, help="Dedicated trusted project directory on the Docker host")
    parser.add_argument("--report", type=Path, required=True, help="New operator-owned JSON evidence destination; existing paths are never overwritten")
    args = parser.parse_args()
    # Reserve the destination before contacting Docker. Exclusive creation also
    # refuses existing symlinks and preserves failed as well as successful runs.
    try:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        report_file = args.report.open("x", encoding="utf-8")
    except OSError as exc:
        print("Cannot create new evidence report: " + str(exc), file=sys.stderr)
        return 1
    with report_file:
        report = {"schema_version": 1, "run_id": uuid.uuid4().hex,
                  "time_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  "image": args.image, "platform": platform.platform(), "python": platform.python_version(),
                  "work_root": str(args.work_root.absolute()),
                  "tests": [], "inspected_containers": [], "passed": False,
                  "physical_hardware_verified": False}
        try:
            sources = source_hashes()
            report.update({"source_sha256": sources, "sandbox_source_sha256": sources["src/playground/sandbox.py"],
                           "checker_source_sha256": sources["scripts/qualify_sandbox.py"]})
            if not __debug__:
                raise SandboxError("Qualification requires Python assertions enabled")
            DockerSandbox(args.image)  # Validate all trusted image settings before invoking Docker.
            if platform.system() != "Linux" or not hasattr(os, "getuid") or os.getuid() == 0:
                raise SandboxError("Run qualification as a non-root Linux user on the Docker host")
            qualify(args.image, args.work_root.resolve(strict=True), report)
            completed_sources = source_hashes()
            report["completed_source_sha256"] = completed_sources
            report["source_unchanged"] = completed_sources == sources
            if not report["source_unchanged"]:
                raise SandboxError("Qualification source changed during execution; evidence is invalid")
            report["passed"] = True
        except (Exception, KeyboardInterrupt) as exc:
            report["error"] = str(exc)
            if report.get("active_test"):
                report["tests"].append({"name": report["active_test"], "passed": False, "detail": str(exc)})
            print("Qualification failed: " + str(exc), file=sys.stderr)
        report["completed_time_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        try:
            json.dump(report, report_file, indent=2)
            report_file.write("\n")
            report_file.flush()
            os.fsync(report_file.fileno())
        except OSError as exc:
            print("Cannot finish evidence report: " + str(exc), file=sys.stderr)
            return 1
    print("Evidence: " + str(args.report))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
