#!/usr/bin/env bash
set -euo pipefail
readonly CONTAINER_NAME=${AISH_PERF_CONTAINER_NAME:-chip-recommend}
readonly LOCK_FILE=${AISH_PERF_DATA_AGENT_LOCK:-/home/lxc/chip-recommend/data-agent.lock}
exec /usr/bin/flock -w 60 "$LOCK_FILE" \
  /usr/bin/docker exec -i -e PYTHONPATH=/app "$CONTAINER_NAME" python scripts/hermes_data_agent_finish_server.py
