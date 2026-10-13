# Run the whole system on Spark

Spark owns the local model, trusted supervisor, execution containers, archive, simulation or hardware controller, and browser UI. Connect a monitor by HDMI and use Spark's browser at `http://127.0.0.1:8765`. Windows is optional for setup and SSH convenience. It is not part of the running architecture. NVIDIA describes Spark's ARM64 processor, unified memory and bundled Docker runtime in its [system overview](https://docs.nvidia.com/dgx/dgx-spark/system-overview.html).

The current host is not ready for deployment. The read-only audit found only 2.7 GiB available on its root filesystem, with unrelated inference and Isaac Lab workloads running. The launch scripts fail admission before pulling an image or starting a process. Read [the audit](spark-audit.md) and review free space before proceeding. No host software or existing service was changed for this project.

After copying this project to a dedicated directory on Spark, use a terminal on Spark as account `leo`:

```bash
cd /home/leo/projects/robot-playground
cp deploy/spark.env.example deploy/spark.env
bash scripts/spark-prepare.sh
bash deploy/start.sh --demo
bash deploy/status.sh
bash deploy/stop.sh
```

Preparation requires Linux ARM64, Python 3.11 or later, Docker access and at least 20 GiB available under the project and Docker storage. It explicitly pulls only the pinned CPU image and runs `self-check`. Start never pulls an image. The image is the [Docker official Python image](https://hub.docker.com/_/python), pinned to registry index `sha256:1b668429b3511ab407d8e00648891631b0b1a4d7e15e3ca70f38ab5b91ad4ab4`. Registry inspection on 2026-10-12 found Python 3.12.15 Alpine 3.24 with ARM64 child manifest `sha256:a4ccdf11e73bd3f74e07f95e63f1b003467bb16cb77e942ddf45476c2cb8467c`. Architecture was checked from the registry, but the container has not been launched on this full Spark host.

`--demo` launches four bounded simulated generations plus the read-only status server. The built-in CPU demo uses fixed reviewed code, so it does not require Docker or a model service at runtime. Run the CLI directly for a demo on another development machine:

```bash
PYTHONPATH=src python3 -m playground demo --state-dir state/demo --generations 4 --steps 12
```

For model-generated agents, edit `deploy/spark.env` and the reviewed `configs/research.json` to point to a local OpenAI-compatible model and choose its identifier. The audit observed a service at port 18080, but its current model and generation behavior have not been qualified. Inspect it without restarting it:

```bash
curl --fail --max-time 5 http://127.0.0.1:18080/v1/models
bash deploy/start.sh
bash deploy/status.sh
tail -n 80 state/runtime/worker.log
bash deploy/stop.sh
```

Startup checks `/v1/models` and the local pinned CPU image. It starts `python3 -m playground run --config configs/research.json` and a separate read-only `serve` process. A listed model alone does not establish that generation, JSON tool calls or the research mission work. Start with simulation and inspect the archived evidence before choosing hardware mode. Hardware arming is a separate operator action subject to the firmware and bench checks.

The reviewed configuration bounds generations and runtime. Stop terminates only processes whose recorded PID, Linux start time and command identify this project. It removes only containers labeled with that launch session. PID records and logs live under `state/runtime`. The archive and accepted workspaces under the state directory survive stop and restart. CPU tmpfs content survives only after validated copyback; interrupted jobs publish no new snapshot. Re-running the supervisor resumes from persistent state according to the core archive implementation. No automatic boot service or global configuration is installed.

The scripts accept `PLAYGROUND_STATE_DIR` for an absolute alternate state directory, `PLAYGROUND_PORT` for the read-only UI port, and `PLAYGROUND_MIN_FREE_DISK_GIB` for a reviewed disk floor. The launcher passes that state directory to both worker and viewer. `deploy/spark.env` is trusted operator shell configuration. Do not let generated agents edit it. The sandbox reads `PLAYGROUND_SANDBOX_IMAGE` from this environment, while each generated command is offline.

V1 status stays on loopback. Use Spark's HDMI display and local browser. Viewing through an authenticated SSH tunnel can be added later without moving any runtime off Spark. LAN or phone access needs a separate authenticated deployment decision; no LAN binding option is provided here.

Windows may invoke the same on-Spark scripts after the project has been copied there:

```powershell
.\scripts\spark-control.ps1 -Action audit
.\scripts\spark-control.ps1 -Action start -Demo
.\scripts\spark-control.ps1 -Action status
.\scripts\spark-control.ps1 -Action stop
```

The PowerShell helper uses the existing NVIDIA Sync identity and strict host-key checking. It does not edit keys, install software, expose a model port or transfer credentials.

An optional model-service template is in `deploy/compose.yaml`, disabled behind the `models` profile. Prefer a suitable existing local service to another large model process. The template requires an operator-selected pinned ARM64 image with a verified vLLM entrypoint, a compatible already-cached model and a project-owned cache volume. It never pulls automatically, publishes only `127.0.0.1:18081`, and requests GPU access only for this fixed service. The exact template has not been deployed or function-tested. Its 48 GiB container memory limit and vLLM utilization setting do not provide a hard GPU memory guarantee on unified-memory Spark.

Do not enable a second model or GPU job alongside existing workloads without measuring available unified RAM. Fixed GPU jobs are optional and disabled by default. Their recipe, admission floor and inference serialization are described in [isolation](isolation.md). No arbitrary GPU training code is supported. Stopping the playground does not stop existing model services or remove model volumes.
