#!/usr/bin/env python3
"""Start the loopback-only Hermes open-web tool service."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import uvicorn


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> None:
    uvicorn.run(
        "chip_model.open_web_tool_server:app",
        host=os.getenv("AISH_PERF_OPEN_WEB_TOOL_HOST", "127.0.0.1"),
        port=int(os.getenv("AISH_PERF_OPEN_WEB_TOOL_PORT", "5341")),
        log_level=os.getenv("AISH_PERF_OPEN_WEB_TOOL_LOG_LEVEL", "info"),
    )


if __name__ == "__main__":
    main()
