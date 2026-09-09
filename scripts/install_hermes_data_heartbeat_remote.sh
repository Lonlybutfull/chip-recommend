#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT=${1:-$(cd "$(dirname "$0")/.." && pwd)}
HERMES_ROOT=${HERMES_HOME:-"$HOME/.hermes"}
REMOTE_HOST=${AISH_PERF_REMOTE_HOST:-"81.70.231.92"}
REMOTE_PORT=${AISH_PERF_REMOTE_PORT:-"22"}
REMOTE_USER=${AISH_PERF_REMOTE_USER:-"lxc"}
IDENTITY_FILE=${AISH_PERF_IDENTITY_FILE:-"$HERMES_ROOT/ssh/aishperf_source_refresh_ed25519"}
KNOWN_HOSTS_FILE=${AISH_PERF_KNOWN_HOSTS_FILE:-"$HERMES_ROOT/ssh/known_hosts"}

SKILL_SOURCE="$PROJECT_ROOT/.hermes-skills/aishperf-data-heartbeat"
SKILL_TARGET="$HERMES_ROOT/skills/aishperf-data-heartbeat"
GATE_SOURCE="$PROJECT_ROOT/scripts/hermes_source_refresh_gate.py"
GATE_TARGET="$HERMES_ROOT/scripts/hermes_source_refresh_gate.py"
CONFIG_TARGET="$HERMES_ROOT/scripts/source_refresh_gate.json"

test -f "$SKILL_SOURCE/SKILL.md" || { echo "Missing $SKILL_SOURCE/SKILL.md" >&2; exit 1; }
test -f "$GATE_SOURCE" || { echo "Missing $GATE_SOURCE" >&2; exit 1; }
test -f "$IDENTITY_FILE" || { echo "Missing restricted SSH identity: $IDENTITY_FILE" >&2; exit 1; }
test -f "$KNOWN_HOSTS_FILE" || { echo "Missing pinned known_hosts: $KNOWN_HOSTS_FILE" >&2; exit 1; }

mkdir -p "$SKILL_TARGET" "$HERMES_ROOT/scripts"
cp -R "$SKILL_SOURCE/." "$SKILL_TARGET/"
cp "$GATE_SOURCE" "$GATE_TARGET"
chmod 700 "$GATE_TARGET"

python3 -c '
import json, pathlib, sys
target, host, port, user, identity, known_hosts = sys.argv[1:]
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
' "$CONFIG_TARGET" "$REMOTE_HOST" "$REMOTE_PORT" "$REMOTE_USER" "$IDENTITY_FILE" "$KNOWN_HOSTS_FILE"
chmod 600 "$CONFIG_TARGET"

echo "Installed skill: $SKILL_TARGET"
echo "Installed heartbeat gate: $GATE_TARGET"
echo "Installed heartbeat config: $CONFIG_TARGET"
echo "Create the Hermes heartbeat job with:"
echo "hermes cron create '0 2 * * *' '根据注入的 source_refresh 上下文生成中文巡检通知；禁止修改业务数据。' --name 'AISHPerf 数据更新心跳' --skill aishperf-data-heartbeat --script hermes_source_refresh_gate.py --deliver local"

