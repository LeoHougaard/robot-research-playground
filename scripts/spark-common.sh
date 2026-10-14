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
    identify_docker
  fi
}

# Resolve once, pin every invocation, and record the daemon itself. A different
# local socket/context or recreated daemon must never look like successful
# cleanup of resources belonging to the previous daemon.
identify_docker() {
  [[ -z "${DOCKER_CONTEXT:-}" ]] || { echo 'Unset DOCKER_CONTEXT; a pinned local Docker endpoint is required.' >&2; return 1; }
  command -v docker >/dev/null && command -v timeout >/dev/null || {
    echo 'Docker and timeout are required.' >&2; return 1;
  }
  local endpoint="${DOCKER_HOST:-}" info
  if [[ -z "$endpoint" ]]; then
    endpoint="$(timeout --kill-after=2s 10s docker context inspect --format '{{json .Endpoints.docker.Host}}' | python3 -c 'import json,sys;print(json.load(sys.stdin))')" || return 1
  fi
  [[ "$endpoint" == unix:///* && "$endpoint" != unix:/// && "$endpoint" != *$'\n'* ]] || {
    echo 'A local Unix-socket Docker endpoint is required.' >&2; return 1;
  }
  DOCKER_ENDPOINT="$endpoint"
  export DOCKER_HOST="$endpoint"
  info="$(docker_local info --format '{"id":{{json .ID}},"root":{{json .DockerRootDir}}}')" || return 1
  info="$(python3 - "$info" <<'PYCODE'
import json,sys
value=json.loads(sys.argv[1])
if not isinstance(value.get('id'),str) or not value['id'] or any(c.isspace() for c in value['id']):
    raise SystemExit('Docker daemon has no usable identity.')
if not isinstance(value.get('root'),str) or not value['root'].startswith('/') or '\n' in value['root']:
    raise SystemExit('Docker daemon has no usable storage root.')
print(value['id']);print(value['root'])
PYCODE
)" || return 1
  DOCKER_ID="${info%%$'\n'*}"
  DOCKER_ROOT="${info#*$'\n'}"
}

docker_local() {
  [[ -z "${DOCKER_CONTEXT:-}" ]] || { echo 'Docker context override changed; refusing operation.' >&2; return 1; }
  local deadline=10s
  [[ "${1:-}" == pull ]] && deadline=1800s
  timeout --kill-after=2s "$deadline" docker --host "$DOCKER_ENDPOINT" "$@"
}

require_disk() {
  python3 - "$PROJECT_ROOT" "${PLAYGROUND_MIN_FREE_DISK_GIB:-20}" "$STATE_DIR" "${DOCKER_ROOT:-}" <<'PY'
import math,shutil,sys
floor_gib=float(sys.argv[2])
if not math.isfinite(floor_gib) or floor_gib < 1:
    raise SystemExit('Disk floor must be finite and at least 1 GiB.')
floor=floor_gib*1024**3
for path in dict.fromkeys(p for p in (sys.argv[1],sys.argv[3],sys.argv[4]) if p):
    free=shutil.disk_usage(path).free
    if free < floor:
        raise SystemExit(f'Deployment blocked: {path} has {free/1024**3:.1f} GiB available; needs {floor/1024**3:.1f}. No pull or workload started.')
PY
}

# Serialize trusted lifecycle operations. Children must close fd 9 so a running
# viewer/worker cannot retain this lock after the launcher exits.
lock_runtime() {
  command -v flock >/dev/null || { echo 'flock is required for safe lifecycle operations.' >&2; return 1; }
  exec 9>"$RUNTIME_DIR/launcher.lock"
  flock -n 9 || { echo 'Another launcher operation is in progress.' >&2; return 1; }
  lock_registration
}

# A child registers before exec under this separate lock. If its parent is
# killed before the child runs, the next launcher either sees its durable PID
# or retires its session first, causing the delayed child to refuse execution.
lock_registration() {
  [[ "${REGISTRATION_LOCKED:-false}" == true ]] && return 0
  exec 8>"$RUNTIME_DIR/registration.lock"
  flock -w 2 8 || { echo 'Process registration is busy; retaining session.' >&2; return 1; }
  REGISTRATION_LOCKED=true
}

unlock_registration() {
  flock -u 8
  exec 8>&-
  REGISTRATION_LOCKED=false
}

