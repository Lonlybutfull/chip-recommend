"""Backend-only launcher for one Hermes-driven open-web test run."""

from __future__ import annotations

import os
import shlex
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

from chip_model.pipeline.hermes_open_web_contracts import HERMES_OPEN_WEB_SKILLS
from chip_model.pipeline.hermes_open_web_state import (
    TERMINAL_UNIT_STATUSES,
    aggregate_parent_manifest,
    create_hermes_open_web_run,
    load_manifest,
    load_unit_manifest,
    resolve_run_dir,
    resolve_unit_dir,
    save_manifest,
    save_unit_manifest,
)
from chip_model.pipeline.open_web_test import _now, database_fingerprints


Runner = Callable[..., Any]


def build_hermes_prompt(
    *, run_id: str, unit_id: str, scope_type: str, skill: str,
    target_chip: str, target_fields: list[str]
) -> str:
    fields = "、".join(target_fields) if target_fields else "该 Skill 的全部字段"
    target = target_chip or "开放互联网中的新芯片与新来源"
    return (
        f"RUN_ID={run_id}\n"
        f"UNIT_ID={unit_id}\n"
        f"SCOPE_TYPE={scope_type}\n"
        f"目标 Skill：{skill}\n"
        f"目标对象：{target}\n"
        f"目标字段：{fields}\n\n"
        "这是一次隔离测试。按照已加载 Skill 的完整操作规程完成本单元；"
        "Skill 是业务步骤、判断标准和输出合同的唯一来源。"
    )


def _default_command() -> list[str]:
    configured = os.getenv("HERMES_CLI_COMMAND", "hermes").strip()
    return shlex.split(configured, posix=os.name != "nt") or ["hermes"]


def run_hermes_open_web_test(
    *,
    source_db: str | Path,
    skill: str,
    target_chip: str | None = None,
    target_fields: list[str] | None = None,
    timeout: int = 900,
    runner: Runner = subprocess.run,
    hermes_command: list[str] | None = None,
    max_workers: int | None = None,
    max_attempts: int = 2,
    resume_run_id: str | None = None,
) -> dict[str, Any]:
    if skill not in HERMES_OPEN_WEB_SKILLS:
        raise ValueError(f"不支持的 Skill：{skill}。")
    source = Path(source_db).resolve()
    if resume_run_id:
        folder = resolve_run_dir(source, resume_run_id)
        manifest = load_manifest(folder)
        if manifest.get("skill") != skill:
            raise ValueError("续跑 Skill 与原运行不一致。")
        run = {**manifest, "db_path": str(folder / "data.db")}
    else:
        run = create_hermes_open_web_run(
            source_db=source,
            skill=skill,
            target_chip=target_chip,
            target_fields=target_fields,
        )
        folder = resolve_run_dir(source, run["session_id"])
    def execute_unit(unit_summary: dict[str, Any]) -> None:
        unit_id = str(unit_summary["unit_id"])
        unit_folder = resolve_unit_dir(source, run["session_id"], unit_id)
        unit = load_unit_manifest(unit_folder)
        if unit.get("status") in TERMINAL_UNIT_STATUSES:
            return
        last_error = ""
        for _ in range(max(1, int(max_attempts))):
            unit = load_unit_manifest(unit_folder)
            unit.update({
                "status": "running", "stage": "waiting_for_hermes",
                "started_at": unit.get("started_at") or _now(),
                "attempts": int(unit.get("attempts", 0)) + 1,
                "error": "",
            })
            save_unit_manifest(unit_folder, unit)
            prompt = build_hermes_prompt(
                run_id=run["session_id"], unit_id=unit_id,
                scope_type=str(unit["scope_type"]), skill=skill,
                target_chip=str(unit.get("target_chip") or ""),
                target_fields=list(unit["target_fields"]),
            )
            command = [
                *(hermes_command or _default_command()),
                "--skills", skill,
                "--toolsets", "aishperf_open_web",
                "-z", prompt,
            ]
            try:
                completed = runner(
                    command, capture_output=True, text=True,
                    timeout=max(30, int(timeout)), check=False,
                )
                last_error = str(
                    completed.stderr or completed.stdout or "Hermes 执行失败"
                )[:2000]
                (unit_folder / "hermes_stdout_tail.txt").write_text(
                    str(completed.stdout or "")[-4000:], encoding="utf-8"
                )
                unit = load_unit_manifest(unit_folder)
                if completed.returncode == 0 and unit.get("status") in TERMINAL_UNIT_STATUSES:
                    return
                if completed.returncode == 0:
                    last_error = "Hermes 已退出，但没有通过提交工具完成运行。"
            except Exception as exc:
                last_error = str(exc)[:2000]
        unit = load_unit_manifest(unit_folder)
        unit.update({
            "status": "failed", "stage": "completed", "finished_at": _now(),
            "error": last_error or "Hermes 执行失败。",
        })
        save_unit_manifest(unit_folder, unit)

    workers = max_workers
    if workers is None:
        workers = int(os.getenv("HERMES_OPEN_WEB_MAX_WORKERS", "1"))
    workers = max(1, min(int(workers), 8))
    units = list(load_manifest(folder).get("units") or [])
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(execute_unit, unit) for unit in units]
        for future in as_completed(futures):
            future.result()
            aggregate_parent_manifest(folder)

    manifest = aggregate_parent_manifest(folder)
    after = database_fingerprints(source)
    before = manifest.get("formal_database_before") or {}
    manifest["formal_database_after"] = after
    manifest["formal_database_modified"] = before.get("db") != after.get("db")
    if manifest["formal_database_modified"]:
        manifest.update({
            "status": "failed", "stage": "completed",
            "audit_warning": "检测到正式数据库主体文件发生变化。",
        })
    save_manifest(folder, manifest)
    return {**run, **manifest, "db_path": run["db_path"]}
