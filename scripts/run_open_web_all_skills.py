#!/usr/bin/env python3
"""Run all six open-web chip information Skills in one isolated test session."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from chip_model.database import get_db_path  # noqa: E402
from chip_model.pipeline.open_web_full_search import run_all_skill_search  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="六类芯片信息全量搜索（只写隔离测试区）")
    parser.add_argument("--db", default=str(get_db_path()))
    parser.add_argument("--chip", action="append", default=[])
    parser.add_argument("--search-limit", type=int, default=10)
    parser.add_argument("--visit-limit", type=int, default=10)
    parser.add_argument("--proxy", default="")
    args = parser.parse_args(argv)
    result = run_all_skill_search(
        source_db=args.db, chips=args.chip or None,
        search_limit=args.search_limit, visit_limit=args.visit_limit,
        proxy=args.proxy or None,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "success" else 2


if __name__ == "__main__":
    raise SystemExit(main())
