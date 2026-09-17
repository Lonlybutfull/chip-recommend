#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT=${1:-$(cd "$(dirname "$0")/.." && pwd)}
HERMES_ROOT=${HERMES_HOME:-"$HOME/.hermes"}
if [[ -z "${AISH_PERF_REMOTE_HOST:-}" ]]; then
  echo "Remote mirror installer is not the primary deployment path. Run install_hermes_data_heartbeat_primary.sh on the current primary host, or explicitly set AISH_PERF_REMOTE_HOST for a mirror." >&2
  exit 1
fi
REMOTE_HOST=$AISH_PERF_REMOTE_HOST
REMOTE_PORT=${AISH_PERF_REMOTE_PORT:-"22"}
REMOTE_USER=${AISH_PERF_REMOTE_USER:-"lxc"}
IDENTITY_FILE=${AISH_PERF_IDENTITY_FILE:-"$HERMES_ROOT/ssh/aishperf_source_refresh_ed25519"}
KNOWN_HOSTS_FILE=${AISH_PERF_KNOWN_HOSTS_FILE:-"$HERMES_ROOT/ssh/known_hosts"}

SKILL_SOURCE="$PROJECT_ROOT/.hermes-skills/aishperf-data-heartbeat"
SKILL_TARGET="$HERMES_ROOT/skills/aishperf-data-heartbeat"
GATE_SOURCE="$PROJECT_ROOT/scripts/hermes_source_refresh_gate.py"
GATE_TARGET="$HERMES_ROOT/scripts/hermes_source_refresh_gate.py"
APPLY_SOURCE="$PROJECT_ROOT/scripts/hermes_source_auto_apply.py"
APPLY_TARGET="$HERMES_ROOT/scripts/hermes_source_auto_apply.py"
DATA_AGENT_GATE_SOURCE="$PROJECT_ROOT/scripts/hermes_data_agent_gate.py"
DATA_AGENT_GATE_TARGET="$HERMES_ROOT/scripts/hermes_data_agent_gate.py"
DATA_AGENT_RUNNER_SOURCE="$PROJECT_ROOT/scripts/run_data_agent.py"
DATA_AGENT_RUNNER_TARGET="$HERMES_ROOT/scripts/run_data_agent.py"
DATA_AGENT_WORKER_SOURCE="$PROJECT_ROOT/scripts/hermes_data_agent_worker.py"
DATA_AGENT_WORKER_TARGET="$HERMES_ROOT/scripts/hermes_data_agent_worker.py"
DATA_CONFIG_TARGET="$HERMES_ROOT/scripts/data_agent_gate.json"
CONFIG_TARGET="$HERMES_ROOT/scripts/source_refresh_gate.json"

test -f "$SKILL_SOURCE/SKILL.md" || { echo "Missing $SKILL_SOURCE/SKILL.md" >&2; exit 1; }
test -f "$GATE_SOURCE" || { echo "Missing $GATE_SOURCE" >&2; exit 1; }
test -f "$APPLY_SOURCE" || { echo "Missing $APPLY_SOURCE" >&2; exit 1; }
test -f "$DATA_AGENT_GATE_SOURCE" || { echo "Missing $DATA_AGENT_GATE_SOURCE" >&2; exit 1; }
test -f "$DATA_AGENT_RUNNER_SOURCE" || { echo "Missing $DATA_AGENT_RUNNER_SOURCE" >&2; exit 1; }
test -f "$DATA_AGENT_WORKER_SOURCE" || { echo "Missing $DATA_AGENT_WORKER_SOURCE" >&2; exit 1; }
test -f "$IDENTITY_FILE" || { echo "Missing restricted SSH identity: $IDENTITY_FILE" >&2; exit 1; }
test -f "$KNOWN_HOSTS_FILE" || { echo "Missing pinned known_hosts: $KNOWN_HOSTS_FILE" >&2; exit 1; }

