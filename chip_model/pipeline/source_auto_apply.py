"""Validated automatic publication of source-refresh field changes.

Hermes/LLM output is treated as untrusted input.  This module only accepts a
small, explicit chip-field payload tied to an existing ``source_diffs`` row.
It validates the source domain, exact chip identity, candidate field, value
shape and verbatim evidence before staging the update on a database copy.  A
successful publication always writes ``field_provenance`` through
``database.update_chip_fields``.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import tempfile
import unicodedata
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from chip_model.database import (
    ChipFilters,
    get_chip_profile,
    get_chip_recommend_candidates,
    get_db,
    get_db_stats,
    search_chips,
    update_chip_fields,
)


DEFAULT_OFFICIAL_DOMAINS = {
    "amd.com",
    "amazon.com",
    "aws.amazon.com",
    "cambricon.com",
    "hiascend.com",
    "intel.com",
    "nvidia.com",
}

NUMERIC_FIELDS: dict[str, tuple[float, float, bool]] = {
    "vram_gb": (0.1, 4096.0, False),
    "vram_bw_gb_s": (0.1, 200_000.0, False),
    "tdp_w": (0.1, 10_000.0, False),
    "process_node_nm": (0.1, 1000.0, False),
    "interconnect_bw_gb_s": (0.1, 2_000_000.0, False),
    "compute_units": (1.0, 100_000_000.0, True),
}
TEXT_FIELDS = {"architecture", "precision_perf"}
AUTO_FIELDS = set(NUMERIC_FIELDS) | TEXT_FIELDS
EVIDENCE_MARKERS: dict[str, tuple[str, ...]] = {
    "vram_gb": ("vram", "hbm", "显存", "memory capacity", "gpu memory"),
    "vram_bw_gb_s": ("memory bandwidth", "hbm bandwidth", "显存带宽"),
    "tdp_w": ("tdp", "thermal design power", "power consumption", "功耗"),
    "process_node_nm": ("process", "nanometer", "制程", " nm"),
    "interconnect_bw_gb_s": (
        "interconnect", "nvlink", "infinity fabric", "hccs", "互联",
    ),
    "compute_units": ("compute unit", "cuda core", "tensor core", "计算单元"),
    "architecture": ("architecture", "架构"),
    "precision_perf": ("tflops", "tops", "flops", "算力"),
}


class AutoApplyError(RuntimeError):
    """Reject an unsafe or ambiguous automatic update."""


@dataclass
class ApplySummary:
    status: str
    source_update_id: int
    chip_id: int
    chip_model: str
    source_url: str
    fields: dict[str, str]
    old_values: dict[str, Any]
    provenance_written: int
    backup_path: str
    validation: dict[str, Any]
    started_at: str
    finished_at: str
    business_tables_modified: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _normal_text(value: Any) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(value or ""))).strip()


def _official_domains() -> set[str]:
    raw = os.environ.get("SOURCE_AUTO_APPLY_OFFICIAL_DOMAINS", "")
    configured = {part.strip().lower().lstrip(".") for part in raw.split(",") if part.strip()}
    return configured or DEFAULT_OFFICIAL_DOMAINS


def _validate_source_url(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme != "https" or not host:
        raise AutoApplyError("自动更新只接受 HTTPS 官方来源。")
    if not any(host == domain or host.endswith("." + domain) for domain in _official_domains()):
        raise AutoApplyError(f"来源域名 {host} 不在自动更新官方域名白名单中。")
    return host


def _validate_fields(raw_fields: Any) -> dict[str, str]:
    if not isinstance(raw_fields, dict) or not raw_fields:
        raise AutoApplyError("fields 必须是非空对象。")
    unknown = sorted(set(map(str, raw_fields)) - AUTO_FIELDS)
    if unknown:
        raise AutoApplyError(f"字段不允许自动更新：{', '.join(unknown)}。")

    clean: dict[str, str] = {}
    for field, raw in raw_fields.items():
        value = _normal_text(raw)
        if not value or len(value) > 1000:
            raise AutoApplyError(f"字段 {field} 的值为空或过长。")
        if field in NUMERIC_FIELDS:
            if not re.fullmatch(r"\d+(?:\.\d+)?", value):
                raise AutoApplyError(f"字段 {field} 必须是无单位数字。")
            number = float(value)
            lower, upper, integer_only = NUMERIC_FIELDS[field]
            if not lower <= number <= upper or (integer_only and not number.is_integer()):
                raise AutoApplyError(f"字段 {field} 的值 {value} 超出合理范围。")
        elif field == "architecture" and len(value) > 200:
            raise AutoApplyError("architecture 最长 200 个字符。")
        elif field == "precision_perf":
            if not re.search(r"(?:FP|BF|TF|INT)\d*", value, re.IGNORECASE) or not re.search(r"\d", value):
                raise AutoApplyError("precision_perf 必须包含精度标签和数值。")
        clean[str(field)] = value
    return clean


def _load_update_context(db_path: Path, update_id: int) -> dict[str, Any]:
    with get_db(db_path, readonly=True) as db:
        row = db.execute(
            "SELECT d.id, d.run_id, d.link_id, d.diff_path, d.candidate_fields_json, "
            "c.outcome, c.requested_url, c.final_url, c.checked_at, "
            "l.description, l.vendor, l.category "
            "FROM source_diffs d "
            "JOIN source_checks c ON c.id=d.source_check_id "
            "LEFT JOIN link_library l ON l.id=d.link_id WHERE d.id=?",
            (update_id,),
        ).fetchone()
    if not row:
        raise AutoApplyError(f"来源变化记录 {update_id} 不存在。")
    item = dict(row)
    if item.get("outcome") != "changed":
        raise AutoApplyError("只有 outcome=changed 的来源变化可以自动更新。")
    try:
        candidates = json.loads(item.get("candidate_fields_json") or "[]")
    except json.JSONDecodeError as exc:
        raise AutoApplyError("候选字段记录损坏，停止自动更新。") from exc
    item["candidate_fields"] = candidates if isinstance(candidates, list) else []
    return item


def _record_run_event(
    db_path: Path,
    context: dict[str, Any],
    *,
    actor: str,
    stage: str,
    status: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> None:
    """Append validation/publication progress when the observability table exists."""
    with get_db(db_path) as db:
        exists = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='update_run_events'"
        ).fetchone()
        if not exists:
            return
        db.execute(
            "INSERT INTO update_run_events "
            "(run_id, link_id, actor, stage, status, message, details_json, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                int(context["run_id"]),
                int(context["link_id"]),
                actor,
                stage,
                status,
                message,
                json.dumps(details or {}, ensure_ascii=False, separators=(",", ":")),
                _utc_now(),
            ),
        )
        db.commit()


def _resolve_chip(db_path: Path, chip_model: str) -> dict[str, Any]:
    exact = _normal_text(chip_model)
    if not exact:
        raise AutoApplyError("chip_model 不能为空。")
    with get_db(db_path, readonly=True) as db:
        rows = db.execute(
            "SELECT id, chip_model, vendor, vendor_display FROM chips "
            "WHERE lower(trim(chip_model))=lower(trim(?))",
            (exact,),
        ).fetchall()
    if len(rows) != 1:
        raise AutoApplyError(f"chip_model={exact!r} 必须精确匹配唯一芯片，当前匹配 {len(rows)} 条。")
    return dict(rows[0])


def _distinctive_model_tokens(chip_model: str) -> list[str]:
    tokens = re.findall(r"[A-Za-z]*\d+[A-Za-z0-9-]*|[\u4e00-\u9fff]{2,}", chip_model)
    ignored = {"gb", "pcie", "sxm", "oam"}
    return [token.casefold() for token in tokens if token.casefold() not in ignored and len(token) >= 2]


def _read_diff(project_root: Path, diff_value: str | None) -> str:
    if not diff_value:
        raise AutoApplyError("变化记录没有 diff_path，不能自动更新。")
    path = Path(diff_value)
    if not path.is_absolute():
        path = project_root / path
    path = path.resolve()
    allowed = (project_root / "data" / "source_snapshots").resolve()
    try:
        path.relative_to(allowed)
    except ValueError as exc:
        raise AutoApplyError("diff_path 不在受控快照目录中。") from exc
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise AutoApplyError(f"无法读取差异文件：{exc}") from exc


def _validate_evidence(
    context: dict[str, Any], chip: dict[str, Any], fields: dict[str, str],
    evidence: Any, diff_text: str,
) -> dict[str, str]:
    if not isinstance(evidence, dict):
        raise AutoApplyError("evidence 必须按字段提供网页原文证据。")
    added_text = "\n".join(
        line[1:] for line in diff_text.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )
    added_folded = _normal_text(added_text).casefold()
    source_identity = " ".join(
        str(context.get(key) or "")
        for key in ("requested_url", "final_url", "description", "vendor")
    ).casefold()
    tokens = _distinctive_model_tokens(str(chip.get("chip_model") or ""))
    if not tokens or not any(token in source_identity or token in added_folded for token in tokens):
        raise AutoApplyError("来源与目标芯片型号之间缺少可验证的唯一标识。")

    candidate_added = {
        str(item.get("field"))
        for item in context.get("candidate_fields", [])
        if isinstance(item, dict) and item.get("change_type") == "added"
    }
    missing_candidates = sorted(set(fields) - candidate_added)
    if missing_candidates:
        raise AutoApplyError(
            "以下字段没有新增行候选证据：" + ", ".join(missing_candidates)
        )

    clean_evidence: dict[str, str] = {}
    for field, value in fields.items():
        quote = _normal_text(evidence.get(field))
        if not quote or quote.casefold() not in added_folded:
            raise AutoApplyError(f"字段 {field} 的证据不是差异中的新增原文。")
        quote_folded = quote.casefold()
        if not any(marker.casefold() in quote_folded for marker in EVIDENCE_MARKERS[field]):
            raise AutoApplyError(f"字段 {field} 的证据缺少对应的字段语义。")
        value_numbers = re.findall(r"\d+(?:\.\d+)?", value)
        quote_numbers = re.findall(r"\d+(?:\.\d+)?", quote)
        if value_numbers and not any(number in quote_numbers for number in value_numbers):
            raise AutoApplyError(f"字段 {field} 的新值未出现在对应证据中。")
        if field in TEXT_FIELDS and value.casefold() not in quote_folded:
            raise AutoApplyError(f"字段 {field} 的新值必须原样出现在证据中。")
        clean_evidence[field] = quote
    return clean_evidence


def _backup_database(db_path: Path, backup_dir: Path, update_id: int) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    target = backup_dir / f"source-update-{update_id}-{stamp}.db"
    with closing(sqlite3.connect(db_path)) as source, closing(sqlite3.connect(target)) as destination:
        source.backup(destination)
    return target.resolve()


def _ensure_audit_table(db: sqlite3.Connection) -> None:
    db.execute(
        "CREATE TABLE IF NOT EXISTS source_auto_applies ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, source_diff_id INTEGER NOT NULL, "
        "chip_id INTEGER NOT NULL, chip_model TEXT NOT NULL, status TEXT NOT NULL, "
        "source_url TEXT NOT NULL, fields_json TEXT NOT NULL, old_values_json TEXT, "
        "evidence_json TEXT NOT NULL, validation_json TEXT NOT NULL, backup_path TEXT, "
        "started_at TEXT NOT NULL, finished_at TEXT, error_summary TEXT, "
        "UNIQUE(source_diff_id, chip_id), "
        "FOREIGN KEY(source_diff_id) REFERENCES source_diffs(id), "
        "FOREIGN KEY(chip_id) REFERENCES chips(id))"
    )


def _apply_to_database(
    db_path: Path, update_id: int, chip: dict[str, Any], fields: dict[str, str],
    evidence: dict[str, str], source_url: str, backup_path: str,
    validation: dict[str, Any], started_at: str,
) -> tuple[dict[str, Any], int, dict[str, str]]:
    with get_db(db_path) as db:
        _ensure_audit_table(db)
        # Finish the idempotent schema migration first, then hold one write
        # reservation for the duplicate check, chip mutation, provenance and
        # audit record. This prevents two publishers racing past the check.
        db.commit()
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute(
            "SELECT id, status FROM source_auto_applies WHERE source_diff_id=? AND chip_id=?",
            (update_id, chip["id"]),
        ).fetchone()
        if existing:
            raise AutoApplyError(
                f"变化记录 {update_id} 已对芯片 {chip['chip_model']} 处理过，拒绝重复写入。"
            )
        row = db.execute(
            "SELECT " + ", ".join(fields) + " FROM chips WHERE id=?", (chip["id"],)
        ).fetchone()
        old_values = dict(row) if row else {}
        changed_fields = {key: value for key, value in fields.items() if str(old_values.get(key) or "") != value}
        if not changed_fields:
            raise AutoApplyError("候选值与数据库当前值完全相同，无需更新。")

        source = {
            "source_type": "official_datasheet",
            "source_url": source_url,
            "source_detail": f"Hermes 自动更新；source_diff_id={update_id}",
            "confidence": "high",
            "is_official": "1",
            "notes": json.dumps({"evidence": evidence}, ensure_ascii=False),
        }
        before_provenance = db.execute(
            "SELECT COUNT(*) FROM field_provenance WHERE table_name='chips' AND row_id=?",
            (str(chip["id"]),),
        ).fetchone()[0]
        update_chip_fields(db, int(chip["id"]), changed_fields, source)
        after_provenance = db.execute(
            "SELECT COUNT(*) FROM field_provenance WHERE table_name='chips' AND row_id=?",
            (str(chip["id"]),),
        ).fetchone()[0]
        provenance_written = int(after_provenance - before_provenance)
        if provenance_written != len(changed_fields):
            raise AutoApplyError("字段溯源写入数量与业务字段更新数量不一致。")

        finished_at = _utc_now()
        db.execute(
            "INSERT INTO source_auto_applies "
            "(source_diff_id, chip_id, chip_model, status, source_url, fields_json, "
            "old_values_json, evidence_json, validation_json, backup_path, started_at, finished_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                update_id, chip["id"], chip["chip_model"], "success", source_url,
                json.dumps(changed_fields, ensure_ascii=False, sort_keys=True),
                json.dumps(old_values, ensure_ascii=False, sort_keys=True),
                json.dumps(evidence, ensure_ascii=False, sort_keys=True),
                json.dumps(validation, ensure_ascii=False, sort_keys=True),
                backup_path, started_at, finished_at,
            ),
        )
        integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = db.execute("PRAGMA foreign_key_check").fetchall()
        if integrity != "ok" or foreign_keys:
            raise AutoApplyError("数据库完整性检查失败，事务已回滚。")
        db.commit()
    return old_values, provenance_written, changed_fields


def _run_functional_smoke(db_path: Path, chip: dict[str, Any], fields: dict[str, str]) -> None:
    """Exercise the existing read/profile/recommend data contracts on a staged DB."""
    search = search_chips(
        ChipFilters(search=str(chip["chip_model"])), limit=10, db_path=db_path,
    )
    matches = [row for row in search.get("chips", []) if int(row.get("id") or 0) == int(chip["id"])]
    if len(matches) != 1:
        raise AutoApplyError("影子库芯片搜索测试失败。")
    for field, expected in fields.items():
        if str(matches[0].get(field) or "") != expected:
            raise AutoApplyError(f"影子库字段读取测试失败：{field}。")

    profile = get_chip_profile(str(chip["id"]), db_path=db_path)
    profile_identity = ((profile or {}).get("chip") or {}).get("identity") or {}
    if int(profile_identity.get("id") or 0) != int(chip["id"]):
        raise AutoApplyError("影子库芯片画像测试失败。")
    json.dumps(profile, ensure_ascii=False)

    stats = get_db_stats(db_path)
    if stats.get("chips") is None or stats.get("field_provenance") is None:
        raise AutoApplyError("影子库系统状态测试失败。")

    with get_db(db_path, readonly=True) as db:
        model = db.execute(
            "SELECT model_id FROM models WHERE COALESCE(total_params_b,'') != '' ORDER BY id LIMIT 1"
        ).fetchone()
    if model:
        model_data, candidates = get_chip_recommend_candidates(
            str(model["model_id"]), scenario="inference", tier="datacenter", db_path=db_path,
        )
        if model_data is None or not isinstance(candidates, list):
            raise AutoApplyError("影子库算力推荐候选测试失败。")


def apply_source_update(
    payload: dict[str, Any], *, db_path: str | Path, project_root: str | Path,
    backup_dir: str | Path | None = None,
) -> ApplySummary:
    """Validate, stage-test and atomically publish one Agent-proposed update."""
    started_at = _utc_now()
    if not isinstance(payload, dict):
        raise AutoApplyError("payload 必须是 JSON 对象。")
    try:
        update_id = int(payload.get("source_update_id"))
    except (TypeError, ValueError) as exc:
        raise AutoApplyError("source_update_id 必须是正整数。") from exc
    if update_id <= 0:
        raise AutoApplyError("source_update_id 必须是正整数。")

    db_path = Path(db_path).resolve()
    project_root = Path(project_root).resolve()
    backup_root = Path(backup_dir).resolve() if backup_dir else project_root / "backups" / "source-auto-apply"
    fields = _validate_fields(payload.get("fields"))
    context = _load_update_context(db_path, update_id)
    _record_run_event(
        db_path,
        context,
        actor="validator",
        stage="candidate_validation",
        status="started",
        message="开始核验 Agent 提交的候选字段、芯片身份与网页证据。",
        details={"source_update_id": update_id, "fields": fields},
    )
    try:
        chip = _resolve_chip(db_path, str(payload.get("chip_model") or ""))
        source_url = str(context.get("final_url") or context.get("requested_url") or "")
        source_host = _validate_source_url(source_url)
        diff_text = _read_diff(project_root, context.get("diff_path"))
        evidence = _validate_evidence(
            context, chip, fields, payload.get("evidence"), diff_text
        )
    except Exception as exc:
        _record_run_event(
            db_path,
            context,
            actor="validator",
            stage="candidate_validation",
            status="failed",
            message="候选更新未通过机器校验，已自动拒绝且未修改业务数据。",
            details={"fields": fields, "error_message": str(exc)},
        )
        raise
    validation = {
        "source_host": source_host,
        "exact_chip_match": True,
        "candidate_fields": sorted(fields),
        "evidence_in_added_diff": True,
        "field_formats_valid": True,
        "stage_integrity_check": "pending",
    }
    _record_run_event(
        db_path,
        context,
        actor="validator",
        stage="candidate_validation",
        status="success",
        message="字段格式、唯一芯片匹配和新增原文证据校验通过。",
        details={
            "chip_model": chip["chip_model"],
            "source_url": source_url,
            "fields": fields,
        },
    )

    # Test the exact mutation on a private database copy before touching the
    # live file.  The staging transaction exercises schema, provenance and
    # integrity constraints with the same implementation as production.
    try:
        with tempfile.TemporaryDirectory(prefix="aishperf-source-stage-") as temp_dir:
            stage_db = Path(temp_dir) / "stage.db"
            with closing(sqlite3.connect(db_path)) as source, closing(sqlite3.connect(stage_db)) as destination:
                source.backup(destination)
            stage_backup = Path(temp_dir) / "stage-backup.db"
            shutil.copy2(stage_db, stage_backup)
            _, _, staged_fields = _apply_to_database(
                stage_db, update_id, chip, fields, evidence, source_url,
                str(stage_backup), validation, started_at,
            )
            _run_functional_smoke(stage_db, chip, staged_fields)
            validation["stage_integrity_check"] = "passed"
            validation["existing_function_smoke"] = [
                "chip_search", "chip_profile", "db_status", "recommend_candidate", "json_serialization"
            ]
    except Exception as exc:
        _record_run_event(
            db_path,
            context,
            actor="validator",
            stage="shadow_test",
            status="failed",
            message="影子数据库或现有功能测试失败，已停止正式发布。",
            details={"fields": fields, "error_message": str(exc)},
        )
        raise
    _record_run_event(
        db_path,
        context,
        actor="validator",
        stage="shadow_test",
        status="success",
        message="影子数据库写入、完整性检查和现有功能冒烟测试通过。",
        details={"tests": validation["existing_function_smoke"]},
    )

    backup_path = _backup_database(db_path, backup_root, update_id)
    _record_run_event(
        db_path,
        context,
        actor="publisher",
        stage="publish",
        status="started",
        message="数据库备份完成，开始事务写入正式数据与字段溯源。",
        details={"backup_path": str(backup_path), "fields": fields},
    )
    try:
        old_values, provenance_written, changed_fields = _apply_to_database(
            db_path, update_id, chip, fields, evidence, source_url,
            str(backup_path), validation, started_at,
        )
    except Exception as exc:
        # The production write is transactional, but retain the pre-write
        # backup for audit/recovery rather than deleting evidence.
        _record_run_event(
            db_path,
            context,
            actor="publisher",
            stage="publish",
            status="failed",
            message="正式数据库事务写入失败，业务数据已回滚。",
            details={
                "backup_path": str(backup_path),
                "fields": fields,
                "error_message": str(exc),
            },
        )
        raise

    summary = ApplySummary(
        status="success",
        source_update_id=update_id,
        chip_id=int(chip["id"]),
        chip_model=str(chip["chip_model"]),
        source_url=source_url,
        fields=changed_fields,
        old_values=old_values,
        provenance_written=provenance_written,
        backup_path=str(backup_path),
        validation=validation,
        started_at=started_at,
        finished_at=_utc_now(),
        business_tables_modified=True,
    )
    _record_run_event(
        db_path,
        context,
        actor="publisher",
        stage="publish",
        status="success",
        message=f"正式数据更新完成，共写入 {provenance_written} 条字段溯源。",
        details={
            "chip_model": chip["chip_model"],
            "fields": changed_fields,
            "old_values": old_values,
            "provenance_written": provenance_written,
            "backup_path": str(backup_path),
        },
    )
    return summary
