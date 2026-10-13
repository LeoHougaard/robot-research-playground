# Runtime review and verification

The local integration suite uses a real loopback HTTP server that speaks the OpenAI-compatible model endpoints. It exercises `LocalModel`, the structured-tool loop, the archive and simulation controller together. The server returns deterministic test decisions; no external endpoint or real model is called.

The sandbox fixture writes one fixed reviewed helper file. It does not execute the model's command on the host. Actual Docker isolation checks use an instrumented fake Docker runner and argv assertions because deployment admission blocks execution on Spark's full disk.

The integration checks cover:

- Prediction records committed before the actual simulated controller action.
- Replacement of one agent generation by a fresh generation, with inherited findings.
- An archived helper imported by a successor without changing its source.
- Integrity rejection when a predecessor file changes while retaining the same size.
- Fabricated evidence rejected and agent claims kept as hypotheses even when requesting verified status.
- Unknown hardware-arming tools rejected.
- Invalid structured decisions stopped after three failed generations, with the controller disarmed.
- HTTP redirects rejected without contacting their destination.
- Slow response bytes constrained by the total model-request budget and no action performed after the run deadline.
- Incomplete dripping response headers interrupted by a socket watchdog before a late action.
- Transcript storage limited by the number of bytes actually written, including JSON framing.
- Deep model decisions rejected before tool execution or serialization amplification.
- Camera writes rejected before exceeding the aggregate workspace quota.
- A second supervisor unable to recover or modify live evidence in the same state directory.

All ten local integration tests passed after the review fixes. The exact transcript test fills records to the boundary and checks the file's byte size, including array framing. Slow header and body tests check elapsed time, deadline status and absence of robot experiments. A fixed camera fixture verifies that no image is written when the workspace has reached its admitted byte limit.

The model client connects directly to its validated loopback HTTP endpoint. A daemon timer shuts down the connected socket when the whole request budget expires, including incomplete response headers. The connection and timer are closed or cancelled in `finally`. Redirects and proxy environment variables cannot change its destination. Python documents the connection API in [http.client](https://docs.python.org/3/library/http.client.html) and socket shutdown in [socket](https://docs.python.org/3/library/socket.html).

Review identified and prompted fixes for unbounded host transcripts, whole-response HTTP timeouts, deadline checks before actions, missing predecessor artifact access, simultaneous supervisor ownership, and camera/import writes that bypassed workspace admission. The review also checks the exact transcript framing and deadline outcome regressions in `tests/test_agent_integration.py`.

The twelve Docker launcher tests inspect network, capabilities, user, immutable image selection, tmpfs size, timeout cleanup, output bounds and artifact handling. Failed cleanup cannot return success. Exact-name inspection can confirm absence after a known start, while ambiguous startup timeout halts admission even when the name is temporarily absent. Removal and inspection share one ten-second allowance. Export rejects traversal, absolute paths, symlinks, hard links, devices, duplicate names and excess data before publishing. Docker startup warnings fail closed rather than silently accepting unsupported kernel limits. Fixed CPU recipes have meaningful held-out measurements; optional GPU smoke recipes remain disabled by default.

Fourteen focused prediction and policy tests also pass. They cover simulator grasp effects, object errors matched by ID, unavailable measurements, bounded numeric inputs, policy execution mediated by a fake sandbox, pre-action rejection of traversal and overflow proposals, and exploration-only training export. The core interpreter skips eight checks when optional OpenCV dependencies are absent. The final run in the task-local build environment passed all 96 tests with no skips; docs/verification.json records the result. Simulator comparisons do not establish real object-contact behavior.

The following checks remain pending:

- Real Docker execution on Spark, with kernel resource enforcement and actual container filesystem/network escape attempts.
- Generation and tool reliability of the selected local model, including vision requests if used.
- Optional GPU smoke recipe and model-service template on a reviewed ARM64/CUDA image.
- Physical servo protocol, TTL levels, current conversion, camera calibration, default-off enable circuitry, watchdog timing and emergency-stop behavior.

The read-only Spark audit found 2.7 GiB available and existing unrelated workloads. No remote deployment, image pull, driver change or service restart was attempted. The 20 GiB default admission floor blocks deployment until space is reviewed. These tests establish the local software behavior and boundaries they exercise; they do not establish physical robot readiness or real Docker isolation on this host.
