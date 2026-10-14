#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "$0")/spark-common.sh"
require_spark host-only
lock_runtime
read_session
stop_runtime
echo 'Archive and workspace data retained. Existing model and robot services untouched.'
