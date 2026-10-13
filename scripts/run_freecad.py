"""Run a trusted project CAD script with task-local FreeCAD settings."""
from pathlib import Path
import argparse
import json
import os
import re
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("script")
    parser.add_argument("--executable", help="FreeCADCmd path; defaults to task-local bootstrap manifest")
    parser.add_argument("--profile", default="cad")
    parser.add_argument("--timeout", type=float, default=600)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,39}", args.profile) or not 1 <= args.timeout <= 3600:
        raise ValueError("Invalid profile or timeout")
    source = Path(args.script).resolve()
    if not source.is_relative_to(root) or not source.is_file():
        raise ValueError("CAD script must be a trusted file in this project")
    tools = root / ".tools"
    tools.mkdir(exist_ok=True)
    executable = args.executable
    if not executable:
        executable = json.loads((tools / "freecad-runtime.json").read_text())["freecadcmd"]
    arguments = [executable, "-u", str(tools / (args.profile + "-user.cfg")),
                 "-s", str(tools / (args.profile + "-system.cfg")),
                 "--log-file", str(tools / (args.profile + ".log")), str(source)]
    # Native CAD builder scripts must run in FreeCAD's embedded namespace and exit.
    # Check their generated reports; some FreeCAD script exceptions still exit zero.
    return subprocess.run(arguments, cwd=root, timeout=args.timeout, env=os.environ.copy()).returncode


if __name__ == "__main__":
    raise SystemExit(main())
