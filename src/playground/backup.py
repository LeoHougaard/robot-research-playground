"""Stopped-state, checksummed snapshots and validated restore to a NEW directory.

The supervisor OS lock covers SQLite backup and artifact copying. A staging
folder is fsynced and atomically published; incomplete staging folders are not
valid backups. Hashes detect corruption, not a maliciously rewritten manifest.
Keep an independently protected/off-host copy for actual disk-loss recovery.
"""
from __future__ import annotations

from contextlib import closing
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import tempfile
import time

from .archive import canonical
from .provenance import (BINDING_NAME, check_database_binding, file_digest,
                         fsync_directory, inspect_state, read_binding,
                         read_only_database, relative_path, safe_directory,
                         state_files, validate_artifacts)
from .state import StateLock

DATABASE = "archive/evidence.sqlite3"
TRANSIENT = {"supervisor.lock", DATABASE + "-wal", DATABASE + "-shm", DATABASE + "-journal"}
MANIFEST = "snapshot.json"
DEFAULT_DISK_RESERVE = 20 * 1024**3
COPY_HEADROOM = 16 * 1024**2
MAX_MANIFEST_BYTES = 32 * 1024**2
MAX_SNAPSHOT_FILES = 100000
INODE_RESERVE = 1024
TEMPORARY_INODES = 8


def _capacity_preflight(names):
    """Bound format capacity before allocating a payload or building its JSON.

    JSON escaping is counted exactly for names; 128 bytes per record covers the
    fixed hash, a 20-digit size, punctuation and commas. 512 covers the envelope.
    """
    count, estimate, directories = 0, 512, set()
    for name in names:
        path = relative_path(name)
        count += 1
        estimate += len(canonical(name).encode("utf-8")) + 128
        if count > MAX_SNAPSHOT_FILES or estimate > MAX_MANIFEST_BYTES:
            raise ValueError("Snapshot capacity exceeded before copying: at most 100000 files and a 32 MiB estimated manifest are supported")
        directories.update(parent.as_posix() for parent in path.parents if parent != PurePosixPath("."))
    return {"files": count, "estimated_manifest_bytes": estimate,
            "directories": directories, "inodes": count + len(directories) + TEMPORARY_INODES}


def _inode_admission(parent, required):
    if hasattr(os, "statvfs"):
        information = os.statvfs(parent)
        # Filesystems without a fixed inode pool do not expose a meaningful cap.
        if information.f_files > 0 and information.f_favail < required + INODE_RESERVE:
            raise RuntimeError("Snapshot admission refused: destination needs copy inodes plus 1024 reserved inodes")


def _created_inode_cost(name, created_directories):
    directories = {parent.as_posix() for parent in PurePosixPath(name).parents if parent != PurePosixPath(".")}
    new = directories - created_directories
    created_directories.update(new)
    return 1 + len(new)


def _reserve(binding, requested):
    saved = binding["payload"].get("config", {}).get("disk_reserve_bytes", DEFAULT_DISK_RESERVE)
    for value in (saved, saved if requested is None else requested):
        if type(value) is not int or value <= 0:
            raise ValueError("Snapshot disk reserve must be a positive integer")
    return max(saved, saved if requested is None else requested)


def _disk_admission(parent, reserve, remaining):
    if shutil.disk_usage(parent).free < reserve + remaining:
        raise RuntimeError("Snapshot admission refused: destination needs the complete copy plus its disk reserve")


