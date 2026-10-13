"""Bounded fixed recipes, with no arbitrary-code fallback outside Docker."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
import shutil
import threading
import time
import uuid
from typing import Callable

from .sandbox import DockerSandbox, SandboxError, SandboxCleanupError, validate_workspace


@dataclass
class Job:
    id: str
    recipe: str
    workspace: Path
    parameters: dict
    state: str = "queued"
    result: dict | None = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)

    def to_dict(self):
        value = asdict(self)
        value["workspace"] = str(self.workspace)
        return value


class JobQueue:
    """One job at a time; GPU jobs also hold the shared inference gate.

    CPU linear_prediction is fixed stdlib code. GPU smoke training runs only
    the fixed reviewed recipe below in an operator-provided pinned image.
    No generated program, command, image, mount or Dockerfile is accepted here.
    """
    def __init__(self, *, enable_gpu: bool = False, gpu_image: str | None = None,
                 min_free_disk_bytes: int = 20 * 1024**3,
                 reserved_ram_bytes: int = 16 * 1024**3,
                 inference_gate: threading.Lock | None = None,
                 memory_available: Callable[[], int] | None = None,
                 docker_runner: Callable | None = None):
        self.jobs: dict[str, Job] = {}
        self.enable_gpu, self.gpu_image = enable_gpu, gpu_image
        self.min_free_disk_bytes, self.reserved_ram_bytes = min_free_disk_bytes, reserved_ram_bytes
        self.inference_gate = inference_gate or threading.Lock()
        self.memory_available = memory_available or available_ram
        self.docker_runner = docker_runner
        self._lock = threading.Lock()
        self.cleanup_error: SandboxCleanupError | None = None

    def submit(self, recipe: str, workspace: Path, parameters: dict | None = None) -> Job:
        parameters = parameters or {}
        if recipe not in {"linear_prediction", "gpu_linear_smoke"}:
            raise ValueError("unknown fixed recipe")
        if recipe == "gpu_linear_smoke" and not self.enable_gpu:
            raise ValueError("GPU jobs are disabled")
        if recipe == "gpu_linear_smoke" and parameters:
            raise ValueError("GPU smoke recipe has no configurable parameters")
        if parameters.keys() - {"samples"}:
            raise ValueError("only the bounded samples parameter is accepted")
        samples = parameters.get("samples", 64)
        if type(samples) is not int or not 8 <= samples <= 1024:
            raise ValueError("samples must be an integer from 8 to 1024")
        workspace = Path(workspace)
        workspace.mkdir(parents=True, exist_ok=True)
        validate_workspace(workspace, 64 * 1024**2)
        job = Job(uuid.uuid4().hex, recipe, workspace.resolve(), {"samples": samples})
        with self._lock:
            if self.cleanup_error is not None:
                raise self.cleanup_error
            if sum(j.state in {"queued", "running"} for j in self.jobs.values()) >= 32:
                raise ValueError("fixed job queue is full")
            self.jobs[job.id] = job
        return job

    def run_next(self) -> Job | None:
        with self._lock:
            if self.cleanup_error is not None:
                raise self.cleanup_error
            job = next((j for j in self.jobs.values() if j.state == "queued"), None)
            if job is None:
                return None
            job.state = "running"
            try:
                if shutil.disk_usage(job.workspace).free < self.min_free_disk_bytes:
                    raise SandboxError("free disk is below admission floor")
                if job.recipe == "linear_prediction":
                    job.result = linear_prediction(job.parameters["samples"])
                else:
                    with self.inference_gate:
                        if self.memory_available() < self.reserved_ram_bytes + 4 * 1024**3:
                            raise SandboxError("GPU job would consume reserved unified RAM")
                        kwargs = {"runner": self.docker_runner} if self.docker_runner else {}
                        sandbox = _GpuRecipeSandbox(self.gpu_image, memory="4096m",
                                                   min_free_disk_bytes=self.min_free_disk_bytes, **kwargs)
                        result = sandbox.execute(job.workspace, GPU_SMOKE_COMMAND, timeout_s=60)
                        job.result = result.to_dict()
                        if result.exit_code:
                            raise SandboxError(result.stderr or "GPU recipe failed")
                (job.workspace / "job-result.json").write_text(json.dumps(job.result, indent=2), encoding="utf-8")
                job.state = "completed"
            except SandboxCleanupError as exc:
                job.state, job.error = "failed", str(exc)
                self.cleanup_error = exc
                raise
            except Exception as exc:
                job.state, job.error = "failed", str(exc)
            return job


def available_ram() -> int:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) * 1024
    except OSError:
        pass
    return 0  # GPU admission fails closed away from a verified Linux host.


def linear_prediction(samples: int) -> dict:
    """Fit y=2x+1 to fixed synthetic data and report held-out error."""
    xs = [i / samples for i in range(samples)]
    ys = [2 * x + 1 for x in xs]
    split = max(4, samples * 3 // 4)
    train_x, train_y = xs[:split], ys[:split]
    mean_x, mean_y = sum(train_x) / split, sum(train_y) / split
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(train_x, train_y)) / sum((x - mean_x)**2 for x in train_x)
    intercept = mean_y - slope * mean_x
    mse = sum((slope*x + intercept-y)**2 for x, y in zip(xs[split:], ys[split:])) / (samples-split)
    return {"recipe": "linear_prediction", "samples": samples, "train_samples": split,
            "test_samples": samples-split, "slope": slope, "intercept": intercept, "held_out_mse": mse}


class _GpuRecipeSandbox(DockerSandbox):
    def run_argv(self, workspace: Path, name: str) -> list[str]:
        args = super().run_argv(workspace, name)
        args[1:1] = ["--gpus=all", "--env", "CUDA_VISIBLE_DEVICES=0"]
        return args


# Fixed source, never interpolate generated text or request parameters.
GPU_SMOKE_COMMAND = """python -I - <<'PY'
import json, torch
torch.manual_seed(0)
torch.set_num_threads(1)
x=torch.linspace(0,1,64,device='cuda').reshape(-1,1)
y=2*x+1
model=torch.nn.Linear(1,1).cuda()
optimizer=torch.optim.SGD(model.parameters(),lr=0.1)
for _ in range(100):
    optimizer.zero_grad()
    loss=((model(x[:48])-y[:48])**2).mean()
    loss.backward()
    optimizer.step()
with torch.no_grad():
    mse=((model(x[48:])-y[48:])**2).mean().item()
print(json.dumps({'recipe':'gpu_linear_smoke','held_out_mse':mse,'steps':100}))
PY"""
