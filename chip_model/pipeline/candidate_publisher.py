"""Validated single-writer publisher for data-agent candidate facts."""

from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
from contextlib import closing, nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from chip_model.database import (
    ChipFilters,
    ModelFilters,
    _provenance_for_fields,
    add_benchmark,
    add_compat,
    get_db,
    get_db_stats,
    search_chips,
    search_models,
    update_chip_fields,
    update_model_fields,
)
from chip_model.pipeline.candidate_validation import (
    CandidateValidationError, normalize_fact_entity_key, validate_fact_value,
)


ALLOWED_TABLES = {
    "chips", "models", "chip_model_benchmarks",
    "chip_model_compatibility", "deployment_guides",
}


class CandidatePublishError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _decode(raw: str | None) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise CandidatePublishError("候选事实的实体键不是有效 JSON。") from exc
    if not isinstance(value, dict):
        raise CandidatePublishError("候选事实的实体键必须是对象。")
    return value


def _source(candidate: dict[str, Any]) -> dict[str, str]:
    url = str(candidate.get("source_url") or "")
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise CandidatePublishError("Publisher 只接受带 HTTPS 原文证据的候选事实。")
    return {
        "source_type": str(candidate.get("source_type") or "official_page"),
        "source_url": url,
        "source_detail": f"data-agent candidate #{candidate['id']}",
        "confidence": str(candidate.get("confidence") or "medium"),
        "is_official": "1" if str(candidate.get("source_type") or "").startswith("official") else "0",
        "notes": json.dumps(
            {"evidence": candidate.get("evidence_text") or ""}, ensure_ascii=False
        ),
    }


def _columns(db: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in db.execute(f"PRAGMA table_info({table})").fetchall()}


