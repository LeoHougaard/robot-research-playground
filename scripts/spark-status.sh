#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "$0")/spark-common.sh"
require_spark host-only
lock_runtime
read_session
for role in worker server; do
  if pid="$(owned_pid "$role")"; then echo "$role running PID $pid"; else
    result=$?
    if [[ "$result" == 1 ]]; then echo "$role stopped"; else echo "$role identity unconfirmed"; fi
  fi
done
python3 -m playground status --state-dir "$STATE_DIR"
df -h -- "$PROJECT_ROOT" "$STATE_DIR"
if [[ -n "$SESSION_ID" && "$SESSION_MODE" != demo ]] && command -v docker >/dev/null; then
  verify_session_docker
  docker_local ps -a --filter "label=org.robot-playground.session=$SESSION_ID" --format '{{.Names}} {{.Status}}'
fi
