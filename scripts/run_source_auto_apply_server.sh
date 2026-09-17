#!/usr/bin/env bash
set -euo pipefail

# Fixed server-side entrypoint. Payload is accepted only on stdin and is fully
# validated again inside the container before any business field is written.
readonly CONTAINER_NAME="chip-recommend"
readonly LOCK_FILE="/home/lxc/chip-recommend/source-refresh.lock"

if [[ "$(/usr/bin/docker inspect -f '{{.State.Running}}' "$CONTAINER_NAME" 2>/dev/null || true)" != "true" ]]; then
  echo "source auto apply rejected: container is not running" >&2
  exit 1
fi

exec /usr/bin/flock -n "$LOCK_FILE" \
  /usr/bin/docker exec -i "$CONTAINER_NAME" \
  python scripts/run_source_auto_apply.py \
  --db data/data.db \
  --backup-dir data/backups/source-auto-apply