read_session() {
  SESSION_ID=''
  SESSION_MODE=''
  [[ -e "$RUNTIME_DIR/session" ]] || return 0
  local value
  value="$(python3 - "$RUNTIME_DIR/session" <<'PYCODE'
import json,pathlib,re,sys
text=pathlib.Path(sys.argv[1]).read_text()
try: value=json.loads(text)
except ValueError:
    parts=text.split()
    if len(parts) not in (1,2) or not re.fullmatch(r'[a-f0-9]{32}',parts[0]):
        raise SystemExit('Invalid session record; retain it for operator review.')
    value={'id':parts[0],'mode':parts[1] if len(parts)==2 else 'research'}
if not isinstance(value,dict) or not re.fullmatch(r'[a-f0-9]{32}',str(value.get('id',''))):
    raise SystemExit('Invalid session identifier; refusing cleanup.')
if value.get('mode') not in ('research','demo'):
    raise SystemExit('Invalid session mode; refusing cleanup.')
print(value['id'],value['mode'])
PYCODE
)" || return 1
  read -r SESSION_ID SESSION_MODE <<< "$value"
}

create_session() {
  python3 - "$RUNTIME_DIR/session" "$1" "${DOCKER_ENDPOINT:-}" "${DOCKER_ID:-}" "${DOCKER_ROOT:-}" <<'PYCODE'
import json,os,pathlib,sys,uuid
path=pathlib.Path(sys.argv[1])
# Exclusive creation refuses to overwrite even an unexpected existing record.
with path.open('x') as out:
    value={'version':2,'id':uuid.uuid4().hex,'mode':sys.argv[2],'docker':None}
    if sys.argv[2]=='research':
        value['docker']={'endpoint':sys.argv[3],'daemon_id':sys.argv[4],'root':sys.argv[5]}
    json.dump(value,out); out.write('\n')
    out.flush(); os.fsync(out.fileno())
fd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
try: os.fsync(fd)
finally: os.close(fd)
PYCODE
  read_session
  export PLAYGROUND_SESSION_ID="$SESSION_ID"
}

# The same helper records before exec and verifies before pidfd signaling.
# Boot ID + start ticks + exact argv + session environment reject PID reuse.
# Exit 1 means absent/exited; exit 2 means ambiguous ownership: never signal it.
PROCESS_HELPER="$(cat <<'PYCODE'
import fcntl,json,os,pathlib,signal,sys
operation,role,runtime,state,session=sys.argv[1:6]
record_path=pathlib.Path(runtime)/f'{role}.pid'
boot=pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip()
def process(pid):
    proc=pathlib.Path(f'/proc/{pid}')
    fields=proc.joinpath('stat').read_text().rsplit(') ',1)[1].split()
    return proc,fields[0],fields[19]
def write_record():
    pid=os.getpid()
    _,_,ticks=process(pid)
    value={'version':1,'pid':pid,'start_ticks':ticks,'boot_id':boot,
           'role':role,'state_dir':state,'session_id':session}
    temp=record_path.with_suffix('.pid.tmp')
    with temp.open('w') as out:
        json.dump(value,out); out.write('\n'); out.flush(); os.fsync(out.fileno())
    os.replace(temp,record_path)
    fd=os.open(runtime,os.O_RDONLY|os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)
if operation=='launch':
    with open(pathlib.Path(runtime)/'registration.lock','a') as registration:
        fcntl.flock(registration,fcntl.LOCK_EX)
        active=json.loads((pathlib.Path(runtime)/'session').read_text())
        if active.get('id')!=session:
            raise SystemExit('Launch session was retired; refusing delayed startup.')
        write_record()
        # Python descriptors are close-on-exec, releasing registration only
        # once the registered process has its final verifiable command line.
        os.execvp(sys.argv[6],sys.argv[6:])
try:
    text=record_path.read_text()
except FileNotFoundError:
    sys.exit(1)
try:
    value=json.loads(text)
except ValueError:
    # Legacy records lack a boot/session identity. Only an absent PID is safe
    # to forget automatically; a live legacy PID needs operator review.
    old=text.split()
    if len(old)==2 and all(v.isdigit() for v in old):
        if not pathlib.Path(f'/proc/{int(old[0])}').exists(): sys.exit(1)
    print(f'{role}: invalid or live legacy PID record; refusing ownership.',file=sys.stderr)
    sys.exit(2)
