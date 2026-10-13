"""Trusted Docker launcher. Generated shell commands never run on the host."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import threading
import time
import uuid
from typing import Callable


@dataclass(frozen=True)
class ExecutionResult:
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


class SandboxError(RuntimeError):
    pass


class SandboxCleanupError(SandboxError):
    """The supervisor must stop admitting jobs until this exact name is reviewed."""
    def __init__(self, container_name: str, session_id: str, detail: str):
        self.container_name, self.session_id = container_name, session_id
        super().__init__(f"Sandbox cleanup unconfirmed for {container_name}; session {session_id}. {detail}")


def validate_workspace(path: Path, limit: int, max_files: int = 4096) -> int:
    """Reject links, devices and excessive data before importing or publishing."""
    if path.is_symlink() or not path.is_dir():
        raise SandboxError("workspace must be a real directory")
    total = 0
    count = 0
    for root, dirs, files in os.walk(path, followlinks=False):
        for name in dirs + files:
            item = Path(root) / name
            info = item.lstat()
            count += 1
            if count > max_files:
                raise SandboxError("workspace file count exceeds quota")
            if stat.S_ISLNK(info.st_mode) or not (
                stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)
            ):
                raise SandboxError("workspace contains a link or special file")
            if stat.S_ISREG(info.st_mode):
                if info.st_nlink > 1:
                    raise SandboxError("workspace contains a hard link")
                total += info.st_size
                if total > limit:
                    raise SandboxError("workspace exceeds byte quota")
    return total


def bounded_run(argv: list[str], *, timeout_s: float, output_limit: int,
                stdout_file: Path | None = None) -> ExecutionResult:
    """Drain pipes concurrently, retaining at most output_limit bytes per stream."""
    deadline = time.monotonic() + timeout_s
    proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, shell=False)
    buffers = [bytearray(), bytearray()]
    overflow = threading.Event()

    def drain(pipe, destination, output_path=None):
        target = output_path.open("wb") if output_path else None
        captured = 0
        try:
            while block := pipe.read(8192):
                remaining = output_limit - captured
                accepted = block[:max(0, remaining)]
                if target:
                    target.write(accepted)
                else:
                    destination.extend(accepted)
                captured += len(accepted)
                if len(block) > remaining:
                    overflow.set()
                    proc.kill()
                    return
        finally:
            pipe.close()
            if target:
                target.close()

    readers = [threading.Thread(target=drain, args=(proc.stdout, buffers[0], stdout_file), daemon=True),
               threading.Thread(target=drain, args=(proc.stderr, buffers[1]), daemon=True)]
    for reader in readers:
        reader.start()
    timed_out = False
    try:
        # Reserve a small part of the requested budget for killing and draining.
        proc.wait(timeout=max(.001, deadline - time.monotonic() - .1))
    except subprocess.TimeoutExpired:
        timed_out = True
        proc.kill()
        try:
            proc.wait(timeout=max(.001, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            pass  # Cleanup confirmation must fail closed if Docker itself stalls.
    for reader in readers:
        reader.join(timeout=max(0, deadline - time.monotonic()))
    if any(reader.is_alive() for reader in readers):
        timed_out = True
        proc.kill()
    out, err = (bytes(buf).decode("utf-8", errors="replace") for buf in buffers)
    if overflow.is_set():
        err += "\noutput quota exceeded; container terminated"
    return ExecutionResult(124 if timed_out else (125 if overflow.is_set() else proc.returncode),
                           out, err, timed_out)


def extract_workspace(archive: Path, destination: Path, limit: int, max_files: int = 4096) -> None:
    """Parse tar data without extractall, rejecting every nonregular entry."""
    count = total = 0
    seen = set()
    with tarfile.open(archive, mode="r:") as source:
        for member in source:
            name = PurePosixPath(member.name)
            if name.is_absolute() or ".." in name.parts or "\\" in member.name or ":" in member.name:
                raise SandboxError("artifact path traversal rejected")
            if not member.isdir() and (not member.isfile() or member.issparse()):
                raise SandboxError("artifact link or special file rejected")
            if str(name) == "." and member.isdir():
                continue
            if not name.parts or str(name) in seen:
                raise SandboxError("duplicate or empty artifact path")
            seen.add(str(name))
            count += 1
            if count > max_files:
                raise SandboxError("artifact file count exceeds quota")
            target = destination.joinpath(*name.parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True, mode=0o700)
                continue
            total += member.size
            if member.size < 0 or total > limit:
                raise SandboxError("artifact bytes exceed quota")
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            payload = source.extractfile(member)
            if payload is None:
                raise SandboxError("artifact payload missing")
            with payload, target.open("xb") as output:
                shutil.copyfileobj(payload, output, length=65536)
            target.chmod(0o600 | (member.mode & 0o100))


class DockerSandbox:
    """Image selection and resource flags are trusted operator configuration only.

    /input is the exact validated host workspace, mounted read-only. /workspace is
    a size-limited tmpfs. Successful or failed bounded executions can publish their
    validated workspace snapshot. Timeouts and output quota violations publish none.
    """

    def __init__(self, image: str | None = None, *, workspace_limit_bytes: int = 64 * 1024**2,
                 stdout_limit_bytes: int = 1024**2, cpu: float = 1.0,
                 memory: str = "512m", pids: int = 64,
                 min_free_disk_bytes: int = 20 * 1024**3,
                 runner: Callable = bounded_run):
        self.image = image or os.environ.get("PLAYGROUND_SANDBOX_IMAGE", "")
        if not re.fullmatch(r"[a-zA-Z0-9./:_-]+@sha256:[a-f0-9]{64}", self.image):
            raise SandboxError("configure a reviewed sandbox image pinned by sha256 digest")
        if not 1024 <= workspace_limit_bytes <= 256 * 1024**2:
            raise SandboxError("workspace quota must be 1 KiB to 256 MiB")
        if not 1024 <= stdout_limit_bytes <= 8 * 1024**2 or not 0 < cpu <= 4 or not 8 <= pids <= 128:
            raise SandboxError("invalid sandbox resource limits")
        if not re.fullmatch(r"(?:[1-9][0-9]{1,3})m", memory) or not 64 <= int(memory[:-1]) <= 4096:
            raise SandboxError("memory must be 64m to 4096m")
        self.workspace_limit_bytes = workspace_limit_bytes
        self.stdout_limit_bytes = stdout_limit_bytes
        self.cpu, self.memory, self.pids, self.runner = cpu, memory, pids, runner
        self.min_free_disk_bytes = min_free_disk_bytes
        self.session_id = os.environ.get("PLAYGROUND_SESSION_ID", uuid.uuid4().hex)
        if not re.fullmatch(r"[a-f0-9]{32}", self.session_id):
            raise SandboxError("invalid trusted launcher session identifier")
        self._lock = threading.Lock()

    def _run(self, args: list[str], timeout: float = 10) -> ExecutionResult:
        return self.runner(["docker", *args], timeout_s=max(0.1, timeout),
                           output_limit=self.stdout_limit_bytes)

    def _cleanup(self, name: str, *, uncertain_startup: bool) -> None:
        # Reserve time to inspect an exact name if Docker cannot acknowledge rm.
        deadline = time.monotonic() + 10
        detail = ""
        try:
            removed = self._run(["rm", "--force", name], 8)
            if removed.exit_code == 0 and not removed.timed_out:
                return
            detail = removed.stderr[:2000] or "Docker did not acknowledge removal"
        except Exception as exc:
            detail = str(exc)[:2000]
        remaining = deadline - time.monotonic()
        if remaining > 0:
            try:
                inspected = self._run(["container", "inspect", "--format", "{{.Id}}", name], remaining)
                absent = inspected.exit_code != 0 and not inspected.timed_out and re.search(
                    rf"No such (?:object|container):\s*{re.escape(name)}(?:\s|$)", inspected.stderr)
                if absent and not uncertain_startup:
                    return
                if absent and uncertain_startup:
                    detail = "Startup request was interrupted; absence now cannot exclude delayed creation. " + detail
                elif inspected.exit_code == 0:
                    detail = "Exact container still exists after attempted removal. " + detail
                else:
                    detail += "; inspection: " + inspected.stderr[:2000]
            except Exception as exc:
                detail += "; inspection: " + str(exc)[:2000]
        raise SandboxCleanupError(name, self.session_id, detail)

    def run_argv(self, workspace: Path, name: str) -> list[str]:
        uid = os.getuid() if hasattr(os, "getuid") else 1000
        gid = os.getgid() if hasattr(os, "getgid") else 1000
        if uid == 0:
            raise SandboxError("run the supervisor as a non-root user")
        return ["run", "--detach", "--pull=never", "--name", name,
                "--label", f"org.robot-playground.session={self.session_id}",
                "--network=none", "--cap-drop=ALL", "--security-opt=no-new-privileges:true",
                "--read-only", "--user", f"{uid}:{gid}", "--cpus", str(self.cpu),
                "--memory", self.memory, "--memory-swap", self.memory,
                "--pids-limit", str(self.pids), "--ulimit", "nofile=256:256",
                "--ulimit", f"fsize={self.workspace_limit_bytes}:{self.workspace_limit_bytes}",
                "--log-driver=none", "--stop-timeout=1",
                "--tmpfs", f"/workspace:rw,nosuid,nodev,size={self.workspace_limit_bytes},mode=1777",
                "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=16777216,mode=1777",
                "--mount", f"type=bind,src={workspace},dst=/input,readonly",
                "--workdir", "/workspace", "--env", "HOME=/tmp",
                "--env", "PYTHONDONTWRITEBYTECODE=1", "--entrypoint", "/bin/sleep",
                self.image, "3600"]

    def execute(self, workspace: Path, command: str, timeout_s: float = 30) -> ExecutionResult:
        if not isinstance(command, str) or not command or len(command) > 65536 or "\0" in command:
            raise SandboxError("command must contain 1 to 65536 characters without NUL")
        if not 0 < timeout_s <= 300:
            raise SandboxError("timeout must be greater than zero and at most 300 seconds")
        original = Path(workspace).absolute()
        # A resolved path alone would hide a caller's top-level symlink.
        if original.is_symlink():
            raise SandboxError("workspace must not be a symlink")
        workspace = original.resolve(strict=True)
        if "," in str(workspace) or workspace == Path(workspace.anchor):
            raise SandboxError("unsafe workspace path")
        with self._lock:
            validate_workspace(workspace, self.workspace_limit_bytes)
            if shutil.disk_usage(workspace).free < self.min_free_disk_bytes:
                raise SandboxError("free disk is below admission floor")
            name = "playground-job-" + uuid.uuid4().hex
            deadline = time.monotonic() + timeout_s
            launch_attempted = False
            uncertain_startup = False
            def checked(args):
                nonlocal launch_attempted, uncertain_startup
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise SandboxError("sandbox setup exceeded time budget")
                launching = args[0] == "run"
                if launching:
                    launch_attempted = uncertain_startup = True
                try:
                    result = self._run(args, remaining)
                except FileNotFoundError:
                    if launching:
                        launch_attempted = False  # No Docker process was started.
                    raise
                if launching and result.exit_code == 0 and not result.timed_out:
                    uncertain_startup = False
                if result.exit_code:
                    raise SandboxError(result.stderr or "Docker operation failed")
                if "warning:" in result.stderr.lower():
                    raise SandboxError("Docker warning prevents verified isolation: " + result.stderr)
            try:
                checked(self.run_argv(workspace, name))
                checked(["exec", name, "/bin/sh", "-c", "cp -R /input/. /workspace/"])
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return ExecutionResult(124, stderr="sandbox setup exceeded time budget", timed_out=True)
                result = self._run(["exec", name, "/bin/sh", "-c", command], remaining)
                if result.timed_out or result.exit_code == 125:
                    return result
                # Freeze background children before snapshotting artifacts.
                checked(["pause", name])
                with tempfile.TemporaryDirectory(prefix=".playground-export-", dir=workspace.parent) as staging:
                    snapshot = Path(staging) / "workspace"
                    snapshot.mkdir()
                    archive = Path(staging) / "workspace.tar"
                    copied = self.runner(["docker", "cp", f"{name}:/workspace/.", "-"],
                                         timeout_s=max(0.1, deadline-time.monotonic()),
                                         output_limit=self.workspace_limit_bytes + 16*1024**2,
                                         stdout_file=archive)
                    if copied.exit_code:
                        raise SandboxError(copied.stderr or "artifact copy failed")
                    extract_workspace(archive, snapshot, self.workspace_limit_bytes)
                    validate_workspace(snapshot, self.workspace_limit_bytes)
                    # Publish a validated snapshot without clearing the original first.
                    backup = Path(staging) / "previous"
                    workspace.rename(backup)
                    try:
                        snapshot.rename(workspace)
                    except OSError:
                        backup.rename(workspace)
                        raise
                return result
            except FileNotFoundError as exc:
                raise SandboxError("Docker CLI or workspace is unavailable; arbitrary code has no host fallback") from exc
            finally:
                # Exact generated name, never prune or touch another workload.
                if launch_attempted:
                    self._cleanup(name, uncertain_startup=uncertain_startup)
