#!/usr/bin/env python3
"""Run one backend-only Hermes open-web test."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from chip_model.database import get_db_path  # noqa: E402
from chip_model.pipeline.hermes_open_web_contracts import HERMES_OPEN_WEB_SKILLS  # noqa: E402
from chip_model.pipeline.hermes_open_web_orchestrator import run_hermes_open_web_test  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Hermes 单 Skill 开放互联网隔离测试")
    parser.add_argument("--db", default=str(get_db_path()), help="正式数据库，只读复制")
    parser.add_argument("--skill", choices=HERMES_OPEN_WEB_SKILLS, required=True)
    parser.add_argument("--chip", required=True, help="目标芯片")
    parser.add_argument("--field", action="append", default=[], help="目标字段，可重复")
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args(argv)
    result = run_hermes_open_web_test(
        source_db=Path(args.db), skill=args.skill, target_chip=args.chip,
        target_fields=args.field or None, timeout=args.timeout,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") in {"success", "partial"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