fd=None
try:
    if not isinstance(value,dict) or value.get('version')!=1: raise ValueError()
    pid=value['pid']
    if type(pid) is not int or pid<=1: raise ValueError()
    if value['boot_id']!=boot:
        # A record from a different boot cannot identify any process now.
        sys.exit(1)
    if (value['role'],value['state_dir'],value['session_id'])!=(role,state,session): raise ValueError()
    if not session: raise ValueError()
    if operation in ('TERM','KILL'):
        fd=os.pidfd_open(pid)
    proc,status,ticks=process(pid)
    if ticks!=value['start_ticks']: raise ValueError()
    if status in ('Z','X'): sys.exit(1)
    args=[v.decode() for v in proc.joinpath('cmdline').read_bytes().split(b'\0') if v]
    env=proc.joinpath('environ').read_bytes().split(b'\0')
    if args[1:3]!=['-m','playground']: raise ValueError()
    if args[3] not in (('serve',) if role=='server' else ('run','demo')): raise ValueError()
    if args.count('--state-dir')!=1 or args[args.index('--state-dir')+1]!=state: raise ValueError()
    if f'PLAYGROUND_SESSION_ID={session}'.encode() not in env: raise ValueError()
    if operation in ('TERM','KILL'):
        signal.pidfd_send_signal(fd,getattr(signal,'SIG'+operation))
    else:
        print(pid)
except (FileNotFoundError,ProcessLookupError):
    sys.exit(1)
except (OSError,ValueError,KeyError,IndexError,TypeError,AttributeError,UnicodeError):
    print(f'{role}: process identity unconfirmed; refusing to signal or replace its record.',file=sys.stderr)
    sys.exit(2)
finally:
    if fd is not None: os.close(fd)
PYCODE
)"

owned_pid() {
  python3 -c "$PROCESS_HELPER" check "$1" "$RUNTIME_DIR" "$STATE_DIR" "$SESSION_ID"
}

preserve_logs() {
  python3 - "$RUNTIME_DIR" "${1:-}" <<'PYCODE'
import os,pathlib,sys,uuid
root=pathlib.Path(sys.argv[1])
for role in ('worker','server'):
    source=root/f'{role}.log'
    if source.exists():
        archive=root/'logs'; archive.mkdir(exist_ok=True)
        identity=sys.argv[2] or uuid.uuid4().hex
        target=archive/f'{identity}-{role}.log'
        if target.exists(): target=archive/f'{identity}-{uuid.uuid4().hex}-{role}.log'
        os.rename(source,target)
PYCODE
}

# Drain indefinitely after the fixed prefix so a chatty process cannot fill
# state storage or block on its log pipe. Old logs are retained at each launch.
LOG_HELPER="$(cat <<'PYCODE'
import fcntl,json,pathlib,signal,sys
signal.signal(signal.SIGHUP,signal.SIG_IGN)
remaining=8*1024*1024
with open(pathlib.Path(sys.argv[2])/'registration.lock','a') as registration:
    fcntl.flock(registration,fcntl.LOCK_EX)
    active=json.loads((pathlib.Path(sys.argv[2])/'session').read_text())
    if active.get('id')!=sys.argv[3]: raise SystemExit('Retired log session')
    out=open(sys.argv[1],'xb',buffering=0)
with out:
    while True:
        data=sys.stdin.buffer.read1(65536)
        if not data: break
        if remaining:
            written=data[:remaining]
            out.write(written)
            remaining-=len(written)
PYCODE
)"

launch_process() {
  local role="$1"
  shift
  nohup python3 -c "$PROCESS_HELPER" launch "$role" "$RUNTIME_DIR" "$STATE_DIR" "$SESSION_ID" "$@" \
    > >(python3 -c "$LOG_HELPER" "$RUNTIME_DIR/$role.log" "$RUNTIME_DIR" "$SESSION_ID" 8>&- 9>&- >/dev/null 2>&1) 2>&1 </dev/null 8>&- 9>&- &
  LAUNCHED_PID="$!"
}

stop_process() {
  local role="$1" result attempt
  if python3 -c "$PROCESS_HELPER" TERM "$role" "$RUNTIME_DIR" "$STATE_DIR" "$SESSION_ID"; then
    for attempt in {1..25}; do
      if owned_pid "$role" >/dev/null; then sleep 0.2; else
        result=$?; [[ "$result" == 1 ]] && return 0; return "$result"
      fi
    done
    if python3 -c "$PROCESS_HELPER" KILL "$role" "$RUNTIME_DIR" "$STATE_DIR" "$SESSION_ID"; then :; else
      result=$?; [[ "$result" == 1 ]] && return 0; return "$result"
    fi
    for attempt in {1..10}; do
      if owned_pid "$role" >/dev/null; then sleep 0.2; else
        result=$?; [[ "$result" == 1 ]] && return 0; return "$result"
      fi
    done
    echo "$role did not exit; retaining session and process record." >&2
    return 1
  else
    result=$?; [[ "$result" == 1 ]] && return 0; return "$result"
  fi
}

