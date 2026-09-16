#!/usr/bin/env bash
set -euo pipefail

readonly CONTAINER_NAME=${AISH_PERF_CONTAINER_NAME:-chip-recommend}
readonly LOCK_FILE=${AISH_PERF_DATA_AGENT_LOCK:-/home/lxc/chip-recommend/data-agent.lock}
SOURCE_LIMIT=${AISH_PERF_SOURCE_LIMIT:-50}

if [[ "$(/usr/bin/docker inspect -f '{{.State.Running}}' "$CONTAINER_NAME" 2>/dev/null || true)" != "true" ]]; then
  echo "[$(/usr/bin/date --iso-8601=seconds)] data agent skipped: container is not running" >&2
  exit 1
fi

exec /usr/bin/flock -n "$LOCK_FILE" \
  /usr/bin/docker exec "$CONTAINER_NAME" \
  python scripts/run_data_agent.py run --limit "$SOURCE_LIMIT" "$@"
