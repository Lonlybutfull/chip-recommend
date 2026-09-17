#!/usr/bin/env python3
"""CLI entry point for the safe source refresh pilot."""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from chip_model.database import get_db_path
from chip_model.pipeline.source_refresh import (
    DEFAULT_SNAPSHOT_DIR,
    ConfigurationError,
    SourceRefreshError,
    SourceRefresher,
    parse_link_ids,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "增量复查 link_library 中显式选定的来源。默认 dry-run；不会修改芯片、模型、"
            "实测、兼容性或 field_provenance。"
        )
    )
    parser.add_argument("--link-ids", required=True, help="逗号分隔的 link_library ID，例如 20,21")
    parser.add_argument("--limit", type=int, help="只处理白名单中的前 N 条")
    parser.add_argument("--apply-state", action="store_true", help="执行网络检查并写控制表/快照")
    parser.add_argument("--dry-run", action="store_true", help="显式声明预览模式（默认行为）")
    parser.add_argument("--force", action="store_true", help="忽略 next_check_at，强制复查")
    parser.add_argument("--db", type=Path, default=get_db_path(), help="SQLite 数据库路径")
    parser.add_argument(
        "--snapshot-dir", type=Path, default=DEFAULT_SNAPSHOT_DIR, help="内容寻址快照目录"
    )
    parser.add_argument("--connect-timeout", type=float, default=5.0, help="连接超时秒数")
    parser.add_argument("--read-timeout", type=float, default=20.0, help="读取超时秒数")
    parser.add_argument("--proxy", default=os.environ.get("SOURCE_REFRESH_PROXY"), help="可选 HTTP(S) 代理")
    parser.add_argument("--max-bytes", type=int, default=5 * 1024 * 1024, help="单响应最大字节数")
    parser.add_argument("--max-attempts", type=int, default=2, help="可重试错误的最大尝试次数")
    parser.add_argument("--max-redirects", type=int, default=5, help="最大重定向次数")
    parser.add_argument(
        "--stale-after-minutes", type=int, default=360, help="running 批次超过该时间后可恢复"
    )
    parser.add_argument("--json-output", type=Path, help="额外保存 JSON 摘要到指定文件")
    parser.add_argument("--verbose", action="store_true", help="失败时输出完整堆栈")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.apply_state and args.dry_run:
        parser.error("--apply-state 与 --dry-run 不能同时使用。")
    positive_values = {
        "--connect-timeout": args.connect_timeout,
        "--read-timeout": args.read_timeout,
        "--max-bytes": args.max_bytes,
        "--max-attempts": args.max_attempts,
        "--stale-after-minutes": args.stale_after_minutes,
    }
    if args.limit is not None:
        positive_values["--limit"] = args.limit
    invalid = [name for name, value in positive_values.items() if value <= 0]
    if invalid:
        parser.error(f"{', '.join(invalid)} 必须大于 0。")
    if args.max_redirects < 0:
        parser.error("--max-redirects 不能小于 0。")

    refresher = SourceRefresher(
        db_path=args.db,
        snapshot_dir=args.snapshot_dir,
        connect_timeout=args.connect_timeout,
        read_timeout=args.read_timeout,
        proxy=args.proxy,
        max_bytes=args.max_bytes,
        max_attempts=args.max_attempts,
        max_redirects=args.max_redirects,
        stale_after_minutes=args.stale_after_minutes,
    )
    try:
        link_ids = parse_link_ids(args.link_ids)
        if args.limit is not None:
            link_ids = link_ids[: args.limit]
        summary = refresher.run(link_ids, apply_state=args.apply_state, force=args.force)
        payload = json.dumps(summary.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)
        print(payload)
        if args.json_output:
            args.json_output.parent.mkdir(parents=True, exist_ok=True)
            args.json_output.write_text(payload + "\n", encoding="utf-8")
        return 2 if summary.status == "partial" else 0
    except (ConfigurationError, SourceRefreshError, OSError, ValueError) as exc:
        error = {
            "status": "failed",
            "error_code": exc.__class__.__name__,
            "message": str(exc),
            "fix": "检查 link_ids、数据库路径、网络/代理、快照目录权限后重试。",
            "business_tables_modified": False,
        }
        print(json.dumps(error, ensure_ascii=False, indent=2), file=sys.stderr)
        if args.verbose:
            traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
