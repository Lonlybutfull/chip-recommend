#!/usr/bin/env bash
set -euo pipefail
readonly CONTAINER_NAME=${AISH_PERF_CONTAINER_NAME:-chip-recommend}
readonly SKILLS="url-discovery,chip-catalog,chip-basic,chip-compute,chip-interconnect,chip-ecosystem,chip-price,model-catalog,model-activity,benchmark-ingest,deployment-ingest,data-quality-review"
exec /usr/bin/docker exec "$CONTAINER_NAME" python scripts/run_data_agent.py claim \
  --worker hermes-heartbeat --skills "$SKILLS" --limit "${AISH_PERF_AGENT_JOB_LIMIT:-5}"
