#!/usr/bin/env python3
"""Hermes test heartbeat: only wake Agent for the latest isolated test session."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def latest_session(root: Path) -> Path | None:
    sessions = sorted(
        (p for p in (root / "test_runs").glob("*") if p.is_dir() and (p / "test_mode.json").is_file()),
        reverse=True,
    )
    for session in sessions:
        if (session / "data.db").is_file():
            return session
    return None


def session_config(session: Path) -> Path:
    session_id = session.name
    if not session_id.replace("-", "").isalnum():
        raise ValueError("测试会话目录名称无效。")
    config = {
        "transport": "local_docker", "container": "chip-recommend", "mode": "test",
        "db_path": f"/app/data/test_runs/{session_id}/data.db",
        "source_limit": 10, "job_limit": 5, "worker_id": f"hermes-test-{session_id}",
        "skills": ["url-discovery", "chip-catalog", "chip-basic", "chip-compute"],
        "timeout_seconds": 1800,
        "claim_command": "python3 ~/.hermes/scripts/hermes_data_agent_test_worker.py claim",
    }
    path = session / "hermes_test_gate.json"
    path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    return path


def main() -> int:
    data_root = Path(os.environ.get("AISH_PERF_DATA_ROOT", "/root/chip-recommend/data"))
    session = latest_session(data_root)
    if session is None:
        print('{"wakeAgent":false}')
        return 0
    gate = Path(__file__).with_name("hermes_data_agent_gate.py")
    result = subprocess.run(
        [sys.executable, str(gate), "--config", str(session_config(session))],
        capture_output=True, text=True, timeout=1900, check=False,
    )
    if result.stdout:
        print(result.stdout.rstrip())
    if result.stderr:
        print(result.stderr.rstrip(), file=sys.stderr)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