def _copy_file(source, target):
    """Copy a validated regular file and prove the copy matches its source."""
    expected = file_digest(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    with os.fdopen(os.open(source, flags), "rb") as src, target.open("xb") as out:
        remaining = expected["bytes"]
        while remaining:
            data = src.read(min(1024 * 1024, remaining))
            if not data:
                raise ValueError("Source shrank during snapshot")
            out.write(data)
            remaining -= len(data)
        if src.read(1):
            raise ValueError("Source grew during snapshot")
        out.flush()
        os.fsync(out.fileno())
    if file_digest(target) != expected or file_digest(source) != expected:
        raise ValueError("Source changed during snapshot")
    return expected


def _fsync_tree(root):
    for directory, _, files in os.walk(root, topdown=False):
        for filename in files:
            with (Path(directory) / filename).open("rb") as stream:
                os.fsync(stream.fileno())
        fsync_directory(directory)


def _publish_directory(staging, destination):
    """Publish without replacing an existing user's directory (even if empty)."""
    if os.name == "nt":
        # Windows rename refuses any existing destination.
        os.rename(staging, destination)
    else:
        # Reserve the final name using exclusive mkdir. POSIX rename can then
        # replace only our own empty reservation, never a pre-existing folder.
        destination.mkdir()
        try:
            os.replace(staging, destination)
        except BaseException:
            destination.rmdir()
            raise
    fsync_directory(destination.parent)


def _new_destination(destination, source):
    destination = safe_directory(destination)
    if destination.exists():
        raise ValueError("Destination must be a NEW directory; originals are never overwritten")
    if destination.is_relative_to(source) or source.is_relative_to(destination):
        raise ValueError("Snapshot and state directories must be separate")
    safe_directory(destination.parent)
    if not destination.parent.is_dir():
        raise ValueError("Destination parent directory must already exist")
    return destination


def _validate_database(root, manifest):
    with closing(read_only_database(root / DATABASE)) as db:
        result = db.execute("PRAGMA integrity_check").fetchall()
        if len(result) != 1 or result[0][0] != "ok":
            raise ValueError("Evidence database integrity check failed")
        check_database_binding(db, manifest)
        validate_artifacts(db, root)


def create_backup(state_dir, destination, *, disk_reserve_bytes=None):
    """Snapshot an exclusively owned, stopped state; no recovery mutates source."""
    source = safe_directory(state_dir)
    if not source.is_dir():
        raise ValueError("State directory does not exist")
    destination = _new_destination(destination, source)
    lock = StateLock(source)
    staging = None
    try:
        binding = inspect_state(source)
        if binding is None or not (source / DATABASE).is_file():
            raise ValueError("A bound evidence database is required for backup")
        _validate_database(source, binding)
        files = state_files(source)
        capacity = _capacity_preflight(name for name in files if name not in TRANSIENT)
        reserve = _reserve(binding, disk_reserve_bytes)
        # Include source WAL, database-copy/journal headroom, filesystem blocks,
        # per-entry metadata and manifest space, not merely logical payload bytes.
        remaining = sum(max(p.stat().st_size, getattr(p.stat(), "st_blocks", 0) * 512) for p in files.values())
        remaining += ((source / DATABASE).stat().st_size + len(files) * 16384 + COPY_HEADROOM
                      + capacity["estimated_manifest_bytes"] + len(capacity["directories"]) * 8192)
        _disk_admission(destination.parent, reserve, remaining)
        _inode_admission(destination.parent, capacity["inodes"])
        staging = Path(tempfile.mkdtemp(prefix=".snapshot-", dir=destination.parent))
        payload = staging / "state"
        (payload / "archive").mkdir(parents=True)
        # SQLite's backup API produces a coherent database, including committed
        # WAL content. Raw copying evidence.sqlite3 would silently lose WAL rows.
        with closing(read_only_database(source / DATABASE)) as live:
            with closing(sqlite3.connect(payload / DATABASE)) as snapshot:
                live.backup(snapshot)
                snapshot.execute("PRAGMA journal_mode=DELETE")
                snapshot.commit()
        records = {}
        created_directories = {"archive"}
        remaining_inodes = capacity["inodes"] - 2  # Database and archive directory exist.
        remaining = (sum(p.stat().st_size + 16384 for rel, p in files.items() if rel not in TRANSIENT and rel != DATABASE)
                     + COPY_HEADROOM + capacity["estimated_manifest_bytes"] + len(capacity["directories"]) * 8192)
        _disk_admission(destination.parent, reserve, remaining)
        for rel, path in sorted(files.items()):
            if rel not in TRANSIENT and rel != DATABASE:
                _disk_admission(destination.parent, reserve, remaining)
                _inode_admission(destination.parent, remaining_inodes)
                records[rel] = _copy_file(path, payload / rel)
                remaining -= records[rel]["bytes"] + 16384
                remaining_inodes -= _created_inode_cost(rel, created_directories)
        # Ensure no source file appeared/disappeared/changed during the copy.
        after_files = state_files(source)
        included = lambda names: set(names) - TRANSIENT - {DATABASE}
        if included(after_files) != included(files) or any(file_digest(after_files[rel]) != value for rel, value in records.items()):
            raise ValueError("State changed during snapshot")
        records[DATABASE] = file_digest(payload / DATABASE)
        _validate_database(payload, binding)
        manifest = {"format_version": 1, "created_unix": time.time(),
                    "provenance_fingerprint": binding["fingerprint"], "files": records}
        with (staging / MANIFEST).open("x", encoding="utf-8") as stream:
            stream.write(canonical(manifest) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        validate_backup(staging)
        _fsync_tree(staging)
        _disk_admission(destination.parent, reserve, 0)
        _inode_admission(destination.parent, 2)
        _publish_directory(staging, destination)
        staging = None
        return {"backup": str(destination), "files": len(records),
                "bytes": sum(v["bytes"] for v in records.values()),
                "provenance_fingerprint": binding["fingerprint"],
                "snapshot_sha256": file_digest(destination / MANIFEST)["sha256"]}
    finally:
        if staging is not None and staging.exists():
            shutil.rmtree(staging)
        lock.close()


def validate_backup(backup_dir):
    """Validate every byte and archived artifact without changing the backup."""
    root = safe_directory(backup_dir)
    all_files = state_files(root)
    if MANIFEST not in all_files or all_files[MANIFEST].stat().st_size > MAX_MANIFEST_BYTES:
        raise ValueError("Snapshot manifest is missing or oversized; backup is incomplete")
    file_digest(root / MANIFEST)
    manifest = json.loads((root / MANIFEST).read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or set(manifest) != {"format_version", "created_unix", "provenance_fingerprint", "files"} or manifest["format_version"] != 1:
        raise ValueError("Unsupported snapshot format")
    records = manifest["files"]
    if not isinstance(records, dict) or not {DATABASE, BINDING_NAME}.issubset(records):
        raise ValueError("Snapshot is missing required evidence")
    _capacity_preflight(records)
    for name, value in records.items():
        relative_path(name)
        if name in TRANSIENT or not isinstance(value, dict) or set(value) != {"sha256", "bytes"}:
            raise ValueError("Invalid snapshot file record")
        if type(value["bytes"]) is not int or value["bytes"] < 0 or not isinstance(value["sha256"], str) or len(value["sha256"]) != 64:
            raise ValueError("Invalid snapshot file hash or size")
    if set(all_files) != {MANIFEST, *("state/" + name for name in records)}:
        raise ValueError("Snapshot file set differs from its manifest")
    for name, value in records.items():
        if file_digest(root / "state" / name) != value:
            raise ValueError("Snapshot file integrity check failed")
    binding = read_binding(root / "state")
    if binding["fingerprint"] != manifest["provenance_fingerprint"]:
        raise ValueError("Snapshot provenance does not match its state")
    _validate_database(root / "state", binding)
    return manifest


def restore_backup(backup_dir, new_state_dir, *, disk_reserve_bytes=None):
    """Validate first, copy into staging, validate again, publish NEW state only."""
    source = safe_directory(backup_dir)
    destination = _new_destination(new_state_dir, source)
    manifest = validate_backup(source)
    manifest_hash = file_digest(source / MANIFEST)
    reserve = _reserve(read_binding(source / "state"), disk_reserve_bytes)
    capacity = _capacity_preflight(manifest["files"])
    remaining_inodes = capacity["inodes"]
    created_directories = set()
    remaining = (sum(value["bytes"] + 16384 for value in manifest["files"].values())
                 + COPY_HEADROOM + len(capacity["directories"]) * 8192)
    _disk_admission(destination.parent, reserve, remaining)
    _inode_admission(destination.parent, remaining_inodes)
    staging = Path(tempfile.mkdtemp(prefix=".restore-", dir=destination.parent))
    try:
        for rel, expected in sorted(manifest["files"].items()):
            _disk_admission(destination.parent, reserve, remaining)
            _inode_admission(destination.parent, remaining_inodes)
            if _copy_file(source / "state" / rel, staging / rel) != expected:
                raise ValueError("Backup changed during restore")
            remaining -= expected["bytes"] + 16384
            remaining_inodes -= _created_inode_cost(rel, created_directories)
        if file_digest(source / MANIFEST) != manifest_hash:
            raise ValueError("Backup manifest changed during restore")
        binding = inspect_state(staging)
        _validate_database(staging, binding)
        _fsync_tree(staging)
        _disk_admission(destination.parent, reserve, 0)
        _inode_admission(destination.parent, 2)
        _publish_directory(staging, destination)
        staging = None
        return {"state_dir": str(destination), "files": len(manifest["files"]),
                "provenance_fingerprint": binding["fingerprint"]}
    finally:
        if staging is not None and staging.exists():
            shutil.rmtree(staging)
