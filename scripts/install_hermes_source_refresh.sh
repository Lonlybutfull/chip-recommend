#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
PROJECT_ROOT=${1:-$(cd "$SCRIPT_DIR/.." && pwd)}
HERMES_ROOT=${HERMES_HOME:-"$HOME/.hermes"}
PYTHON_BIN=${PYTHON_BIN:-python3}
LINK_IDS=${SOURCE_REFRESH_LINK_IDS:-"20,21,322,343,344,354,355,356,379,380,382,408,410,438,439,440,442,443,444"}

SKILL_SOURCE="$PROJECT_ROOT/.hermes-skills/source-refresh"
GATE_SOURCE="$PROJECT_ROOT/scripts/hermes_source_refresh_gate.py"
SKILL_TARGET="$HERMES_ROOT/skills/source-refresh"
GATE_TARGET="$HERMES_ROOT/scripts/hermes_source_refresh_gate.py"
CONFIG_TARGET="$HERMES_ROOT/scripts/source_refresh_gate.json"

test -f "$SKILL_SOURCE/SKILL.md" || { echo "Missing $SKILL_SOURCE/SKILL.md" >&2; exit 1; }
test -f "$GATE_SOURCE" || { echo "Missing $GATE_SOURCE" >&2; exit 1; }
test -f "$PROJECT_ROOT/data/data.db" || { echo "Missing $PROJECT_ROOT/data/data.db" >&2; exit 1; }
command -v "$PYTHON_BIN" >/dev/null 2>&1 || { echo "Python not found: $PYTHON_BIN" >&2; exit 1; }

mkdir -p "$SKILL_TARGET" "$HERMES_ROOT/scripts"
cp "$SKILL_SOURCE/SKILL.md" "$SKILL_TARGET/SKILL.md"
cp "$GATE_SOURCE" "$GATE_TARGET"
chmod 700 "$GATE_TARGET"

"$PYTHON_BIN" -c '
import json, pathlib, sys
target, project_root, python_bin, raw_ids = sys.argv[1:]
link_ids = [int(value) for value in raw_ids.split(",") if value.strip()]
payload = {
    "project_root": str(pathlib.Path(project_root).resolve()),
    "python_executable": python_bin,
    "link_ids": link_ids,
    "db_path": "data/data.db",
    "snapshot_dir": "data/source_snapshots",
    "failure_notify_threshold": 3,
    "notify_on_new": False,
    "timeout_seconds": 900,
    "max_attempts": 2,
}
pathlib.Path(target).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
' "$CONFIG_TARGET" "$PROJECT_ROOT" "$(command -v "$PYTHON_BIN")" "$LINK_IDS"
chmod 600 "$CONFIG_TARGET"

echo "Installed source-refresh skill: $SKILL_TARGET"
echo "Installed cron gate: $GATE_TARGET"
echo "Installed gate config: $CONFIG_TARGET"
echo
echo "Create the recurring job from the Hermes conversation that should receive notifications:"
echo "hermes cron create \"every day at 2am\" \"Use the source-refresh skill to summarize the injected source_refresh context in Chinese. Do not modify business data.\" --skill source-refresh --script hermes_source_refresh_gate.py --workdir \"$PROJECT_ROOT\" --deliver origin --name \"AISHPerf source refresh\""
echo
echo "Then verify: hermes cron run \"AISHPerf source refresh\" && hermes cron doctor"
