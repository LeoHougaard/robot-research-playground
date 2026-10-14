"""Run focused software acceptance without touching hardware or remote hosts."""
from pathlib import Path
import argparse
import datetime
import json
import hashlib
import os
import platform
import re
import subprocess
import sys
import tempfile


def source_hashes(root):
    paths = [root / "pyproject.toml"]
    for directory in ("src", "tests", "scripts", "configs"):
        paths.extend(p for p in (root / directory).rglob("*")
                     if p.is_file() and p.suffix in {".py", ".sh", ".json"})
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(paths)}


def save_report(path, report):
    if path:
        # Verification reports are evidence, not a replace-in-place cache.
        with Path(path).open("x", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2)
            stream.write("\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", help="Optional JSON evidence report path")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if args.report and Path(args.report).exists():
        parser.error("Report already exists; use a new evidence filename")
    sources = source_hashes(root)
    env = dict(os.environ, PYTHONPATH=str(root / "src"))
    result = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"], cwd=root, env=env, capture_output=True, text=True)
    print(result.stderr, end="", file=sys.stderr)
    print(result.stdout, end="")
    match = re.search(r"Ran (\d+) tests", result.stderr)
    skipped = re.search(r"skipped=(\d+)", result.stderr)
    report = {"time_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(), "python": platform.python_version(),
              "platform": platform.platform(), "test_count": int(match[1]) if match else None,
              "skipped": int(skipped[1]) if skipped else 0, "tests_passed": result.returncode == 0,
              "physical_hardware_verified": False, "spark_runtime_verified": False}
    report["source_sha256"] = sources
    if result.returncode:
        report["source_unchanged"] = source_hashes(root) == sources
        report["passed"] = False
        save_report(args.report, report)
        return result.returncode
    with tempfile.TemporaryDirectory(prefix="robot-playground-acceptance-") as state:
        demo = subprocess.run([sys.executable, "-m", "playground", "demo", "--state-dir", state, "--generations", "4", "--steps", "12"], cwd=root, env=env, capture_output=True, text=True)
        print(demo.stdout, end="")
        print(demo.stderr, end="", file=sys.stderr)
        report["demo_passed"] = demo.returncode == 0
        if demo.returncode == 0:
            summary = json.loads(demo.stdout[demo.stdout.rfind("\n{") + 1:])
            report["demo"] = {key: summary[key] for key in ("status", "generations", "experiments", "evaluations", "findings")}
        report["source_unchanged"] = source_hashes(root) == sources
        report["passed"] = report["demo_passed"] and report["source_unchanged"] and report["skipped"] == 0
        save_report(args.report, report)
        return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
