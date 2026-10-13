"""Run focused software acceptance without touching hardware or remote hosts."""
from pathlib import Path
import argparse
import datetime
import json
import os
import platform
import re
import subprocess
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", help="Optional JSON evidence report path")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
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
    if result.returncode:
        if args.report:
            Path(args.report).write_text(json.dumps(report, indent=2), encoding="utf-8")
        return result.returncode
    with tempfile.TemporaryDirectory(prefix="robot-playground-acceptance-") as state:
        demo = subprocess.run([sys.executable, "-m", "playground", "demo", "--state-dir", state, "--generations", "4", "--steps", "12"], cwd=root, env=env, capture_output=True, text=True)
        print(demo.stdout, end="")
        print(demo.stderr, end="", file=sys.stderr)
        report["demo_passed"] = demo.returncode == 0
        if demo.returncode == 0:
            summary = json.loads(demo.stdout[demo.stdout.rfind("\n{") + 1:])
            report["demo"] = {key: summary[key] for key in ("status", "generations", "experiments", "evaluations", "findings")}
        if args.report:
            Path(args.report).write_text(json.dumps(report, indent=2), encoding="utf-8")
        return demo.returncode


if __name__ == "__main__":
    raise SystemExit(main())
