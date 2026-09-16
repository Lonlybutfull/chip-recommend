#!/usr/bin/env python3
"""Restricted, read-only bridge for Hermes to consume saved evidence snapshots."""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
from chip_model.database import get_db_path

_selected_db = get_db_path().resolve()
SNAPSHOT_ROOT = (
    (_selected_db.parent if _selected_db.parent.parent.name == "test_runs" else ROOT / "data")
    / "source_snapshots"
).resolve()
MAX_BYTES = 64_000


def read_snapshot(
    snapshot_path: str, *, offset: int = 0, max_bytes: int = MAX_BYTES
) -> dict[str, object]:
    path = Path(snapshot_path).resolve()
    try:
        path.relative_to(SNAPSHOT_ROOT)
    except ValueError as exc:
        raise ValueError("只允许读取 data/source_snapshots 目录内的证据。") from exc
    if not path.is_file():
        raise FileNotFoundError("证据快照不存在。")
    raw = path.read_bytes()
    safe_offset = max(0, int(offset))
    safe_limit = min(MAX_BYTES, max(1, int(max_bytes)))
    end = min(len(raw), safe_offset + safe_limit)
    content = raw[safe_offset:end].decode("utf-8", errors="replace")
    truncated = end < len(raw)
    return {
        "snapshot_path": str(path),
        "content": content,
        "bytes": len(raw),
        "offset": safe_offset,
        "next_offset": end if truncated else None,
        "truncated": truncated,
    }


def main() -> int:
    payload = json.load(sys.stdin)
    if not isinstance(payload, dict) or not payload.get("snapshot_path"):
        raise ValueError("payload 必须包含 snapshot_path。")
    print(
        json.dumps(
            read_snapshot(
                str(payload["snapshot_path"]),
                offset=int(payload.get("offset") or 0),
                max_bytes=int(payload.get("max_bytes") or MAX_BYTES),
            ),
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
