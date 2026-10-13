"""Content-bound evidence identity and read-only integrity preflight.

A fingerprint identifies supplied files and software, not physical commissioning
or the firmware actually flashed on a controller. Legacy evidence is never
silently adopted into a new identity.
"""
from __future__ import annotations

from dataclasses import asdict
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import stat
import tempfile

from .archive import canonical

BINDING_NAME = "provenance.json"
TABLES = ("runs", "generations", "experiments", "findings", "evaluations", "artifacts")


def safe_directory(path):
    """Reject links in all existing components before resolving a path."""
    path = Path(os.path.abspath(path))
    for item in (path, *path.parents):
        if item.is_symlink():
            raise ValueError("Linked state or snapshot paths are not permitted")
        if item.exists() and not item.is_dir():
            raise ValueError("Expected a directory")
    return path


def relative_path(value):
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise ValueError("Unsafe relative evidence path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(p in (".", "..", "") for p in value.split("/")) or path.as_posix() != value:
        raise ValueError("Unsafe relative evidence path")
    return path


def file_digest(path):
    """Hash a regular file without following final or parent symlinks."""
    path = Path(os.path.abspath(path))
    safe_directory(path.parent)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise ValueError("Evidence must be an unlinked regular file")
    digest, size = hashlib.sha256(), 0
    with os.fdopen(os.open(path, flags), "rb") as stream:
        opened = os.fstat(stream.fileno())
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise ValueError("Evidence changed while opening")
        while data := stream.read(1024 * 1024):
            digest.update(data)
            size += len(data)
        after = os.fstat(stream.fileno())
    current = path.lstat()
    signature = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    if signature(before) != signature(after) or signature(after) != signature(current):
        raise ValueError("Evidence changed while hashing")
    return {"sha256": digest.hexdigest(), "bytes": size}


def state_files(root):
    root = safe_directory(root)
    result = {}
    if not root.is_dir():
        raise ValueError("State directory does not exist")
    for directory, directories, files in os.walk(root, followlinks=False):
        for name in directories:
            safe_directory(Path(directory) / name)
        for name in files:
            path = Path(directory) / name
            rel = path.relative_to(root).as_posix()
            relative_path(rel)
            mode = path.lstat().st_mode
            if not stat.S_ISREG(mode) or path.is_symlink() or path.stat().st_nlink != 1:
                raise ValueError("State contains a linked or nonregular file")
            result[rel] = path
    return result


def read_only_database(path):
    file_digest(path)  # Validate path/type before SQLite opens it.
    db = sqlite3.connect(Path(path).absolute().as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    return db


def validate_manifest(manifest):
    if not isinstance(manifest, dict) or set(manifest) != {"format_version", "fingerprint", "payload"} or manifest["format_version"] != 1:
        raise ValueError("Unsupported evidence provenance")
    payload = manifest["payload"]
    required = {"mode", "config", "backend", "safety_limits", "tether_limit_rad", "inputs", "runtime_files", "physical_attestation", "geometry"}
    if (not isinstance(payload, dict) or set(payload) != required
            or payload["mode"] not in ("physical", "simulation")
            or not isinstance(payload["config"], dict) or not isinstance(payload["safety_limits"], dict)
            or not isinstance(payload["backend"], str) or not payload["backend"]
            or not isinstance(payload["geometry"], dict)
            or payload["physical_attestation"] != "not established by file fingerprints"):
        raise ValueError("Invalid evidence provenance payload")
    for key in ("inputs", "runtime_files"):
        records = payload[key]
        if not isinstance(records, dict) or (key == "runtime_files" and not records):
            raise ValueError("Invalid provenance source files")
        for name, value in records.items():
            if (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", name)
                    or not isinstance(value, dict) or set(value) != {"sha256", "bytes"}
                    or type(value["bytes"]) is not int or value["bytes"] < 0
                    or not isinstance(value["sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", value["sha256"])):
                raise ValueError("Invalid provenance source hash or size")
    if payload["mode"] == "physical" and not {"geometry", "firmware", "calibration", "calibration_validation"}.issubset(payload["inputs"]):
        raise ValueError("Physical provenance source files are incomplete")
    expected = hashlib.sha256(canonical(payload).encode()).hexdigest()
    if manifest["fingerprint"] != expected:
        raise ValueError("Evidence provenance fingerprint is corrupt")
    return manifest


def build_provenance(config, controller):
    if not isinstance(config, dict):
        raise ValueError("Configuration must be an object")
    effective = json.loads(canonical(config))
    effective.pop("state_dir", None)  # A validated restore may use a new location.
    inputs = effective.pop("evidence_inputs", {})
    if not isinstance(inputs, dict) or any(not isinstance(k, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", k) or not isinstance(v, str) for k, v in inputs.items()):
        raise ValueError("evidence_inputs must map names to actual local file paths")
    mode = "physical" if controller.backend.is_real else "simulation"
    if mode == "physical" and not {"geometry", "firmware", "calibration", "calibration_validation"}.issubset(inputs):
        raise ValueError("Physical provenance requires geometry, firmware, calibration and calibration_validation files")
    payload = {
        "mode": mode,
        "config": effective,
        "backend": type(controller.backend).__module__ + "." + type(controller.backend).__qualname__,
        "safety_limits": asdict(controller.limits),
        "tether_limit_rad": controller._tether_limit,
        "inputs": {key: file_digest(Path(value)) for key, value in sorted(inputs.items())},
        "runtime_files": {path.name: file_digest(path) for path in sorted(Path(__file__).parent.glob("*.py"))},
        "physical_attestation": "not established by file fingerprints",
    }
    if mode == "simulation":
        payload["geometry"] = {name: getattr(controller.backend, name) for name in
                               ("width", "height", "wheel_base", "radius", "max_wheel_mps")}
    else:
        payload["geometry"] = {"source": "inputs.geometry", "measurement_validity": "requires operator commissioning"}
    return {"format_version": 1, "fingerprint": hashlib.sha256(canonical(payload).encode()).hexdigest(), "payload": payload}


def read_binding(root):
    path = Path(root) / BINDING_NAME
    file_digest(path)
    if path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError("Evidence provenance file is oversized")
    return validate_manifest(json.loads(path.read_text(encoding="utf-8")))


def validate_artifacts(db, root):
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not set(TABLES).issubset(tables):
        raise ValueError("Evidence database schema is incomplete")
    for row in db.execute("SELECT path,sha256,bytes FROM artifacts"):
        rel = relative_path(row["path"])
        if len(rel.parts) < 3 or rel.parts[0] != "workspaces":
            raise ValueError("Archived artifact escapes the workspace tree")
        actual = file_digest(Path(root).joinpath(*rel.parts))
        if actual != {"sha256": row["sha256"], "bytes": row["bytes"]}:
            raise ValueError("Archived artifact integrity check failed")


def check_database_binding(db, manifest, *, allow_empty=False):
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "provenance" not in tables:
        nonempty = any(db.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone() for table in TABLES if table in tables)
        if not allow_empty or nonempty:
            raise ValueError("Existing unbound evidence requires a new state directory; it cannot inherit provenance")
        return False
    rows = db.execute("SELECT manifest_json FROM provenance").fetchall()
    if len(rows) != 1 or validate_manifest(json.loads(rows[0][0])) != manifest:
        raise ValueError("Database evidence provenance differs from the state binding")
    return True


def _launcher_session(path):
    """Parse only the bounded, versioned lifecycle identity written by launchers."""
    if path.stat().st_size > 4096:
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(value, dict) or set(value) != {"version", "id", "mode", "docker"}
                or value["version"] != 2 or value["mode"] not in ("research", "demo")
                or not isinstance(value["id"], str) or not re.fullmatch(r"[a-f0-9]{32}", value["id"])):
            return None
        docker = value["docker"]
        if docker is not None:
            if (not isinstance(docker, dict) or set(docker) != {"endpoint", "daemon_id", "root"}
                    or not isinstance(docker["endpoint"], str) or not docker["endpoint"].startswith("unix:///")
                    or not isinstance(docker["daemon_id"], str) or not 1 <= len(docker["daemon_id"]) <= 256
                    or not isinstance(docker["root"], str) or not Path(docker["root"]).is_absolute()):
                return None
        if value["mode"] == "research" and docker is None:
            return None
        return value
    except (ValueError, KeyError, TypeError):
        return None


def _launcher_only(root, files):
    """Only bounded lifecycle metadata may precede first research binding.

    These logs/PIDs are never promoted into measured evidence. Existing archive,
    workspaces, checkpoints or unrecognized files always require a new state.
    """
    if any(path.name not in {"supervisor.lock", "runtime"} for path in root.iterdir()):
        return False
    session = None
    if "runtime/session" in files:
        value = _launcher_session(files["runtime/session"])
        if value is None:
            return False
        session = value["id"]
    histories = 0
    total_logs = 0
    for name, path in files.items():
        if name == "supervisor.lock":
            continue
        if name in {"runtime/launcher.lock", "runtime/registration.lock"}:
            if path.stat().st_size > 1:
                return False
        elif name == "runtime/session":
            continue
        elif re.fullmatch(r"runtime/sessions/[a-f0-9]{32}\.json", name):
            value = _launcher_session(path)
            histories += 1
            if value is None or value["id"] != path.stem or histories > 256:
                return False
        elif re.fullmatch(r"runtime/(worker|server)\.pid(\.tmp)?", name):
            if path.stat().st_size > 4096:
                return False
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
                role = path.name.split(".")[0]
                if (set(value) != {"version", "pid", "start_ticks", "boot_id", "role", "state_dir", "session_id"}
                        or value["version"] != 1 or type(value["pid"]) is not int or value["pid"] <= 1
                        or value["role"] != role or value["state_dir"] != str(root) or not session
                        or value["session_id"] != session or not isinstance(value["start_ticks"], str)
                        or not value["start_ticks"].isdigit()
                        or not isinstance(value["boot_id"], str)
                        or not re.fullmatch(r"[a-f0-9-]{36}", value["boot_id"])):
                    return False
            except (ValueError, KeyError, TypeError):
                return False
        elif re.fullmatch(r"runtime/((worker|server)\.log|logs/[a-f0-9]{32}(-[a-f0-9]{32})?-(worker|server)\.log)", name):
            size = path.stat().st_size
            total_logs += size
            if size > 8 * 1024**2 or total_logs > 64 * 1024**2:
                return False
        else:
            return False
    runtime = root / "runtime"
    if runtime.exists():
        for directory, children, _ in os.walk(runtime):
            if any((Path(directory) / name).relative_to(root).as_posix() not in {"runtime/logs", "runtime/sessions"} for name in children):
                return False
    return True


def inspect_state(root, expected=None):
    """Read-only check used before Archive performs any migrations or recovery."""
    root = safe_directory(root)
    files = state_files(root)
    if BINDING_NAME not in files:
        if not _launcher_only(root, files):
            raise ValueError("Existing unbound evidence requires a new state directory; it cannot inherit provenance")
        return None
    manifest = read_binding(root)
    if expected is not None and manifest != validate_manifest(expected):
        raise ValueError("Evidence configuration or source fingerprint changed; use a new state directory")
    database = root / "archive" / "evidence.sqlite3"
    if database.exists():
        with closing(read_only_database(database)) as db:
            if check_database_binding(db, manifest, allow_empty=True):
                validate_artifacts(db, root)
    elif set(files) - {"supervisor.lock", BINDING_NAME}:
        raise ValueError("Bound evidence is missing its database")
    return manifest


def fsync_directory(path):
    if os.name != "nt":
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def bind_state(root, manifest):
    """Caller holds StateLock. Refusals leave all earlier evidence untouched."""
    validate_manifest(manifest)
    root = safe_directory(root)
    previous = inspect_state(root, manifest)
    if previous is not None:
        return manifest
    fd, name = tempfile.mkstemp(prefix=".provenance-", suffix=".tmp", dir=root)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(canonical(manifest) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, root / BINDING_NAME)
        fsync_directory(root)
    finally:
        temporary.unlink(missing_ok=True)
    return manifest


def bind_archive(db, manifest):
    """Store the same identity in SQLite; never upgrade nonempty legacy data."""
    validate_manifest(manifest)
    if check_database_binding(db, manifest, allow_empty=True):
        return
    with db:
        # sqlite3's context manager does not start a transaction for DDL.
        # Explicit BEGIN makes schema creation and its one binding row atomic.
        if not db.in_transaction:
            db.execute("BEGIN IMMEDIATE")
        db.execute("CREATE TABLE provenance (singleton INTEGER PRIMARY KEY CHECK(singleton=1), manifest_json TEXT NOT NULL)")
        db.execute("INSERT INTO provenance VALUES(1,?)", (canonical(manifest),))