session_containers() {
  docker_local ps -aq --no-trunc --filter "label=org.robot-playground.session=$SESSION_ID"
}

verify_session_docker() {
  identify_docker || return 1
    python3 - "$RUNTIME_DIR/session" "$DOCKER_ENDPOINT" "$DOCKER_ID" "$DOCKER_ROOT" <<'PYCODE' || return 1
import json,pathlib,sys
try:
    value=json.loads(pathlib.Path(sys.argv[1]).read_text())
    expected={'endpoint':sys.argv[2],'daemon_id':sys.argv[3],'root':sys.argv[4]}
    if value.get('version')!=2 or value.get('docker')!=expected: raise ValueError()
except (ValueError,AttributeError):
    raise SystemExit('Session Docker identity is missing or changed; retain session for operator review.')
PYCODE
}

retire_session() {
  python3 - "$RUNTIME_DIR" "$SESSION_ID" <<'PYCODE'
import os,pathlib,sys
root=pathlib.Path(sys.argv[1])
source=root/'session'
archive=root/'sessions'; archive.mkdir(exist_ok=True)
target=archive/f'{sys.argv[2]}.json'
payload=source.read_bytes()
if target.exists():
    if target.read_bytes()!=payload: raise SystemExit('Retired session identity conflicts; preserving active record.')
else:
    temp=target.with_suffix('.json.tmp')
    with temp.open('wb') as out:
        out.write(payload); out.flush(); os.fsync(out.fileno())
    os.replace(temp,target)
fd=os.open(archive,os.O_RDONLY|os.O_DIRECTORY)
try: os.fsync(fd)
finally: os.close(fd)
# Persist the history directory entry before retiring the active identity.
fd=os.open(root,os.O_RDONLY|os.O_DIRECTORY)
try: os.fsync(fd)
finally: os.close(fd)
source.unlink()
fd=os.open(root,os.O_RDONLY|os.O_DIRECTORY)
try: os.fsync(fd)
finally: os.close(fd)
PYCODE
}

refuse_inflight() {
  if compgen -G "$RUNTIME_DIR/docker-inflight-*.json" >/dev/null; then
    echo 'Unresolved durable Docker intent; retain session and request operator review before any new work.' >&2
    return 1
  fi
}

reconcile_session() {
  [[ -n "$SESSION_ID" ]] || return 0
  # Demo sessions execute trusted CPU simulation only. Legacy untyped sessions
  # are always treated as research and require confirmed Docker cleanup.
  if [[ "$SESSION_MODE" != demo ]]; then
    command -v docker >/dev/null && command -v timeout >/dev/null || {
      echo 'Docker/timeout unavailable; retaining old session until cleanup can be confirmed.' >&2; return 1;
    }
    verify_session_docker || return 1
    local ids id label remaining
    ids="$(session_containers)" || { echo 'Cannot list old session containers; retaining session.' >&2; return 1; }
    if [[ -n "$ids" ]]; then
      while IFS= read -r id; do
        [[ "$id" =~ ^[a-f0-9]{64}$ ]] || { echo 'Unexpected container ID; refusing cleanup.' >&2; return 1; }
        label="$(docker_local inspect --format '{{index .Config.Labels "org.robot-playground.session"}}' "$id")" || return 1
        [[ "$label" == "$SESSION_ID" ]] || { echo 'Container session label mismatch; refusing cleanup.' >&2; return 1; }
        docker_local rm --force "$id" || return 1
      done <<< "$ids"
    fi
    remaining="$(session_containers)" || return 1
    [[ -z "$remaining" ]] || { echo 'Old session cleanup unconfirmed; retaining session.' >&2; return 1; }
    verify_session_docker || return 1
  fi
  # Empty listing cannot disprove a delayed creation by an interrupted CLI.
  # Never clear the sole active identity while any durable intent remains.
  refuse_inflight || return 1
  retire_session || return 1
  SESSION_ID=''
  SESSION_MODE=''
}

stop_runtime() {
  local role result
  lock_registration || return $?
  # Validate every live identity before making any changes.
  for role in worker server; do
    if owned_pid "$role" >/dev/null; then :; else
      result=$?; [[ "$result" == 1 ]] || return "$result"
    fi
  done
  for role in worker server; do stop_process "$role" || return $?; done
  reconcile_session || return $?
  rm -f -- "$RUNTIME_DIR/worker.pid" "$RUNTIME_DIR/server.pid"
}
