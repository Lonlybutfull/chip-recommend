"""Schema and vocabulary checks shared by Agent finish and Candidate Publisher."""

from __future__ import annotations

import sqlite3
from typing import Any


class CandidateValidationError(ValueError):
    pass


ALLOWED_FACT_TABLES = {
    "chips", "models", "chip_model_benchmarks",
    "chip_model_compatibility", "deployment_guides",
}


def _unique_row(db: sqlite3.Connection, sql: str, params: tuple[Any, ...], label: str):
    rows = db.execute(sql + " LIMIT 2", params).fetchall()
    if len(rows) != 1:
        raise CandidateValidationError(f"{label}不能唯一匹配现有数据（匹配 {len(rows)} 条）。")
    return rows[0]


def normalize_fact_entity_key(
    db: sqlite3.Connection, table: str, entity_key: dict[str, Any]
) -> dict[str, Any]:
    """Resolve only exact existing identities; never guess a chip or model."""
    if not isinstance(entity_key, dict) or not entity_key:
        raise CandidateValidationError("实体键必须是非空对象。")
    if table not in ALLOWED_FACT_TABLES:
        raise CandidateValidationError(f"不允许的候选目标表：{table}。")
    columns = {str(row[1]) for row in db.execute(f"PRAGMA table_info({table})")}
    unknown = sorted(set(entity_key) - columns)
    if unknown:
        raise CandidateValidationError("实体键字段不属于目标表：" + ", ".join(unknown))

    if table == "chips":
        if entity_key.get("id"):
            row = _unique_row(
                db, "SELECT id,chip_model,vendor FROM chips WHERE id=?",
                (int(entity_key["id"]),), "芯片实体",
            )
        else:
            name = str(entity_key.get("chip_model") or "").strip()
            vendor = str(entity_key.get("vendor") or "").strip()
            if not name or not vendor:
                raise CandidateValidationError("芯片实体键须有现有 id，或精确 chip_model + vendor。")
            row = _unique_row(
                db,
                "SELECT id,chip_model,vendor FROM chips "
                "WHERE lower(trim(chip_model))=lower(?) AND lower(trim(vendor))=lower(?)",
                (name, vendor), "芯片实体",
            )
        if entity_key.get("chip_model") and str(entity_key["chip_model"]).casefold() != str(row["chip_model"]).casefold():
            raise CandidateValidationError("芯片实体键型号与现有记录不一致。")
        if entity_key.get("vendor") and str(entity_key["vendor"]).casefold() != str(row["vendor"]).casefold():
            raise CandidateValidationError("芯片实体键厂商与现有记录不一致。")
        return {"id": int(row["id"]), "chip_model": str(row["chip_model"])}

    if table == "models":
        if entity_key.get("id"):
            row = _unique_row(
                db, "SELECT id,model_id FROM models WHERE id=?",
                (int(entity_key["id"]),), "模型实体",
            )
        else:
            model_id = str(entity_key.get("model_id") or "").strip()
            if not model_id:
                raise CandidateValidationError("模型实体键须有现有 id 或完整 model_id。")
            row = _unique_row(
                db, "SELECT id,model_id FROM models WHERE model_id=?",
                (model_id,), "模型实体",
            )
        if entity_key.get("model_id") and entity_key["model_id"] != row["model_id"]:
            raise CandidateValidationError("模型实体键 model_id 与现有记录不一致。")
        return {"id": int(row["id"]), "model_id": str(row["model_id"])}

    if table in {"chip_model_benchmarks", "chip_model_compatibility"}:
        required = {"chip_model", "model_id"}
        if table == "chip_model_benchmarks":
            required |= {"workload_type", "suite_name", "chip_count", "precision"}
        else:
            required |= {"compat_status"}
        missing = sorted(key for key in required if not str(entity_key.get(key) or "").strip())
        if missing:
            raise CandidateValidationError("芯片×模型实体键缺少测试/适配条件：" + ", ".join(missing))
        _unique_row(
            db, "SELECT id FROM chips WHERE chip_model=?",
            (entity_key["chip_model"],), "实测/适配芯片型号",
        )
        _unique_row(
            db, "SELECT id FROM models WHERE model_id=?",
            (entity_key["model_id"],), "实测/适配模型 ID",
        )
        if table == "chip_model_benchmarks":
            try:
                if int(entity_key["chip_count"]) <= 0:
                    raise ValueError
            except (TypeError, ValueError) as exc:
                raise CandidateValidationError("实测 chip_count 必须是正整数。") from exc
            if str(entity_key["workload_type"]) not in {"training", "inference", "quantization"}:
                raise CandidateValidationError("实测 workload_type 须为 training、inference 或 quantization。")
        return entity_key

    if table == "deployment_guides":
        if not str(entity_key.get("url") or "").startswith("https://") or not str(entity_key.get("title") or "").strip():
            raise CandidateValidationError("部署实体键须包含 HTTPS url 和 title。")
        return entity_key

    raise CandidateValidationError(f"不允许的候选目标表：{table}。")


def validate_fact_value(table: str, field_name: str, value: Any) -> None:
    """Block values that would break existing filters or imply measured data."""
    text = str(value).strip()
    if table == "chips":
        if field_name == "is_released" and text not in {"0", "1"}:
            raise CandidateValidationError("chips.is_released 只接受 0/1，不接受 true/false。")
        if field_name == "production_status" and text.casefold() in {
            "announced", "released", "unreleased", "planned", "true", "false"
        }:
            raise CandidateValidationError("chips.production_status 须使用项目既有中文状态，而非英文枚举。")
        if field_name == "chip_type" and text.casefold() in {
            "ai加速器", "ai芯片", "ai accelerator", "ai chip"
        }:
            raise CandidateValidationError("chips.chip_type 须写明 GPU/NPU/ASIC 等具体类型。")
    if table == "chip_model_benchmarks" and field_name == "mfu_pct":
        try:
            mfu = float(text)
        except ValueError as exc:
            raise CandidateValidationError("mfu_pct 必须是数值百分比。") from exc
        if not 0 <= mfu <= 100:
            raise CandidateValidationError("mfu_pct 必须在 0–100 之间。")
