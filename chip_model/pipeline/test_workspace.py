"""Isolated development runs; never use the authoritative SQLite connection for a test write."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from chip_model.database import get_db_path
from chip_model.pipeline.data_agent import DataAgentOrchestrator, canonicalize_source_url
from chip_model.pipeline.link_families import (
    is_chip_source, read_family_selection, selected_family_rows,
)


def runs_root(source_db: str | Path | None = None) -> Path:
    source = Path(source_db or get_db_path()).resolve()
    return (source.parent / "test_runs").resolve()


def latest_test_db(source_db: str | Path | None = None) -> Path | None:
    root = runs_root(source_db)
    if not root.is_dir():
        return None
    sessions = sorted(
        (p for p in root.iterdir() if p.is_dir() and (p / "test_mode.json").is_file()),
        key=lambda p: p.name, reverse=True,
    )
    for session in sessions:
        db = (session / "data.db").resolve()
        try:
            db.relative_to(root)
        except ValueError:
            continue
        if db.is_file():
            return db
    return None


def seed_config_path(source_db: str | Path | None = None) -> Path:
    return runs_root(source_db) / "test_seed_config.json"


def read_test_seeds(source_db: str | Path | None = None) -> list[dict[str, str]]:
    path = seed_config_path(source_db)
    if not path.is_file():
        return []
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list):
        raise ValueError("测试种子配置格式无效。")
    return value


def save_test_seed(
    url: str, description: str, category: str, vendor: str = "",
    *, enabled: bool = True, source_db: str | Path | None = None,
) -> dict[str, str]:
    canonical = canonicalize_source_url(url)
    if len(description) > 300 or len(category) > 80 or len(vendor) > 80:
        raise ValueError("种子描述、类别或厂商字段过长。")
    item = {"url": canonical, "description": description.strip(),
            "category": category.strip(), "vendor": vendor.strip(),
            "enabled": bool(enabled)}
    if not item["description"] or not item["category"]:
        raise ValueError("种子 URL 须有描述和数据类别。")
    path = seed_config_path(source_db)
    path.parent.mkdir(parents=True, exist_ok=True)
    items = read_test_seeds(source_db)
    items = [old for old in items if old.get("url") != canonical]
    items.append(item)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
    return item


def create_test_workspace(source_db: str | Path | None = None) -> dict[str, str]:
    source = Path(source_db or get_db_path()).resolve()
    if not source.is_file():
        raise FileNotFoundError("正式数据库不存在，无法创建测试副本。")
    root = runs_root(source)
    root.mkdir(parents=True, exist_ok=True)
    session_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
    session = root / session_id
    session.mkdir(mode=0o700)
    db_path = session / "data.db"
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as live:
        with sqlite3.connect(db_path) as clone:
            live.backup(clone)
            now = datetime.now(timezone.utc).isoformat()
            seed_ids: list[int] = []
            for seed in read_test_seeds(source):
                if not seed.get("enabled", True):
                    continue
                row = clone.execute("SELECT id FROM link_library WHERE url=?", (seed["url"],)).fetchone()
                if row:
                    seed_ids.append(int(row[0]))
                    clone.execute(
                        "UPDATE link_library SET accessible='1',description=?,category=?,vendor=? "
                        "WHERE id=?",
                        (seed["description"], seed["category"], seed.get("vendor", ""), int(row[0])),
                    )
                    continue
                cursor = clone.execute(
                    "INSERT INTO link_library (url,description,category,vendor,created_at,updated_at) "
                    "VALUES (?,?,?,?,?,?)",
                    (seed["url"], seed["description"], seed["category"],
                     seed.get("vendor", ""), now, now),
                )
                seed_ids.append(int(cursor.lastrowid))
            # The copied database is a snapshot, not a continuation of an in-flight formal run.
            clone.execute(
                "UPDATE update_runs SET status='interrupted',finished_at=? "
                "WHERE status='running' AND run_type='source_refresh'",
                (now,),
            )
            # A copied unfinished formal cycle must never be claimed by the test Agent.
            clone.execute(
                "UPDATE data_agent_jobs SET status='rejected',finished_at=?,updated_at=?,"
                "error_summary='测试副本不续跑复制来的正式任务' "
                "WHERE status IN ('queued','running','awaiting_agent','awaiting_publish')",
                (now, now),
            )
            clone.execute(
                "UPDATE update_runs SET status='interrupted',finished_at=? "
                "WHERE run_type='data_agent_cycle' "
                "AND status IN ('running','awaiting_agent','awaiting_publish')",
                (now,),
            )
            clone.commit()
            if clone.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise RuntimeError("测试数据库副本完整性检查失败。")
    marker = {"mode": "test", "created_at": datetime.now(timezone.utc).isoformat(),
              "source_db": str(source), "db_path": str(db_path), "seed_ids": seed_ids}
    (session / "test_mode.json").write_text(json.dumps(marker, ensure_ascii=False), encoding="utf-8")
    (session / "source_snapshots").mkdir()
    return {"session_id": session_id, "db_path": str(db_path),
            "snapshot_dir": str(session / "source_snapshots"),
            "seed_ids": seed_ids}


def _apply_selected_family(db_path: str | Path, rows: list[dict]) -> list[int]:
    """Write selected catalogue links and their graph edges to the clone only."""
    now = datetime.now(timezone.utc).isoformat()
    selected: list[int] = []
    parent_id: int | None = None
    with sqlite3.connect(db_path) as clone:
        for item in rows:
            url = item["url"]
            existing = clone.execute("SELECT id FROM link_library WHERE url=?", (url,)).fetchone()
            if existing:
                link_id = int(existing[0])
                clone.execute(
                    "UPDATE link_library SET description=?,category=?,vendor=?,accessible='1' "
                    "WHERE id=?",
                    (item["description"], item["category"], item["vendor"], link_id),
                )
            else:
                link_id = int(clone.execute(
                    "INSERT INTO link_library "
                    "(url,description,category,vendor,access_method,accessible,created_at,updated_at) "
                    "VALUES (?,?,?,?, 'catalogue-test','1',?,?)",
                    (url, item["description"], item["category"], item["vendor"], now, now),
                ).lastrowid)
            if item["role"] == "listing":
                parent_id = link_id
            elif parent_id is None:
                raise ValueError("测试来源缺少父URL页面。")
            canonical = canonicalize_source_url(url)
            clone.execute(
                "INSERT INTO source_registry "
                "(link_id,canonical_url,url_role,crawl_depth,parent_link_id,discovered_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(link_id) DO UPDATE SET "
                "canonical_url=excluded.canonical_url,url_role=excluded.url_role,"
                "crawl_depth=excluded.crawl_depth,parent_link_id=excluded.parent_link_id,"
                "updated_at=excluded.updated_at",
                (link_id, canonical, item["role"], 0 if parent_id == link_id else 1,
                 None if parent_id == link_id else parent_id, now, now),
            )
            selected.append(link_id)
        clone.commit()
        if clone.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise RuntimeError("芯片族测试数据库完整性检查失败。")
    return selected


def _reset_test_source_baseline(db_path: str | Path, link_ids: list[int]) -> None:
    """A copied formal hash must not suppress extraction in a test run."""
    with sqlite3.connect(db_path) as clone:
        clone.executemany(
            "DELETE FROM source_monitor_state WHERE link_id=?",
            [(link_id,) for link_id in link_ids],
        )
        clone.commit()


def start_test_cycle(
    *, source_db: str | Path | None = None, limit: int = 5,
    proxy: str | None = None, max_attempts: int = 2,
) -> dict[str, object]:
    if not 1 <= limit <= 10:
        raise ValueError("测试模式每轮种子数必须在 1–10 之间。")
    family = read_family_selection(source_db)
    family_rows = selected_family_rows(family) if family else None
    if not family_rows and not any(
        seed.get("enabled", True) and is_chip_source(seed.get("url", ""), seed.get("category", ""))
        for seed in read_test_seeds(source_db)
    ):
        raise ValueError("请先选择芯片父URL族或配置明确的芯片测试来源；不会从全库随机抽取。")
    workspace = create_test_workspace(source_db)
    if family_rows:
        selected_ids = _apply_selected_family(workspace["db_path"], family_rows)
        workspace["family_parent_url"] = family_rows[0]["url"]
        workspace["family_urls"] = [item["url"] for item in family_rows]
    else:
        allowed_urls = {
            seed["url"] for seed in read_test_seeds(source_db)
            if seed.get("enabled", True) and is_chip_source(seed.get("url", ""), seed.get("category", ""))
        }
        with sqlite3.connect(workspace["db_path"]) as clone:
            selected_ids = [int(row[0]) for row in clone.execute(
                "SELECT id,url FROM link_library WHERE url IN (" + ",".join("?" for _ in allowed_urls) + ")",
                sorted(allowed_urls),
            ).fetchall()]
    if not selected_ids:
        raise ValueError("测试副本中没有可用的芯片来源。")
    _reset_test_source_baseline(workspace["db_path"], selected_ids)
    summary = DataAgentOrchestrator(
        db_path=workspace["db_path"], snapshot_dir=workspace["snapshot_dir"]
    ).run_cycle(limit=max(limit, len(selected_ids)), force=True, proxy=proxy,
                max_attempts=max_attempts, mode="test",
                selected_link_ids=selected_ids)
    return {**workspace, **summary.to_dict(), "mode": "test",
            "selected_link_ids": selected_ids}
