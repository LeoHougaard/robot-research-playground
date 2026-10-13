# Spark read-only audit

Observed from Windows on 2026-10-12, using the n-gb10 skill's `Test-NGb10Host.ps1`. No container, package, service or host setting was changed.

| Item | Observed |
| --- | --- |
| SSH account | `leo`, UID 1001, Docker group membership |
| Architecture | `aarch64` |
| Kernel | `6.17.0-1029-nvidia` |
| Docker client and server | `29.2.1` |
| GPU | NVIDIA GB10 |
| NVIDIA driver | `580.173.02` |
| Docker storage | `/var/lib/docker` |
| Root filesystem | 916 GiB total, 867 GiB used, 2.7 GiB available, reported 100% used |

Deployment is blocked by disk admission. The launch and image preparation scripts require 20 GiB available by default. No image or model download was attempted. Free space must be reviewed by Leo or provided on a dedicated project disk. No cleanup command is included because the audit cannot determine which existing data Leo wants to retain.

Existing workloads include running Pixel model and perception services, `gb10-local-models` bound to `127.0.0.1:18080`, and `isaac-lab-gb10`. `qwen36-vllm` was stopped. Those services belong to other projects and must stay untouched. A listening model endpoint has not yet been verified to serve the model or tool format required by this project.

GB10 uses unified CPU/GPU memory. The audit's GPU memory fields were `N/A`; admission must use Linux available RAM and measured workload behavior, rather than pretending `nvidia-smi` provides a separate VRAM budget. NVIDIA documents the shared 128 GB architecture and memory reporting limitation in the [porting guide](https://docs.nvidia.com/dgx/dgx-spark-porting-guide/overview.html) and [release notes](https://docs.nvidia.com/dgx/dgx-spark/release-notes.html).
