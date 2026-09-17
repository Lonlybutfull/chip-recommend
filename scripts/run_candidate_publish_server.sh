#!/usr/bin/env bash
set -euo pipefail
readonly CONTAINER_NAME=${AISH_PERF_CONTAINER_NAME:-chip-recommend}
exec /usr/bin/docker exec -i -e PYTHONPATH=/app "$CONTAINER_NAME" python scripts/hermes_candidate_publish_server.py
