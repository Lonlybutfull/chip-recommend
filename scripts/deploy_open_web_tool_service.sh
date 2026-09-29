#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
IMAGE="${AISH_PERF_IMAGE:-chip-recommend:hermes-open-web}"
CONTAINER="${AISH_PERF_TOOL_CONTAINER:-chip-recommend-open-web-tools}"
ENV_FILE="${AISH_PERF_TOOL_ENV_FILE:-${PROJECT_ROOT}/.env.open-web-tools}"
DATA_DIR="${AISH_PERF_DATA_DIR:-${PROJECT_ROOT}/data}"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "缺少环境文件：${ENV_FILE}" >&2
  echo "至少配置 AISH_PERF_OPEN_WEB_TOOL_TOKEN 和 DATA_AGENT_LLM_API_KEY。" >&2
  exit 1
fi
if [[ ! -f "${DATA_DIR}/data.db" ]]; then
  echo "缺少数据库：${DATA_DIR}/data.db" >&2
  exit 1
fi

docker build -t "${IMAGE}" "${PROJECT_ROOT}"
docker rm -f "${CONTAINER}" >/dev/null 2>&1 || true
docker run -d \
  --name "${CONTAINER}" \
  --restart unless-stopped \
  --env-file "${ENV_FILE}" \
  -e AISH_PERF_OPEN_WEB_TOOL_HOST=0.0.0.0 \
  -e AISH_PERF_OPEN_WEB_TOOL_PORT=5341 \
  -e DATA_AGENT_LLM_BASE_URL=https://api.moonshot.cn \
  -e DATA_AGENT_LLM_MODEL=kimi-k2.6 \
  -e DATA_DB_PATH=/app/data/data.db \
  -v "${DATA_DIR}:/app/data" \
  -p 127.0.0.1:5341:5341 \
  "${IMAGE}" \
  python scripts/run_open_web_tool_server.py >/dev/null

echo "工具服务已启动：http://127.0.0.1:5341（仅本机可访问）"
