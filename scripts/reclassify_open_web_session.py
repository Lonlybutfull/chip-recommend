#!/usr/bin/env python3
"""Apply offline Skill-keyword classification to one isolated test session."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from chip_model.database import get_db_path  # noqa: E402
from chip_model.pipeline.open_web_full_search import reclassify_run_with_keywords  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="对已保存的测试快照补做离线信息分类")
    parser.add_argument("session_id")
    parser.add_argument("--db", default=str(get_db_path()))
    args = parser.parse_args(argv)
    print(json.dumps(reclassify_run_with_keywords(
        source_db=args.db, session_id=args.session_id
    ), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
