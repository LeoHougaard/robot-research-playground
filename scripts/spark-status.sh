#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "$0")/spark-common.sh"
require_spark host-only
for role in worker server; do
  if pid="$(owned_pid "$role")"; then echo "$role running PID $pid"; else echo "$role stopped"; fi
done
python3 -m playground status --state-dir "$STATE_DIR"
df -h -- "$PROJECT_ROOT" /var/lib/docker
if [[ -f "$RUNTIME_DIR/session" ]]; then
  session="$(cat "$RUNTIME_DIR/session")"
  if [[ "$session" =~ ^[a-f0-9]{32}$ ]] && command -v docker >/dev/null; then
    docker ps -a --filter "label=org.robot-playground.session=$session" --format '{{.Names}} {{.Status}}'
  fi
fi
