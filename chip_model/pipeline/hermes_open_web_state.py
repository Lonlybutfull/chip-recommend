"""Filesystem state for isolated Hermes open-web runs."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any

from chip_model.pipeline.hermes_open_web_contracts import HERMES_OPEN_WEB_SKILLS, SKILL_LABELS
from chip_model.pipeline.open_web_test import (
    _ensure_test_tables,
    _now,
    _resolve_target_fields,
    database_fingerprints,
    resolve_test_skill,
)
from chip_model.pipeline.test_workspace import create_test_workspace, runs_root


HERMES_OPEN_WEB_SCHEMA = "hermes-open-web-v2"
LEGACY_HERMES_OPEN_WEB_SCHEMA = "hermes-open-web-v1"
TERMINAL_UNIT_STATUSES = {"success", "partial", "failed"}


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def append_jsonl(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def read_artifact(folder: Path, name: str, *, default: Any) -> Any:
    if Path(name).name != name:
        raise ValueError("产物名称无效。")
    path = folder / name
    if not path.is_file() or path.is_symlink():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def read_jsonl(folder: Path, name: str) -> list[dict[str, Any]]:
    if Path(name).name != name:
        raise ValueError("产物名称无效。")
    path = folder / name
    if not path.is_file() or path.is_symlink():
        return []
    values: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            values.append(value)
    return values


def resolve_run_dir(source_db: str | Path, run_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{7,80}", str(run_id)):
        raise ValueError("无效运行 ID。")
    root = runs_root(source_db)
    folder = (root / str(run_id)).resolve()
    try:
        folder.relative_to(root)
    except ValueError as exc:
        raise ValueError("运行目录越界。") from exc
    if folder.is_symlink() or not folder.is_dir():
        raise ValueError("运行不存在。")
    marker = read_artifact(folder, "test_mode.json", default={})
    if marker.get("mode") != "test" or marker.get("pipeline") != "hermes-open-web":
        raise ValueError("不是 Hermes 开放互联网隔离运行。")
    return folder


def resolve_unit_dir(source_db: str | Path, run_id: str, unit_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}", str(unit_id)):
        raise ValueError("无效单元 ID。")
    run_folder = resolve_run_dir(source_db, run_id)
    units_root = (run_folder / "units").resolve()
    folder = (units_root / str(unit_id)).resolve()
    try:
        folder.relative_to(units_root)
    except ValueError as exc:
        raise ValueError("运行单元目录越界。") from exc
    if folder.is_symlink() or not folder.is_dir():
        raise ValueError("运行单元不存在。")
    return folder


def stable_candidate_id(canonical_url: str) -> str:
    digest = hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()[:16]
    return f"candidate-{digest}"


def stable_unit_id(target_chip: str) -> str:
    digest = hashlib.sha256(target_chip.casefold().encode("utf-8")).hexdigest()[:16]
    return f"chip-{digest}"


def _database_chip_snapshot(source: Path) -> list[str]:
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as db:
        values = [str(row[0] or "").strip() for row in db.execute(
            "SELECT chip_model FROM chips WHERE TRIM(COALESCE(chip_model,''))<>''"
        )]
    unique: dict[str, str] = {}
    for value in values:
        unique.setdefault(value.casefold(), value)
    return sorted(unique.values(), key=str.casefold)


def _unit_manifest(
    *, session_id: str, skill: str, scope_type: str, target_chip: str,
    target_fields: list[str], unit_id: str,
) -> dict[str, Any]:
    return {
        "schema_version": HERMES_OPEN_WEB_SCHEMA,
        "session_id": session_id,
        "unit_id": unit_id,
        "mode": "test",
        "skill": skill,
        "skill_label": SKILL_LABELS[skill],
        "scope_type": scope_type,
        "target_chip": target_chip,
        "target_fields": target_fields,
        "status": "pending",
        "stage": "waiting_for_hermes",
        "attempts": 0,
        "started_at": None,
        "finished_at": None,
        "error": "",
        "counts": {
            "queries": 0, "search_results": 0, "unique_candidates": 0,
            "previewed": 0, "reachable": 0, "selected": 0,
            "rejected_urls": 0, "extracted": 0, "validated": 0,
            "rejected": 0, "linked_tasks": 0, "new_chip_candidates": 0,
        },
        "artifacts": {
            "search_plan": "hermes_search_plan.json",
            "search_results": "search_results.jsonl",
            "previews": "url_previews.jsonl",
            "decisions": "url_decisions.jsonl",
            "tool_trace": "hermes_tool_trace.jsonl",
            "url_assets": "url_assets.jsonl",
            "facts": "extracted_facts.jsonl",
            "linked_tasks": "linked_tasks.jsonl",
            "new_chip_candidates": "new_chip_candidates.jsonl",
        },
    }


def create_hermes_open_web_run(
    *,
    source_db: str | Path,
    skill: str,
    target_chip: str | None = None,
    target_fields: list[str] | None = None,
) -> dict[str, Any]:
    if skill not in HERMES_OPEN_WEB_SKILLS:
        raise ValueError(f"不支持的 Skill：{skill}。")
    source = Path(source_db).resolve()
    if not source.is_file():
        raise FileNotFoundError("正式数据库不存在。")
    _, skill_config = resolve_test_skill(skill)
    fields = _resolve_target_fields(skill_config, target_fields)
    before = database_fingerprints(source)
    workspace = create_test_workspace(source)
    folder = Path(workspace["db_path"]).parent
    marker = read_artifact(folder, "test_mode.json", default={})
    selected_chip = str(target_chip or "").strip()
    scope = "single" if selected_chip else "all"
    chips = [selected_chip] if selected_chip else _database_chip_snapshot(source)
    marker.update({
        "pipeline": "hermes-open-web",
        "schema_version": HERMES_OPEN_WEB_SCHEMA,
        "skill": skill,
        "scope": scope,
        "target_chip": selected_chip,
    })
    atomic_json(folder / "test_mode.json", marker)
    _ensure_test_tables(Path(workspace["db_path"]))
    units: list[dict[str, Any]] = []
    for chip in chips:
        units.append({
            "unit_id": stable_unit_id(chip), "scope_type": "chip",
            "target_chip": chip, "status": "pending",
        })
    if scope == "all":
        units.append({
            "unit_id": "discovery", "scope_type": "discovery",
            "target_chip": "", "status": "pending",
        })
    for summary in units:
        unit_folder = folder / "units" / summary["unit_id"]
        unit_folder.mkdir(parents=True, exist_ok=False)
        (unit_folder / "source_snapshots").mkdir()
        atomic_json(unit_folder / "manifest.json", _unit_manifest(
            session_id=workspace["session_id"], skill=skill,
            scope_type=summary["scope_type"], target_chip=summary["target_chip"],
            target_fields=fields, unit_id=summary["unit_id"],
        ))
    manifest = {
        "schema_version": HERMES_OPEN_WEB_SCHEMA,
        "session_id": workspace["session_id"],
        "mode": "test",
        "skill": skill,
        "skill_label": SKILL_LABELS[skill],
        "scope": scope,
        "target_chip": selected_chip,
        "chips": chips,
        "unit_count": len(units),
        "units": units,
        "target_fields": fields,
        "status": "running",
        "stage": "units_pending",
        "started_at": _now(),
        "finished_at": None,
        "formal_database_before": before,
        "formal_database_modified": False,
        "counts": {
            "units_total": len(units),
            "units_completed": 0,
            "units_success": 0,
            "units_partial": 0,
            "units_failed": 0,
            "queries": 0,
            "search_results": 0,
            "unique_candidates": 0,
            "previewed": 0,
            "reachable": 0,
            "selected": 0,
            "rejected_urls": 0,
            "extracted": 0,
            "validated": 0,
            "rejected": 0,
            "linked_tasks": 0,
            "new_chip_candidates": 0,
        },
        "artifacts": {
            "search_plan": "hermes_search_plan.json",
            "search_results": "search_results.jsonl",
            "previews": "url_previews.jsonl",
            "decisions": "url_decisions.jsonl",
            "tool_trace": "hermes_tool_trace.jsonl",
            "url_assets": "url_assets.jsonl",
            "facts": "extracted_facts.jsonl",
            "linked_tasks": "linked_tasks.jsonl",
        },
    }
    atomic_json(folder / "manifest.json", manifest)
    return {**workspace, **manifest}


def load_manifest(folder: Path) -> dict[str, Any]:
    value = read_artifact(folder, "manifest.json", default={})
    if value.get("schema_version") not in {
        HERMES_OPEN_WEB_SCHEMA, LEGACY_HERMES_OPEN_WEB_SCHEMA,
    }:
        raise ValueError("运行清单版本不匹配。")
    return value


def save_manifest(folder: Path, manifest: dict[str, Any]) -> None:
    atomic_json(folder / "manifest.json", manifest)


def load_unit_manifest(folder: Path) -> dict[str, Any]:
    value = read_artifact(folder, "manifest.json", default={})
    if value.get("schema_version") != HERMES_OPEN_WEB_SCHEMA or not value.get("unit_id"):
        raise ValueError("运行单元清单版本不匹配。")
    return value


def save_unit_manifest(folder: Path, manifest: dict[str, Any]) -> None:
    if manifest.get("status") in TERMINAL_UNIT_STATUSES and manifest.get("stage") != "completed":
        raise ValueError("终态单元必须处于 completed 阶段。")
    atomic_json(folder / "manifest.json", manifest)


def aggregate_parent_manifest(folder: Path) -> dict[str, Any]:
    manifest = load_manifest(folder)
    if manifest.get("schema_version") != HERMES_OPEN_WEB_SCHEMA:
        return manifest
    unit_manifests: list[dict[str, Any]] = []
    units: list[dict[str, Any]] = []
    for summary in manifest.get("units") or []:
        unit_folder = folder / "units" / str(summary["unit_id"])
        unit = load_unit_manifest(unit_folder)
        unit_manifests.append(unit)
        units.append({
            "unit_id": unit["unit_id"], "scope_type": unit["scope_type"],
            "target_chip": unit.get("target_chip") or "", "status": unit["status"],
            "stage": unit["stage"], "attempts": unit.get("attempts", 0),
            "error": unit.get("error") or "", "counts": unit.get("counts") or {},
        })
    count_keys = (
        "queries", "search_results", "unique_candidates", "previewed", "reachable",
        "selected", "rejected_urls", "extracted", "validated", "rejected",
        "linked_tasks", "new_chip_candidates",
    )
    statuses = [unit["status"] for unit in unit_manifests]
    counts = {key: sum(int((unit.get("counts") or {}).get(key, 0)) for unit in unit_manifests)
              for key in count_keys}
    counts.update({
        "units_total": len(unit_manifests),
        "units_completed": sum(status in TERMINAL_UNIT_STATUSES for status in statuses),
        "units_success": statuses.count("success"),
        "units_partial": statuses.count("partial"),
        "units_failed": statuses.count("failed"),
    })
    manifest["units"] = units
    manifest["counts"] = counts
    if unit_manifests and counts["units_completed"] == len(unit_manifests):
        if counts["units_failed"] == len(unit_manifests):
            manifest["status"] = "failed"
        elif counts["units_failed"] or counts["units_partial"]:
            manifest["status"] = "partial"
        else:
            manifest["status"] = "success"
        manifest["stage"] = "completed"
        manifest["finished_at"] = _now()
    else:
        manifest["status"] = "running"
        manifest["stage"] = "units_running"
    save_manifest(folder, manifest)
    return manifest


def trace_tool(
    folder: Path,
    *,
    tool: str,
    status: str,
    started_at: str,
    finished_at: str,
    input_summary: dict[str, Any],
    output_summary: dict[str, Any] | None = None,
    error: str = "",
) -> None:
    append_jsonl(folder / "hermes_tool_trace.jsonl", {
        "schema_version": HERMES_OPEN_WEB_SCHEMA,
        "tool": tool,
        "status": status,
        "started_at": started_at,
        "finished_at": finished_at,
        "input_summary": input_summary,
        "output_summary": output_summary or {},
        "error": str(error)[:1000],
    })
