#!/usr/bin/env python3
"""Apply one validated Hermes source-change payload to the chip database."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from chip_model.database import get_db_path, get_project_root
from chip_model.pipeline.source_auto_apply import AutoApplyError, apply_source_update


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="验证并自动发布一条来源字段更新")
    parser.add_argument("--payload", type=Path, help="JSON 文件；不提供时从 stdin 读取")
    parser.add_argument("--db", type=Path, default=get_db_path())
    parser.add_argument("--backup-dir", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        raw = args.payload.read_text(encoding="utf-8") if args.payload else sys.stdin.read()
        payload = json.loads(raw)
        result = apply_source_update(
            payload,
            db_path=args.db,
            project_root=get_project_root(),
            backup_dir=args.backup_dir,
        )
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except (AutoApplyError, json.JSONDecodeError, OSError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "status": "rejected",
                    "error_code": exc.__class__.__name__,
                    "message": str(exc),
                    "business_tables_modified": False,
                },
                ensure_ascii=False,
                indent=2,
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
