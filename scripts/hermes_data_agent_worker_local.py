#!/usr/bin/env python3
"""Hermes worker entrypoint pinned to this host's local Docker application."""

from __future__ import annotations

import sys
from pathlib import Path

import hermes_data_agent_worker


LOCAL_CONFIG = Path(__file__).with_name("local_docker_worker.json")


def main() -> int:
    if "--config" in sys.argv:
        raise ValueError("本机 Worker 不允许覆盖固定配置。")
    sys.argv.extend(["--config", str(LOCAL_CONFIG)])
    return hermes_data_agent_worker.main()


if __name__ == "__main__":
    raise SystemExit(main())
