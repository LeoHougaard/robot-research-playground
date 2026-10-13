# Isolation and deployment review

Reviewed on 2026-10-12. No remote resources were changed. Real Docker is unavailable on this Windows machine. The Spark disk blocker remains unresolved. These checks do not establish endurance or physical readiness.

## Reproduced and repaired

1. High severity. The fixed GPU command used `python -` in the generated workspace. A local reproduction placed `torch.py` there, ran the exact recipe source, and observed its marker and exit code 73. The recipe now uses `python -I -`. The regression test creates both `torch.py` and `json.py` and verifies neither executes. This proves Python import isolation locally, not GPU execution on Spark.
2. High severity. `JobQueue.run_next` swallowed `SandboxCleanupError`. An injected cleanup fault followed by a CPU job produced `failed`, then `completed`. The queue now retains and reraises the exact exception, refuses subsequent queued work and rejects new admission. The regression test verifies all these transitions. Recovery requires trusted operator review and a new queue.

GPU RAM admission now happens after acquiring the inference gate, and the configured disk reserve reaches the sandbox. Seven focused `test_jobs.py` tests passed in `.build-venv`.

## Real qualification tool

`scripts/qualify_sandbox.py` never pulls images, installs packages, prunes resources or stops unrelated containers. It requires a non-root Linux user, an already installed digest-pinned image, a local Unix-socket Docker daemon, and 20 GiB free under both the workspace root and actual Docker root.

It inspects each exact task container and tests workspace copyback, denied writes, missing GPU/serial/Docker access, offline networking, memory exhaustion, process limits, workspace size, stdout flooding, command timeout, links and FIFO artifacts, original preservation and exact container removal. It stops on the first failure.

Run only after Spark admission passes:

```bash
python3 scripts/qualify_sandbox.py --image "$PLAYGROUND_SANDBOX_IMAGE" --report state/qualification/sandbox.json
```

Schema version 1 contains `passed`, UTC `time_utc` and `completed_time_utc`, `image`, `image_id`, `image_architecture`, `sandbox_source_sha256`, `checker_source_sha256`, selected `docker` settings, `inspected_containers`, `session_id` and `tests`. Gate consumers must reject old, future, image-mismatched or source-mismatched evidence. `physical_hardware_verified` stays false.

Local validation covered CLI help, Python compilation, and refusal on Windows before Docker invocation. Its refusal report has `passed=false`. No successful real-container report exists.

## Open findings

- Blocking compatibility risk. Docker's [current copy documentation](https://docs.docker.com/reference/cli/docker/container/cp/) lists tmpfs among resources that cannot be copied directly and suggests `docker exec tar`. The runtime pauses the container before `docker cp` copies its tmpfs `/workspace`. Modern daemon behavior may differ. The qualifier's first real roundtrip must pass before accepting persistence. Mocked tar output does not resolve this. No export redesign was made without daemon evidence.
- High severity, static review. Startup overwrites `runtime/session` after checking only live worker/server PIDs. After a crash, old labeled containers may remain, but normal stop cleanup loses their session identifier. Preserve and reconcile old exact-session resources before restarting.
- Medium severity, static review. Startup verifies the viewer response and server PID, but does not verify the research worker remains alive. Initial model-call failure can leave a healthy viewer while startup reports success.
- Medium severity, static review. The standalone robot gateway uses unbounded HTTP threads without socket deadlines, allowing incomplete loopback requests to retain threads indefinitely. Its SDK follows urllib redirects and lacks base-URL and response-size limits. These components are outside the offline sandbox path but need bounded local-only behavior before gateway deployment.
- Real Docker enforcement, the selected model and ARM64 image behavior remain unverified. Shell launchers, templates and passing fake runners cannot satisfy those gates. Resolve disk admission without pruning or changing unrelated workloads.
