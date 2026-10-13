# Existing Spark storage options

Read-only observations from `leo@gx10-ddb2.local` on 2026-10-12. The account check returned `leo`. Commands were `df`, a nonrecursive block-device inventory, directory ownership `stat` and `docker system df` with a twenty-second timeout. No files, containers, images, volumes or services were changed.

| Existing filesystem | Device | Format | Total | Available | Usage |
| --- | --- | --- | --- | --- | --- |
| `/` | `/dev/nvme0n1p2` | ext4 | 916 GiB | 2.7 GiB | 100% |
| `/mnt/gb10-ssd1` | `/dev/sda1` | ext4 | 916 GiB | 683 GiB | 26% |
| `/mnt/gb10-ssd2` | `/dev/sdb1` | ext4 | 916 GiB | 916 GiB | 1% |

Two already-mounted disks have room for the project, archive, models and datasets. The second disk has very little reported usage. Both mount roots are mode `755`, owned by `root:root`, so `leo` cannot create a new project directory directly at either mount root. An existing writable child directory or an explicitly approved directory setup is required. This inventory establishes current capacity only; mount persistence, disk health and suitability for long-running workloads need a deployment review before relocation.

Moving the project or `PLAYGROUND_STATE_DIR` to another disk can give its files room, but it does not change Docker's current `/var/lib/docker` storage on the full root filesystem. The startup scripts check both the project filesystem and Docker storage against their 20 GiB floor. They still block deployment until Docker storage passes admission. No cached container was run as an exception.

Docker reported this shared storage accounting:

| Category | Total count | Active count | Size | Reported reclaimable |
| --- | --- | --- | --- | --- |
| Images | 29 | 10 | 150.2 GB | 118 GB, 78% |
| Containers | 81 | 7 | 2.909 GB | 2.566 GB, 88% |
| Local volumes | 21 | 21 | 49.87 GB | 0 B |
| Build cache | 146 | 0 | 2.842 GB | 786.2 MB |

These are Docker's labels and decimal GB totals, not a list of safe deletion targets. Stopped containers, old images and caches may still be valuable to other projects. Existing services and model volumes belong to shared workloads. No prune or delete command was run or authorized by this inventory.

Two concrete paths can resolve the blocker after Leo chooses one. Review exact unused image/container resources and remove only selected project-approved targets, retaining needed data. Alternatively, plan Docker storage relocation to an existing SSD with a recovery plan and an explicit host-maintenance window. Relocation affects the shared Docker daemon and existing workloads, so it is separate from copying this project. No host configuration edit or service interruption has been prepared here.
