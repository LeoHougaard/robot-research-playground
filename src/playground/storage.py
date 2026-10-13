"""Conservative aggregate admission for trusted state writers.

This is an application quota, not a filesystem/project quota: another process on
this filesystem can still consume the free-space reserve. No evidence is evicted.
A generation reserves its full capped database growth, a complete possible WAL,
workspace/copyback peak, and separate finalization and launcher-log headroom.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import time
import json

MIB = 1024**2
MAX_WORKSPACE_ENTRIES = 4096
TRANSCRIPT_BYTES = MIB
RECOVERY_BYTES = 16 * MIB
LOG_HEADROOM_BYTES = 16 * MIB
AUXILIARY_BYTES = MIB
# Small files/directories consume blocks even when their logical size is zero.
ENTRY_OVERHEAD = 8192


class StorageLimitError(RuntimeError):
    """Stop admission, preserve every existing file, and leave restart possible."""


def positive_int(value, name, *, maximum=None):
    if type(value) is not int or value <= 0 or (maximum is not None and value > maximum):
        raise ValueError(f"{name} must be a positive integer" + (f" at most {maximum}" if maximum else ""))
    return value


def positive_number(value, name):
    try:
        valid = type(value) in (int, float) and math.isfinite(value) and value > 0
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError(f"{name} must be a positive finite number")
    return value


def validate_storage_config(config):
    if not isinstance(config, dict):
        raise ValueError("Research configuration must be an object")
    try:
        if len(json.dumps(config, allow_nan=False).encode()) > 32768:
            raise ValueError("Research configuration exceeds 32768 bytes")
    except (TypeError, OverflowError, RecursionError) as exc:
        raise ValueError("Research configuration must be bounded JSON") from exc
    positive_int(config.get("state_limit_bytes", 16 * 1024**3), "state_limit_bytes")
    positive_int(config.get("disk_reserve_bytes", 20 * 1024**3), "disk_reserve_bytes")
    workspace = positive_int(config.get("workspace_limit_bytes", 64 * MIB), "workspace_limit_bytes", maximum=256 * MIB)
    if workspace < MIB:
        raise ValueError("workspace_limit_bytes must be at least one MiB")
    positive_int(config.get("generation_database_limit_bytes", 32 * MIB), "generation_database_limit_bytes")
    for key, maximum in (("max_generations", 100000), ("steps_per_generation", 1000)):
        if key in config:
            positive_int(config[key], key, maximum=maximum)
    for key in ("max_run_seconds", "max_action_seconds"):
        if key in config:
            positive_number(config[key], key)


def allocated_bytes(info):
    return max(ENTRY_OVERHEAD, info.st_size, getattr(info, "st_blocks", 0) * 512)


def tree_bytes(root):
    """Count all state, including unknown logs, sparse files and staging debris.

    Links/special files and nested mount points cannot hide unaccounted storage.
    Count hard links conservatively more than once rather than undercounting.
    """
    root = Path(root)
    if not root.exists():
        return 0
    device = root.lstat().st_dev
    total = 0
    stack = [root]
    while stack:
        path = stack.pop()
        info = path.lstat()
        if info.st_dev != device or stat.S_ISLNK(info.st_mode) or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
            raise StorageLimitError(f"State contains a linked, mounted or special path: {path}")
        total += allocated_bytes(info)
        if stat.S_ISDIR(info.st_mode):
            with os.scandir(path) as entries:
                stack.extend(Path(entry.path) for entry in entries)
    return total


@dataclass(frozen=True)
class Reservation:
    existing_bytes: int
    additional_bytes: int
    limit_bytes: int


class BudgetConnection(sqlite3.Connection):
    """One bounded WAL transaction at a time, including with pinned readers.

    Cache spilling is disabled so a page occurs at most once per transaction's
    WAL. max_page_count bounds the database, and checkpointing before the next
    writer prevents an old reader from permitting unbounded WAL accumulation.
    """
    def __init__(self, *args, storage, **kwargs):
        super().__init__(*args, **kwargs)
        self.storage = storage
        self._inside_script = False

    def _before_write(self):
        if not self.in_transaction and not self._inside_script:
            self.storage.before_transaction(self)

    @staticmethod
    def _is_write(sql):
        return sql.lstrip().split(None, 1)[0].upper() in {"INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "DROP", "ALTER", "BEGIN"}

    def execute(self, sql, parameters=(), /):
        if self._is_write(sql):
            self._before_write()
        try:
            return super().execute(sql, parameters)
        except sqlite3.Error as exc:
            if getattr(exc, "sqlite_errorcode", None) == sqlite3.SQLITE_FULL:
                self.storage.fail("Generation database growth limit reached")
            raise

    def executemany(self, sql, parameters, /):
        if self._is_write(sql):
            self._before_write()
        try:
            return super().executemany(sql, parameters)
        except sqlite3.Error as exc:
            if getattr(exc, "sqlite_errorcode", None) == sqlite3.SQLITE_FULL:
                self.storage.fail("Generation database growth limit reached")
            raise

    def executescript(self, script, /):
        self._before_write()
        # Schema initialization is trusted and contains no transaction control.
        # One transaction also bounds its WAL rather than appending per DDL row.
        self._inside_script = True
        try:
            return super().executescript("BEGIN IMMEDIATE;\n" + script + "\nCOMMIT;")
        finally:
            self._inside_script = False


class StateStorage:
    def __init__(self, directory, config, *, on_stop=None):
        validate_storage_config(config)
        self.directory = Path(directory)
        self.limit = config.get("state_limit_bytes", 16 * 1024**3)
        self.disk_reserve = config.get("disk_reserve_bytes", 20 * 1024**3)
        self.workspace_limit = config.get("workspace_limit_bytes", 64 * MIB)
        self.database_growth = config.get("generation_database_limit_bytes", 32 * MIB)
        self.on_stop = on_stop
        self.failed = None
        self.connection = None
        self.active_workspace = None
        self.phase = "recovery"
        self.database_cap = 0
        self._fixed_bytes = 0
        self._log_baseline = 0
        self._workspace_bytes = 0
        self.reservation = None
        runtime = os.environ.get("PLAYGROUND_RUNTIME_DIR")
        if runtime and not Path(runtime).resolve().is_relative_to(self.directory.resolve()):
            raise ValueError("PLAYGROUND_RUNTIME_DIR must be inside the state directory for storage accounting")
        self.refresh()

    def fail(self, reason):
        self.failed = reason
        if self.on_stop is not None:
            self.on_stop(reason)
        raise StorageLimitError(reason + "; original evidence has been preserved")

    @property
    def workspace_peak(self):
        # Original + tar (bounded by sandbox) + extracted snapshot coexist.
        return 3 * self.workspace_limit + 16 * MIB + 2 * MAX_WORKSPACE_ENTRIES * ENTRY_OVERHEAD + TRANSCRIPT_BYTES

    @staticmethod
    def _live_files(directory):
        # Only these flat directories have trusted mutable files. Archived logs,
        # completed workspaces and arbitrary old subtrees are immutable during
        # a run and are counted in the full generation-boundary inventory.
        if not directory.exists():
            return 0
        total = allocated_bytes(directory.lstat())
        for entry in directory.iterdir():
            if not entry.is_dir() or entry.is_symlink():
                total += tree_bytes(entry)
        return total

    def _dynamic_bytes(self):
        if not self.directory.exists():
            return 0
        total = self._live_files(self.directory)
        for name in ("archive", "runtime"):
            total += self._live_files(self.directory / name)
        return total

    def refresh(self):
        total = tree_bytes(self.directory)
        self._fixed_bytes = total - self._dynamic_bytes()
        self._log_baseline = self._live_files(self.directory / "runtime")
        return total

    def _wal_bytes(self, database_cap=None):
        cap = self.database_cap if database_cap is None else database_cap
        page = self.page_size if self.connection is not None else 4096
        pages = (cap + page - 1) // page
        # WAL headers/frames plus WAL-index shared memory rounded in 32KiB chunks.
        return 32 + pages * (page + 24) + (pages // 4062 + 2) * 32768

    def _check_inodes(self, required):
        if hasattr(os, "statvfs"):
            information = os.statvfs(self.directory)
            # Some filesystems report no fixed inode pool. Do not invent one.
            if information.f_files > 0 and information.f_favail < required:
                self.fail("Insufficient free inodes for bounded state writes")

    def _admit(self, additional, *, refresh=True):
        self._check_inodes(128)
        current = self.refresh() if refresh else self._fixed_bytes + self._dynamic_bytes()
        if current + additional > self.limit:
            self.fail(f"Aggregate state quota cannot reserve the next bounded generation ({current} + {additional} > {self.limit} bytes)")
        if shutil.disk_usage(self.directory).free < self.disk_reserve + additional:
            self.fail("Free disk cannot reserve the full bounded generation plus the disk reserve")
        self.reservation = Reservation(current, additional, self.limit)
        return self.reservation

    def connect(self, path):
        path = Path(path)
        existing = path.stat().st_size if path.exists() else 0
        # After a crash, committed pages may exist only in the WAL. Its complete
        # byte length is a conservative bound on those additional database pages.
        wal = Path(str(path) + "-wal")
        if wal.exists():
            existing += wal.stat().st_size
        self._admit(RECOVERY_BYTES + self._wal_bytes(existing + RECOVERY_BYTES) + LOG_HEADROOM_BYTES + AUXILIARY_BYTES)
        connection = sqlite3.connect(path, timeout=1, factory=lambda *a, **kw: BudgetConnection(*a, storage=self, **kw))
        self.connection = connection
        self.page_size = sqlite3.Connection.execute(connection, "PRAGMA page_size").fetchone()[0]
        sqlite3.Connection.execute(connection, "PRAGMA cache_spill=OFF")
        sqlite3.Connection.execute(connection, "PRAGMA wal_autocheckpoint=0")
        sqlite3.Connection.execute(connection, "PRAGMA journal_size_limit=0")
        # A single stored row or SQL string may not make an unbounded transaction.
        connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 64 * 1024)
        self.recovery_cap = self.database_bytes() + RECOVERY_BYTES
        self._set_cap(self.recovery_cap)
        return connection

    def database_bytes(self):
        return sqlite3.Connection.execute(self.connection, "PRAGMA page_count").fetchone()[0] * self.page_size

    def _set_cap(self, size):
        pages = (size + self.page_size - 1) // self.page_size
        actual = sqlite3.Connection.execute(self.connection, f"PRAGMA max_page_count={pages}").fetchone()[0]
        self.database_cap = actual * self.page_size

    def begin_generation(self):
        # The new workspace and extracted snapshot coexist during copyback.
        self._check_inodes(2 * MAX_WORKSPACE_ENTRIES + 128)
        self.before_transaction(self.connection)
        base = self.database_bytes()
        additional = self.workspace_peak + self.database_growth + RECOVERY_BYTES + self._wal_bytes(base + self.database_growth + RECOVERY_BYTES) + LOG_HEADROOM_BYTES + AUXILIARY_BYTES
        result = self._admit(additional)
        self.generation_base = base
        self.recovery_cap = base + self.database_growth + RECOVERY_BYTES
        self._set_cap(base + self.database_growth)
        self.phase = "generation"
        self.failed = None
        return result

    def finish_generation(self):
        # This tail was reserved before admitting the generation. It remains
        # available after an ordinary tool exhausts its database growth budget.
        self.phase = "recovery"
        self._set_cap(self.recovery_cap)

    def end_generation(self):
        self.active_workspace = None
        self._workspace_bytes = 0
        self.refresh()
        self.phase = "recovery"
        # Keep the original admitted cap. Repeated finalization cannot expand it.
        self._set_cap(self.recovery_cap)

    def check(self, *, measure_workspace=True):
        self._check_inodes(32)
        if self.failed and self.phase != "recovery":
            self.fail(self.failed)
        # Only bounded mutable locations are revisited per tool/transaction.
        # Older workspaces are rescanned once at each generation boundary.
        used = self._fixed_bytes + self._dynamic_bytes()
        if measure_workspace:
            self._workspace_bytes = tree_bytes(self.active_workspace) if self.active_workspace else 0
        if used + self._workspace_bytes > self.limit:
            self.fail("Aggregate state quota reached")
        if self._live_files(self.directory / "runtime") > self._log_baseline + LOG_HEADROOM_BYTES:
            self.fail("Runtime log growth exceeded reserved headroom")
        if shutil.disk_usage(self.directory).free < self.disk_reserve:
            self.fail("Disk reserve reached")

    def before_transaction(self, connection):
        self.check(measure_workspace=False)
        # Do not permit a second WAL transaction to accumulate behind a reader.
        # A short total retry window permits normal viewer snapshots without
        # letting a persistent reader turn this into an unbounded wait.
        old_timeout = sqlite3.Connection.execute(connection, "PRAGMA busy_timeout").fetchone()[0]
        sqlite3.Connection.execute(connection, "PRAGMA busy_timeout=0")
        try:
            deadline = time.monotonic() + .25
            while True:
                result = sqlite3.Connection.execute(connection, "PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                if not result or not result[0] or time.monotonic() >= deadline:
                    break
                time.sleep(min(.01, max(0, deadline - time.monotonic())))
        finally:
            sqlite3.Connection.execute(connection, f"PRAGMA busy_timeout={old_timeout}")
        if result and result[0]:
            self.fail("A reader prevents bounded WAL checkpointing; stop the reader before restart")
        # A transaction can rewrite existing pages as well as grow the database.
        remaining = max(0, self.database_cap - self.database_bytes()) + self._wal_bytes()
        if shutil.disk_usage(self.directory).free < self.disk_reserve + remaining:
            self.fail("Disk no longer has the reserved database/WAL headroom")
