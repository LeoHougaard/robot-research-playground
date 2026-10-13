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
require_disk
PORT="${PLAYGROUND_PORT:-8765}"
[[ "$PORT" =~ ^[0-9]+$ ]] && (( PORT >= 1024 && PORT <= 65535 )) || { echo 'Invalid status port.' >&2; exit 1; }
for role in worker server; do
  if pid="$(owned_pid "$role")"; then
    echo "$role already running as PID $pid. Use spark-status.sh or spark-stop.sh."
    exit 1
  fi
done
if [[ "$MODE" == research ]]; then
  docker image inspect "$PLAYGROUND_SANDBOX_IMAGE" >/dev/null
  python3 - "${PLAYGROUND_LLM_BASE_URL:-http://127.0.0.1:18080/v1}" <<'PY'
import sys
from playground.llm import LocalModel
LocalModel(sys.argv[1], timeout_seconds=5).discover()
print('Local endpoint lists models; generation/tool reliability still needs qualification.')
PY
fi
python3 -m playground self-check
export PLAYGROUND_SESSION_ID="$(python3 -c 'import uuid;print(uuid.uuid4().hex)')"
printf '%s\n' "$PLAYGROUND_SESSION_ID" > "$RUNTIME_DIR/session"
trap 'bash "$PROJECT_ROOT/scripts/spark-stop.sh" || true' ERR
cd -- "$PROJECT_ROOT"
if [[ "$MODE" == demo ]]; then
  nohup python3 -m playground demo --state-dir "$STATE_DIR" --generations 4 --steps 12 > "$RUNTIME_DIR/worker.log" 2>&1 </dev/null &
else
  nohup python3 -m playground run --config "$PROJECT_ROOT/configs/research.json" --state-dir "$STATE_DIR" > "$RUNTIME_DIR/worker.log" 2>&1 </dev/null &
fi
worker_pid="$!"
if ! record_pid "$worker_pid" "$RUNTIME_DIR/worker.pid"; then
  if [[ "$MODE" == demo ]]; then wait "$worker_pid"; else exit 1; fi
fi
nohup python3 -m playground serve --state-dir "$STATE_DIR" --host "$HOST" --port "$PORT" > "$RUNTIME_DIR/server.log" 2>&1 </dev/null &
record_pid "$!" "$RUNTIME_DIR/server.pid"
python3 - "$HOST" "$PORT" <<'PY'
import sys,time,urllib.request
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
url=f'http://127.0.0.1:{sys.argv[2]}/'
for attempt in range(25):
    try:
        with opener.open(url,timeout=1) as response:
            if response.status==200:
                print(f'Status UI: {url}; runtime entirely on Spark.')
                break
    except OSError:
        time.sleep(0.2)
else:
    raise SystemExit('UI did not start; inspect state/runtime/server.log and run spark-stop.sh.')
PY
owned_pid server >/dev/null || { echo 'Recorded status server exited; inspect server.log.' >&2; exit 1; }
echo "Worker log: $RUNTIME_DIR/worker.log"
trap - ERR
