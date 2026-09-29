"""Filesystem state for isolated Hermes open-web runs."""

from __future__ import annotations

import hashlib
import json
import os
import re
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


HERMES_OPEN_WEB_SCHEMA = "hermes-open-web-v1"


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


def stable_candidate_id(canonical_url: str) -> str:
    digest = hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()[:16]
    return f"candidate-{digest}"


def create_hermes_open_web_run(
    *,
    source_db: str | Path,
    skill: str,
    target_chip: str,
    target_fields: list[str] | None = None,
) -> dict[str, Any]:
    if skill not in HERMES_OPEN_WEB_SKILLS:
        raise ValueError(f"不支持的 Skill：{skill}。")
    source = Path(source_db).resolve()
    if not source.is_file():
        raise FileNotFoundError("正式数据库不存在。")
    if not str(target_chip).strip():
        raise ValueError("目标芯片不能为空。")
    _, skill_config = resolve_test_skill(skill)
    fields = _resolve_target_fields(skill_config, target_fields)
    before = database_fingerprints(source)
    workspace = create_test_workspace(source)
    folder = Path(workspace["db_path"]).parent
    marker = read_artifact(folder, "test_mode.json", default={})
    marker.update({
        "pipeline": "hermes-open-web",
        "schema_version": HERMES_OPEN_WEB_SCHEMA,
        "skill": skill,
        "target_chip": str(target_chip).strip(),
    })
    atomic_json(folder / "test_mode.json", marker)
    _ensure_test_tables(Path(workspace["db_path"]))
    manifest = {
        "schema_version": HERMES_OPEN_WEB_SCHEMA,
        "session_id": workspace["session_id"],
        "mode": "test",
        "skill": skill,
        "skill_label": SKILL_LABELS[skill],
        "target_chip": str(target_chip).strip(),
        "chips": [str(target_chip).strip()],
        "target_fields": fields,
        "status": "running",
        "stage": "waiting_for_hermes",
        "started_at": _now(),
        "finished_at": None,
        "formal_database_before": before,
        "formal_database_modified": False,
        "counts": {
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
    if value.get("schema_version") != HERMES_OPEN_WEB_SCHEMA:
        raise ValueError("运行清单版本不匹配。")
    return value


def save_manifest(folder: Path, manifest: dict[str, Any]) -> None:
    atomic_json(folder / "manifest.json", manifest)


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
