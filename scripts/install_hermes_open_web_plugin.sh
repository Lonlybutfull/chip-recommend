#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
HERMES_HOME="${HERMES_HOME:-${HOME}/.hermes}"
HERMES_CLI="${HERMES_CLI:-hermes}"
PLUGIN_NAME="aishperf-open-web"

if [[ ! -f "${PROJECT_ROOT}/.hermes-plugins/${PLUGIN_NAME}/plugin.yaml" ]]; then
  echo "找不到 Hermes 插件：${PROJECT_ROOT}/.hermes-plugins/${PLUGIN_NAME}" >&2
  exit 1
fi

skills=(
  chip-identity
  chip-specs
  chip-compute
  chip-compatibility
  chip-benchmark
  chip-deployment
)

mkdir -p "${HERMES_HOME}/plugins" "${HERMES_HOME}/skills" "${HERMES_HOME}/backups"
backup_dir="${HERMES_HOME}/backups/aishperf-open-web-$(date -u +%Y%m%d-%H%M%S)"
mkdir -p "${backup_dir}/plugins" "${backup_dir}/skills"
if [[ -d "${HERMES_HOME}/plugins/${PLUGIN_NAME}" ]]; then
  cp -R "${HERMES_HOME}/plugins/${PLUGIN_NAME}" "${backup_dir}/plugins/${PLUGIN_NAME}"
fi
rm -rf "${HERMES_HOME}/plugins/${PLUGIN_NAME}"
cp -R "${PROJECT_ROOT}/.hermes-plugins/${PLUGIN_NAME}" "${HERMES_HOME}/plugins/${PLUGIN_NAME}"

for skill in "${skills[@]}"; do
  source_dir="${PROJECT_ROOT}/.agents/skills/${skill}"
  if [[ ! -f "${source_dir}/SKILL.md" ]]; then
    echo "缺少 Skill：${source_dir}/SKILL.md" >&2
    exit 1
  fi
  if [[ -d "${HERMES_HOME}/skills/${skill}" ]]; then
    cp -R "${HERMES_HOME}/skills/${skill}" "${backup_dir}/skills/${skill}"
  fi
  rm -rf "${HERMES_HOME}/skills/${skill}"
  cp -R "${source_dir}" "${HERMES_HOME}/skills/${skill}"
done

"${HERMES_CLI}" plugins enable "${PLUGIN_NAME}" --no-allow-tool-override
echo "已安装 ${PLUGIN_NAME} 插件和 6 个芯片信息 Skill；旧版本备份在 ${backup_dir}。"
