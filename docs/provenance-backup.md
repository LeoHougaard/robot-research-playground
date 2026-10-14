# Evidence identity and stopped-state recovery

Every new research state is bound before its archive is opened. `provenance.json`
and the SQLite `provenance` table contain the same manifest and SHA-256 identity.
The identity covers the supplied configuration (except state location), actual
installed Python source bytes, controller safety limits, tether limit, actual
simulation dimensions/speed, and every supplied `evidence_inputs` file's bytes.
File locations are replaced by content hashes, so relocating unchanged inputs or
restoring to a new directory does not change the scientific identity.

Changing configuration, software, geometry or bound input content requires a NEW
state directory. Startup rejects a mismatch before migrating, recovering trials,
or loading the old retained model. It also checks registered artifact bytes.
Existing unbound archives, workspaces and model caches cannot silently acquire a
new identity. They remain readable as legacy evidence; preserve them separately.
Only a strict bounded allowlist of launcher session/PID/lock/log metadata may
precede first binding. Up to 256 versioned historical sessions are accepted there
only when their IDs match their filenames; malformed records and incomplete
retirement files remain refused. Those logs never become measured research evidence.

Physical mode additionally requires real local `evidence_inputs` paths for
`geometry`, `firmware`, `calibration`, and `calibration_validation`. The hardware
CLI binds its actual calibration and validation files. Geometry and firmware
must be supplied explicitly. A firmware file hash does **not** establish what is
flashed on a device, and file fingerprints do not establish measured calibration,
mechanical fit, safe wiring or physical commissioning. All existing physical
acceptance gates and `HARDWARE_VERIFIED=0` remain in force.

## Make and validate a recovery copy

Stop the research supervisor first. The backup command requires its exclusive OS
lock and refuses an active supervisor. Stop launcher processes through the reviewed
launcher procedure where applicable; do not kill unrelated processes or clear an
unconfirmed session merely to obtain a snapshot.

```bash
python -m playground backup --state-dir state/research --destination /mounted-backup/research-2026-10-13
python -m playground restore --backup /mounted-backup/research-2026-10-13 --state-dir state/recovered-2026-10-13
```

Destination parents must exist. Both destinations must be NEW directories,
separate from their sources. Existing destinations, even empty ones, are refused.
The saved positive `disk_reserve_bytes` floor applies to the destination too;
absent a saved override it is 20 GiB. `--disk-reserve-bytes` may increase that
floor, never reduce it. Admission includes the complete copy, WAL/database-copy
headroom and filesystem/manifest overhead, with additional checks while copying.
There is no automatic evidence deletion to make space.

The current single-manifest format supports at most **100,000 regular files**
and a conservative estimated manifest of at most **32 MiB (33,554,432 bytes)**.
Preflight estimates `512 + sum(JSON-encoded relative-path bytes + 128)`; JSON
escaping is included. Either bound refuses the backup **before staging or
copying starts**, preserving the source. These are format capacities, not a
promise that every state admitted by the larger runtime byte quota is
snapshot-capable. For example, 56,000 512-byte paths exceed the manifest budget
even if their file contents are tiny. Plan bounded research runs and separately
validated backups within these limits; there is no automatic pruning, migration
or sharded-backup fallback for larger existing states.

On filesystems that report a fixed inode pool, destination admission also
requires enough free inodes for every copied file and necessary parent directory,
eight temporary/publication entries, and **1,024 additional reserved inodes**.
It checks this before creating staging and again during copying and publication.
Filesystems that do not expose a fixed inode pool cannot provide this check.
Byte/inode admission is conservative application-level checking, not an exclusive
filesystem reservation against unrelated processes consuming space concurrently.

A snapshot contains a consistent SQLite backup, including committed WAL rows,
all retained regular state files, and `snapshot.json` with SHA-256 and byte counts.
The transient supervisor lock and SQLite sidecars are excluded. Registered
artifacts must also match their independent hashes inside SQLite. Symlinks,
hardlinks, nonregular files and unsafe relative paths are refused. The staged
copy is validated and fsynced before atomic publication; interruption leaves the
original intact and never publishes a partial destination. A crash may leave a
hidden staging folder or an empty reserved destination, neither a valid backup.
Inspect such debris before any manual cleanup.

Restore validates all hashes, the database integrity, its provenance binding and
registered artifacts before copying, then verifies the NEW copy before publishing
it. It never overwrites the source state or backup. Incomplete trials retain their
original statuses in the backup; the first authorized runtime restart performs
normal interrupted-trial recovery, never converts them into successful evidence.

Run the simulator directly against the restored directory only with the same
configuration and source/input bytes, after reviewing the validation result.
Restoring files is not permission to restart hardware. Historical launcher
session and PID files are preserved verbatim, including their original state
path and process/daemon identities. A Spark launcher may correctly refuse those
stale identities on the new path. Review the original session's exact process
and container ownership through the documented status/stop procedure first;
ambiguous ownership requires operator review. Do not rewrite identities, delete
records, or remove containers just to make the restored launcher proceed.

## Scope of verification

`tests/test_provenance_backup.py` covers configuration and geometry drift,
unchanged restart, actual input hashing, legacy refusal, altered artifacts,
committed WAL snapshot, corruption (including a forged outer file checksum),
interrupted copy/restore, low destination disk/inodes, pre-copy manifest capacity,
unsafe paths/links, existing-target protection and retention of historical launcher records. Integration tests also
reject corruption introduced after startup at artifact import.

These are software recovery fixtures, not a real power-loss or failed-disk test.
Checksums detect corruption, not an adversary who rewrites all records. A copy on
the same disk does not survive that disk's failure. Keep a separately protected,
independently validated off-host copy before claiming disaster recovery.
