"""Trusted Docker launcher. Generated shell commands never run on the host."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
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


def validate_workspace(path: Path, limit: int, max_files: int = 4096, *, max_path_bytes: int | None = None) -> int:
    """Reject links, devices and excessive data before importing or publishing."""
    if path.is_symlink() or not path.is_dir():
        raise SandboxError("workspace must be a real directory")
    total = 0
    count = 0
    for root, dirs, files in os.walk(path, followlinks=False):
        for name in dirs + files:
            item = Path(root) / name
            info = item.lstat()
            if max_path_bytes is not None and len(item.relative_to(path).as_posix().encode()) > max_path_bytes:
                raise SandboxError("workspace artifact path exceeds metadata quota")
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


def _publication_sync(directory: Path) -> None:
    if os.name == "nt":
        return
    fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _publication_journal(stage: Path, target: str, phase: str) -> None:
    value = {"version": 1, "workspace_name": target, "phase": phase}
    temporary = stage / "publication.json.tmp"
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, stage / "publication.json")
    _publication_sync(stage)
    _publication_sync(stage.parent)


def _read_publication(stage: Path) -> dict:
    marker = stage / "publication.json"
    if stage.is_symlink() or not stage.is_dir() or marker.is_symlink() or not marker.is_file():
        raise SandboxError(f"Unidentified copyback stage retained for operator review: {stage}")
    if marker.stat().st_size > 1024:
        raise SandboxError("Copyback journal exceeds its bound; stage retained")
    try:
        value = json.loads(marker.read_text(encoding="utf-8"))
        target = value["workspace_name"]
        if (set(value) != {"version", "workspace_name", "phase"} or value["version"] != 1
                or not isinstance(target, str) or not target or len(target.encode()) > 255
                or target in (".", "..") or any(c in target for c in ("/", "\\", "\0"))
                or target.startswith(".playground-export-")
                or value["phase"] not in ("copying", "prepared", "committed", "rolled_back")):
            raise ValueError()
    except (ValueError, KeyError, TypeError, UnicodeError) as exc:
        raise SandboxError("Invalid copyback journal; stage retained for operator review") from exc
    return value


def _discard_publication(stage: Path) -> None:
    """Caller has established that the authoritative workspace is intact.

    Remove the journal last. A crash during cleanup leaves a replayable journal
    or an empty stage, never an unidentified directory containing the original.
    """
    for child in stage.iterdir():
        if child.name == "publication.json":
            continue
        if child.is_symlink() or not child.is_dir():
            child.unlink()
        else:
            shutil.rmtree(child)
    marker = stage / "publication.json"
    marker.unlink(missing_ok=True)
    stage.rmdir()
    _publication_sync(stage.parent)


def _recover_publication(stage: Path, limit: int) -> bool:
    """Restore an uncommitted original; quarantine its candidate, never adopt it."""
    value = _read_publication(stage)
    target = stage.parent / value["workspace_name"]
    previous, snapshot = stage / "previous", stage / "workspace"
    for path in (target, previous, snapshot):
        if path.is_symlink() or (path.exists() and not path.is_dir()):
            raise SandboxError("Unsafe copyback recovery path; stage retained")
    phase = value["phase"]
    if phase == "rolled_back":
        if previous.exists() or not target.is_dir():
            raise SandboxError("Previously restored workspace changed; stage retained")
        return False
    if phase == "committed":
        if not target.is_dir() or snapshot.exists():
            raise SandboxError("Committed copyback is incomplete; stage retained")
        validate_workspace(target, limit)
        _discard_publication(stage)
        return True
    if phase == "copying":
        if not target.is_dir() or previous.exists():
            raise SandboxError("Copyback source is missing; stage retained")
    elif previous.exists():
        validate_workspace(previous, limit)
        if target.exists():
            if snapshot.exists():
                raise SandboxError("Ambiguous copyback destinations; stage retained")
            # The second rename happened but commit did not. Retain the
            # candidate separately and restore the last accepted original.
            target.rename(snapshot)
            _publication_sync(stage.parent)
            _publication_sync(stage)
        previous.rename(target)
        _publication_sync(stage.parent)
        _publication_sync(stage)
    elif not target.is_dir() or not snapshot.is_dir():
        raise SandboxError("Copyback original cannot be identified; stage retained")
    validate_workspace(target, limit)
    _publication_journal(stage, target.name, "rolled_back")
    return True


def recover_workspace_publications(root: Path, limit: int) -> int:
    """Call only while holding supervisor ownership, after provenance validation.

    Unresolved journals fail closed. Rolled-back candidates remain quarantined
    under the state tree and count toward aggregate storage admission.
    """
    root = Path(root)
    if not root.exists():
        return 0
    if root.is_symlink() or not root.is_dir():
        raise SandboxError("Unsafe workspace publication root")
    recovered = 0
    for stage in sorted(root.glob(".playground-export-*")):
        if stage.is_dir() and not stage.is_symlink() and not any(stage.iterdir()):
            stage.rmdir()  # Only a crash after final journal unlink can leave this.
            recovered += 1
            continue
        recovered += _recover_publication(stage, limit)
    return recovered


class DockerSandbox:
    """Image selection and resource flags are trusted operator configuration only.

    /input is the exact validated host workspace, mounted read-only. /workspace is
    a size-limited tmpfs. Successful or failed bounded executions can publish their
    validated workspace snapshot. Timeouts and output quota violations publish none.
    """

    def __init__(self, image: str | None = None, *, workspace_limit_bytes: int = 64 * 1024**2,
                 stdout_limit_bytes: int = 1024**2, workspace_path_limit_bytes: int = 468, cpu: float = 1.0,
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
        if type(workspace_path_limit_bytes) is not int or not 1 <= workspace_path_limit_bytes <= 468:
            raise SandboxError("workspace path quota must be 1..468 UTF-8 bytes")
        self.workspace_limit_bytes = workspace_limit_bytes
        self.workspace_path_limit_bytes = workspace_path_limit_bytes
        self.stdout_limit_bytes = stdout_limit_bytes
        self.cpu, self.memory, self.pids, self.runner = cpu, memory, pids, runner
        self.min_free_disk_bytes = min_free_disk_bytes
        self.session_id = os.environ.get("PLAYGROUND_SESSION_ID", uuid.uuid4().hex)
        if not re.fullmatch(r"[a-f0-9]{32}", self.session_id):
            raise SandboxError("invalid trusted launcher session identifier")
        self._lock = threading.Lock()

    def _begin_intent(self, name: str) -> Path | None:
        """Persist launch ambiguity outside the generated-code workspace.

        Spark launchers set both variables. Direct/qualification callers without
        a launcher session retain their existing explicit cleanup contract.
        """
        if not os.environ.get("PLAYGROUND_SESSION_ID"):
            return None
        state = os.environ.get("PLAYGROUND_STATE_DIR")
        if not state:
            raise SandboxError("launcher session requires its trusted state directory")
        runtime = Path(state).resolve() / "runtime"
        if runtime.is_symlink() or not runtime.is_dir():
            raise SandboxError("trusted launcher runtime directory is unavailable")
        path = runtime / f"docker-inflight-{self.session_id}.json"
        try:
            with path.open("x", encoding="utf-8") as out:
                json.dump({"version": 1, "session_id": self.session_id,
                           "container_name": name}, out)
                out.write("\n")
                out.flush()
                os.fsync(out.fileno())
            self._sync_intent_directory(runtime)
        except FileExistsError as exc:
            raise SandboxCleanupError(name, self.session_id,
                                      "Prior durable Docker intent remains; operator review required") from exc
        return path

    @staticmethod
    def _sync_intent_directory(directory: Path) -> None:
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _finish_intent(self, path: Path) -> None:
        path.unlink()
        self._sync_intent_directory(path.parent)

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
            validate_workspace(workspace, self.workspace_limit_bytes, max_path_bytes=self.workspace_path_limit_bytes)
            if shutil.disk_usage(workspace).free < self.min_free_disk_bytes:
                raise SandboxError("free disk is below admission floor")
            name = "playground-job-" + uuid.uuid4().hex
            deadline = time.monotonic() + timeout_s
            launch_attempted = False
            uncertain_startup = False
            intent = None
            def checked(args):
                nonlocal launch_attempted, uncertain_startup, intent
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise SandboxError("sandbox setup exceeded time budget")
                launching = args[0] == "run"
                if launching:
                    intent = self._begin_intent(name)
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
                stage = Path(tempfile.mkdtemp(prefix=".playground-export-", dir=workspace.parent))
                publication_started = False
                publication_committed = False
                try:
                    _publication_journal(stage, workspace.name, "copying")
                    snapshot = stage / "workspace"
                    snapshot.mkdir()
                    archive = stage / "workspace.tar"
                    copied = self.runner(["docker", "cp", f"{name}:/workspace/.", "-"],
                                         timeout_s=max(0.1, deadline-time.monotonic()),
                                         output_limit=self.workspace_limit_bytes + 16*1024**2,
                                         stdout_file=archive)
                    if copied.exit_code:
                        raise SandboxError(copied.stderr or "artifact copy failed")
                    extract_workspace(archive, snapshot, self.workspace_limit_bytes)
                    validate_workspace(snapshot, self.workspace_limit_bytes, max_path_bytes=self.workspace_path_limit_bytes)
                    _publication_journal(stage, workspace.name, "prepared")
                    publication_started = True
                    # Both renames are inside the BaseException boundary. No
                    # auto-cleanup may ever delete the sole remaining original.
                    workspace.rename(stage / "previous")
                    _publication_sync(workspace.parent)
                    _publication_sync(stage)
                    snapshot.rename(workspace)
                    _publication_sync(workspace.parent)
                    _publication_sync(stage)
                    _publication_journal(stage, workspace.name, "committed")
                    publication_committed = True
                    _discard_publication(stage)
                except BaseException as exc:
                    if publication_started and not publication_committed:
                        try:
                            _recover_publication(stage, self.workspace_limit_bytes)
                        except BaseException as recovery_error:
                            raise SandboxCleanupError(name, self.session_id,
                                f"Copyback rollback unconfirmed; original/candidate retained at {stage}: {recovery_error}") from exc
                        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                            raise
                        raise SandboxCleanupError(name, self.session_id,
                            f"Copyback rolled back; candidate retained at {stage} for review") from exc
                    if not publication_started:
                        # Original has not moved; failed extraction may safely
                        # discard only its own bounded temporary candidate.
                        _discard_publication(stage)
                    raise
                return result
            except FileNotFoundError as exc:
                raise SandboxError("Docker CLI or workspace is unavailable; arbitrary code has no host fallback") from exc
            finally:
                # Exact generated name, never prune or touch another workload.
                if launch_attempted:
                    self._cleanup(name, uncertain_startup=uncertain_startup)
                    if uncertain_startup:
                        raise SandboxCleanupError(name, self.session_id,
                                                  "Startup was interrupted; durable intent retained for operator review")
                if intent is not None:
                    self._finish_intent(intent)
