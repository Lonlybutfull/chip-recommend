#!/usr/bin/env python3
"""Restricted test Worker wrapper; never falls back to the formal database."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from hermes_data_agent_test_gate import latest_session, session_config


def main() -> int:
    root = Path(os.environ.get("AISH_PERF_DATA_ROOT", "/root/chip-recommend/data"))
    session = latest_session(root)
    if session is None:
        raise RuntimeError("没有可用的隔离测试会话。")
    worker = Path(__file__).with_name("hermes_data_agent_worker.py")
    command = [sys.executable, str(worker), *sys.argv[1:], "--config", str(session_config(session))]
    return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
