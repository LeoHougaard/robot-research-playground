# Independent cloud reliability review

This review concerns local software behavior only. It does not qualify actual
Docker, the selected model, Spark, physical hardware or unattended research.
Reproductions use temporary directories, simulated control, local fixture
sockets/processes and injected Docker results. Original project evidence is
preserved.

| Finding | Correction / regression coverage |
| --- | --- |
| A different Docker context could satisfy the apparent local-daemon gate | Pin each Docker call to the inspected local Unix endpoint; reject context overrides and mismatched daemon/image/session identity |
| Any sandbox exception counted as hostile-artifact rejection | Require successful command/export stages and the artifact validator's exact rejection; verify the entire original workspace |
| Interrupted provenance-table creation could leave an empty committed table and block restart | Explicit transaction wraps binding-table creation and its row; injected interruption rolls back both |
| Empty files consumed inodes without meaningfully charging retained storage | Charge a conservative per-entry floor and admit against destination free inodes as well as bytes |
| A long artifact path was rejected only after copyback, leaving all future recovery blocked | Enforce the path budget before sandbox publication; preserve the original workspace on refusal |
| Held-out simulations reverted nondefault geometry to defaults under the original fingerprint | Copy actual simulator geometry, speed, controller limits and tether bounds; reject unknown simulator implementations |
| Model listener matching used only port, allowing a different local server to receive provenance credit | Match exact address/family/port, pin serving process/socket identity throughout, and require its executable's actual hash |
| Model/process/input identity could drift during qualification | Recheck serving identity around requests and sampling; rehash config, provenance, model/runtime files and evidence before passing |
| Future-dated commissioning measurements and reviews preceding their evidence were accepted | Validate actual timestamps and calibration/measurement/review/endurance ordering; planning templates cannot contain measured passes |
| A cancellation between workspace renames could delete the only original copy | Durable publication journal, BaseException rollback and restart recovery preserve originals; independent hard-exit probes cover both renames and rollback, including idempotent recovery |

The archive-summary optimization is checked against raw SQL aggregates, including
transaction rollback, deletion/update, legacy migration, restart and read-only
viewer access. A real process-boundary simulation crash preserves an unfinished
trial as interrupted and recovers its useful program without fabricating a
successful outcome.

These tests narrow specific software risks; they do not establish that arbitrary
future crashes, power loss, Docker daemon failures or hardware faults are safe.
See [remaining gates](cloud-reliability-20261013.md#remaining-gates).

Final aggregate acceptance: [229 tests, zero skips, unchanged source](evidence/cloud-20261013/software-acceptance-01.json). Independent focused recovery review passed 87 tests and separately 22 launcher tests; these overlap the aggregate and are not additive.