def _apply_group(
    db_path: Path,
    candidates: list[dict[str, Any]],
    *,
    connection: sqlite3.Connection | None = None,
    commit: bool = True,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    first = candidates[0]
    table = str(first["target_table"])
    if table not in ALLOWED_TABLES:
        raise CandidatePublishError(f"不允许发布到 {table}。")
    entity_key = _decode(first.get("entity_key_json"))
    fields = {str(item["field_name"]): str(item["proposed_value"]) for item in candidates}
    source = _source(first)
    if any(str(item.get("source_url") or "") != source["source_url"] for item in candidates):
        raise CandidatePublishError("同一发布批次必须来自同一个来源 URL。")
    if any(not str(item.get("evidence_text") or "").strip() for item in candidates):
        raise CandidatePublishError("每个字段都必须有原文证据。")

    database = nullcontext(connection) if connection is not None else get_db(db_path)
    with database as db:
        table_columns = _columns(db, table)
        allowed_columns = table_columns - {"id", "created_at", "updated_at"}
        unknown = sorted(set(fields) - allowed_columns)
        if unknown:
            raise CandidatePublishError("目标表不存在字段：" + ", ".join(unknown))
        unknown_keys = sorted(set(entity_key) - table_columns)
        if unknown_keys:
            raise CandidatePublishError("实体键包含目标表不存在的字段：" + ", ".join(unknown_keys))
        try:
            entity_key = normalize_fact_entity_key(db, table, entity_key)
            for item in candidates:
                other_key = normalize_fact_entity_key(db, table, _decode(item.get("entity_key_json")))
                if other_key != entity_key:
                    raise CandidateValidationError("同一批次包含不同实体或测试条件。")
                validate_fact_value(table, str(item["field_name"]), item["proposed_value"])
        except CandidateValidationError as exc:
            raise CandidatePublishError(str(exc)) from exc
        old_values: dict[str, Any] = {}
        if table == "chips":
            row_id = int(entity_key.get("id") or 0)
            row = db.execute("SELECT * FROM chips WHERE id=?", (row_id,)).fetchone()
            if not row or (entity_key.get("chip_model") and row["chip_model"] != entity_key["chip_model"]):
                raise CandidatePublishError("芯片实体键不能唯一匹配现有记录。")
            old_values = {key: row[key] for key in fields}
            changed = {key: value for key, value in fields.items() if str(old_values.get(key) or "") != value}
            if not changed:
                raise CandidatePublishError("候选值与芯片当前值一致。")
            update_chip_fields(db, row_id, changed, source)
            fields = changed
        elif table == "models":
            row_id = int(entity_key.get("id") or 0)
            row = db.execute("SELECT * FROM models WHERE id=?", (row_id,)).fetchone()
            if not row or (entity_key.get("model_id") and row["model_id"] != entity_key["model_id"]):
                raise CandidatePublishError("模型实体键不能唯一匹配现有记录。")
            old_values = {key: row[key] for key in fields}
            changed = {key: value for key, value in fields.items() if str(old_values.get(key) or "") != value}
            if not changed:
                raise CandidatePublishError("候选值与模型当前值一致。")
            update_model_fields(db, row_id, changed, source)
            fields = changed
        elif table == "chip_model_benchmarks":
            required = {"chip_model", "model_id", "workload_type", "suite_name"}
            record = {**entity_key, **fields}
            if not required.issubset({key for key, value in record.items() if str(value).strip()}):
                raise CandidatePublishError("实测候选缺少芯片、模型、场景或测试套件唯一键。")
            duplicate = db.execute(
                "SELECT id FROM chip_model_benchmarks WHERE chip_model=? AND model_id=? "
                "AND workload_type=? AND suite_name=? AND COALESCE(precision,'')=? "
                "AND COALESCE(chip_count,'')=?",
                (
                    record["chip_model"], record["model_id"], record["workload_type"],
                    record["suite_name"], str(record.get("precision") or ""),
                    str(record.get("chip_count") or ""),
                ),
            ).fetchone()
            if duplicate:
                raise CandidatePublishError("相同测试条件的实测记录已存在。")
            row_id = add_benchmark(db, record, source)
        elif table == "chip_model_compatibility":
            required = {"chip_model", "model_id", "compat_status"}
            record = {**entity_key, **fields}
            if not required.issubset({key for key, value in record.items() if str(value).strip()}):
                raise CandidatePublishError("兼容候选缺少芯片、模型或兼容状态。")
            duplicate = db.execute(
                "SELECT id FROM chip_model_compatibility WHERE chip_model=? AND model_id=? "
                "AND compat_status=? AND COALESCE(framework,'')=? AND COALESCE(precision,'')=?",
                (
                    record["chip_model"], record["model_id"], record["compat_status"],
                    str(record.get("framework") or ""), str(record.get("precision") or ""),
                ),
            ).fetchone()
            if duplicate:
                raise CandidatePublishError("相同条件的兼容记录已存在。")
            row_id = add_compat(db, record, source)
        else:
            record = {**entity_key, **fields}
            required = {"url", "title"}
            if not required.issubset({key for key, value in record.items() if str(value).strip()}):
                raise CandidatePublishError("部署候选缺少具体 URL 或标题。")
            if urlparse(str(record["url"])).scheme != "https":
                raise CandidatePublishError("部署指南 URL 必须是 HTTPS。")
            duplicate = db.execute(
                "SELECT id FROM deployment_guides WHERE COALESCE(chip_model,'')=? "
                "AND COALESCE(model_id,'')=? AND url=?",
                (str(record.get("chip_model") or ""), str(record.get("model_id") or ""), record["url"]),
            ).fetchone()
            if duplicate:
                raise CandidatePublishError("部署指南已经存在。")
            record.setdefault("created_at", _now())
            record.setdefault("updated_at", _now())
            cols = list(record)
            db.execute(
                f"INSERT INTO deployment_guides ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",
                [record[key] for key in cols],
            )
            row_id = int(db.execute("SELECT last_insert_rowid()").fetchone()[0])
            _provenance_for_fields(db, "deployment_guides", row_id, record, source)
        integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
        foreign = db.execute("PRAGMA foreign_key_check").fetchall()
        if integrity != "ok" or foreign:
            raise CandidatePublishError("发布后数据库完整性检查失败。")
        if commit:
            db.commit()
    return str(row_id), old_values, fields


def _smoke(db_path: Path, table: str, row_id: str) -> list[str]:
    tests = ["integrity", "foreign_keys", "db_status", "json_serialization"]
    stats = get_db_stats(db_path)
    json.dumps(stats, ensure_ascii=False)
    if table == "chips":
        result = search_chips(ChipFilters(), limit=5, db_path=db_path)
        json.dumps(result, ensure_ascii=False)
        tests.append("chip_search")
    elif table == "models":
        result = search_models(ModelFilters(), limit=5, db_path=db_path)
        json.dumps(result, ensure_ascii=False)
        tests.append("model_search")
    with get_db(db_path, readonly=True) as db:
        if not db.execute(f"SELECT 1 FROM {table} WHERE id=?", (row_id,)).fetchone():
            raise CandidatePublishError("影子库无法读取刚发布的目标记录。")
    return tests


def publish_candidates(
    candidate_ids: list[int], *, db_path: str | Path, backup_dir: str | Path
) -> dict[str, Any]:
    if not candidate_ids:
        raise CandidatePublishError("candidate_ids 不能为空。")
    db_path = Path(db_path).resolve()
    started_at = _now()
    marks = ",".join("?" for _ in candidate_ids)
    with get_db(db_path, readonly=True) as db:
        rows = db.execute(
            f"SELECT * FROM extraction_candidates WHERE id IN ({marks}) ORDER BY id",
            candidate_ids,
        ).fetchall()
    candidates = [dict(row) for row in rows]
    if len(candidates) != len(set(candidate_ids)):
        raise CandidatePublishError("部分候选事实不存在。")
    if any(item["status"] != "ready_to_publish" for item in candidates):
        raise CandidatePublishError("所有候选事实都必须处于 ready_to_publish。")
    grouping = {
        (item["cycle_run_id"], item["target_table"], item["entity_key_json"], item["source_url"])
        for item in candidates
    }
    if len(grouping) != 1:
        raise CandidatePublishError("一次只能发布同一实体、目标表和来源的候选字段。")
    cycle_run_id = int(candidates[0]["cycle_run_id"])
    table = str(candidates[0]["target_table"])

    with tempfile.TemporaryDirectory(prefix="aishperf-candidate-stage-") as temp_dir:
        stage = Path(temp_dir) / "stage.db"
        with closing(sqlite3.connect(db_path)) as source, closing(sqlite3.connect(stage)) as target:
            source.backup(target)
        stage_row, _, _ = _apply_group(stage, candidates)
        tests = _smoke(stage, table, stage_row)

    backup_root = Path(backup_dir).resolve()
    backup_root.mkdir(parents=True, exist_ok=True)
    backup = backup_root / f"candidate-{candidate_ids[0]}-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.db"
    with closing(sqlite3.connect(db_path)) as source, closing(sqlite3.connect(backup)) as target:
        source.backup(target)
    finished_at = _now()
    with get_db(db_path) as db:
        db.execute("BEGIN IMMEDIATE")
        live_rows = db.execute(
            f"SELECT * FROM extraction_candidates WHERE id IN ({marks}) ORDER BY id",
            candidate_ids,
        ).fetchall()
        live_candidates = [dict(row) for row in live_rows]
        if len(live_candidates) != len(set(candidate_ids)):
            raise CandidatePublishError("部分候选事实不存在。")
        if any(item["status"] != "ready_to_publish" for item in live_candidates):
            raise CandidatePublishError("候选事实已被其他 Publisher 处理或状态已变化。")
        row_id, old_values, new_values = _apply_group(
            db_path, live_candidates, connection=db, commit=False
        )
        db.execute(
            f"UPDATE extraction_candidates SET status='published',published_at=? "
            f"WHERE status='ready_to_publish' AND id IN ({marks})",
            [finished_at, *candidate_ids],
        )
        if db.execute("SELECT changes()").fetchone()[0] != len(set(candidate_ids)):
            raise CandidatePublishError("候选事实状态发生并发变化，发布已回滚。")
        db.execute(
            "INSERT INTO candidate_publications "
            "(cycle_run_id,target_table,target_row_id,candidate_ids_json,status,old_values_json,"
            "new_values_json,source_url,backup_path,validation_json,started_at,finished_at) "
            "VALUES (?,?,?,?, 'success',?,?,?,?,?,?,?)",
            (
                cycle_run_id, table, row_id, json.dumps(candidate_ids),
                json.dumps(old_values, ensure_ascii=False), json.dumps(new_values, ensure_ascii=False),
                candidates[0]["source_url"], str(backup),
                json.dumps({"shadow_tests": tests}, ensure_ascii=False), started_at, finished_at,
            ),
        )
        db.execute(
            "INSERT INTO update_run_events "
            "(run_id,actor,stage,status,message,details_json,created_at) "
            "VALUES (?,'publisher','candidate_publish','success',?,?,?)",
            (
                cycle_run_id,
                f"{table} 发布成功，共 {len(new_values)} 个字段。",
                json.dumps(
                    {"row_id": row_id, "fields": new_values, "backup_path": str(backup), "tests": tests},
                    ensure_ascii=False,
                ),
                finished_at,
            ),
        )
        unfinished_jobs = int(
            db.execute(
                "SELECT COUNT(*) FROM data_agent_jobs WHERE cycle_run_id=? "
                "AND status IN ('queued','running','awaiting_agent')",
                (cycle_run_id,),
            ).fetchone()[0]
        )
        pending_candidates = int(
            db.execute(
                "SELECT COUNT(*) FROM extraction_candidates WHERE cycle_run_id=? "
                "AND status='ready_to_publish'",
                (cycle_run_id,),
            ).fetchone()[0]
        )
        failed_jobs = int(
            db.execute(
                "SELECT COUNT(*) FROM data_agent_jobs WHERE cycle_run_id=? AND status='failed'",
                (cycle_run_id,),
            ).fetchone()[0]
        )
        if unfinished_jobs == 0 and pending_candidates == 0:
            cycle_status = "partial" if failed_jobs else "success"
            db.execute(
                "UPDATE update_runs SET status=?,finished_at=? WHERE id=?",
                (cycle_status, finished_at, cycle_run_id),
            )
            db.execute(
                "INSERT INTO update_run_events "
                "(run_id,actor,stage,status,message,details_json,created_at) "
                "VALUES (?,'orchestrator','cycle_completed',?,?,?,?)",
                (
                    cycle_run_id,
                    cycle_status,
                    "全部 Agent 任务及候选发布完成，周期关闭。",
                    json.dumps({"failed_jobs": failed_jobs}, ensure_ascii=False),
                    finished_at,
                ),
            )
        db.commit()
    return {
        "status": "success", "cycle_run_id": cycle_run_id, "target_table": table,
        "target_row_id": row_id, "candidate_ids": candidate_ids, "old_values": old_values,
        "new_values": new_values, "backup_path": str(backup), "validation": {"shadow_tests": tests},
        "started_at": started_at, "finished_at": finished_at,
    }
