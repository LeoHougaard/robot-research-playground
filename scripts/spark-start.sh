#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "$0")/spark-common.sh"
MODE=research
HOST=127.0.0.1
for arg in "$@"; do
  case "$arg" in
    --demo) MODE=demo ;;
    *) echo 'Usage: spark-start.sh [--demo]' >&2; exit 2 ;;
  esac
done
if [[ "$MODE" == demo ]]; then require_spark host-only; else require_spark; fi
lock_runtime
read_session
for role in worker server; do
  if pid="$(owned_pid "$role")"; then
    echo "$role already running as PID $pid. Use spark-status.sh or spark-stop.sh."
    exit 1
  else
    result=$?
    [[ "$result" == 1 ]] || exit "$result"
  fi
done
# Never replace the sole identity of containers left by a crash. Cleanup is
# allowed even when disk admission for new work is currently blocked.
PREVIOUS_SESSION_ID="$SESSION_ID"
reconcile_session
refuse_inflight
rm -f -- "$RUNTIME_DIR/worker.pid" "$RUNTIME_DIR/server.pid"
require_disk
PORT="${PLAYGROUND_PORT:-8765}"
[[ "$PORT" =~ ^[0-9]+$ ]] && (( PORT >= 1024 && PORT <= 65535 )) || { echo 'Invalid status port.' >&2; exit 1; }
if [[ "$MODE" == research ]]; then
  docker_local image inspect "$PLAYGROUND_SANDBOX_IMAGE" >/dev/null
  python3 - "${PLAYGROUND_LLM_BASE_URL:-http://127.0.0.1:18080/v1}" <<'PY'
import sys
from playground.llm import LocalModel
LocalModel(sys.argv[1], timeout_seconds=5).discover()
print('Local endpoint lists models; generation/tool reliability still needs qualification.')
PY
fi
python3 -m playground self-check
preserve_logs "$PREVIOUS_SESSION_ID"
create_session "$MODE"
# EXIT also handles explicit failure exits; INT/TERM roll back this exact session.
# SIGKILL leaves durable session/PID records for the next trusted invocation.
startup_cleanup() {
  local result=$?
  trap - EXIT INT TERM
  if ! stop_runtime; then echo 'Startup cleanup unconfirmed; session retained for spark-stop.sh.' >&2; fi
  exit "$result"
}
trap startup_cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
cd -- "$PROJECT_ROOT"
unlock_registration
# Register the viewer first so the research provenance scan cannot race an
# incomplete server PID temp file. The viewer is read-only before state exists.
launch_process server python3 -m playground serve --state-dir "$STATE_DIR" --host "$HOST" --port "$PORT"
server_pid="$LAUNCHED_PID"
for attempt in {1..25}; do
  if owned_pid server >/dev/null 2>&1; then break; fi
  sleep 0.04
done
owned_pid server >/dev/null || { echo 'Status server failed during launch; inspect server.log.' >&2; exit 1; }
if [[ "$MODE" == demo ]]; then
  launch_process worker python3 -m playground demo --state-dir "$STATE_DIR" --generations 4 --steps 12
else
  launch_process worker python3 -m playground run --config "$PROJECT_ROOT/configs/research.json" --state-dir "$STATE_DIR"
fi
worker_pid="$LAUNCHED_PID"
# The child persists its identity before exec. Give registration a bounded
# chance to finish; completed demos are verified with wait below.
for attempt in {1..25}; do
  [[ -f "$RUNTIME_DIR/worker.pid" ]] && break
  sleep 0.04
done
worker_completed=false
check_worker() {
  local result
  if [[ "$worker_completed" == true ]]; then return 0; fi
  if owned_pid worker >/dev/null; then return 0; else result=$?; fi
  # The intentionally bounded demo can finish before UI startup. Its actual
  # successful child exit is required; an early research exit always fails.
  if [[ "$result" == 1 && "$MODE" == demo ]] && wait "$worker_pid"; then
    worker_completed=true
    echo 'Bounded demo completed successfully.'
    return 0
  fi
  echo 'Research/demo worker exited or ownership is unconfirmed; inspect worker.log.' >&2
  return 1
}
ready=0
for attempt in {1..25}; do
  check_worker
  if ! owned_pid server >/dev/null; then
    echo 'Recorded status server exited or ownership is unconfirmed; inspect server.log.' >&2
    exit 1
  fi
  if python3 - "$PORT" <<'PY'
import sys,urllib.request
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
try:
    with opener.open(f'http://127.0.0.1:{sys.argv[1]}/',timeout=1) as response:
        if response.status!=200: raise SystemExit(1)
except OSError:
    raise SystemExit(1)
PY
  then ready=$((ready + 1)); else ready=0; fi
  # Require survival across several readiness observations, not only fork().
  if (( ready >= 5 )); then break; fi
  sleep 0.2
done
(( ready >= 5 )) || { echo 'UI did not stabilize; inspect server.log.' >&2; exit 1; }
check_worker
owned_pid server >/dev/null || { echo 'Recorded status server exited; inspect server.log.' >&2; exit 1; }
echo "Status UI: http://$HOST:$PORT/; runtime entirely on Spark."
echo "Worker log: $RUNTIME_DIR/worker.log"
trap - EXIT INT TERM
