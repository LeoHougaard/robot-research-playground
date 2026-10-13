#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "$0")/spark-common.sh"
require_spark host-only
for role in worker server; do
  if pid="$(owned_pid "$role")"; then
    kill -TERM "$pid"
    for attempt in {1..25}; do
      if ! owned_pid "$role" >/dev/null; then break; fi
      sleep 0.2
    done
    if owned_pid "$role" >/dev/null; then kill -KILL "$pid"; fi
    echo "Stopped $role PID $pid."
  fi
  rm -f -- "$RUNTIME_DIR/$role.pid"
done
if [[ -f "$RUNTIME_DIR/session" ]]; then
  session="$(cat "$RUNTIME_DIR/session")"
  [[ "$session" =~ ^[a-f0-9]{32}$ ]] || { echo 'Invalid session record; refusing Docker cleanup.' >&2; exit 1; }
  if command -v docker >/dev/null; then
    container_ids="$(docker ps -aq --filter "label=org.robot-playground.session=$session")"
    if [[ -n "$container_ids" ]]; then
      mapfile -t task_containers <<< "$container_ids"
      docker rm --force "${task_containers[@]}"
    fi
  fi
  rm -f -- "$RUNTIME_DIR/session"
fi
echo 'Archive and workspace data retained. Existing model and robot services untouched.'
