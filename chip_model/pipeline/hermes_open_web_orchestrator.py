"""Backend-only launcher for one Hermes-driven open-web test run."""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path
from typing import Any, Callable

from chip_model.pipeline.hermes_open_web_contracts import HERMES_OPEN_WEB_SKILLS
from chip_model.pipeline.hermes_open_web_state import (
    create_hermes_open_web_run,
    load_manifest,
    resolve_run_dir,
    save_manifest,
)
from chip_model.pipeline.open_web_test import _now, database_fingerprints


Runner = Callable[..., Any]


def build_hermes_prompt(
    *, run_id: str, skill: str, target_chip: str, target_fields: list[str]
) -> str:
    fields = "、".join(target_fields) if target_fields else "该 Skill 的全部字段"
    return (
        f"RUN_ID={run_id}\n"
        f"目标 Skill：{skill}\n"
        f"目标芯片：{target_chip}\n"
        f"目标字段：{fields}\n\n"
        "这是一次隔离测试。严格按照已加载 Skill 执行：先生成恰好 10 条互不重复的搜索词，"
        "调用 open_web_search；随后把返回的全部 candidate_id 交给 open_web_preview；"
        "阅读每个不超过 500 字的页面预览后，为每个候选给出选择或拒绝理由，最后调用 "
        "open_web_submit_selection。不得使用 Hermes 自带网络搜索，不得编造 URL，不得跳过候选。"
    )


def _default_command() -> list[str]:
    configured = os.getenv("HERMES_CLI_COMMAND", "hermes").strip()
    return shlex.split(configured, posix=os.name != "nt") or ["hermes"]


def run_hermes_open_web_test(
    *,
    source_db: str | Path,
    skill: str,
    target_chip: str,
    target_fields: list[str] | None = None,
    timeout: int = 900,
    runner: Runner = subprocess.run,
    hermes_command: list[str] | None = None,
) -> dict[str, Any]:
    if skill not in HERMES_OPEN_WEB_SKILLS:
        raise ValueError(f"不支持的 Skill：{skill}。")
    source = Path(source_db).resolve()
    run = create_hermes_open_web_run(
        source_db=source,
        skill=skill,
        target_chip=target_chip,
        target_fields=target_fields,
    )
    folder = resolve_run_dir(source, run["session_id"])
    prompt = build_hermes_prompt(
        run_id=run["session_id"], skill=skill, target_chip=target_chip,
        target_fields=list(run["target_fields"]),
    )
    command = [
        *(hermes_command or _default_command()),
        "--skills", skill,
        "--toolsets", "aishperf_open_web",
        "-z", prompt,
    ]
    completed = runner(
        command,
        capture_output=True,
        text=True,
        timeout=max(30, int(timeout)),
        check=False,
    )
    manifest = load_manifest(folder)
    if completed.returncode != 0:
        manifest.update({
            "status": "failed",
            "stage": "hermes_failed",
            "finished_at": _now(),
            "error": str(completed.stderr or completed.stdout or "Hermes 执行失败")[:2000],
        })
        save_manifest(folder, manifest)
    elif manifest.get("status") == "running":
        manifest.update({
            "status": "failed",
            "stage": "incomplete",
            "finished_at": _now(),
            "error": "Hermes 已退出，但没有通过提交工具完成运行。",
        })
        save_manifest(folder, manifest)
    manifest = load_manifest(folder)
    after = database_fingerprints(source)
    before = manifest.get("formal_database_before") or {}
    manifest["formal_database_after"] = after
    manifest["formal_database_modified"] = before.get("db") != after.get("db")
    manifest["hermes_stdout_tail"] = str(completed.stdout or "")[-2000:]
    save_manifest(folder, manifest)
    return {**run, **manifest, "db_path": run["db_path"]}
