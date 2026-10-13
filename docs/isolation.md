# Execution and resource limits

The trusted Python supervisor runs on Spark. It talks to the local model and hardware controller, validates tool requests, and invokes the Docker CLI. Generated code receives neither Docker access nor supervisor credentials. Do not run the supervisor in a container with the Docker socket mounted.

`DockerSandbox.execute(workspace, command, timeout_s=30)` sends the command as one argument to `/bin/sh -c` inside a pinned CPU image. The host uses an argument list with `shell=False`. Missing Docker, a missing image, or invalid configuration fails closed. There is no arbitrary-code host fallback.

Every CPU container has network disabled, all capabilities dropped, no new privileges, a non-root UID, read-only root filesystem, no GPU, no USB device, and no published port. Docker's default seccomp policy stays enabled. Startup warnings, including unsupported kernel resource limits, abort the job. The reviewed image is the Docker official Python 3.12.15 Alpine 3.24 multiarchitecture image pinned in `deploy/spark.env.example`. Its registry index contains Linux ARM64. Agents cannot choose an image, Dockerfile, capability or mount.

| Resource | Default bound |
| --- | --- |
| CPU | 1 CPU |
| Memory including tmpfs | 512 MiB, with equal memory and swap limits |
| Processes | 64 |
| Open files per process | 256 |
| Workspace | 64 MiB tmpfs, at most 4096 accepted file/directory entries |
| Temporary files | 16 MiB tmpfs |
| Individual file size | 64 MiB |
| Captured output | 1 MiB per stdout/stderr stream, excess output terminates execution |
| Command runtime budget | 30 seconds including setup, operator maximum 300 seconds |
| Free disk admission | 20 GiB |

The exact host workspace is mounted read-only at `/input`. Fixed trusted setup copies it into the container's `/workspace` tmpfs. A bind-mounted host directory has no portable Docker aggregate byte quota, so generated code cannot write directly into the host workspace. This is a deliberate constraint. Docker's [tmpfs documentation](https://docs.docker.com/engine/storage/tmpfs) explains size limits and memory accounting; [resource constraints](https://docs.docker.com/engine/containers/resource_constraints) covers CPU and memory limits.

When the command ends within its bounds, the launcher pauses the entire container, copies its workspace to a staging directory, rejects links, hard links, devices and excessive data, then replaces the host workspace with the validated snapshot. Both normal and nonzero command exits can publish valid outputs. Timeout or excess output publishes nothing. Invalid outputs preserve the previous workspace. File deletions inside the sandbox appear in the accepted snapshot. Each attempted exact container name is removed in a `finally` block. Failed removal triggers exact-name inspection. If absence cannot be confirmed, `SandboxCleanupError` reports the name and session, and the supervisor stops admitting jobs. A timed-out startup without acknowledged removal also stops admission because a late daemon operation could still create the container. A host crash can lose tmpfs results; persistent archive writes happen only after snapshot acceptance.

The subprocess watchdog kills the Docker CLI inside its requested deadline. Process reaping and output-pipe joins share that deadline. Cleanup receives one additional 10-second budget, with up to 8 seconds for removal and the remainder for exact-name inspection. Failed confirmation becomes a cleanup error; the launcher does not silently return a successful job. Host scheduling and kernel stalls can delay any process, so these are application watchdog deadlines rather than a real-time hardware guarantee. Docker and kernel isolation have residual escape risk. This implementation does not claim a virtual-machine boundary. Docker explains its daemon authority and reduced capability model in [Engine security](https://docs.docker.com/engine/security/) and [container run options](https://docs.docker.com/reference/cli/docker/container/run/).

`JobQueue` runs one fixed job at a time. `linear_prediction` is trusted stdlib code with 8 to 1024 synthetic samples and held-out error. This provides a meaningful local demo when Docker is absent. It never interprets submitted code. Requests may name a fixed recipe and bounded parameter values only.

GPU jobs are disabled by default. The optional `gpu_linear_smoke` recipe is a fixed 100-step synthetic PyTorch regression, requires a reviewed pinned ARM64/CUDA image, runs for at most 60 seconds, and reserves 16 GiB available unified RAM in addition to its 4 GiB CPU container limit. It shares an inference lock with the model caller. The supervisor must use that same lock for inference. This serializes admitted training against its own inference requests, but cannot control unrelated services already running on Spark.

GPU access exposes the shared NVIDIA driver. Docker memory limits do not establish an arbitrary hard GPU allocation limit. The unified-memory admission floor is a conservative check, not a guarantee. Enable only small reviewed recipes after measuring them on an idle enough host. The optional model compose profile is a template pending exact model/image validation, not a model stack that was deployed or tested here.
