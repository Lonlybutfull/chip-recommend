"""Admin-only manual data-agent controls, separate from unauthenticated read APIs."""

from __future__ import annotations

import hmac
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from chip_model.database import get_db, get_db_path
from chip_model.pipeline.data_agent import canonicalize_source_url
from chip_model.pipeline.test_workspace import read_test_seeds, save_test_seed


def control_root() -> Path:
    return (get_db_path().resolve().parent / "manual_control").resolve()


def admin_token_valid(provided: str | None) -> bool:
    path = control_root() / "admin_token"
    if not path.is_file() or not provided:
        return False
    expected = path.read_text(encoding="utf-8").strip()
    return bool(expected) and hmac.compare_digest(provided, expected)


def list_seed_urls(mode: str) -> list[dict[str, Any]]:
    if mode == "test":
        return read_test_seeds()
    if mode != "formal":
        raise ValueError("运行模式仅支持 formal 或 test。")
    with get_db(readonly=True) as db:
        rows = db.execute(
            "SELECT id,url,description,category,vendor,access_method,accessible "
            "FROM link_library WHERE COALESCE(url,'')!='' ORDER BY id DESC LIMIT 1000"
        ).fetchall()
    return [dict(row) for row in rows]


def save_seed_url(
    mode: str, url: str, description: str, category: str, vendor: str = "",
    *, enabled: bool = True,
) -> dict[str, Any]:
    canonical = canonicalize_source_url(url)
    description = description.strip()
    category = category.strip()
    vendor = vendor.strip()
    if not description or not category:
        raise ValueError("请填写来源描述和数据类别。")
    if len(description) > 300 or len(category) > 80 or len(vendor) > 80:
        raise ValueError("来源描述、类别或厂商字段过长。")
    if mode == "test":
        return save_test_seed(canonical, description, category, vendor, enabled=enabled)
    if mode != "formal":
        raise ValueError("运行模式仅支持 formal 或 test。")
    raise ValueError("正式来源维护已暂停；请在芯片隔离测试中配置来源。")


def queue_manual_run(mode: str, limit: int = 5) -> dict[str, Any]:
    if mode not in {"formal", "test"}:
        raise ValueError("运行模式仅支持 formal 或 test。")
    if mode == "formal":
        raise ValueError("正式巡检已暂停；目前只允许手动芯片隔离测试。")
    if not 1 <= limit <= 10:
        raise ValueError("手动执行的测试种子数必须在 1–10 之间。")
    root = control_root()
    pending = root / "requests"
    pending.mkdir(parents=True, exist_ok=True)
    for path in pending.glob("*.json"):
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if old.get("mode") == mode and old.get("status") in {"queued", "running"}:
            raise RuntimeError(f"已有 {mode} 手动执行请求正在排队或运行。")
    request = {"id": uuid.uuid4().hex, "mode": mode, "limit": limit,
               "status": "queued", "created_at": datetime.now(timezone.utc).isoformat()}
    target = pending / (request["id"] + ".json")
    temporary = pending / (request["id"] + ".tmp")
    temporary.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
    temporary.replace(target)
    return request


def latest_manual_result(mode: str) -> dict[str, Any] | None:
    root = control_root() / "requests"
    if not root.is_dir():
        return None
    results = []
    for path in root.glob("*.json"):
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if item.get("mode") == mode:
            results.append(item)
    return max(results, key=lambda item: item.get("created_at", "")) if results else None
