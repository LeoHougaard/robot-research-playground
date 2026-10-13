from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from playground.jobs import JobQueue, linear_prediction, GPU_SMOKE_COMMAND
from playground.sandbox import SandboxCleanupError


class JobTests(unittest.TestCase):
    def test_fixed_training_held_out_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = JobQueue(min_free_disk_bytes=0)
            job = queue.submit("linear_prediction", Path(tmp))
            self.assertIs(queue.run_next(), job)
            self.assertEqual(job.state, "completed")
            self.assertAlmostEqual(job.result["slope"], 2)
            self.assertLess(job.result["held_out_mse"], 1e-20)
            self.assertGreater(job.result["test_samples"], 0)
            self.assertTrue((Path(tmp) / "job-result.json").is_file())
            self.assertIsNone(queue.run_next())

    def test_rejects_code_image_command_and_unbounded_parameters(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = JobQueue(min_free_disk_bytes=0)
            for recipe, params in (("shell", {}), ("linear_prediction", {"command": "rm -rf /"}),
                                   ("linear_prediction", {"samples": 10000000}),
                                   ("linear_prediction", {"samples": True}),
                                   ("gpu_linear_smoke", {})):
                with self.assertRaises(ValueError):
                    queue.submit(recipe, Path(tmp), params)

    def test_disk_admission_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = JobQueue(min_free_disk_bytes=10**30)
            job = queue.submit("linear_prediction", Path(tmp))
            queue.run_next()
            self.assertEqual(job.state, "failed")
            self.assertIn("disk", job.error)
            self.assertFalse((Path(tmp) / "job-result.json").exists())

    def test_gpu_ram_admission_before_docker(self):
        with tempfile.TemporaryDirectory() as tmp:
            calls = []
            queue = JobQueue(enable_gpu=True, min_free_disk_bytes=0,
                             memory_available=lambda: 1024,
                             docker_runner=lambda *args, **kwargs: calls.append(args))
            job = queue.submit("gpu_linear_smoke", Path(tmp))
            queue.run_next()
            self.assertEqual(job.state, "failed")
            self.assertIn("RAM", job.error)
            self.assertEqual(calls, [])

    def test_cpu_queue_serializes_jobs(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = JobQueue(min_free_disk_bytes=0)
            jobs = [queue.submit("linear_prediction", Path(tmp) / str(i)) for i in range(8)]
            threads = [threading.Thread(target=queue.run_next) for _ in jobs]
            for thread in threads: thread.start()
            for thread in threads: thread.join()
            self.assertTrue(all(job.state == "completed" for job in jobs))

    def test_gpu_recipe_cannot_import_generated_workspace_modules(self):
        launcher, source = GPU_SMOKE_COMMAND.split("<<'PY'\n", 1)
        args = shlex.split(launcher)
        args[0] = sys.executable
        source = source.rsplit("\nPY", 1)[0]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for module in ("torch", "json"):
                (root / (module + ".py")).write_text(
                    "from pathlib import Path; Path('escaped').write_text('generated code'); raise SystemExit(73)")
            # PyTorch may be absent locally. Either its trusted package or an import
            # error is acceptable; workspace Python must never be evaluated.
            result = subprocess.run(args, input=source, text=True, cwd=root,
                                    capture_output=True, timeout=20)
            self.assertFalse((root / "escaped").exists(), result.stdout + result.stderr)
            self.assertNotEqual(result.returncode, 73)

    def test_cleanup_failure_latches_all_future_job_admission(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = JobQueue(enable_gpu=True, gpu_image="python@sha256:" + "a" * 64,
                             min_free_disk_bytes=0, memory_available=lambda: 100 * 1024**3)
            gpu = queue.submit("gpu_linear_smoke", Path(tmp) / "gpu")
            cpu = queue.submit("linear_prediction", Path(tmp) / "cpu")
            fault = SandboxCleanupError("playground-job-orphan", "b" * 32, "still exists")
            with patch("playground.jobs._GpuRecipeSandbox.execute", side_effect=fault):
                with self.assertRaises(SandboxCleanupError) as caught:
                    queue.run_next()
            self.assertIs(caught.exception, fault)
            self.assertEqual(gpu.state, "failed")
            self.assertEqual(cpu.state, "queued")
            with self.assertRaises(SandboxCleanupError):
                queue.run_next()
            with self.assertRaises(SandboxCleanupError):
                queue.submit("linear_prediction", Path(tmp) / "later")
            self.assertEqual(cpu.state, "queued")
            self.assertFalse((cpu.workspace / "job-result.json").exists())


if __name__ == "__main__":
    unittest.main()
