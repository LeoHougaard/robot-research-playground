#!/usr/bin/env bash
# Shared trusted launcher helpers, sourced only by reviewed scripts.
set -euo pipefail
PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
ENV_FILE="$PROJECT_ROOT/deploy/spark.env"
if [[ -f "$ENV_FILE" ]]; then
  set -a
  # This is operator configuration, never an agent-produced file.
  source "$ENV_FILE"
  set +a
fi
STATE_DIR="${PLAYGROUND_STATE_DIR:-$PROJECT_ROOT/state}"
if [[ "$STATE_DIR" != /* ]]; then STATE_DIR="$PROJECT_ROOT/$STATE_DIR"; fi
mkdir -p -- "$STATE_DIR"
STATE_DIR="$(cd -- "$STATE_DIR" && pwd -P)"
RUNTIME_DIR="$STATE_DIR/runtime"
mkdir -p -- "$RUNTIME_DIR"
export PYTHONPATH="$PROJECT_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
export PLAYGROUND_STATE_DIR="$STATE_DIR"
export PLAYGROUND_SANDBOX_IMAGE="${PLAYGROUND_SANDBOX_IMAGE:-python@sha256:1b668429b3511ab407d8e00648891631b0b1a4d7e15e3ca70f38ab5b91ad4ab4}"

require_spark() {
  [[ "$(id -un)" == leo ]] || { echo 'Expected Spark account leo.' >&2; exit 1; }
  [[ "$(uname -m)" == aarch64 ]] || { echo 'Expected ARM64 Spark host.' >&2; exit 1; }
  python3 -c 'import sys; assert sys.version_info >= (3,11), "Python 3.11+ required"'
  if [[ "${1:-docker}" == docker ]]; then
    docker version --format '{{.Server.Version}}' >/dev/null
  fi
}

require_disk() {
  python3 - "$PROJECT_ROOT" "${PLAYGROUND_MIN_FREE_DISK_GIB:-20}" <<'PY'
import shutil,sys
floor=float(sys.argv[2])*1024**3
if floor < 1:
    raise SystemExit('Disk floor must be at least 1 GiB.')
for path in (sys.argv[1], '/var/lib/docker'):
    free=shutil.disk_usage(path).free
    if free < floor:
        raise SystemExit(f'Deployment blocked: {path} has {free/1024**3:.1f} GiB available; needs {floor/1024**3:.1f}. No pull or workload started.')
PY
}

record_pid() {
  python3 - "$1" "$2" <<'PY'
import pathlib,sys
pid=int(sys.argv[1]); output=pathlib.Path(sys.argv[2])
try:
    ticks=pathlib.Path(f'/proc/{pid}/stat').read_text().rsplit(') ',1)[1].split()[19]
except FileNotFoundError:
    raise SystemExit('Worker exited before PID could be recorded; inspect its log.')
output.write_text(f'{pid} {ticks}\n')
PY
}

owned_pid() {
  python3 - "$RUNTIME_DIR/$1.pid" "$1" "$STATE_DIR" <<'PY'
import pathlib,sys
try:
    record=pathlib.Path(sys.argv[1]).read_text().split()
    if len(record)!=2 or not all(v.isdigit() for v in record): raise ValueError()
    pid=int(record[0]); proc=pathlib.Path(f'/proc/{pid}')
    ticks=proc.joinpath('stat').read_text().rsplit(') ',1)[1].split()[19]
    args=proc.joinpath('cmdline').read_bytes().split(b'\0')
    args=[v.decode() for v in args if v]
    if ticks!=record[1] or args[1:3]!=['-m','playground']: raise ValueError()
    if sys.argv[2]=='server' and (args[3]!='serve' or sys.argv[3] not in args): raise ValueError()
    if sys.argv[2]=='worker' and (args[3] not in ('run','demo') or sys.argv[3] not in args): raise ValueError()
    print(pid)
except (OSError,ValueError,IndexError):
    raise SystemExit(1)
PY
}
