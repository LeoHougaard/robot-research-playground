"""Opt-in real Docker isolation checks. Never pull images or alter unrelated resources."""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from playground.sandbox import DockerSandbox, ExecutionResult, SandboxError, SandboxCleanupError, bounded_run


def python_command(source: str) -> str:
    return "python -I - <<'PY'\n" + source + "\nPY"


class RealDocker:
    def __init__(self, report: dict):
        self.report = report
        self.names: list[str] = []

    def __call__(self, argv: list[str], **kwargs) -> ExecutionResult:
        result = bounded_run(argv, **kwargs)
        if argv[1] == "run":
            name = argv[argv.index("--name") + 1]
            self.names.append(name)
            if result.exit_code == 0 and not result.timed_out:
                inspected = bounded_run(["docker", "container", "inspect", name],
                                        timeout_s=5, output_limit=1024**2)
                if inspected.exit_code or inspected.timed_out:
                    raise SandboxError("Cannot inspect exact qualification container")
                item = json.loads(inspected.stdout)[0]
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
        result = bounded_run(["docker", "container", "inspect", name], timeout_s=5, output_limit=4096)
        assert not result.timed_out and result.exit_code != 0 and (
            "No such object: " + name in result.stderr or "No such container: " + name in result.stderr
        ), "Exact qualification container removal is unconfirmed: " + name


def qualify(image: str, work_root: Path, report: dict) -> None:
    # Only a local daemon can enforce admission against this host's filesystems.
    endpoint = os.environ.get("DOCKER_HOST")
    if endpoint is None:
        context = bounded_run(["docker", "context", "inspect", "--format", "{{json .Endpoints.docker.Host}}"],
                              timeout_s=5, output_limit=4096)
        assert context.exit_code == 0 and not context.timed_out, context.stderr
        endpoint = json.loads(context.stdout)
    assert endpoint.startswith("unix://"), "Qualification requires the local Linux Unix-socket Docker daemon"
    daemon = bounded_run(["docker", "info", "--format", "{{json .}}"], timeout_s=5, output_limit=1024**2)
    assert daemon.exit_code == 0 and not daemon.timed_out, daemon.stderr
    info = json.loads(daemon.stdout)
    report["docker"] = {key: info.get(key) for key in ("ID", "Name", "ServerVersion", "OSType", "Architecture", "DockerRootDir", "CgroupVersion")}
    assert info["OSType"] == "linux", "Linux Docker daemon required"
    for path in (work_root, Path(info["DockerRootDir"])):
        assert shutil.disk_usage(path).free >= 20 * 1024**3, "20 GiB disk reserve unavailable at " + str(path)
    image_result = bounded_run(["docker", "image", "inspect", image], timeout_s=5, output_limit=1024**2)
    assert image_result.exit_code == 0 and not image_result.timed_out, "Pinned image must already be installed"
    image_data = json.loads(image_result.stdout)[0]
    report["image_id"] = image_data["Id"]
    report["image_architecture"] = image_data["Architecture"]
    machine = platform.machine().lower()
    expected_arch = "arm64" if machine in {"aarch64", "arm64"} else "amd64" if machine in {"x86_64", "amd64"} else machine
    assert image_data["Architecture"] == expected_arch and image_data["Os"] == "linux", "Image platform mismatch"
    runner = RealDocker(report)
    sandbox = DockerSandbox(image, runner=runner, workspace_limit_bytes=1024**2,
                            stdout_limit_bytes=4096, memory="64m", pids=16)
    report["session_id"] = sandbox.session_id

    def check(name, command, expect="success", seconds=12):
        report["active_test"] = name
        with tempfile.TemporaryDirectory(prefix="sandbox-check-", dir=work_root) as tmp:
            workspace = Path(tmp) / "work"
            workspace.mkdir()
            (workspace / "seed.txt").write_text("preserve original", encoding="utf-8")
            error = None
            result = None
            before_names = len(runner.names)
            try:
                result = sandbox.execute(workspace, python_command(command), timeout_s=seconds)
            except SandboxCleanupError:
                raise
            except SandboxError as exc:
                error = str(exc)
            for container in runner.names[before_names:]:
                runner.assert_removed(container)
            if expect == "invalid_artifact":
                assert error is not None, "Unsafe artifacts were accepted"
                assert (workspace / "seed.txt").read_text() == "preserve original"
            else:
                assert error is None and result is not None, error
                if expect == "success":
                    assert result.exit_code == 0 and not result.timed_out, result.to_dict()
                elif expect == "roundtrip":
                    assert result.exit_code == 0 and (workspace / "proof.txt").read_text() == "real copyback", result.to_dict()
                elif expect == "oom":
                    assert result.exit_code == 137 and not result.timed_out, result.to_dict()
                elif expect == "output":
                    assert result.exit_code == 125 and not result.timed_out, result.to_dict()
                elif expect == "timeout":
                    assert result.timed_out, result.to_dict()
                if expect in {"output", "timeout"}:
                    assert (workspace / "seed.txt").read_text() == "preserve original"
                    assert not (workspace / "partial.txt").exists()
            detail = {"exit_code": result.exit_code if result else None, "timed_out": result.timed_out if result else None,
                      "expected_rejection": error, "containers_removed": runner.names[before_names:]}
            report["tests"].append({"name": name, "passed": True, "detail": detail})
            report.pop("active_test", None)
            print(name + " passed", flush=True)

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
    check("artifact_symlink", "from pathlib import Path\nPath('escape').symlink_to('/input/seed.txt')", "invalid_artifact")
    check("artifact_hardlink", "from pathlib import Path\nimport os\nPath('a').write_text('x')\nos.link('a','b')", "invalid_artifact")
    check("artifact_fifo", "import os\nos.mkfifo('fifo')", "invalid_artifact")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default=os.environ.get("PLAYGROUND_SANDBOX_IMAGE", ""), help="Reviewed installed image pinned by sha256 digest")
    parser.add_argument("--work-root", type=Path, default=ROOT, help="Dedicated trusted project directory on the Docker host")
    parser.add_argument("--report", type=Path, required=True, help="Operator-owned JSON evidence destination")
    args = parser.parse_args()
    sources = {str(path.relative_to(ROOT)).replace('\\', '/'): hashlib.sha256(path.read_bytes()).hexdigest()
               for path in (ROOT / "src/playground/sandbox.py", Path(__file__).resolve())}
    report = {"schema_version": 1, "time_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "image": args.image, "platform": platform.platform(), "source_sha256": sources,
              "sandbox_source_sha256": sources["src/playground/sandbox.py"],
              "checker_source_sha256": sources["scripts/qualify_sandbox.py"],
              "tests": [], "inspected_containers": [], "passed": False,
              "physical_hardware_verified": False}
    try:
        if not __debug__:
            raise SandboxError("Qualification requires Python assertions enabled")
        DockerSandbox(args.image)  # Validate all trusted image settings before invoking Docker.
        if not hasattr(os, "getuid") or os.getuid() == 0:
            raise SandboxError("Run qualification as a non-root Linux user on the Docker host")
        qualify(args.image, args.work_root.resolve(strict=True), report)
        report["passed"] = True
    except (Exception, KeyboardInterrupt) as exc:
        report["error"] = str(exc)
        if report.get("active_test"):
            report["tests"].append({"name": report["active_test"], "passed": False, "detail": str(exc)})
        print("Qualification failed: " + str(exc), file=sys.stderr)
    report["completed_time_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("Evidence: " + str(args.report))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
