#!/usr/bin/env bash
set -euo pipefail

# Host-side scheduler entrypoint for the production Docker deployment.
readonly CONTAINER_NAME="chip-recommend"
readonly LOCK_FILE="/home/lxc/chip-recommend/source-refresh.lock"
readonly LINK_IDS="20,21,322,343,344,354,355,356,379,380,382,408,410,438,439,440,442,443,444"

if [[ "$(/usr/bin/docker inspect -f '{{.State.Running}}' "$CONTAINER_NAME" 2>/dev/null || true)" != "true" ]]; then
  echo "[$(/usr/bin/date --iso-8601=seconds)] source refresh skipped: container is not running" >&2
  exit 1
fi

exec /usr/bin/flock -n "$LOCK_FILE" \
  /usr/bin/docker exec "$CONTAINER_NAME" \
  python scripts/run_source_refresh.py \
  --link-ids "$LINK_IDS" \
  --apply-state \
  --force
