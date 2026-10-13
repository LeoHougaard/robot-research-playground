# Endurance review

This independent review used the local Python simulation and isolated SQLite fixtures. It did not actuate hardware, access Spark, or qualify a real model or Docker runtime. The original reproductions and results are retained in `.tools/verify-endurance/reproduce.py` and `.tools/verify-endurance/results.json`.

## Reproduced failures and fixes

| Severity | Failure and exact evidence | Expected behavior and implemented fix |
| --- | --- | --- |
| High | Twenty invalid tool calls across five generations returned a completed run, five completed generations, and zero experiments. Valid observation-only churn had the same missing research outcome. | A rejected request may recover within its generation. Three generations without new measured trials, findings, evaluations, or novel agent file content now stop with `no_progress`. A bounded run with no such progress also returns `no_progress`. Imported copies and supervisor observation/export files alone do not count. |
| High | With 101 completed physical-labelled fixture holdouts, evaluation raised `Findings need 1..100 existing experiment IDs` after saving a passed evaluation and checkpoint, with no finding. | Physical comparison now uses at most the newest 100 recorded holdouts. Evaluation and its verified finding commit in one SQLite transaction. Failed finding creation rolls back the evaluation and does not promote a checkpoint. The committed archive restores the retained model if the JSON cache is missing or stale. |
| High | A useful program written by an interrupted generation remained on disk, but restart registered zero artifacts, leaving successors no import identifier. | Restart validates and registers unfinished generation workspaces before marking their generations interrupted. Artifact registration is idempotent and rejects changed previously registered content. Generation completion now follows transcript writing and artifact archival. |
| Medium | After 100 newer artifact rows, an older program disappeared from the tool's 30-item list and even the archive's maximum 100-item list. No tool supported finding it again. | Bounded cursor pagination now lets successors traverse all archived artifacts, including older programs. Each tool result contains at most 30 artifacts and a `next_before` cursor. |

The focused tests are `tests/test_endurance.py`. They cover both invalid-tool and observation-only churn, complete evaluation with more than 100 holdouts, transaction rollback on finding failure, checkpoint recovery without the cache, traversal to an older program, and interrupted program recovery/import without duplicate registration. Existing integration tests still verify that an invalid tool request can be followed by a real experiment and finding. Two observation/rejection-only fixtures now expect `no_progress` rather than `completed`.

The final local verification command `.build-venv/Scripts/python.exe scripts/verify.py` passed 96 tests with no skips and the four-generation demo with 112 recorded trials. Five focused endurance tests and all ten HTTP integration tests passed. These checks establish the reproduced software behavior, not week-long performance or real research usefulness.

Indexes support recent experiment/artifact lookup, novel-content checks, and generation evidence checks. These reduce avoidable scans; they do not establish acceptable performance on a week-sized archive.

## Remaining limits

The runtime is not qualified for unattended physical research. The documented Spark disk blocker, real-container qualification, selected-model qualification, physical reset/tether reliability, and measured endurance gates still apply.

There is no aggregate project-state quota or retention budget. At 64 MiB per generation, 100,000 generations can retain about 6.1 TiB of workspace files before archive overhead. Imported predecessor files are copied and retained again. The free-disk reserve eventually stops admission, but it does not promise a week of capacity or preserve a budget for unrelated workloads beyond that floor. A positive `max_state_bytes` limit with startup accounting and bounded per-generation updates is a concrete next fix. It should reserve space for a full generation before admission and stop without deleting evidence.

Archive summaries calculate aggregate prediction errors over all completed trials, and digest exports repeat this every generation. Matched large-archive performance testing remains necessary. The configuration's maximum generation count and wall-clock ceiling are bounds, not evidence that useful research will last a week.

Evidence and retained models are not bound to a checked configuration, geometry, firmware, or calibration fingerprint. Documentation currently requires a new state directory when these change. Runtime enforcement remains a necessary follow-up before physical restart qualification.

Checkpoint recovery reads committed evaluations and treats `best-model.json` as a cache. No durable backup, external evidence replication, power-loss injection, or disk-failure recovery was tested. No software fixture establishes useful discoveries by an actual local model.
