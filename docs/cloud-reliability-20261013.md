# Cloud reliability continuation — 2026-10-13

Status: cloud software acceptance and independent review passed; **not week-ready**.
This continuation starts from `857fbfa` and works only in an isolated cloud
checkout. It does not deploy to Spark, change other workloads, operate a camera,
modify firmware or move hardware. Previous verification reports and hardware
assets remain unchanged.

## Completed baseline

The unmodified source passed **96 tests, zero skips**, plus the four-generation
simulation with **112 trials and four evaluations**, on Linux x86-64/Python
3.12.14 with OpenCV-contrib-headless 4.14.0.94. The original verification reports
remain historical evidence from their original machines.

The first cloud attempt used a 4.9 GiB `/tmp` filesystem and failed ten tests at
its real 20 GiB free-space floor, with eight unavailable-OpenCV skips. Verification
was repeated in a dedicated virtual environment and a workspace temporary
directory on the larger filesystem. No runtime disk gate was reduced to make
those tests pass. Fixture-only explicit zero reserves were changed to one byte
when strict positive configuration validation was added.

## Reviewed changes

- Lifecycle serialization; boot/start-time/argv/session-bound process identity;
  pidfd-based exact-process signaling; worker survival checks; exact local Docker
  daemon/session binding; preserved old logs and session identities. Ambiguous
  ownership or in-flight launch cleanup refuses new work.
- Aggregate state and free-space admission before each generation; configured
  workspace limits; bounded SQLite growth/WAL; reserved finalization/copyback/log
  space; retained-file and inode accounting. Evidence is never evicted.
- Configuration, source, geometry, safety-limit and supplied input-file
  fingerprints, checked before archive mutation or retained-model loading.
  Unbound legacy research evidence remains separate rather than silently adopted.
- Stopped-state SQLite backup including committed WAL rows, all retained
  artifacts, checksummed snapshots, staged publication and restore to a NEW
  directory. See [recovery guide](provenance-backup.md).
- Transactional cached archive counts/error totals and indexed extrema. Both
  digest export and the read-only viewer use the cache. Legacy viewer access
  stays read-only and does not migrate old archives.
- Stricter actual-Docker qualification, a provenance/resource-bound local-model
  qualification harness, and commissioning/reset preparation with explicitly
  unqualified example evidence. See [runtime qualification](runtime-qualification.md)
  and [commissioning plan](commissioning-plan.md).
- Acceptance reports refuse overwrites, include source hashes, and cannot return
  an aggregate pass with skipped tests or source changes during the run.

## Measured cloud acceptance

Final source-stable acceptance: **229 tests passed, zero skips**, plus 112
simulation trials/four evaluations. See [exact source hashes and results](evidence/cloud-20261013/software-acceptance-01.json).
Independent recovery review closed all reproduced high-impact findings in its
scope, including cancellation and hard exits around both publication renames
and rollback steps. Durable journals preserve originals and quarantine unaccepted
candidates; restart recovery is idempotent. See [review ledger](review-cloud-reliability.md).

A final bounded accelerated simulation completed **225 generations, 6,300 trials
and 225 evaluations** in 20.13 seconds against a 20-second budget. It stopped
with SQLite integrity `ok` and zero pending trials. This is accelerated software
simulation, not wall-clock physical endurance. [Report](evidence/cloud-20261013/stress-20s.json).

The stopped 6,300-trial state was backed up, restored into a new directory and
continued to **6,328 trials**, retaining integrity and zero pending trials.
[Recovery result](evidence/cloud-20261013/restored-continuation.json).
The source state and snapshot remain intact. This is a same-disk cloud test.

A matched synthetic 100,000-trial archive benchmark returned identical aggregates:
median raw SQL **8.765 ms**, cached summary **0.0366 ms** over 20 repetitions.
This measures summary-query performance only. [Benchmark](evidence/cloud-20261013/archive-100k.json).

## Scope and limitations

This is an application budget, not an OS filesystem quota. Another process can
consume disk space. State inventory happens at generation boundaries and may
become expensive with many retained files. A reader pinning SQLite WAL for longer
than the bounded checkpoint retry causes a safe stop. Paths over the metadata
budget are rejected before sandbox publication. All existing originals remain.

The full configuration is immutable within one evidence identity, including
storage budgets. Changing it requires a new state; moving identical source/input
bytes or restoring to a new state location does not. No automatic provenance
migration or physical restart has been added.

Checksums detect corruption, not malicious rewriting of all hashes. A same-disk
snapshot is not off-host disaster recovery. Actual power-loss, failed-disk and
large production-archive restore acceptance remain necessary. The snapshot format
currently admits at most 100,000 files and a conservative 32 MiB manifest; it
refuses larger copies before staging. A runtime-admitted state can exceed those
backup limits, so representative-scale recovery remains an explicit gate. Restore preserves
historical launcher metadata; ambiguous ownership requires operator review.

The commissioning validator checks records for review. A passed record stage is
not permission to arm hardware. It rejects future or incorrectly ordered actual
measurements/reviews and invalid endurance claims, but cannot establish physical
truth from a human-entered file. The firmware remains `HARDWARE_VERIFIED=0`.

## Remaining gates

1. Publish the reviewed branch/PR through GitHub authenticated as LeoHougaard.
   Cloud CLI authentication was unavailable; connection was requested. No push
   or PR is claimed until a remote SHA and PR URL are verified.
2. Re-audit Spark through its approved access route. Resolve its documented root
   disk shortage and choose exact approved storage resources. The historical
   host inventory is not a current audit. Do not prune, stop unrelated workloads,
   relocate Docker or change drivers without the required approval.
3. Qualify actual ARM64 Docker copyback and resource enforcement, including the
   paused-tmpfs export behavior and crash/late-creation cases. Local fake-Docker
   fixtures cannot close these gates.
4. Qualify the chosen actual local model's guided tool behavior, stable process
   and model identity, latency and resource headroom; then test longer useful
   research runs. The cloud's configured model endpoint refused TCP connection;
   Docker and NVIDIA tooling are absent here.
5. Validate backups and independent storage recovery at representative scale,
   and select an off-host destination/schedule. No automatic backup schedule or
   new external sharing has been configured.
6. Close the optional standalone gateway HTTP/SDK bounds findings in the
   original isolation review before deploying that gateway. This continuation
   does not qualify that separate interface.
7. Build and commission with Leo present: fits, electrical cutoff, servo units,
   exposure-time freshness, calibrated geometry, tether and gripper behavior.
   Implement measured trusted reset/start-state checks before unattended trials.
8. Pass separate rescue-free 15-minute, 1-hour, 24-hour, 72-hour and one-week
   physical stages. Simulation duration and software tests never substitute.

## Reproduce local software checks

Use a filesystem with the required free-space floor. For this cloud checkout:

```bash
mkdir -p .tools/test-tmp
TMPDIR="$PWD/.tools/test-tmp" .build-venv/bin/python scripts/verify.py --report NEW-REPORT.json
TMPDIR="$PWD/.tools/test-tmp" .build-venv/bin/python scripts/benchmark_archive.py --trials 100000 --report NEW-BENCHMARK.json
.build-venv/bin/python scripts/validate_commissioning.py configs/commissioning-evidence.example.json
```

The last command validates the planning template as unqualified. Requiring any
measured stage must reject that template. Preserve every previous evidence file.
