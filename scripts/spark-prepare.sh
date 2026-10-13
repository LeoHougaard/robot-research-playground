#!/usr/bin/env bash
# Explicit operator preparation, never called automatically by start.
set -euo pipefail
source "$(dirname -- "$0")/spark-common.sh"
require_spark
require_disk
[[ "$PLAYGROUND_SANDBOX_IMAGE" =~ ^[A-Za-z0-9./:_-]+@sha256:[a-f0-9]{64}$ ]] || { echo 'Pinned image digest required.' >&2; exit 1; }
docker pull --platform linux/arm64 "$PLAYGROUND_SANDBOX_IMAGE"
docker image inspect "$PLAYGROUND_SANDBOX_IMAGE" --format 'CPU image: {{.Architecture}} {{.Os}} {{.Id}}'
python3 -m playground self-check