mkdir -p "$SKILL_TARGET" "$HERMES_ROOT/scripts"
cp -R "$SKILL_SOURCE/." "$SKILL_TARGET/"
cp "$GATE_SOURCE" "$GATE_TARGET"
cp "$APPLY_SOURCE" "$APPLY_TARGET"
cp "$DATA_AGENT_GATE_SOURCE" "$DATA_AGENT_GATE_TARGET"
cp "$DATA_AGENT_RUNNER_SOURCE" "$DATA_AGENT_RUNNER_TARGET"
cp "$DATA_AGENT_WORKER_SOURCE" "$DATA_AGENT_WORKER_TARGET"
for skill_dir in "$PROJECT_ROOT"/.hermes-skills/url-* "$PROJECT_ROOT"/.hermes-skills/data-* "$PROJECT_ROOT"/.hermes-skills/chip-* "$PROJECT_ROOT"/.hermes-skills/model-* "$PROJECT_ROOT"/.hermes-skills/benchmark-* "$PROJECT_ROOT"/.hermes-skills/deployment-*; do
  [[ -d "$skill_dir" ]] || continue
  target="$HERMES_ROOT/skills/$(basename "$skill_dir")"
  mkdir -p "$target"
  cp -R "$skill_dir/". "$target/"
done
chmod 700 "$GATE_TARGET" "$APPLY_TARGET" "$DATA_AGENT_GATE_TARGET" "$DATA_AGENT_RUNNER_TARGET" "$DATA_AGENT_WORKER_TARGET"

python3 -c '
import json, pathlib, sys
target, data_target, host, port, user, identity, known_hosts = sys.argv[1:]
payload = {
    "transport": "ssh",
    "failure_notify_threshold": 3,
    "notify_on_new": False,
    "timeout_seconds": 900,
    "max_attempts": 2,
    "ssh": {
        "host": host,
        "port": int(port),
        "user": user,
        "identity_file": str(pathlib.Path(identity).resolve()),
        "known_hosts_file": str(pathlib.Path(known_hosts).resolve()),
    },
}
pathlib.Path(target).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
data_payload = {**payload, "source_limit": 50}
pathlib.Path(data_target).write_text(json.dumps(data_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
' "$CONFIG_TARGET" "$DATA_CONFIG_TARGET" "$REMOTE_HOST" "$REMOTE_PORT" "$REMOTE_USER" "$IDENTITY_FILE" "$KNOWN_HOSTS_FILE"
chmod 600 "$CONFIG_TARGET" "$DATA_CONFIG_TARGET"

echo "Installed skill: $SKILL_TARGET"
echo "Installed heartbeat gate: $GATE_TARGET"
echo "Installed auto-apply bridge: $APPLY_TARGET"
echo "Installed complete data-agent gate: $DATA_AGENT_GATE_TARGET"
echo "Installed heartbeat config: $CONFIG_TARGET"
echo "App server requirement: install these executable entrypoints under /home/lxc/chip-recommend/:"
echo "  run_source_refresh.sh       <- scripts/run_source_refresh_server.sh"
echo "  run_source_auto_apply.sh    <- scripts/run_source_auto_apply_server.sh"
echo "  run_hermes_remote_command.sh <- scripts/run_hermes_remote_command.sh"
echo "  run_data_agent.sh          <- scripts/run_data_agent_server.sh"
echo "  run_data_agent_claim.sh    <- scripts/run_data_agent_claim_server.sh"
echo "  run_data_agent_snapshot.sh <- scripts/run_data_agent_snapshot_server.sh"
echo "  run_data_agent_finish.sh   <- scripts/run_data_agent_finish_server.sh"
echo "  run_candidate_publish.sh   <- scripts/run_candidate_publish_server.sh"
echo "The dedicated SSH key must use run_hermes_remote_command.sh as its ForceCommand;"
echo "the dispatcher accepts only the listed source/data-agent commands."
echo "Create the Hermes heartbeat job with:"
echo "hermes cron create '0 2 * * *' '根据注入的 source_refresh 上下文处理来源变化；按 aishperf-data-heartbeat Skill 自动校验并发布明确的官方字段更新，然后用中文报告。' --name 'AISHPerf 数据更新心跳' --skill aishperf-data-heartbeat --script hermes_source_refresh_gate.py --deliver local"
echo "完整数据智能体建议改用："
echo "hermes cron create '0 2 * * *' '根据 data_agent 上下文领取任务并按对应领域 Skill 处理；候选结果必须经 Publisher 校验。' --name 'AISHPerf 数据抓取智能体' --skill data-update-orchestrator --script hermes_data_agent_gate.py --deliver local"
echo "启用完整任务后请停用旧的单独 source_refresh 心跳，避免同一时刻重复巡检。"
