#!/usr/bin/env python3
"""Run one isolated, category-specific open-web chip information test."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from chip_model.database import get_db_path  # noqa: E402
from chip_model.pipeline.open_web_test import (  # noqa: E402
    DEFAULT_TEST_SKILL,
    TEST_SKILL_REGISTRY,
    run_open_web_test,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="开放互联网芯片信息分类测试（只写隔离测试区）"
    )
    parser.add_argument("--db", default=str(get_db_path()), help="正式库路径；只读复制")
    parser.add_argument("--chip", action="append", default=[], help="目标芯片，可重复")
    parser.add_argument("--query", action="append", default=[], help="补充搜索词，可重复")
    parser.add_argument(
        "--field",
        action="append",
        default=[],
        help="本轮目标字段，可重复；不提供时检查当前类别全部字段",
    )
    parser.add_argument(
        "--skill",
        choices=tuple(TEST_SKILL_REGISTRY),
        default=DEFAULT_TEST_SKILL,
        help="要查找的信息类别",
    )
    parser.add_argument("--search-limit", type=int, default=10)
    parser.add_argument("--visit-limit", type=int, default=10)
    parser.add_argument("--proxy", default="")
    args = parser.parse_args(argv)
    result = run_open_web_test(
        source_db=Path(args.db),
        chips=args.chip,
        skill_name=args.skill,
        extra_queries=args.query,
        target_fields=args.field,
        search_limit=args.search_limit,
        visit_limit=args.visit_limit,
        proxy=args.proxy or None,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] in {"success", "partial"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
